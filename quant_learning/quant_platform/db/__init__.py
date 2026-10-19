# -*- coding: utf-8 -*-
"""
quant_platform.db —— SQL 关系型数据库持久层
============================================
平台里所有"值得留下"的东西都在这 14 张表里：

    API 抓回的行情  →  assets / bars / ingest_log
    特征与训练集    →  feature_sets / features
    实验与回测      →  experiments / backtest_runs / backtest_metrics
                       / equity_points / trades
    研究结论        →  model_registry
    实盘运营        →  orders / position_snapshots / account_snapshots

三分钟上手：
    python -m quant_platform.cli db-init                 # 建库建表
    python -m quant_platform.cli db-import               # 导入已有 CSV 缓存
    python -m quant_platform.cli db-stats                # 看各表行数
    python -m quant_platform.cli db-query --sql "SELECT COUNT(*) FROM bars"
    python -m quant_platform.cli db-features --name demo --symbols SPY QQQ

代码里怎么用：
    from quant_platform.db import upsert_bars, load_bars, run_sql
    upsert_bars("AAPL", "1 day", df, source="ibkr")
    df = load_bars("AAPL", "1 day", start="2024-01-01")
"""

from __future__ import annotations

from .schema import (                                       # noqa: F401
    TABLE_COMMENTS, TABLE_GROUPS, AccountSnapshot, Asset, BacktestMetric,
    BacktestRun, Bar, Base, EquityPoint, Experiment, Feature, FeatureSet,
    IngestLog, ModelRegistry, Order, PositionSnapshot, Trade, table_names,
)
from .session import (                                      # noqa: F401
    ENV_URL, db_location, db_size_bytes, default_url, get_engine,
    get_session, get_session_factory, init_db, is_sqlite, reset_db,
    resolve_url, session_scope,
)

__all__ = [
    "TABLE_GROUPS", "TABLE_COMMENTS", "ENV_URL", "Base", "table_names",
    "Asset", "Bar", "IngestLog", "FeatureSet", "Feature",
    "Experiment", "BacktestRun", "BacktestMetric", "EquityPoint", "Trade",
    "ModelRegistry", "Order", "PositionSnapshot", "AccountSnapshot",
    "default_url", "resolve_url", "get_engine", "get_session",
    "get_session_factory", "session_scope", "init_db", "reset_db",
    "is_sqlite", "db_location", "db_size_bytes",
]

# features / migrate / repository 依赖 pandas 等子模块，
# 放在包初始化末尾导入，避免任何循环依赖。
from . import repository                                    # noqa: E402,F401
from .repository import (                                   # noqa: E402,F401
    DB_ENABLED, assert_readonly, bar_coverage, example_queries, get_detail,
    get_equity_curve, get_ranking, get_or_create_asset, ingest_logged,
    list_experiments, list_feature_sets, list_models, list_orders,
    load_bars, load_feature_set, run_sql, save_account_snapshot,
    save_experiment, save_feature_set, save_order, save_positions,
    set_enabled, table_schema, table_stats, upsert_bars, recent_ingests,
    account_curve,
)
from . import features                                      # noqa: E402,F401
from . import migrate                                       # noqa: E402,F401

__all__ += [
    "repository", "features", "migrate", "DB_ENABLED", "set_enabled",
    "upsert_bars", "load_bars", "get_or_create_asset", "bar_coverage",
    "ingest_logged", "recent_ingests", "save_experiment",
    "list_experiments", "get_ranking", "get_detail", "get_equity_curve",
    "register_model", "list_models", "save_feature_set", "load_feature_set",
    "list_feature_sets", "save_order", "list_orders", "save_positions",
    "save_account_snapshot", "account_curve", "table_stats",
    "table_schema", "run_sql", "assert_readonly", "example_queries",
    "build_training_set", "load_training_set", "describe_feature_set",
]
