# -*- coding: utf-8 -*-
"""
data/base.py —— 数据源抽象与统一数据格式
========================================
所有数据源（IB / yfinance / CSV / 合成）都必须返回同一格式的 DataFrame：

    DatetimeIndex（日期或时间戳，升序、去重）
    列：open, high, low, close, volume   （float）

这样上层策略/回测完全不用关心数据从哪来。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Sequence

import pandas as pd

# 统一列名与顺序
OHLCV_COLUMNS: Sequence[str] = ("open", "high", "low", "close", "volume")


@dataclass
class DataRequest:
    """一次数据请求的完整描述（也是缓存 key 的来源）。"""
    symbol: str
    bar_size: str = "1 day"          # '1 day' / '1 hour' / '5 mins' / '1 min'
    duration: str = "5 Y"            # IB 风格：'1 Y' / '2 Y' / '6 M' / '1 D'
    start: Optional[str] = None      # 'YYYY-MM-DD'，备用（yfinance 用）
    end: Optional[str] = None
    what_to_show: str = "TRADES"
    use_rth: bool = True
    exchange: str = "SMART"
    currency: str = "USD"
    primary_exchange: str = ""

    def cache_key(self) -> str:
        """生成稳定的缓存文件名。"""
        safe = self.symbol.replace(".", "_").replace("/", "_")
        bs = self.bar_size.replace(" ", "")
        dur = self.duration.replace(" ", "")
        wts = self.what_to_show
        rth = "RTH" if self.use_rth else "ALL"
        extra = ""
        if self.start or self.end:
            extra = f"_{self.start or ''}_{self.end or ''}"
        return f"{safe}_{bs}_{dur}_{wts}_{rth}{extra}"


class DataSource(ABC):
    """数据源基类。"""

    name: str = "base"

    @abstractmethod
    def fetch(self, req: DataRequest) -> pd.DataFrame:
        """拉取数据，返回统一格式 DataFrame。"""
        raise NotImplementedError

    def available(self) -> bool:
        """该数据源当前是否可用（用于自动降级）。"""
        return True


def normalize_ohlcv(df: pd.DataFrame, tz_naive: bool = True) -> pd.DataFrame:
    """
    把任意来源的 DataFrame 规整成统一格式：
      - 列名小写，保留 open/high/low/close/volume
      - 索引转 DatetimeIndex，排序、去重
      - 缺失 volume 补 0，其余数值列前向填充
      - 去掉含 NaN 的 close 行
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=list(OHLCV_COLUMNS))

    df = df.copy()

    # 1) 索引处理：如果有 date/datetime 列，先拿来当索引
    lower_cols = {c.lower(): c for c in df.columns}
    for cand in ("date", "datetime", "timestamp", "time"):
        if cand in lower_cols and not isinstance(df.index, pd.DatetimeIndex):
            col = lower_cols[cand]
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=False)
            df = df.set_index(col)
            break

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")

    if tz_naive and getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)      # type: ignore[union-attr]

    df.columns = [str(c).lower() for c in df.columns]

    # 2) 只保留需要的列；缺失的补
    for col in OHLCV_COLUMNS:
        if col not in df.columns:
            df[col] = 0.0 if col == "volume" else pd.NA
    df = df[list(OHLCV_COLUMNS)].astype(float)

    # 3) 清洗
    df = df[~df.index.isna()]
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.dropna(subset=["close"])
    df["volume"] = df["volume"].fillna(0.0)
    for col in ("open", "high", "low"):
        df[col] = df[col].fillna(df["close"])
    df = df[df["close"] > 0]

    if df.index.name != "date":
        df.index.name = "date"
    return df


def concat_and_dedup(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """把新数据合并进旧数据（用于增量更新缓存）。"""
    if old is None or len(old) == 0:
        return normalize_ohlcv(new)
    if new is None or len(new) == 0:
        return normalize_ohlcv(old)
    merged = pd.concat([normalize_ohlcv(old), normalize_ohlcv(new)])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    return merged
