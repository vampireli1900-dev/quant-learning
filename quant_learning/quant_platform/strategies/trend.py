# -*- coding: utf-8 -*-
"""
strategies/trend.py —— 趋势跟踪类策略
=====================================
核心假设："趋势会延续"。赢时拿着亏时快跑，靠少数大趋势覆盖多次小亏。
典型表现：胜率低(30~45%)、盈亏比高、在单边行情赚大钱，在震荡市连续磨损。
"""

from __future__ import annotations

import pandas as pd

from ..indicators import adx, atr, donchian, ema, macd, sma, slope
from .base import Strategy, StrategyMeta, register, stateful_position


@register
class BuyHold(Strategy):
    """基准：一直满仓（不择时）。所有策略必须先跑赢它才有意义。"""
    meta = StrategyMeta("买入持有", "基准", "始终满仓，作为对比基准")
    default_params = {}
    param_space = {}
    favored_regime = "长期上涨"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=df.index)


@register
class DualMACrossover(Strategy):
    """双均线交叉：快线上穿慢线做多，下穿离场。"""
    meta = StrategyMeta("双均线交叉", "趋势跟踪",
                        "快均线金叉慢均线买入，死叉卖出", "经典入门策略")
    default_params = {"fast": 20, "slow": 50, "ma_type": "sma"}
    param_space = {"fast": [5, 10, 20, 30], "slow": [50, 60, 100, 200]}
    favored_regime = "单边趋势"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        ma_fn = ema if self.params.get("ma_type") == "ema" else sma
        fast, slow = self.params["fast"], self.params["slow"]
        if fast >= slow:
            fast, slow = min(fast, slow), max(fast, slow)
        f, s = ma_fn(c, fast), ma_fn(c, slow)
        return (f > s).astype(float).fillna(0.0)


@register
class MACDCross(Strategy):
    """MACD 金叉死叉：MACD 线上穿信号线买入。"""
    meta = StrategyMeta("MACD交叉", "趋势跟踪",
                        "DIF 上穿 DEA 买入，下穿卖出", "Gerald Appel")
    default_params = {"fast": 12, "slow": 26, "signal": 9}
    param_space = {"fast": [8, 12, 19], "slow": [21, 26, 39], "signal": [6, 9, 12]}
    favored_regime = "中段趋势"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        m = macd(df["close"], self.params["fast"], self.params["slow"], self.params["signal"])
        return (m["macd"] > m["signal"]).astype(float).fillna(0.0)


@register
class ADXFilterTrend(Strategy):
    """
    ADX 过滤 + 均线方向：
    只有当 ADX 足够高（趋势明确）时才按均线方向持仓，否则空仓。
    目的：过滤掉震荡市的假信号——这是趋势策略最常见的改进手段。
    """
    meta = StrategyMeta("ADX趋势过滤", "趋势跟踪",
                        "ADX>阈值且均线向上才持仓，过滤震荡市假信号", "Wilder")
    default_params = {"ma": 50, "adx_n": 14, "adx_th": 25}
    param_space = {"ma": [30, 50, 100], "adx_n": [10, 14, 20], "adx_th": [20, 25, 30]}
    favored_regime = "强趋势"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        ma = sma(c, self.params["ma"])
        dx = adx(df, self.params["adx_n"])
        up = slope(ma, 10) > 0
        long_cond = (c > ma) & (dx > self.params["adx_th"]) & up
        return long_cond.astype(float).fillna(0.0)


@register
class TurtleDonchian(Strategy):
    """海龟唐奇安通道突破：突破 N 日最高价做多，跌破 M 日最低价离场。"""
    meta = StrategyMeta("唐奇安突破", "突破",
                        "突破N日最高价买入，跌破M日最低价卖出", "海龟交易法则")
    default_params = {"entry_n": 20, "exit_n": 10}
    param_space = {"entry_n": [10, 20, 55], "exit_n": [5, 10, 20]}
    favored_regime = "大级别趋势"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        entry = donchian(df, self.params["entry_n"])
        exit_ch = donchian(df, self.params["exit_n"])
        enter = df["close"] > entry["upper"].shift(1)
        exit_ = df["close"] < exit_ch["lower"].shift(1)
        return stateful_position(enter, exit_, df.index)


@register
class ATRChannelBreakout(Strategy):
    """
    ATR 通道突破（波动率自适应突破）：
    上轨 = 收盘价的 N 日均值 + k×ATR，突破即入场；跌破均线离场。
    相比固定百分比，ATR 通道能自适应不同波动率的标的。
    """
    meta = StrategyMeta("ATR通道突破", "突破",
                        "突破 均线+k×ATR 上轨买入，跌破均卖出", "波动率自适应")
    default_params = {"n": 20, "k": 2.0}
    param_space = {"n": [10, 20, 40], "k": [1.5, 2.0, 3.0]}
    favored_regime = "波动放大"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        mid = ema(c, self.params["n"])
        a = atr(df, self.params["n"])
        upper = mid + self.params["k"] * a
        enter = c > upper
        exit_ = c < mid
        return stateful_position(enter, exit_, df.index)


@register
class MAWithTrendFilter(Strategy):
    """
    双均线 + 长期趋势过滤（只在长期均线向上时做多）。
    这是"多时间框架确认"思想：大周期定方向，小周期定时机。
    """
    meta = StrategyMeta("均线+长周期过滤", "趋势跟踪",
                        "价格>长期均线 且 短均线金叉长均线才买入", "多周期确认")
    default_params = {"fast": 20, "slow": 50, "trend": 200}
    param_space = {"fast": [10, 20], "slow": [50, 60], "trend": [150, 200, 250]}
    favored_regime = "牛市"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        f, s, t = sma(c, self.params["fast"]), sma(c, self.params["slow"]), sma(c, self.params["trend"])
        cond = (f > s) & (c > t)
        return cond.astype(float).fillna(0.0)
