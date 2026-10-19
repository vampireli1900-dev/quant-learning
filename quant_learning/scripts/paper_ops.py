# -*- coding: utf-8 -*-
"""
scripts/paper_ops.py —— 模拟盘（paper）真实下单运营入口
========================================================
按实验里**统计显著**的模型在盈透模拟账户上真实委托，全程有安全闸。

为什么选这两个模型（证据来自 results/enhanced/significance.csv）：
    ADX趋势过滤  组合夏普 1.06 / PSR 0.992 / DSR 0.932，走前样本外衰减最小
    ZScore回归   组合夏普 1.05 / PSR 0.994 / DSR 0.939
    —— 只有这两个 DSR 接近 0.94、且所需最小样本量(538~581)远小于实际样本(1254)，
       即"不是靠试次数堆出来的"。时间序列动量虽显著但走前衰减明显，仅作参考。
风控叠加层：默认**不启用**。
    证据（results/enhanced/risk_ab_summary.csv）：ADX 无风控 0.606 > ATR移动止损 0.495；
    ZScore 无风控 0.699 > 固定止损8% 0.462。日线级止损对这两个模型是负优化。
    改用账户级熔断：净值从峰值回撤超过阈值则停止开新仓（保守起见预留 liquidate 选项）。

安全三道闸：
    1. 账户必须是模拟盘（所有 managedAccounts 以 DU 开头），否则拒绝执行。
    2. 默认 dry_run=True，必须显式 --execute --yes 才真发单。
    3. RiskLimits：单笔金额 / 单标的市值 / 当日笔数 / 总敞口。

用法：
    python scripts/paper_ops.py --once                       # 只看计划
    python scripts/paper_ops.py --once --execute --yes        # 真实下单一次
    python scripts/paper_ops.py --daemon --interval 3600 --execute --yes
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant_platform.config import CONFIG, RESULTS_DIR, IBConfig  # noqa: E402
from quant_platform.data import get_data                        # noqa: E402
from quant_platform.strategies import create                    # noqa: E402
from quant_platform.risk import RiskOverlay, apply_overlay      # noqa: E402
from quant_platform.portfolio import run_portfolio, compute_weights  # noqa: E402
from quant_platform.live.broker import IBBroker, RiskLimits     # noqa: E402
from quant_platform.live.paper import is_us_market_open        # noqa: E402


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META"]
MODELS = ["ADX趋势过滤", "ZScore回归"]        # 双模型投票
DURATION = "5 Y"
BAR_SIZE = "1 day"

# 运营参数（可命令行覆盖）
CAPITAL_FRACTION = 0.20        # 首轮投入 20% 净值 ≈ HKD 20 万
MAX_WEIGHT = 0.35              # 单标的占策略资金上限
MIN_TRADE_VALUE = 800.0        # 低于该金额不调仓（HKD，省手续费）
MAX_DRAWDOWN_HALT = 0.15       # 账户级熔断：回撤超 15% 停止开新仓


def _log(msg: str):
    print(msg, flush=True)


def _to_float(v) -> float:
    try:
        return float(v)
    except Exception:                                          # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# 数据库落库（旁路：任何失败都只打日志，不影响下单主流程）
# ---------------------------------------------------------------------------
def _db_enabled() -> bool:
    try:
        from quant_platform.db.repository import DB_ENABLED
        return bool(DB_ENABLED)
    except Exception:                                          # noqa: BLE001
        return False


def _persist_account(ts: datetime, account: str, summary: dict):
    if not _db_enabled():
        return
    try:
        from quant_platform.db import save_account_snapshot
        save_account_snapshot(ts, account, summary)
    except Exception as e:                                     # noqa: BLE001
        _log(f"  [db] 账户快照落库失败：{str(e)[:60]}")


def _persist_positions(ts: datetime, account: str, positions):
    if not _db_enabled() or positions is None or not len(positions):
        return
    try:
        from quant_platform.db import save_positions
        save_positions(ts, account, positions)
    except Exception as e:                                     # noqa: BLE001
        _log(f"  [db] 持仓快照落库失败：{str(e)[:60]}")


def _persist_order(order: dict):
    if not _db_enabled():
        return
    try:
        from quant_platform.db import save_order
        save_order(order)
    except Exception as e:                                     # noqa: BLE001
        _log(f"  [db] 委托记录落库失败：{str(e)[:60]}")


# ---------------------------------------------------------------------------
# 账户检查 / 取价 / 汇率
# ---------------------------------------------------------------------------
def assert_paper_account(broker: IBBroker) -> str:
    """
    硬闸：只允许在模拟盘账户上执行。
    IB 的模拟账户编号以 D 开头（DU = 模拟，U = 真实）。
    """
    accts = broker.ib.managedAccounts()
    if not accts:
        raise RuntimeError("账户列表为空，无法确认是否为模拟盘")
    bad = [a for a in accts if not a.upper().startswith("DU")]
    if bad:
        raise RuntimeError(
            f"拒绝执行：检测到非模拟盘账户 {bad}（模拟账户应为 DU 开头）。"
            "真实账户下单需要另行确认。")
    return ",".join(accts)


def usd_hkd_rate(broker: IBBroker) -> float:
    """取 USD.HKD 汇率（账户是港币，标的都是美元，需要折算股数）。"""
    from quant_platform.live.broker import fx_rate
    return fx_rate(broker, "USD", "HKD")


def latest_prices(data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
    return {s: float(d["close"].iloc[-1]) for s, d in data.items()}


def et_today() -> datetime.date:
    """当前美东日期（用于判断最后一根日线是否已收盘）。"""
    return datetime.now(timezone(timedelta(hours=-4))).date()


def drop_incomplete_bar(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """
    剔掉"当天"那根未收盘的日线。

    为什么：IB 在盘中也会把当日 bar 返回出来（close 是当前价）。
    日线策略的信号应以**已收盘**数据为准，再用最新成交价执行；
    否则等于拿一个还没定盘的价格当信号输入，回测无法复现。
    """
    if df is None or not len(df):
        return df
    today = et_today()
    idx = pd.to_datetime(df.index)
    keep = idx.date < today
    return df[keep] if keep.any() else df


# ---------------------------------------------------------------------------
# 信号：多模型投票
# ---------------------------------------------------------------------------
def build_signals(data: Dict[str, pd.DataFrame],
                  models: List[str],
                  overlay: Optional[RiskOverlay]) -> tuple:
    """
    对每个标的，把各模型的仓位信号**裁剪后取平均**（等权投票）。
    返回 (组合信号, 每模型最新信号快照)
    """
    combined, snapshot = {}, []
    for s, df in data.items():
        sigs = []
        row = {"标的": s}
        for m in models:
            sig = pd.Series(create(m).signals(df), index=df.index).fillna(0.0)
            sig = sig.clip(-1, 1)
            if overlay is not None and overlay.active():
                sig = apply_overlay(df, sig, overlay)
            sigs.append(sig)
            row[m] = round(float(sig.iloc[-1]), 4)
        snapshot.append(row)
        combined[s] = pd.concat(sigs, axis=1).mean(axis=1)
    return combined, snapshot


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_round(cfg_capital: float, fraction: float, symbols: List[str],
              models: List[str], overlay: Optional[RiskOverlay],
              execute: bool, min_trade_value: float,
              dd_halt: float, offline: bool = False,
              ignore_hours: bool = False) -> dict:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    limits = RiskLimits(max_order_value=100_000.0,
                        max_position_value=250_000.0,
                        max_daily_orders=40,
                        max_gross_exposure=250_000.0)
    # 数据层也会建一条 IB 连接，broker 必须用不同的高位 clientId，
    # 否则两边抢号（IB 错误 326），表现为汇率/持仓偶发取不到
    import random
    bcfg = IBConfig(host=CONFIG.ib.host, port=CONFIG.ib.port,
                    client_id=random.randint(300, 900),
                    timeout=CONFIG.ib.timeout)
    broker = IBBroker(cfg=bcfg, dry_run=not execute, limits=limits)

    netliq, current = 0.0, {}
    if not offline:
        _log(f"[{ts}] 连接盈透…")
        if not broker.connect():
            return {"ok": False, "reason": "无法连接 TWS/Gateway", "时间": ts}
        if execute and not (is_us_market_open() or ignore_hours):
            return {"ok": False,
                    "reason": "非美股交易时段（DAY 单会失效），已跳过下单；"
                              "如需强制请加 --ignore-hours",
                    "时间": ts}
    try:
        if offline:
            acct_str = "OFFLINE（缓存预演）"
            _log(f"[{ts}] 离线预演：使用本地缓存数据，不发单")
        else:
            acct_str = assert_paper_account(broker)
            acc = broker.account()
            netliq = _to_float(acc.get("NetLiquidation") or acc.get("TotalCashValue"))
            _log(f"  账户 {acct_str} | 净值 {netliq:,.0f}")
            _persist_account(datetime.now(), acct_str, acc)   # 仅真实连接时落库
            try:
                pos = broker.positions()
                if len(pos):
                    for _, r in pos.iterrows():
                        current[str(r["symbol"]).upper()] = float(r["position"])
                    _persist_positions(datetime.now(), acct_str, pos)
            except Exception as e:                             # noqa: BLE001
                _log(f"  [提示] 读持仓失败：{str(e)[:60]}")

        # 取数（剔除未收盘的当日 bar，保证信号可复现）
        data, ref_px = {}, {}
        for s in symbols:
            try:
                df = get_data(s, duration=DURATION, bar_size=BAR_SIZE,
                              refresh=not offline)
            except Exception as e:                             # noqa: BLE001
                _log(f"  [跳过] {s}: {str(e)[:70]}")
                continue
            if df is None or len(df) < 120:
                _log(f"  [跳过] {s}: 数据不足")
                continue
            full_last = float(df["close"].iloc[-1])
            df = drop_incomplete_bar(df)
            if df is None or len(df) < 120:
                _log(f"  [跳过] {s}: 剔除当日 bar 后数据不足")
                continue
            data[s] = df
            ref_px[s] = full_last          # 参考价用最新价，信号用已收盘数据
        if not data:
            return {"ok": False, "reason": "无可用数据", "时间": ts}

        # 信号（双模型投票）
        sigs, snapshot = build_signals(data, models, overlay)

        # 组合权重（等权 + 集中度上限）
        # 注意：run_portfolio 内部会把信号 shift(1) 以避免未来函数，
        #      所以它输出的 weights.iloc[-1] 用的是**昨天**的信号 —— 回测正确，
        #      但实盘建仓必须用**今天**的信号，因此这里直接调用 compute_weights。
        res = run_portfolio(data, sigs, name="+".join(models), scheme="equal",
                            max_weight=MAX_WEIGHT, max_leverage=1.0)
        sig_df = pd.DataFrame({k: v.reindex(data[k].index) for k, v in sigs.items()})
        ret_df = pd.DataFrame({k: data[k]["close"].pct_change() for k in sig_df.columns})
        ret_df = ret_df.dropna(how="all").fillna(0.0)
        sig_df = sig_df.reindex(ret_df.index).fillna(0.0)
        w = compute_weights(sig_df, ret_df, scheme="equal",
                             vol_lookback=60, max_weight=MAX_WEIGHT,
                            max_leverage=1.0).iloc[-1]

        px = ref_px
        fx = usd_hkd_rate(broker) if not offline else 7.8

        # 策略资金 = 净值 × 比例；目标股数按 (资金×权重) / (美元价×汇率)
        capital = (netliq * fraction) if netliq else cfg_capital
        targets = {}
        for s in w.index:
            p = px.get(s, np.nan)
            if np.isnan(p) or p <= 0 or abs(w[s]) < 1e-6:
                targets[s] = 0.0
            else:
                targets[s] = float(capital * w[s] / (p * fx))

        # 熔断检查：用 state 里记录的历史峰值
        dir_ = Path(RESULTS_DIR) / "paper" / "ops"
        dir_.mkdir(parents=True, exist_ok=True)
        state_f = dir_ / "state.json"
        prev = {}
        if state_f.exists():
            try:
                prev = json.loads(state_f.read_text(encoding="utf-8"))
            except Exception:                                  # noqa: BLE001
                prev = {}
        peak = float(prev.get("净值峰值", netliq or 0.0))
        peak = max(peak, netliq or 0.0)
        dd = (netliq / peak - 1.0) if peak else 0.0
        halted = dd <= -abs(dd_halt)

        # 生成订单
        orders = []
        for s, tgt in targets.items():
            cur = current.get(s.upper(), 0.0)
            delta = tgt - cur
            p = px.get(s, np.nan)
            value = abs(delta) * (p if not np.isnan(p) else 0.0) * fx
            if abs(delta) < 1e-6 or value < min_trade_value:
                continue
            action = "BUY" if delta > 0 else "SELL"
            if action == "BUY" and halted:
                _log(f"  [熔断] 回撤 {dd:.2%} 超过阈值，跳过 {s} 开新仓")
                continue
            # 限价以昨收为基准加/减 0.5% 缓冲，提高成交概率又不追高
            lim = round(p * (1.005 if action == "BUY" else 0.995), 2)
            # 整股：美股不支持碎股，取整（至少 1 股）
            qty = max(1, int(round(abs(delta))))
            if qty * p * fx < min_trade_value:
                continue
            o = {"时间": ts, "标的": s, "动作": action, "股数": qty,
                 "参考价(USD)": round(p, 4), "限价(USD)": lim,
                 "金额(HKD)": round(qty * p * fx, 0), "汇率USD.HKD": round(fx, 4)}
            if execute:
                try:
                    info = broker.place_order(s, action, qty,
                                              order_type="LMT", limit_price=lim,
                                              price_hint=p)
                    o["执行"] = info.get("status") or info.get("reason") or str(info)
                    o["订单号"] = info.get("order_id")
                except Exception as e:                         # noqa: BLE001
                    o["执行"] = f"失败：{type(e).__name__}: {str(e)[:80]}"
            else:
                o["执行"] = "DRY-RUN 未发单"
            # 落库：委托计划 + 执行结果一并入库，便于事后审计与复盘
            _persist_order({**o, "账户": acct_str, "round_id": ts})
            orders.append(o)

        result = {
            "ok": True, "时间": ts, "execute": execute,
            "账户": acct_str, "净值(HKD)": round(netliq, 2),
            "策略资金(HKD)": round(capital, 2),
            "净值峰值(HKD)": round(peak, 2),
            "当前回撤": round(dd, 4), "熔断停机": halted,
            "模型": models,
            "目标权重": {k: round(float(v), 4) for k, v in w.items()},
            "当前持仓(股)": current,
            "目标股数": {k: round(v, 2) for k, v in targets.items()},
            "订单": orders,
        }

        # 落盘
        with open(state_f, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        od_f = dir_ / "orders.csv"
        if orders:
            pd.DataFrame(orders).to_csv(od_f, mode="a",
                                        header=not od_f.exists(),
                                        index=False, encoding="utf-8-sig")
        pf = dir_ / "portfolio.csv"
        pd.DataFrame({"时间": ts,
                      **{f"w_{k}": round(float(v), 4) for k, v in w.items()}},
                     index=[0]).to_csv(pf, mode="a",
                                        header=not pf.exists(), index=False,
                                        encoding="utf-8-sig")
    finally:
        broker.disconnect()

    return result


def main():
    ap = argparse.ArgumentParser(description="模拟盘运营（真实下单）")
    ap.add_argument("--once", action="store_true", help="只跑一轮")
    ap.add_argument("--daemon", action="store_true", help="常驻")
    ap.add_argument("--interval", type=int, default=3600)
    ap.add_argument("--capital", type=float, default=100_000.0,
                    help="取不到账户净值时的兜底资金（HKD）")
    ap.add_argument("--fraction", type=float, default=CAPITAL_FRACTION,
                    help="投入净值比例，默认 0.20")
    ap.add_argument("--symbols", nargs="*", default=SYMBOLS)
    ap.add_argument("--models", nargs="*", default=MODELS)
    ap.add_argument("--overlay", default=None,
                    help="风控方案名，默认不启用（回测证据：日线止损是负优化）")
    ap.add_argument("--min-trade-value", type=float, default=MIN_TRADE_VALUE)
    ap.add_argument("--execute", action="store_true", help="真实发单")
    ap.add_argument("--yes", action="store_true", help="二次确认")
    ap.add_argument("--ignore-hours", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="离线预演：仅用缓存数据算信号与订单计划，不连 IB")
    a = ap.parse_args()

    if a.execute and not a.yes:
        print("拒绝执行：请同时提供 --execute --yes")
        return

    ov = None
    if a.overlay:
        from quant_platform.risk import PRESETS
        if a.overlay not in PRESETS:
            print(f"未知风控方案 {a.overlay}，可选：{list(PRESETS)}")
            return
        ov = PRESETS[a.overlay]

    if a.daemon:
        n = 0
        while True:
            n += 1
            open_now = is_us_market_open() or a.ignore_hours
            _log(f"\n===== 第 {n} 轮 {datetime.now():%H:%M:%S} "
                 f"({'交易时段' if open_now else '非交易时段'}) =====")
            try:
                r = run_round(a.capital, a.fraction, a.symbols, a.models,
                              ov, execute=(a.execute and open_now),
                              min_trade_value=a.min_trade_value,
                              dd_halt=MAX_DRAWDOWN_HALT,
                              offline=a.offline, ignore_hours=a.ignore_hours)
                _log(_fmt(r))
            except KeyboardInterrupt:
                _log("[停止] 收到中断")
                break
            except Exception as e:                             # noqa: BLE001
                _log(f"[错误] {type(e).__name__}: {str(e)[:200]}")
            time.sleep(a.interval)
    else:
        r = run_round(a.capital, a.fraction, a.symbols, a.models, ov,
                      execute=a.execute, min_trade_value=a.min_trade_value,
                      dd_halt=MAX_DRAWDOWN_HALT, offline=a.offline,
                      ignore_hours=a.ignore_hours)
        _log(_fmt(r))


def _fmt(r: dict) -> str:
    if not r.get("ok"):
        return f"失败：{r.get('reason')}"
    lines = [
        f"{r['时间']} | 账户 {r['账户']} 净值 {r['净值(HKD)']:,.0f} "
        f"| 策略资金 {r['策略资金(HKD)']:,.0f} | 回撤 {r['当前回撤']:.2%}"
        + ("  ⚠熔断" if r["熔断停机"] else ""),
        f"  模型: {' + '.join(r['模型'])}",
        f"  当前持仓: {r['当前持仓(股)']}",
        f"  目标股数: {r['目标股数']}",
    ]
    if r["订单"]:
        lines.append("  订单:")
        for o in r["订单"]:
            lines.append(f"    {o['动作']:<4} {o['标的']:<6} {o['股数']:>9.2f} 股 "
                         f"限价 {o['限价(USD)']:<9.2f} USD ≈ {o['金额(HKD)']:>10,.0f} HKD"
                         f"  → {o['执行']}")
    else:
        lines.append("  无需调仓（已在目标仓位或低于最小交易额）")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
