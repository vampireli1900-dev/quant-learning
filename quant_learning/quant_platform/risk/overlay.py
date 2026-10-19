# -*- coding: utf-8 -*-
"""
risk/overlay.py —— 可插拔风控叠加层
====================================
把"策略信号"和"风险管理"解耦：同一套信号，挂不同的风控，产出不同净值曲线。
这样就能回答一个实战里最关键的问题：

    "这个策略不行，是逻辑不行，还是风控没做好？"

六个开关（都为 None/0 时不生效，保证可叠加、可对比）：
    1. ATR 移动止损     atr_stop_k   —— 从持仓期最高价回撤 k×ATR 离场（趋势策略标配）
    2. 固定止损         stop_loss    —— 从入场价亏 x% 离场
    3. 固定止盈         take_profit  —— 盈利 x% 离场
    4. 最长持仓         max_hold     —— 持有 N 根 K 线强制离场（防僵尸仓）
    5. 波动率目标仓位   target_vol   —— 按目标年化波动缩放仓位（风险平价思想）
    6. 回撤熔断         dd_circuit   —— 组合从高点回撤 x% 后强制空仓 cooldown 根 K 线

叠加顺序（重要，决定了语义）：
    信号 → 波动率目标缩放 → 止损止盈/移动止损 → 回撤熔断
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd

from ..indicators import atr, realized_vol


@dataclass
class RiskOverlay:
    """风控参数集合。全默认 = 不做任何风控（方便做 A/B 对比）。"""
    name: str = "无风控"

    # --- 仓位规模 ---
    target_vol: Optional[float] = None     # 目标年化波动，如 0.15
    vol_lookback: int = 60
    max_leverage: float = 1.0              # 单标的仓位上限（1.0=不杠杆）
    max_position: float = 1.0              # 信号绝对仓位硬上限

    # --- 离场 ---
    atr_n: int = 14
    atr_stop_k: Optional[float] = None     # 如 3.0
    stop_loss: Optional[float] = None      # 如 0.08
    take_profit: Optional[float] = None    # 如 0.30
    max_hold: Optional[int] = None         # 如 120

    # --- 熔断 ---
    dd_circuit: Optional[float] = None     # 如 0.20（组合级回撤熔断）
    cooldown: int = 10                     # 熔断/止损后冷却的根数

    def active(self) -> bool:
        return any([
            self.target_vol, self.atr_stop_k, self.stop_loss,
            self.take_profit, self.max_hold, self.dd_circuit,
            self.max_leverage != 1.0, self.max_position != 1.0,
        ])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        if not self.active():
            return "无风控"
        parts = []
        if self.target_vol:
            parts.append(f"波动目标{self.target_vol:.0%}")
        if self.atr_stop_k:
            parts.append(f"ATR止损{self.atr_stop_k:g}")
        if self.stop_loss:
            parts.append(f"止损{self.stop_loss:.0%}")
        if self.take_profit:
            parts.append(f"止盈{self.take_profit:.0%}")
        if self.max_hold:
            parts.append(f"最长持仓{self.max_hold}")
        if self.dd_circuit:
            parts.append(f"回撤熔断{self.dd_circuit:.0%}")
        if self.max_leverage != 1.0:
            parts.append(f"杠杆上限{self.max_leverage:g}")
        return "+".join(parts)


# ---------------------------------------------------------------------------
# 预置风控方案（可直接点菜，也可做横向对比）
# ---------------------------------------------------------------------------
PRESETS: Dict[str, RiskOverlay] = {
    "无风控": RiskOverlay(name="无风控"),

    "ATR移动止损": RiskOverlay(name="ATR移动止损", atr_stop_k=3.0, atr_n=14),

    "固定止损8%": RiskOverlay(name="固定止损8%", stop_loss=0.08),

    "ATR止损+止盈": RiskOverlay(name="ATR止损+止盈", atr_stop_k=3.5,
                                take_profit=0.50, max_hold=180),

    "波动率目标15%": RiskOverlay(name="波动率目标15%", target_vol=0.15,
                                 vol_lookback=60, max_leverage=1.5),

    "波动目标+ATR止损": RiskOverlay(name="波动目标+ATR止损", target_vol=0.15,
                                    atr_stop_k=3.0, max_leverage=1.5),

    "全套风控": RiskOverlay(name="全套风控", target_vol=0.15, atr_stop_k=3.0,
                            stop_loss=0.12, max_hold=180,
                            dd_circuit=0.20, cooldown=10, max_leverage=1.5),
}


def preset(name: str) -> RiskOverlay:
    if name not in PRESETS:
        raise KeyError(f"未定义风控方案 {name}；可用：{list(PRESETS)}")
    return PRESETS[name]


# ---------------------------------------------------------------------------
# 各层实现
# ---------------------------------------------------------------------------
def vol_target_scale(close: pd.Series, target_vol: float, lookback: int = 60,
                     max_leverage: float = 2.0) -> pd.Series:
    """波动率目标缩放因子：目标波动 / 实际滚动波动，上限 max_leverage。"""
    rv = realized_vol(close, lookback).replace(0, np.nan)
    scale = (target_vol / rv).clip(upper=max_leverage)
    # 波动率数据不足的开头几根 K 线用 1 倍仓位，避免整段被 nan 吃掉
    return scale.fillna(1.0).clip(lower=0.0, upper=max_leverage)


def apply_atr_stop(df: pd.DataFrame, position: pd.Series,
                   atr_n: int = 14, k: float = 3.0,
                   stop_loss: Optional[float] = None,
                   take_profit: Optional[float] = None,
                   max_hold: Optional[int] = None,
                   cooldown: int = 0) -> pd.Series:
    """
    逐 bar 叠加 ATR 移动止损 / 固定止损 / 止盈 / 最长持仓 / 冷却期。

    与 engine.apply_stops 的区别：
        - 支持 ATR 移动止损（自适应，比固定百分比更贴合波动率）
        - 支持止损后冷却期（避免刚止损又被同一波信号拉回，反复打脸）
        - 冷却期内强制空仓
    """
    pos = position.fillna(0).astype(float).copy()
    a = atr(df, atr_n) if k else None
    high, low, close = df["high"].values, df["low"].values, df["close"].values
    p = pos.values.copy()
    n = len(p)

    if k:
        # ATR 前几根为 NaN，用 0 填充（等价于"波动未知时不触发 ATR 止损"）
        av = np.nan_to_num(np.asarray(pd.Series(a).bfill().ffill(), dtype=float),
                           nan=0.0, posinf=0.0, neginf=0.0)
    else:
        av = np.zeros(n)

    entry_price = None
    entry_i = 0
    peak = None
    cool_until = -1

    for i in range(n):
        # 冷却期内强制空仓
        if i <= cool_until:
            p[i] = 0.0
            entry_price = None
            peak = None
            continue

        if p[i] == 0:
            entry_price = None
            peak = None
            continue

        if entry_price is None:
            entry_price = close[i]
            entry_i = i
            side_i = 1.0 if p[i] > 0 else -1.0
            peak = high[i] if side_i > 0 else low[i]
            continue

        side = 1.0 if p[i] > 0 else -1.0
        peak = max(peak, high[i]) if side > 0 else min(peak, low[i])

        hit = False
        # ATR 移动止损
        if k and av[i] > 0:
            stop_px = peak - side * k * av[i]
            if side > 0 and low[i] <= stop_px:
                hit = True
            elif side < 0 and high[i] >= stop_px:
                hit = True
        # 固定止损
        if not hit and stop_loss:
            r = (low[i] / entry_price - 1) if side > 0 else (high[i] / entry_price - 1) * -1
            if r <= -abs(stop_loss):
                hit = True
        # 止盈
        if not hit and take_profit:
            r = (high[i] / entry_price - 1) if side > 0 else (low[i] / entry_price - 1) * -1
            if r >= abs(take_profit):
                hit = True
        # 最长持仓
        if not hit and max_hold and (i - entry_i) >= max_hold:
            hit = True

        if hit:
            p[i] = 0.0
            entry_price = None
            peak = None
            if cooldown > 0:
                cool_until = i + cooldown

    return pd.Series(p, index=position.index)


def apply_drawdown_circuit(df: pd.DataFrame, position: pd.Series,
                           dd_limit: float = 0.20, cooldown: int = 10) -> pd.Series:
    """
    回撤熔断：用**该持仓序列自身**的净值曲线判断，从高点回撤超过 dd_limit 后
    强制空仓 cooldown 根 K 线。这是真实基金的风控条款（"回撤 20% 清仓冷静期"）。
    """
    if not dd_limit:
        return position
    pos = position.fillna(0).astype(float).copy()
    ret = df["close"].pct_change().fillna(0.0)
    pnl = (pos.shift(1).fillna(0) * ret)
    equity = (1 + pnl).cumprod()
    peak = equity.cummax()
    dd = equity / peak - 1

    out = pos.values.copy()
    cool_until = -1
    for i in range(len(out)):
        if i <= cool_until:
            out[i] = 0.0
            continue
        if dd.iloc[i] <= -abs(dd_limit):
            out[i] = 0.0
            cool_until = i + cooldown
    return pd.Series(out, index=position.index)


def apply_overlay(df: pd.DataFrame, signal: pd.Series,
                  ov: RiskOverlay) -> pd.Series:
    """
    把风控叠加到信号上，输出最终目标仓位（尚未 shift，与 run_backtest 的约定一致：
    run_backtest 内部会再做 shift(1)）。

    调用顺序：
        signal → 仓位上限 → 波动率目标 → ATR/止损/止盈/最长持仓 → 回撤熔断
    """
    s = signal.reindex(df.index).fillna(0).astype(float)

    # 1) 硬上限
    s = s.clip(lower=-ov.max_position, upper=ov.max_position)
    s = s.clip(lower=-ov.max_leverage, upper=ov.max_leverage)

    # 2) 波动率目标
    if ov.target_vol:
        scale = vol_target_scale(df["close"], ov.target_vol,
                                 ov.vol_lookback, ov.max_leverage)
        s = s * scale
        s = s.clip(lower=-ov.max_leverage, upper=ov.max_leverage)

    # 3) 离场类（注意这里传的是"未 shift 的目标仓位"，与 run_backtest 一致）
    if any([ov.atr_stop_k, ov.stop_loss, ov.take_profit, ov.max_hold]):
        s = apply_atr_stop(df, s, atr_n=ov.atr_n,
                           k=ov.atr_stop_k or 0.0,
                           stop_loss=ov.stop_loss,
                           take_profit=ov.take_profit,
                           max_hold=ov.max_hold,
                           cooldown=ov.cooldown)

    # 4) 回撤熔断
    if ov.dd_circuit:
        s = apply_drawdown_circuit(df, s, ov.dd_circuit, ov.cooldown)

    return s


def overlay_grid() -> Dict[str, RiskOverlay]:
    """返回全部预置方案（供实验对比用）。"""
    return dict(PRESETS)
