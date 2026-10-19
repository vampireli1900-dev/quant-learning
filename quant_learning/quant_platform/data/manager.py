# -*- coding: utf-8 -*-
"""
data/manager.py —— 数据管理器（对外唯一入口）
============================================
用法（业务代码只需要这一个函数）：

    from quant_platform.data import get_data
    df = get_data("AAPL", duration="5 Y", bar_size="1 day")

内部做了四件事：
    1. 查缓存 → 命中且足够新就直接返回（毫秒级）
    2. 未命中 → 调数据源拉取（IB 优先，失败自动降级 yfinance）
    3. 增量更新：已有旧缓存时只补齐尾部新数据
    4. 写回缓存
"""

from __future__ import annotations

from typing import Optional, Union

import pandas as pd

from ..config import CONFIG, ensure_dirs
from .base import DataRequest, DataSource, concat_and_dedup
from .cache import DataCache
from .ibkr_source import IBKRSource
from .other_sources import CSVSource, SyntheticSource, YFinanceSource


def persist_to_db(symbol: str, req: DataRequest, df, source: str) -> int:
    """
    把取到的数据写进 SQL 数据库（bars 表）。

    设计原则：这是"旁路写入"——任何失败都只打日志，绝不中断取数流程。
    实验能不能跑，不应该取决于数据库是否可用。
    """
    try:
        from ..db.repository import upsert_bars
        if not getattr(getattr(CONFIG, "db", None), "enabled", False):
            return 0
        if not getattr(CONFIG.db, "persist_bars", True):
            return 0
        return int(upsert_bars(symbol, req.bar_size, df, source=source))
    except Exception as e:                                        # noqa: BLE001
        print(f"[db] 落库失败（不影响主流程）: {type(e).__name__}: {str(e)[:80]}")
        return 0

ensure_dirs()

_CACHE = DataCache(enabled=CONFIG.cache_enabled)
_SOURCES: dict[str, DataSource] = {}


def get_source(name: str) -> DataSource:
    name = name.lower()
    if name not in _SOURCES:
        if name == "ibkr":
            _SOURCES[name] = IBKRSource()
        elif name == "yfinance":
            _SOURCES[name] = YFinanceSource()
        elif name == "csv":
            _SOURCES[name] = CSVSource()
        elif name == "synthetic":
            _SOURCES[name] = SyntheticSource()
        else:
            raise ValueError(f"未知数据源: {name}")
    return _SOURCES[name]


def get_data(symbol: str,
             bar_size: str = "1 day",
             duration: str = "5 Y",
             source: str = "auto",
             use_cache: bool = True,
             refresh: bool = False,
             what_to_show: str = "TRADES",
             use_rth: bool = True,
             exchange: str = "SMART",
             currency: str = "USD",
             start: Optional[str] = None,
             end: Optional[str] = None) -> pd.DataFrame:
    """
    统一取数入口。

    source:
        "auto"      —— 先 IB，失败降级 yfinance，再失败报错
        "ibkr" / "yfinance" / "csv" / "synthetic"
    refresh:
        True 时忽略缓存强制重新拉取（会与旧缓存合并）
    """
    req = DataRequest(symbol=symbol, bar_size=bar_size, duration=duration,
                      what_to_show=what_to_show, use_rth=use_rth,
                      exchange=exchange, currency=currency, start=start, end=end)

    order = ["ibkr", "yfinance"] if source == "auto" else [source]
    if source in ("csv", "synthetic"):
        return get_source(source).fetch(req)

    cached = None if refresh else _CACHE.load(order[0], req)
    if cached is not None and len(cached) > 0:
        # 增量更新：缓存存在时，尝试只拉最近一小段补齐（日线拉 7 天即可）
        if not refresh and source == "auto" and bar_size == "1 day":
            try:
                recent_req = DataRequest(**{**req.__dict__, "duration": "7 D"})
                src = get_source(order[0])
                if src.available():
                    fresh = src.fetch(recent_req)
                    if fresh is not None and len(fresh):
                        merged = concat_and_dedup(cached, fresh)
                        if len(merged) > len(cached):
                            _CACHE.save(order[0], req, merged)
                            persist_to_db(symbol, req, merged, order[0])
                            return merged
            except Exception:
                pass                      # 增量更新失败就直接用旧缓存
        persist_to_db(symbol, req, cached, order[0])
        return cached

    last_err = None
    for name in order:
        try:
            src = get_source(name)
            try:
                from ..db.repository import ingest_logged
                with ingest_logged(name, symbol, req.bar_size, req.duration) as log:
                    df = src.fetch(req)
                    log["rows"] = 0 if df is None else len(df)
            except ImportError:
                df = src.fetch(req)
            if df is not None and len(df):
                if use_cache:
                    if cached is not None and len(cached):
                        df = concat_and_dedup(cached, df)
                    _CACHE.save(name, req, df)
                persist_to_db(symbol, req, df, name)
                return df
        except Exception as e:                       # noqa: BLE001
            last_err = e

    if cached is not None and len(cached):
        return cached
    raise RuntimeError(f"获取数据失败 {symbol}: {last_err}")


def get_multi(symbols, bar_size: str = "1 day", duration: str = "5 Y",
              source: str = "auto", **kwargs) -> dict[str, pd.DataFrame]:
    """批量取数，返回 {symbol: DataFrame}。单个失败不影响其他。"""
    out = {}
    for s in symbols:
        try:
            out[s] = get_data(s, bar_size=bar_size, duration=duration,
                              source=source, **kwargs)
        except Exception as e:                       # noqa: BLE001
            print(f"[get_multi] {s} 失败: {e}")
    return out


def cache_info() -> pd.DataFrame:
    return _CACHE.list_cached()


def clear_cache(source: Optional[str] = None) -> int:
    return _CACHE.clear(source)


def close_all() -> None:
    """断开所有连接（程序退出前调用）。"""
    for name, src in _SOURCES.items():
        if hasattr(src, "disconnect"):
            try:
                src.disconnect()
            except Exception:
                pass
