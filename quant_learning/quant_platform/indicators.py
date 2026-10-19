# -*- coding: utf-8 -*-
"""
indicators.py —— 技术指标库
===========================
全部为「输入 Series/DataFrame → 输出 Series/DataFrame」的纯函数，
向量化实现（无循环），可直接被策略调用。

分类：
    趋势类   SMA / EMA / WMA / MACD / ADX / 唐奇安通道 / 线性斜率
    动量类   RSI / ROC / 动量 / 随机KDJ / CCI / 威廉指标
    波动类   ATR / 布林带 / 肯特纳通道 / 历史波动率 / Z-Score
    量能类   OBV / 量比 / 换手率代理
    统计类   滚动相关 / 滚动Beta / 滚动分位
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# 趋势类
# ---------------------------------------------------------------------------
def sma(s: pd.Series, n: int) -> pd.Series:
    """简单移动平均。"""
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """指数移动平均。"""
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wma(s: pd.Series, n: int) -> pd.Series:
    """加权移动平均（近期权重更高）。"""
    weights = np.arange(1, n + 1, dtype=float)
    return s.rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD：返回 DataFrame(macd, signal, hist)。"""
    dif = ema(s, fast) - ema(s, slow)
    dea = dif.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": dif, "signal": dea, "hist": dif - dea})


def true_range(df: pd.DataFrame) -> pd.Series:
    """真实波幅 TR。"""
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    return pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """平均真实波幅 ATR（Wilder 平滑）。"""
    tr = true_range(df)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """ADX 趋势强度指标（+DI/-DI/DX 的平滑）。"""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr_n = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / n, adjust=False, min_periods=n).mean() / tr_n
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / n, adjust=False, min_periods=n).mean() / tr_n
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def donchian(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """唐奇安通道（海龟策略用）。"""
    return pd.DataFrame({
        "upper": df["high"].rolling(n, min_periods=n).max(),
        "lower": df["low"].rolling(n, min_periods=n).min(),
        "mid": (df["high"].rolling(n, min_periods=n).max()
                + df["low"].rolling(n, min_periods=n).min()) / 2,
    })


def slope(s: pd.Series, n: int = 20) -> pd.Series:
    """滚动线性回归斜率（归一化：每期相对均值的斜率）。"""
    def _slope(x):
        y = np.asarray(x, dtype=float)
        idx = np.arange(len(y))
        if np.std(y) == 0:
            return 0.0
        return np.polyfit(idx, y, 1)[0] / np.mean(y)
    return s.rolling(n, min_periods=n).apply(_slope, raw=True)


# ---------------------------------------------------------------------------
# 动量类
# ---------------------------------------------------------------------------
def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    """RSI（Wilder 平滑版，与主流软件一致）。"""
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0)


def roc(s: pd.Series, n: int = 12) -> pd.Series:
    """变动率 ROC（百分比）。"""
    return s.pct_change(n) * 100


def momentum(s: pd.Series, n: int = 20) -> pd.Series:
    """动量：当前价 / n 期前价格 - 1。"""
    return s / s.shift(n) - 1


