# -*- coding: utf-8 -*-
"""
data/cache.py —— 本地磁盘缓存
==============================
为什么必须有缓存：
    1. IB 历史数据有限频（pacing），反复实验会把额度耗光；
    2. 一个"10 策略 × 6 标的 × 5 年"的实验要拉几百次数据，没缓存根本跑不动；
    3. 缓存后实验结果完全可复现（数据快照固定）。

实现：CSV 文件（人类可读、无额外依赖）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import pandas as pd

from ..config import CACHE_DIR, ensure_dirs
from .base import DataRequest, normalize_ohlcv

META_SUFFIX = ".meta.json"


class DataCache:
    def __init__(self, root: Path | str = CACHE_DIR, enabled: bool = True,
                 max_age_days: Optional[float] = None):
        ensure_dirs()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        # None = 永不过期；否则超过该天数的缓存视为失效（对日线很实用）
        self.max_age_days = max_age_days

    # ---------------- 路径 ----------------
    def _path(self, source: str, req: DataRequest) -> Path:
        d = self.root / source
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{req.cache_key()}.csv"

    def _meta_path(self, p: Path) -> Path:
        return Path(str(p) + META_SUFFIX)

    # ---------------- 读 ----------------
    def load(self, source: str, req: DataRequest) -> Optional[pd.DataFrame]:
        if not self.enabled:
            return None
        p = self._path(source, req)
        if not p.exists():
            return None
        if self.max_age_days is not None:
            age_days = (time.time() - p.stat().st_mtime) / 86400.0
            if age_days > self.max_age_days:
                return None
        try:
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            return normalize_ohlcv(df)
        except Exception:
            return None

    def last_timestamp(self, source: str, req: DataRequest):
        df = self.load(source, req)
        return df.index[-1] if df is not None and len(df) else None

    # ---------------- 写 ----------------
    def save(self, source: str, req: DataRequest, df: pd.DataFrame) -> Path:
        if not self.enabled:
            return Path("")
        p = self._path(source, req)
        df = normalize_ohlcv(df)
        df.to_csv(p)
        meta = {
            "source": source,
            "request": req.__dict__,
            "rows": int(len(df)),
            "start": str(df.index[0]) if len(df) else None,
            "end": str(df.index[-1]) if len(df) else None,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(self._meta_path(p), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return p

    # ---------------- 维护 ----------------
    def list_cached(self) -> pd.DataFrame:
        rows = []
        for p in self.root.rglob(f"*{META_SUFFIX}"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    m = json.load(f)
                m["file"] = p.name[: -len(META_SUFFIX)] if p.name.endswith(META_SUFFIX) else p.name
                rows.append(m)
            except Exception:
                continue
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def clear(self, source: Optional[str] = None) -> int:
        target = self.root / source if source else self.root
        n = 0
        for p in target.rglob("*.csv"):
            p.unlink(missing_ok=True)
            n += 1
        for p in target.rglob(f"*{META_SUFFIX}"):
            p.unlink(missing_ok=True)
        return n
