# -*- coding: utf-8 -*-
"""
quant_platform.db —— 关系型数据库持久层（SQL）
==============================================
平台所有"值得留下"的东西都存在这里：

    API 抓回的行情 → assets / bars / ingest_log
    特征与训练集   → feature_sets / features
    实验与回测     → experiments / backtest_runs / backtest_metrics / equity_points / trades
    研究结论       → model_registry
    实盘运营       → orders / position_snapshots / account_snapshots

最快上手：
    python -m quant_platform.cli db-init               # 建库建表
    python -m quant_platform.cli db-import             # 把已有 CSV 缓存导入库
    python -m quant_platform.cli db-stats              # 看各表行数
    python -m quant_platform.cli db-query --sql "SELECT COUNT(*) FROM bars"
"""

from __future__ import annotations

from .schema import (                                   # noqa: F401
    TABLE_GROUPS, AccountSnapshot, Asset, BacktestMetric, BacktestRun, Bar,
    Base, EquityPoint, Experiment, Feature, FeatureSet, IngestLog,
    ModelRegistry, Order, PositionSnapshot, Trade, table_names,
)
from .session import (                                  # noqa: F401
    ENV_URL, db_file_size, db_path_or_dsn, default_url, get_engine,
    get_session, get_session_factory, init_db, is_sqlite, reset_db,
    resolve_url, session_scope,
)

__all__ = [
    "TABLE_GROUPS", "ENV_URL", "Base",
    "Asset", "Bar", "IngestLog", "FeatureSet", "Feature",
    "Experiment", "BacktestRun", "BacktestMetric", "EquityPoint", "Trade",
    "ModelRegistry", "Order", "PositionSnapshot", "AccountSnapshot",
    "table_names", "default_url", "resolve_url", "get_engine",
    "get_session", "get_session_factory", "session_scope", "init_db",
    "reset_db", "is_sqlite", "db_path_or_dsn", "db_file_size",
]

# 延迟导入：features / migrate / repository 依赖 pandas 与更多子模块，
# 放在包初始化末尾导入，避免任何循环依赖问题。
from . import repository                                # noqa: E402,F401
from .repository import (                               # noqa: E402,F401
    DB_ENABLED, assert_readonly, bar_coverage, example_queries,
    get_detail, get_equity_curve, get_ranking, ingest_logged,
    list_experiments, list_feature_sets, list_models, list_orders,
    load_bars, load_feature_set, recent_ingests, register_model,
    run_sql, save_account_snapshot, save_experiment, save_feature_set,
    save_order, save_positions, set_enabled, table_schema, table_stats,
    upsert_bars,
)
from . import features                                  # noqa: E402,F401
from .features import build_training_set, load_training_set  # noqa: E402,F401
from . import migrate                                   # noqa: E402,F401

__all__ += [
    "repository", "features", "migrate", "DB_ENABLED", "set_enabled",
    "upsert_bars", "load_bars", "bar_coverage", "ingest_logged",
    "recent_ingests", "save_experiment", "list_experiments",
    "get_ranking", "get_detail", "get_equity_curve", "register_model",
    "list_models", "save_feature_set", "load_feature_set",
    "list_feature_sets", "build_training_set", "load_training_set",
    "save_order", "list_orders", "save_positions",
    "save_account_snapshot", "table_stats", "table_schema",
    "run_sql", "assert_readonly", "example_queries",
]