def stochastic(df: pd.DataFrame, n: int = 14, smooth: int = 3) -> pd.DataFrame:
    """随机指标 KDJ。"""
    low_min = df["low"].rolling(n, min_periods=n).min()
    high_max = df["high"].rolling(n, min_periods=n).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    k = k.rolling(smooth, min_periods=1).mean()
    d = k.rolling(smooth, min_periods=1).mean()
    return pd.DataFrame({"k": k, "d": d, "j": 3 * k - 2 * d})


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """商品通道指标 CCI。"""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    ma = tp.rolling(n, min_periods=n).mean()
    md = tp.rolling(n, min_periods=n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """威廉指标 %R。"""
    hh = df["high"].rolling(n, min_periods=n).max()
    ll = df["low"].rolling(n, min_periods=n).min()
    return -100 * (hh - df["close"]) / (hh - ll).replace(0, np.nan)


# ---------------------------------------------------------------------------
# 波动类
# ---------------------------------------------------------------------------
def bollinger(s: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """布林带：返回 mid/upper/lower/width/%B。"""
    mid = sma(s, n)
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    return pd.DataFrame({
        "mid": mid, "upper": upper, "lower": lower,
        "width": (upper - lower) / mid,
        "pct_b": (s - lower) / (upper - lower).replace(0, np.nan),
    })


def keltner(df: pd.DataFrame, n: int = 20, m: float = 2.0) -> pd.DataFrame:
    """肯特纳通道：EMA ± m×ATR。"""
    mid = ema(df["close"], n)
    a = atr(df, n)
    return pd.DataFrame({"mid": mid, "upper": mid + m * a, "lower": mid - m * a})


def realized_vol(s: pd.Series, n: int = 20, annualize: bool = True) -> pd.Series:
    """滚动波动率。"""
    v = s.pct_change().rolling(n, min_periods=n).std()
    return v * np.sqrt(TRADING_DAYS) if annualize else v


def zscore(s: pd.Series, n: int = 20) -> pd.Series:
    """滚动 Z-Score：偏离均值多少个标准差（均值回归核心指标）。"""
    m = sma(s, n)
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return (s - m) / sd.replace(0, np.nan)


# ---------------------------------------------------------------------------
# 量能类
# ---------------------------------------------------------------------------
def obv(df: pd.DataFrame) -> pd.Series:
    """能量潮 OBV。"""
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def volume_ratio(s: pd.Series, n: int = 20) -> pd.Series:
    """量比：当日成交量 / 过去 n 日均量。"""
    return s / s.rolling(n, min_periods=n).mean()


# ---------------------------------------------------------------------------
# 统计 / 特征类（供机器学习策略使用）
# ---------------------------------------------------------------------------
def returns(s: pd.Series, n: int = 1) -> pd.Series:
    return s.pct_change(n)


def rolling_corr(a: pd.Series, b: pd.Series, n: int = 60) -> pd.Series:
    return a.rolling(n, min_periods=n).corr(b)


def rolling_beta(a: pd.Series, benchmark: pd.Series, n: int = 60) -> pd.Series:
    cov = a.rolling(n, min_periods=n).cov(benchmark)
    var = benchmark.rolling(n, min_periods=n).var()
    return cov / var.replace(0, np.nan)


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1


def percentile_rank(s: pd.Series, n: int = 252) -> pd.Series:
    """滚动分位数（0~1），用于横截面排序或自适应阈值。"""
    return s.rolling(n, min_periods=2).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)


# ---------------------------------------------------------------------------
# 特征矩阵：给 ML 策略用
# ---------------------------------------------------------------------------
def build_features(df: pd.DataFrame, lookbacks=(5, 10, 20, 60)) -> pd.DataFrame:
    """
    从 OHLCV 构造一份通用特征表（含未来收益标签 ret_fwd）。
    注意：ret_fwd 只能用于训练，不能用于生成信号（否则就是未来函数）。
    """
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    f = pd.DataFrame(index=df.index)
    for n in lookbacks:
        f[f"ret_{n}"] = c.pct_change(n)
        f[f"mom_{n}"] = c / c.shift(n) - 1
        f[f"vol_{n}"] = c.pct_change().rolling(n).std()
        f[f"zscore_{n}"] = zscore(c, n)
        f[f"ma_ratio_{n}"] = c / sma(c, n) - 1
    f["rsi_14"] = rsi(c, 14) / 100.0
    m = macd(c)
    f["macd_hist"] = m["hist"] / c
    f["atr_14"] = atr(df, 14) / c
    f["adx_14"] = adx(df, 14) / 100.0
    bb = bollinger(c, 20)
    f["bb_pctb"] = bb["pct_b"]
    f["bb_width"] = bb["width"]
    f["vol_ratio"] = volume_ratio(v, 20)
    f["range_pct"] = (h - l) / c
    f["close_pos"] = (c - l) / (h - l).replace(0, np.nan)
    f["ret_fwd"] = c.pct_change(1).shift(-1)      # 次日收益（标签）
    return f
