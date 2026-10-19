# -*- coding: utf-8 -*-
"""数据层：多数据源 + 缓存 + 统一入口。"""

from .base import DataRequest, DataSource, OHLCV_COLUMNS, normalize_ohlcv
from .cache import DataCache
from .ibkr_source import IBKRSource
from .other_sources import (CSVSource, SyntheticSource, YFinanceSource,
                            generate_regime_data)
from .manager import (get_data, get_multi, get_source, cache_info,
                      clear_cache, close_all)

__all__ = [
    "DataRequest", "DataSource", "OHLCV_COLUMNS", "normalize_ohlcv",
    "DataCache", "IBKRSource", "CSVSource", "SyntheticSource", "YFinanceSource",
    "generate_regime_data", "get_data", "get_multi", "get_source",
    "cache_info", "clear_cache", "close_all",
]
