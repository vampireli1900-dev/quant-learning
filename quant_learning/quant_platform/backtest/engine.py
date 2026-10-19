# -*- coding: utf-8 -*-
"""
backtest/engine.py —— 回测引擎
==============================
把"信号"变成"钱"。设计上坚持三条诚实原则：

    1. 无未来函数：今日持仓 = 昨日信号（signal.shift(1)），默认收盘价成交。
    2. 有成本：手续费（比例 + 每笔最低佣金）+ 滑点，按换手发生。
    3. 可风控：支持止损/止盈/移动止损、波动率目标仓位、最大杠杆限制。

支持的信号形式（pd.Series，与 df 索引对齐）：
    - 0/1        ：传统满仓/空仓
    - -1/0/1     ：可做空（需 allow_short=True）
    - 0~1 连续值 ：按信号强度分配仓位（如 ML 概率、波动率目标）

执行价：
    exec_price="close"  今日信号→明日收盘执行（最常用，与教学代码一致）
    exec_price="open"   今日信号→明日开盘执行（更贴近实盘）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ..config import CONFIG, BacktestConfig
from . import metrics as M


@dataclass
class BacktestResult:
    """一次回测的完整结果。"""
    name: str
    symbol: str = ""
    signal: Optional[pd.Series] = None
    position: Optional[pd.Series] = None
    returns: Optional[pd.Series] = None
    equity: Optional[pd.Series] = None
    trades: Optional[pd.DataFrame] = None
    metrics: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    PCT_KEYS = {"总收益率", "年化收益率", "年化波动率", "下行波动率", "最大回撤",
                "胜率", "月度胜率", "单日最大亏损", "VaR95(日)", "CVaR95(日)",
                "跟踪误差", "持仓占比", "单笔均值", "最好一笔", "最差一笔",
                "年化Alpha", "相对基准超额"}
    INT_KEYS = {"交易次数", "回撤持续期", "平均持仓天数", "最长连亏(月)"}

    def summary(self, keys: Optional[list] = None) -> str:
        keys = keys or ["年化收益率", "夏普比率", "最大回撤", "卡玛比率",
                        "交易次数", "胜率", "盈亏比"]
        lines = [f"—— {self.name} ——"]
        for k in keys:
            v = self.metrics.get(k, np.nan)
            if isinstance(v, float) and not np.isnan(v):
                if k in self.INT_KEYS:
                    lines.append(f"  {k:<10}: {int(v)}")
                elif k in self.PCT_KEYS:
                    lines.append(f"  {k:<10}: {v:>8.2%}")
                else:
                    lines.append(f"  {k:<10}: {v:>8.2f}")
        return "\n".join(lines)


def apply_stops(df: pd.DataFrame, position: pd.Series,
                stop_loss: Optional[float] = None,
                take_profit: Optional[float] = None,
                trailing_stop: Optional[float] = None,
                max_hold: Optional[int] = None) -> pd.Series:
    """
    在持仓序列上叠加止损/止盈/移动止损/最长持仓。

    注意：这里用日内 high/low 判断，属于"乐观成交"假设（假设触发即成交）。
    更严格的做法是假设跳空，本平台的默认假设已足够用于策略对比。

    stop_loss    : 如 0.08 表示从入场价亏 8% 离场
    take_profit  : 如 0.20 表示盈利 20% 离场
    trailing_stop: 如 0.10 表示从持仓期间最高价回撤 10% 离场
    max_hold     : 最多持有 N 根 K 线
    """
    if not any([stop_loss, take_profit, trailing_stop, max_hold]):
        return position

    pos = position.fillna(0).astype(float).copy()
    high, low, close = df["high"].values, df["low"].values, df["close"].values
    p = pos.values.copy()
    n = len(p)

    entry_price = None
    entry_i = 0
    peak = None
    for i in range(n):
        cur = p[i]
        if cur != 0 and entry_price is None:
            entry_price = close[i]
            entry_i = i
            peak = high[i]
            continue
        if entry_price is None:
            continue

        peak = max(peak, high[i])
        px_low, px_high = low[i], high[i]
        ret_low = (px_low / entry_price - 1) * np.sign(p[i])
        ret_high = (px_high / entry_price - 1) * np.sign(p[i])
        dd_from_peak = (px_low / peak - 1) * np.sign(p[i])

        hit = False
        if stop_loss is not None and ret_low <= -abs(stop_loss):
            hit = True
        elif take_profit is not None and ret_high >= abs(take_profit):
            hit = True
        elif trailing_stop is not None and dd_from_peak <= -abs(trailing_stop):
            hit = True
        elif max_hold is not None and (i - entry_i) >= max_hold:
            hit = True

        if hit:
            # 触发日及之后先离场（后续再由新信号重新进场）
            p[i] = 0.0
            entry_price = None
            peak = None

    return pd.Series(p, index=position.index)


def volatility_target(signal: pd.Series, close: pd.Series,
                      target_vol: float = 0.15, lookback: int = 60,
                      max_leverage: float = 2.0) -> pd.Series:
    """
    波动率目标仓位：把 0/1 信号缩放到"使组合年化波动接近 target_vol"。
    这是机构常用的风险平价思想，能显著改善不同标的间的可比性。
    """
    rv = close.pct_change().rolling(lookback, min_periods=max(10, lookback // 2)).std() * np.sqrt(252)
    scale = (target_vol / rv.replace(0, np.nan)).clip(upper=max_leverage).fillna(1.0)
    return (signal * scale).clip(lower=-max_leverage, upper=max_leverage)


def run_backtest(df: pd.DataFrame, signal: pd.Series,
                 name: str = "strategy", symbol: str = "",
                 cfg: Optional[BacktestConfig] = None,
                 commission_rate: Optional[float] = None,
                 slippage_rate: Optional[float] = None,
                 min_commission: Optional[float] = None,
                 allow_short: Optional[bool] = None,
                 initial_capital: Optional[float] = None,
                 exec_price: str = "close",
                 vol_target: Optional[float] = None,
                 max_leverage: float = 2.0,
                 stop_loss: Optional[float] = None,
                 take_profit: Optional[float] = None,
                 trailing_stop: Optional[float] = None,
                 max_hold: Optional[int] = None,
                 benchmark: bool = True,
                 params: Optional[dict] = None) -> BacktestResult:
    """
    执行回测，返回 BacktestResult。

    参数说明见文件头；其中 stop_loss/take_profit 等风控参数会在
    「持仓层面」生效（逐 bar 判断 high/low）。
    """
    cfg = cfg or CONFIG.backtest
    commission_rate = cfg.commission_rate if commission_rate is None else commission_rate
    slippage_rate = cfg.slippage_rate if slippage_rate is None else slippage_rate
    min_commission = cfg.min_commission if min_commission is None else min_commission
    allow_short = cfg.allow_short if allow_short is None else allow_short
    initial_capital = cfg.initial_capital if initial_capital is None else initial_capital

    df = df.copy()
    signal = signal.reindex(df.index).fillna(0).astype(float)

    # ---- 1) 信号 → 目标仓位（明日执行） ----
    target = signal.shift(1).fillna(0.0)
    if vol_target is not None:
        target = volatility_target(target, df["close"], target_vol=vol_target,
                                   max_leverage=max_leverage)
    if not allow_short:
        target = target.clip(lower=0.0)
    target = target.clip(lower=-max_leverage, upper=max_leverage)

    # ---- 2) 风控叠加（止损/止盈/移动止损/最长持仓） ----
    position = target.copy()
    if any([stop_loss, take_profit, trailing_stop, max_hold]):
        position = apply_stops(df, position, stop_loss, take_profit,
                               trailing_stop, max_hold)

    # ---- 3) 资产收益 ----
    if exec_price == "open" and "open" in df.columns:
        op = df["open"]
        asset_ret = (op.shift(-1) / op - 1).fillna(0.0)
    else:
        asset_ret = df["close"].pct_change().fillna(0.0)

    gross_ret = position * asset_ret

    # ---- 4) 成本：换手 × (手续费率 + 滑点)，并考虑每笔最低佣金 ----
    turnover = position.diff().abs().fillna(position.abs())
    equity_pre = (1 + gross_ret).cumprod() * initial_capital
    cost_ratio = turnover * (commission_rate + slippage_rate)

    # 最低佣金：只对"成规模的调仓"生效。
    # 重要：连续仓位策略（如波动率目标）每天都在微调仓位，如果对 0.01% 的
    # 微小换手也收一次最低佣金，成本会被放大到荒谬的程度（早期版本正是
    # 在这里把净值算成了负数）。因此设定 min_trade_frac 阈值。
    min_trade_frac = 0.01          # 换手达到权益的 1% 才算"一笔完整交易"
    if min_commission and min_commission > 0:
        notional = (equity_pre * turnover).shift(1).fillna(initial_capital)
        raw = min_commission / notional.replace(0, np.nan)
        min_cost = np.where(turnover >= min_trade_frac, np.nan_to_num(raw), 0.0)
        # 单日佣金成本封顶 1%，避免极小换手下的数值爆炸
        min_cost = np.minimum(min_cost, 0.01)
        cost_ratio = np.maximum(cost_ratio, min_cost)
    cost = pd.Series(cost_ratio, index=df.index).fillna(0.0)

    net_ret = gross_ret - cost
    # 破产保护：单日亏损不可能超过全部本金（理论上连续权重+跳空可能算出 <-100%，
    # 现实中会被强平/归零），这里截断到 -99% 并保留"归零"语义。
    net_ret = net_ret.clip(lower=-0.99)
    equity = (1 + net_ret).cumprod() * initial_capital

    # ---- 5) 基准（买入持有） ----
    bench_ret = asset_ret if benchmark else None

    # ---- 6) 指标 ----
    m = M.compute_metrics(net_ret, equity, position, df["close"],
                          benchmark_returns=bench_ret,
                          risk_free_rate=cfg.risk_free_rate)
    trades = M.extract_trades(position, df["close"])

    return BacktestResult(
        name=name, symbol=symbol, signal=signal, position=position,
        returns=net_ret, equity=equity, trades=trades, metrics=m,
        params=params or {},
    )


def backtest_buy_hold(df: pd.DataFrame, name: str = "买入持有",
                      cfg: Optional[BacktestConfig] = None,
                      initial_capital: Optional[float] = None) -> BacktestResult:
    """基准：买入持有。"""
    cfg = cfg or CONFIG.backtest
    initial_capital = initial_capital or cfg.initial_capital
    ret = df["close"].pct_change().fillna(0.0)
    equity = (1 + ret).cumprod() * initial_capital
    pos = pd.Series(1.0, index=df.index)
    m = M.compute_metrics(ret, equity, pos, df["close"], benchmark_returns=ret,
                          risk_free_rate=cfg.risk_free_rate)
    return BacktestResult(name=name, symbol="", signal=pos, position=pos,
                          returns=ret, equity=equity,
                          trades=M.extract_trades(pos, df["close"]), metrics=m)
