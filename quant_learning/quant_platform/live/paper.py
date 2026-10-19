# -*- coding: utf-8 -*-
"""
live/paper.py —— 模拟盘（paper trading）自动交易守护进程
=========================================================
把"回测里表现好的模型"真正挂到账户上跑，但**默认不真发单**。

安全三道闸（缺一不可）：
    1. dry_run=True（默认）：只打印"打算下什么单"，一笔都不发。
    2. 账户本身是模拟盘 DU 开头（当前 DUT097472），即便发单也不会动真钱。
    3. RiskLimits 限额：单笔金额 / 单标的市值 / 当日下单数 / 总敞口。

运行方式：
    --once    跑一次就退出（适合先看看信号对不对）
    --daemon  常驻：每隔 N 秒检查一次，只在美股交易时段执行

取价说明：
    当前账户**未订阅实时行情**（IB 错误 10089），所以这里用"历史 K 线轮询"
    拿最新收盘价作为下单参考，而不是 tick 快照。日线级策略完全够用。

状态落盘：
    results/paper/<name>/state.json    最新目标仓位与实际持仓
    results/paper/<name>/signals.csv   每次检查的信号快照
    results/paper/<name>/orders.csv    每次产生的订单（含 dry-run 记录）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import CONFIG, RESULTS_DIR
from ..data import get_data
from ..strategies import create
from ..risk import RiskOverlay, apply_overlay
from ..portfolio import run_portfolio
from .broker import IBBroker, RiskLimits


# ---------------------------------------------------------------------------
# 交易时段（美股，美东时间；未装第三方日历库，自己算，够用）
# ---------------------------------------------------------------------------
ET = timezone(timedelta(hours=-4))      # 夏令时 EDT；冬令时差 1 小时，此处容忍


def is_us_market_open(now: Optional[datetime] = None) -> bool:
    """粗略判断美股是否处于交易时段（周一~周五 9:30-16:00 美东）。"""
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now <= close_t


@dataclass
class PaperConfig:
    name: str = "paper_demo"
    symbols: List[str] = field(default_factory=lambda: ["SPY", "QQQ"])
    strategy: str = "ADX趋势过滤"
    strategy_params: Dict = field(default_factory=dict)
    duration: str = "2 Y"
    bar_size: str = "1 day"
    scheme: str = "equal"                # 组合权重方案
    max_weight: float = 0.4
    max_leverage: float = 1.0
    capital: float = 100_000.0           # 用于模拟盘的资金规模（账户币种）
    overlay: Optional[RiskOverlay] = None
    dry_run: bool = True
    min_trade_value: float = 500.0       # 小于该金额的调仓直接跳过（省手续费）


class PaperTrader:
    """模拟盘交易员：信号 → 风控 → 组合权重 → 订单。"""

    def __init__(self, cfg: PaperConfig,
                 limits: Optional[RiskLimits] = None):
        self.cfg = cfg
        self.limits = limits or RiskLimits()
        self.broker = IBBroker(dry_run=cfg.dry_run, limits=self.limits)
        self.dir = Path(RESULTS_DIR) / "paper" / cfg.name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.dir / "state.json"
        self.signal_file = self.dir / "signals.csv"
        self.order_file = self.dir / "orders.csv"

    # ---------------- 核心：一次检查 ----------------
    def check(self, execute: bool = False) -> Dict:
        """
        跑一轮：取数 → 算信号 → 套风控 → 组合权重 → 生成订单。
        execute=False 时只返回计划，不调用下单接口（即便 cfg.dry_run=False）。
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data, sigs, raw_sigs = {}, {}, {}
        ov = self.cfg.overlay or RiskOverlay()

        for s in self.cfg.symbols:
            try:
                df = get_data(s, duration=self.cfg.duration,
                              bar_size=self.cfg.bar_size, refresh=True)
            except Exception as e:                             # noqa: BLE001
                print(f"[跳过] {s}: {str(e)[:80]}")
                continue
            if df is None or len(df) < 60:
                print(f"[跳过] {s}: 数据不足（{0 if df is None else len(df)} 根）")
                continue
            data[s] = df
            raw = create(self.cfg.strategy, **self.cfg.strategy_params).signals(df)
            raw_sigs[s] = raw
            sigs[s] = apply_overlay(df, raw, ov) if ov.active() else raw

        if not data:
            return {"ok": False, "reason": "无可用数据", "时间": ts}

        # 组合权重
        res = run_portfolio(data, sigs, name=self.cfg.name,
                            scheme=self.cfg.scheme,
                            max_weight=self.cfg.max_weight,
                            max_leverage=self.cfg.max_leverage)
        w = res.weights.iloc[-1]

        # 目标金额 → 目标股数
        last_px = {s: float(data[s]["close"].iloc[-1]) for s in data}
        targets = {}
        for s in w.index:
            px = last_px.get(s, np.nan)
            if np.isnan(px) or px <= 0 or abs(w[s]) < 1e-6:
                targets[s] = 0.0
                continue
            targets[s] = float(self.cfg.capital * w[s] / px)

        # 当前持仓（真实下单时才需要；dry-run 下也读，便于对照）
        current = {}
        try:
            pos = self.broker.positions()
            if len(pos):
                for _, r in pos.iterrows():
                    current[str(r["symbol"]).upper()] = float(r["position"])
        except Exception as e:                                 # noqa: BLE001
            print(f"[提示] 读取持仓失败：{str(e)[:60]}")

        # 生成订单
        orders = []
        for s, tgt_sh in targets.items():
            cur_sh = current.get(s.upper(), 0.0)
            delta = tgt_sh - cur_sh
            px = last_px.get(s, np.nan)
            value = abs(delta) * (px if not np.isnan(px) else 0.0)
            if abs(delta) < 1e-6 or value < self.cfg.min_trade_value:
                continue
            orders.append({
                "时间": ts, "标的": s, "现价": round(px, 4),
                "当前股数": round(cur_sh, 2), "目标股数": round(tgt_sh, 2),
                "变动": round(delta, 2), "金额": round(value, 2),
                "动作": "BUY" if delta > 0 else "SELL",
                "目标权重": round(float(w[s]), 4),
            })

        result = {
            "ok": True, "时间": ts, "dry_run": self.cfg.dry_run,
            "策略": self.cfg.strategy,
            "风控": (self.cfg.overlay.describe() if self.cfg.overlay else "无"),
            "标的数": len(data),
            "目标权重": {k: round(float(v), 4) for k, v in w.items()},
            "订单": orders,
            "组合指标": {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in res.metrics.items()
                         if k in ("年化收益率", "夏普比率", "最大回撤", "持仓占比")},
        }

        # 真实发单（双重确认：cfg.dry_run=False 且 execute=True）
        if execute and not self.cfg.dry_run and orders:
            self.broker.connect()
            for o in orders:
                info = self.broker.place_order(o["标的"], o["动作"], abs(o["变动"]),
                                               order_type="MKT", price_hint=o["现价"])
                o["执行结果"] = info.get("msg") or info.get("status") or str(info)
            self.broker.disconnect()
        else:
            for o in orders:
                o["执行结果"] = "DRY-RUN 未发单"

        self._persist(ts, sigs, orders, result)
        return result

    # ---------------- 落盘 ----------------
    def _persist(self, ts: str, sigs: Dict[str, pd.Series],
                 orders: List[Dict], result: Dict):
        # 信号快照
        rows = []
        for s, sig in sigs.items():
            rows.append({"时间": ts, "标的": s,
                         "信号": float(sig.iloc[-1]) if len(sig) else np.nan,
                         "5日前信号": float(sig.iloc[-6]) if len(sig) > 6 else np.nan})
        sig_df = pd.DataFrame(rows)
        header = not self.signal_file.exists()
        sig_df.to_csv(self.signal_file, mode="a", header=header,
                      index=False, encoding="utf-8-sig")

        # 订单
        if orders:
            od_df = pd.DataFrame(orders)
            header = not self.order_file.exists()
            od_df.to_csv(self.order_file, mode="a", header=header,
                         index=False, encoding="utf-8-sig")

        # 状态
        state = {k: v for k, v in result.items() if k != "订单"}
        state["订单数"] = len(orders)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, default=str)

    # ---------------- 常驻 ----------------
    def run_daemon(self, interval: int = 3600, max_rounds: Optional[int] = None,
                   ignore_hours: bool = False, execute: bool = False):
        """常驻循环。execute 必须为 True 且 cfg.dry_run=False 才会真发单。"""
        print(f"[paper:{self.cfg.name}] 启动 策略={self.cfg.strategy} "
              f"标的={self.cfg.symbols} 间隔={interval}s "
              f"dry_run={self.cfg.dry_run} execute={execute}")
        n = 0
        while max_rounds is None or n < max_rounds:
            n += 1
            open_now = is_us_market_open() or ignore_hours
            print(f"\n--- 第 {n} 轮 {datetime.now().strftime('%H:%M:%S')} "
                  f"({'交易时段' if open_now else '非交易时段'}) ---")
            try:
                r = self.check(execute=execute and open_now)
                print(self.format(r))
            except KeyboardInterrupt:
                print("\n[停止] 收到中断")
                break
            except Exception as e:                             # noqa: BLE001
                print(f"[错误] {type(e).__name__}: {str(e)[:200]}")
            if max_rounds is None or n < max_rounds:
                time.sleep(interval)
        print(f"[paper:{self.cfg.name}] 结束，共 {n} 轮")

    # ---------------- 展示 ----------------
    @staticmethod
    def format(result: Dict) -> str:
        if not result.get("ok"):
            return f"检查失败：{result.get('reason')}"
        lines = [
            f"{result['时间']} | 策略={result['策略']} | 风控={result['风控']} | "
            f"标的={result['标的数']} | dry_run={result['dry_run']}",
            f"  回测参考指标: {result.get('组合指标', {})}",
            f"  目标权重: {result.get('目标权重', {})}",
        ]
        if result["订单"]:
            lines.append("  订单计划:")
            for o in result["订单"]:
                lines.append(f"    {o['动作']:<4} {o['标的']:<6} "
                             f"{o['变动']:>10.2f} 股  @{o['现价']:<10.4f} "
                             f"≈{o['金额']:>12,.0f}  → {o['执行结果']}")
        else:
            lines.append("  无需调仓（变动低于阈值或已在目标仓位）")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 预置组合（把回测里表现好的那套直接拿来跑）
# ---------------------------------------------------------------------------
def default_paper_config(name: str = "adx_paper") -> PaperConfig:
    """
    回测结论驱动的默认配置：
      ADX趋势过滤 + 等权组合 + ATR 移动止损，在 8 标的上组合夏普 1.06 / 回撤 -16%，
      是"稳健性"最好的一套（走前验证样本外/样本内 0.86 也最高）。
    """
    return PaperConfig(
        name=name,
        symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META"],
        strategy="ADX趋势过滤",
        scheme="equal",
        max_weight=0.35,
        capital=100_000.0,
        overlay=RiskOverlay(name="ATR移动止损", atr_stop_k=3.0, atr_n=14),
        dry_run=True,
    )
