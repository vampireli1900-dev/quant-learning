# -*- coding: utf-8 -*-
"""
strategies/mean_reversion.py —— 均值回归类策略
==============================================
核心假设："价格偏离均值后会被拉回"。
典型表现：胜率高(60~80%)、盈亏比低、震荡市赚钱，遇到单边大趋势会爆亏
（所以实盘必须配合止损/趋势过滤）。
"""

from __future__ import annotations

import pandas as pd

from ..indicators import bollinger, rsi, sma, zscore
from .base import Strategy, StrategyMeta, register, stateful_position


@register
class RSIMeanReversion(Strategy):
    """RSI 超卖买入、超买卖出。"""
    meta = StrategyMeta("RSI均值回归", "均值回归",
                        "RSI<超卖线买入，RSI>超买线卖出", "Wilder")
    default_params = {"window": 14, "oversold": 30, "overbought": 70}
    param_space = {"window": [7, 14, 21], "oversold": [20, 25, 30], "overbought": [70, 75, 80]}
    favored_regime = "震荡市"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        r = rsi(df["close"], self.params["window"])
        enter = r < self.params["oversold"]
        exit_ = r > self.params["overbought"]
        return stateful_position(enter, exit_, df.index)


@register
class RSI2Extreme(Strategy):
    """
    RSI-2 极端反转（Larry Connors 经典）：
    在"价格处于长期均线上方"的前提下，2 日 RSI 极度超卖时买入，
    回到 50 以上就走。逻辑：顺大势、逆小势。
    """
    meta = StrategyMeta("RSI2极端反转", "均值回归",
                        "价格>200均线 且 2日RSI<5 买入，RSI>50卖出", "Larry Connors")
    default_params = {"window": 2, "entry": 5, "exit": 50, "trend_ma": 200}
    param_space = {"window": [2, 3], "entry": [5, 10], "exit": [50, 60], "trend_ma": [150, 200]}
    favored_regime = "牛市回调"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        r = rsi(c, self.params["window"])
        trend_up = c > sma(c, self.params["trend_ma"])
        enter = (r < self.params["entry"]) & trend_up
        exit_ = r > self.params["exit"]
        return stateful_position(enter, exit_, df.index)


@register
class BollingerReversion(Strategy):
    """布林带均值回归：跌破下轨买入，回到中轨卖出（与"突破"用法相反）。"""
    meta = StrategyMeta("布林带回归", "均值回归",
                        "跌破下轨买入，回到中轨卖出", "John Bollinger")
    default_params = {"window": 20, "k": 2.0}
    param_space = {"window": [10, 20, 30], "k": [1.5, 2.0, 2.5]}
    favored_regime = "震荡市"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        bb = bollinger(c, self.params["window"], self.params["k"])
        enter = c < bb["lower"]
        exit_ = c > bb["mid"]
        return stateful_position(enter, exit_, df.index)


@register
class ZScoreReversion(Strategy):
    """
    Z-Score 统计套利思路：
    价格偏离均线 N 个标准差入场，回归到 0 附近离场，同时设置止损 Z。
    """
    meta = StrategyMeta("ZScore回归", "均值回归",
                        "Z<-entry_z 买入，Z>-exit_z 卖出", "统计套利")
    default_params = {"window": 30, "entry_z": 2.0, "exit_z": 0.5, "stop_z": 4.0}
    param_space = {"window": [20, 30, 60], "entry_z": [1.5, 2.0, 2.5], "exit_z": [0.0, 0.5]}
    favored_regime = "震荡市"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        z = zscore(df["close"], self.params["window"])
        enter = z < -abs(self.params["entry_z"])
        exit_ = (z > -abs(self.params["exit_z"])) | (z < -abs(self.params["stop_z"]))
        return stateful_position(enter, exit_, df.index)


@register
class BollingerRSICombo(Strategy):
    """
    布林带 + RSI 双重确认：
    必须同时满足"跌破布林下轨"和"RSI 超卖"才买，任一回到中性就卖。
    双条件过滤能减少假信号，但也会错过一些机会（信号的普遍权衡）。
    """
    meta = StrategyMeta("布林+RSI双确认", "均值回归",
                        "跌破下轨且RSI超卖买入，回升即卖出", "多指标共振")
    default_params = {"window": 20, "k": 2.0, "rsi_n": 14, "oversold": 30}
    param_space = {"window": [20, 30], "k": [2.0], "rsi_n": [14], "oversold": [25, 30]}
    favored_regime = "震荡市"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        bb = bollinger(c, self.params["window"], self.params["k"])
        r = rsi(c, self.params["rsi_n"])
        enter = (c < bb["lower"]) & (r < self.params["oversold"])
        exit_ = (c > bb["mid"]) | (r > 55)
        return stateful_position(enter, exit_, df.index)
