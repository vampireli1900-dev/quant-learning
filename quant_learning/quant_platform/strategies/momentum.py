# -*- coding: utf-8 -*-
"""
strategies/momentum.py —— 动量 / 择时类策略
==========================================
核心假设："过去涨的将来还会涨"（时间序列动量）。
学术界最有名的证据是 AQR 的《A Century of Evidence on Trend-Following
Investing》，跨 100 年、多资产都有效。

本模块同时提供"波动率目标"版本：把仓位按近期波动倒数缩放，
这是机构最常用、也最容易验证有效的一类风险控制改进。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, ema, momentum, realized_vol, roc, sma
from .base import Strategy, StrategyMeta, register


@register
class TimeSeriesMomentum(Strategy):
    """
    时间序列动量（"绝对动量"）：
    过去 lookback 天（跳过最近 skip 天，避免短期反转效应）收益为正 → 满仓，
    否则空仓。这是最常见的 TAA(战术资产配置) 择时规则。
    """
    meta = StrategyMeta("时间序列动量", "动量",
                        "过去N期(跳过最近M期)收益>0则持仓", "Moskowitz & Pedersen")
    default_params = {"lookback": 120, "skip": 10}
    param_space = {"lookback": [60, 120, 180, 250], "skip": [0, 10, 20]}
    favored_regime = "中长期趋势"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        lb, sk = self.params["lookback"], self.params["skip"]
        c = df["close"]
        if sk > 0:
            ret = c.shift(sk) / c.shift(lb) - 1
        else:
            ret = c / c.shift(lb) - 1
        return (ret > 0).astype(float).fillna(0.0)


@register
class ROCTrendFollowing(Strategy):
    """ROC 变动率阈值：涨过阈值做多，跌破负阈值离场。"""
    meta = StrategyMeta("ROC动量阈值", "动量",
                        "N期收益率>阈值买入，<0卖出")
    default_params = {"n": 60, "threshold": 0.05}
    param_space = {"n": [30, 60, 120], "threshold": [0.0, 0.03, 0.05, 0.10]}
    favored_regime = "趋势启动"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        r = momentum(df["close"], self.params["n"])
        enter = r > self.params["threshold"]
        exit_ = r < 0
        from .base import stateful_position
        return stateful_position(enter, exit_, df.index)


@register
class VolTargetMomentum(Strategy):
    """
    动量 + 波动率目标仓位（连续权重）：
    信号不再是 0/1，而是"动量方向 × 目标波动/实际波动"，
    波动大时减仓、波动小时加仓（上限 max_w）。
    这是本平台里唯一默认输出连续仓位的策略，用于展示权重型信号。
    """
    meta = StrategyMeta("波动率目标动量", "动量",
                        "动量方向 × (目标波动/实际波动)，波动大自动减仓",
                        "风险平价思想")
    default_params = {"lookback": 120, "vol_lb": 60, "target_vol": 0.15, "max_w": 1.5}
    param_space = {"lookback": [60, 120, 250], "target_vol": [0.10, 0.15, 0.20],
                   "max_w": [1.0, 1.5, 2.0]}
    favored_regime = "任意（靠风控自适应）"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        mom = c / c.shift(self.params["lookback"]) - 1
        direction = pd.Series(np.where(mom > 0, 1.0, 0.0), index=df.index)
        vol = realized_vol(c, self.params["vol_lb"], annualize=True)
        scale = (self.params["target_vol"] / vol.replace(0, np.nan)).clip(
            upper=self.params["max_w"]).fillna(1.0)
        sig = (direction * scale).clip(0, self.params["max_w"])
        return sig.fillna(0.0)


@register
class ATRTrailingStop(Strategy):
    """
    ATR 移动止损（"让利润奔跑"）：
    只要价格站上 均线 就持仓，跌破 (最高价 - k×ATR) 就离场。
    这是趋势跟踪里最经典的仓位管理逻辑，也是海龟的核心之一。
    """
    meta = StrategyMeta("ATR移动止损", "趋势跟踪",
                        "价格>均线持仓，跌破 持仓期最高-k×ATR 离场", "海龟/Welles Wilder")
    default_params = {"ma": 50, "atr_n": 14, "k": 3.0}
    param_space = {"ma": [30, 50, 100], "atr_n": [10, 14, 20], "k": [2.0, 3.0, 4.0]}
    favored_regime = "大趋势"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c, high = df["close"], df["high"]
        ma = ema(c, self.params["ma"])
        a = atr(df, self.params["atr_n"])
        k = self.params["k"]

        pos, peak = [], None
        holding = False
        for i in range(len(df)):
            px, hi, m, av = c.iloc[i], high.iloc[i], ma.iloc[i], a.iloc[i]
            if np.isnan(m):
                pos.append(0.0)
                continue
            if not holding:
                if px > m:
                    holding, peak = True, hi
            else:
                peak = max(peak, hi)
                stop = peak - k * (av if not np.isnan(av) else 0)
                if px < stop:
                    holding = False
            pos.append(1.0 if holding else 0.0)
        return pd.Series(pos, index=df.index)
