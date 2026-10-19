# -*- coding: utf-8 -*-
"""
data/other_sources.py —— 备用数据源：yfinance / CSV / 合成数据
=============================================================
用途：
    * yfinance：免费日线，用于交叉验证 IB 数据或 IB 不可用时兜底。
    * CSV      ：导入你自己的数据。
    * 合成     ：几何布朗运动 / 带趋势与波动聚集的随机行情，用于
                 在"已知答案"的数据上检验策略逻辑是否正确（单元测试思路）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .base import DataRequest, DataSource, normalize_ohlcv


class YFinanceSource(DataSource):
    name = "yfinance"

    def available(self) -> bool:
        try:
            import yfinance  # noqa: F401
            return True
        except Exception:
            return False

    def fetch(self, req: DataRequest) -> pd.DataFrame:
        import yfinance as yf

        kwargs = dict(auto_adjust=False, progress=False, threads=False)
        if req.start:
            kwargs["start"] = req.start
        if req.end:
            kwargs["end"] = req.end
        else:
            kwargs["period"] = _yf_period(req.duration)

        df = yf.download(req.symbol, interval=_yf_interval(req.bar_size), **kwargs)
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=list(("open", "high", "low", "close", "volume")))

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        for need in ("open", "high", "low", "close", "volume"):
            if need not in df.columns:
                df[need] = np.nan
        return normalize_ohlcv(df)


def _yf_period(duration: str) -> str:
    d = duration.strip().upper()
    if d.endswith("Y"):
        n = int(float(d[:-1]))
        return f"{max(n, 1)}y" if n <= 30 else "max"
    if d.endswith("M"):
        n = int(float(d[:-1]))
        return f"{max(n // 30, 1)}mo" if n < 30 else "1y"
    if d.endswith("D"):
        n = int(float(d[:-1]))
        return f"{max(n, 1)}d"
    return "2y"


def _yf_interval(bar_size: str) -> str:
    mapping = {
        "1 day": "1d", "1d": "1d", "day": "1d",
        "1 hour": "1h", "1h": "1h",
        "5 mins": "5m", "15 mins": "15m", "30 mins": "30m",
        "1 min": "1m", "1 week": "1wk", "1 month": "1mo",
    }
    return mapping.get(bar_size.strip().lower(), "1d")


class CSVSource(DataSource):
    name = "csv"

    def __init__(self, root: str | Path = "."):
        self.root = Path(root)

    def fetch(self, req: DataRequest) -> pd.DataFrame:
        p = Path(req.symbol)
        if not p.is_absolute():
            p = self.root / p
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        return normalize_ohlcv(df)


class SyntheticSource(DataSource):
    """
    合成数据生成器。
    regime 参数可以造出"趋势市 / 震荡市 / 高波动市"三种市场状态，
    用来检验策略在不同市场环境下的鲁棒性（这是判断过拟合的关键手段）。
    """

    name = "synthetic"

    def fetch(self, req: DataRequest) -> pd.DataFrame:
        # 用 symbol 字段传递生成参数：如 "TREND:500:42" / "RANGE:500:1"
        parts = req.symbol.split(":")
        regime = parts[0].upper() if parts else "TREND"
        n = int(parts[1]) if len(parts) > 1 else 500
        seed = int(parts[2]) if len(parts) > 2 else 42
        return generate_regime_data(n=n, seed=seed, regime=regime)


def generate_regime_data(n: int = 500, seed: int = 42, start_price: float = 100.0,
                         regime: str = "TREND") -> pd.DataFrame:
    """
    生成带市场状态(market regime)的行情：
        TREND  : 强趋势 + 中等波动   —— 趋势策略应该表现好
        RANGE  : 无趋势均值震荡      —— 均值回归策略应该表现好
        VOL    : 高波动 + 波动聚集   —— 检验风控/止损
        MIX    : 前段趋势、后段震荡  —— 检验策略是否"只吃一种行情"
    """
    rng = np.random.default_rng(seed)
    regime = regime.upper()

    if regime == "TREND":
        mu, sigma, persist = 0.0006, 0.012, 0.15
    elif regime == "RANGE":
        mu, sigma, persist = 0.0, 0.010, -0.35     # 负自相关 = 均值回归特性
    elif regime == "VOL":
        mu, sigma, persist = 0.0002, 0.028, 0.05
    elif regime == "MIX":
        mu, sigma, persist = 0.0004, 0.015, 0.10
    else:
        mu, sigma, persist = 0.0003, 0.015, 0.0

    # AR(1) 收益率：persist>0 有惯性(趋势)，<0 有反转(震荡)
    eps = rng.normal(0, 1, n)
    r = np.zeros(n)
    for i in range(1, n):
        r[i] = persist * r[i - 1] + np.sqrt(max(1e-8, 1 - persist ** 2)) * eps[i]

    if regime == "MIX":
        # 后半段改成震荡
        half = n // 2
        r2 = np.zeros(n - half)
        for i in range(1, len(r2)):
            r2[i] = -0.35 * r2[i - 1] + 0.94 * eps[half + i]
        r[half:] = r2

    # 波动聚集（GARCH 风格）：VOL 状态放大波动
    vol_mult = np.ones(n)
    if regime in ("VOL", "MIX"):
        for i in range(1, n):
            vol_mult[i] = 0.9 * vol_mult[i - 1] + 0.1 * (1 + 3 * abs(r[i - 1]) / sigma)

    ret = mu + sigma * vol_mult * r
    close = start_price * np.exp(np.cumsum(ret))

    open_ = np.empty(n)
    open_[0] = start_price
    open_[1:] = close[:-1]
    spread = np.abs(rng.normal(0, sigma * 0.6, n))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = rng.integers(1_000_000, 10_000_000, n).astype(float)

    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    df = pd.DataFrame({"open": open_, "high": high, "low": low,
                       "close": close, "volume": volume}, index=dates)
    return normalize_ohlcv(df)


def load_csv(path: str | Path) -> pd.DataFrame:
    return normalize_ohlcv(pd.read_csv(path, index_col=0, parse_dates=True))
