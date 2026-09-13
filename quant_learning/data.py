# -*- coding: utf-8 -*-
"""
data.py —— 数据获取模块
========================
作用：给回测提供 K 线（OHLCV）数据。

为什么单独一个文件？
    真实量化交易里，数据来源可能五花八门（券商、行情 API、数据库、CSV 文件），
    但策略和回测引擎不应该关心数据从哪来——只要拿到统一格式的 DataFrame 就行。
    这就是"关注点分离"：换数据源，只改这一个文件，策略代码不动。

当前实现：
    1. generate_synthetic_data() —— 用几何布朗运动（GBM）造一段"假"股价，
       保证没有网络、没有券商账号也能跑通整个流程。
    2. load_data() —— 统一入口，以后你想接真实数据，改这里即可（见文件末尾注释）。

统一的数据格式约定（重要，整个项目都按这个来）：
    DataFrame，索引是日期(DatetimeIndex)，列固定为：
        open / high / low / close / volume
"""

import numpy as np
import pandas as pd


def generate_synthetic_data(n=500, seed=42, start_price=100.0,
                            mu=0.0005, sigma=0.02):
    """
    用几何布朗运动（Geometric Brownian Motion）造一段假数据。

    几何布朗运动是金融里最基础的股价模型：
        S_{t+1} = S_t * exp( μ*dt + σ*√dt * ε )，ε ~ 正态分布
    其中 μ 是漂移率（drift，长期平均收益），σ 是波动率（volatility）。

    参数：
        n            : 生成多少个交易日
        seed         : 随机种子，固定后每次结果一样，方便复现
        start_price  : 起始价
        mu           : 日漂移率
        sigma        : 日波动率
    返回：
        DataFrame，列 = open/high/low/close/volume，索引 = 日期
    """
    rng = np.random.default_rng(seed)

    # 1) 先造 n 个日收益率，再累乘得到收盘价序列
    daily_returns = rng.normal(mu, sigma, n)          # 每天的 ε
    close = start_price * np.exp(np.cumsum(daily_returns))  # 累乘得到收盘价

    # 2) 由收盘价反推出开盘/最高/最低/成交量（简化处理，只为让数据看着真实）
    open_ = np.empty(n)
    open_[0] = start_price
    open_[1:] = close[:-1]                            # 今日开盘 = 昨日收盘（简化）

    spread = rng.uniform(0, 0.01, n)                  # 日内上下影线比例
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = rng.integers(1_000_000, 10_000_000, n)   # 成交量，随便给个范围

    # 3) 生成一串"工作日"作为日期索引
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)

    df = pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume.astype(float),
    }, index=dates)

    return df


def load_data(source="synthetic", **kwargs):
    """
    统一数据入口。策略/回测只调用这个函数，不关心底层来源。

    source 可选：
        "synthetic" —— 默认，用上面的假数据，离线可跑
        "csv"       —— 读本地 CSV（需带 open/high/low/close/volume 列）
        （将来可以加 "yfinance" / "ibkr" 等，见文件末尾 TODO）

    使用示例：
        df = load_data()                          # 假数据
        df = load_data("csv", path="aapl.csv")    # 读 CSV
    """
    if source == "synthetic":
        return generate_synthetic_data(**kwargs)

    if source == "csv":
        path = kwargs["path"]
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        return df[["open", "high", "low", "close", "volume"]]

    raise ValueError(f"未知数据源: {source}")


if __name__ == "__main__":
    # 直接运行本文件可单独测试数据模块
    df = load_data()
    print(df.head())
    print("\n形状:", df.shape)


# =====================================================================
# TODO —— 接真实数据（以后想练真行情时，把下面任意一种接进 load_data 即可）
# =====================================================================
#
# 【方案 A：接盈透证券 ib_insync】（你 t.py 里已经在用，直接抄它的逻辑）
#   from ib_insync import IB, Stock, util
#   ib = IB(); ib.connect('127.0.0.1', 7497, clientId=1)
#   contract = Stock('AAPL', 'SMART', 'USD')
#   bars = ib.reqHistoricalData(
#       contract, endDateTime='', durationStr='1 Y',
#       barSizeSetting='1 day', whatToShow='TRADES', useRTH=True)
#   df = util.df(bars)                        # 已含 open/high/low/close/volume
#   df.index = df.pop('date')                 # 把 date 列变成索引
#   ib.disconnect()
#
# 【方案 B：用 yfinance 免费下载】（需 pip install yfinance）
#   import yfinance as yf
#   df = yf.download('AAPL', start='2022-01-01')
#   df.columns = df.columns.str.lower()       # 转成小写 open/high/low/close/volume
#   df = df[['open','high','low','close','volume']]
# =====================================================================
