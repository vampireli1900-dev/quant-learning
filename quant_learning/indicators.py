# -*- coding: utf-8 -*-
"""
indicators.py —— 技术指标计算
=============================
量化策略通常建立在"技术指标"之上。本文件把常用指标做成一个个纯函数：
输入价格序列(pd.Series)，输出指标序列(pd.Series 或 pd.DataFrame)。

学完这个文件你应该能回答：
    SMA 和 EMA 有什么区别？RSI 怎么算、含义是什么？布林带为什么能判断超买超卖？

注意：所有指标前 window-1 个值因为数据不够会是 NaN，这是正常现象，
      策略和回测里会统一用 .fillna(0) 或 dropna 处理。
"""

import pandas as pd


def sma(series, window):
    """简单移动平均 (Simple Moving Average)：最近 window 天的收盘价等权平均。

    SMA 是对价格最朴素、最常用的平滑，能过滤掉短期噪音、体现趋势方向。
    """
    return series.rolling(window=window).mean()


def ema(series, window):
    """指数移动平均 (Exponential Moving Average)：越近的价格权重越大。

    相比 SMA，EMA 对最新价格更敏感，因此"拐头"更快，是 MACD 的基础。
    """
    return series.ewm(span=window, adjust=False).mean()


def rsi(series, window=14):
    """相对强弱指标 (Relative Strength Index)，取值 0~100。

    含义：
        RSI > 70  —— 一般认为"超买"（涨太多，可能要回调）
        RSI < 30  —— 一般认为"超卖"（跌太多，可能反弹）

    计算思路：
        1. 每天相对昨天的涨跌幅 delta
        2. 把涨幅和跌幅分开，分别求平均（这里用 Wilder 平滑，即 EMA）
        3. RS = 平均涨幅 / 平均跌幅，再映射到 0~100

    注意：平均跌幅为 0 时 RS 为无穷大，RSI 会等于 100，这是对的（只涨不跌）。
    """
    delta = series.diff()
    gain = delta.clip(lower=0)          # 只保留上涨部分
    loss = -delta.clip(upper=0)         # 只保留下跌部分（取正）

    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(series, fast=12, slow=26, signal=9):
    """MACD（指数平滑异同移动平均线），是经典的"趋势+动量"指标。

    三个量：
        MACD 线    = 快线 EMA(12) - 慢线 EMA(26)，衡量短期相对长期是否在走强
        信号线      = MACD 线的 EMA(9)，对 MACD 再做平滑
        柱状图(hist) = MACD 线 - 信号线，大于 0 表示多头动能占优

    常见用法：MACD 线从下向上穿越信号线 → 金叉（买）；反之下穿 → 死叉（卖）。
    """
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line

    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def bollinger(series, window=20, num_std=2):
    """布林带 (Bollinger Bands)：中轨 = SMA，上下轨 = 中轨 ± num_std 倍标准差。

    含义：
        价格靠近上轨 → 短期偏贵（可能回调）
        价格靠近下轨 → 短期偏便宜（可能反弹）
        带宽收窄 → 波动率降低，往往酝酿变盘
    """
    mid = sma(series, window)
    std = series.rolling(window=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std

    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower})


if __name__ == "__main__":
    # 单独测试：用 data 里的假数据跑一遍指标
    from data import load_data
    df = load_data(n=100)
    close = df["close"]

    print("SMA(20) 后5个值:\n", sma(close, 20).tail())
    print("\nRSI(14) 后5个值:\n", rsi(close).tail())
    print("\nMACD 后3行:\n", macd(close).tail(3))
    print("\n布林带 后3行:\n", bollinger(close).tail(3))
