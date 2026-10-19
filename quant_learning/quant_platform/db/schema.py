# -*- coding: utf-8 -*-
"""
db/schema.py —— 数据库表结构（ORM 模型）
========================================
平台所有持久化数据分五层，共 14 张表：

    1. 行情层   assets / bars / ingest_log
       API 抓回来的原始 OHLCV，以及每次抓取的审计日志。
       bars 用 (asset_id, bar_size, ts) 做唯一约束，重复抓取不会产生脏数据。

    2. 特征层   feature_sets / features
       训练集。features 用**长表**而不是宽表：
       研究过程中特征数量会变，宽表每加一个特征就要 ALTER TABLE；
       长表不用改结构，代价是行数多，靠 (feature_set_id, feature) 索引解决。

    3. 实验层   experiments / backtest_runs / backtest_metrics / equity_points / trades
       一次实验 = 一组策略 × 一组标的；每次回测一行，指标宽表化便于 SQL 排序。

    4. 模型层   model_registry
       研究结论落库：哪些模型通过了显著性检验、被选入实盘、理由是什么。

    5. 实盘层   orders / position_snapshots / account_snapshots
       委托记录、持仓快照、账户净值快照（用于账户曲线、回撤与熔断判断）。

为什么是"真正的 SQL"：
    全部通过 SQLAlchemy Core/ORM 生成标准 SQL，支持 JOIN / GROUP BY / 窗口函数。
    默认 SQLite（单文件、零配置），但业务代码里没有任何 SQLite 方言，
    改一个 DSN 就能整体切到 PostgreSQL 或 MySQL（见 session.py）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    def to_dict(self) -> dict[str, Any]:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _json_safe(obj: Any) -> Any:
    """
    清洗成可 JSON 序列化的结构。

    指标里经常出现 NaN（无交易）和 inf（盈亏比除以 0），
    直接塞给 JSON 列会报错，这里统一转成 None。
    """
    import math

    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return str(obj)


# ============================================================================
# 1. 行情层
# ============================================================================
class Asset(Base):
    """标的主数据。"""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True,
                                        comment="代码，如 AAPL / 700.HK")

    name: Mapped[str | None] = mapped_column(String(128), default=None)
    sec_type: Mapped[str] = mapped_column(String(16), default="STK")
    exchange: Mapped[str] = mapped_column(String(16), default="SMART")
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    bars: Mapped[list[Bar]] = relationship(back_populates="asset",
                                           cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Asset {self.symbol}>"


class Bar(Base):
    """OHLCV 行情。一张表装所有周期，用 bar_size 区分。"""

    __tablename__ = "bars"
    __table_args__ = (
        UniqueConstraint("asset_id", "bar_size", "ts", name="uq_bars_series"),
        Index("ix_bars_series_ts", "asset_id", "bar_size", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(Integer,
                                          ForeignKey("assets.id", ondelete="CASCADE"))
    bar_size: Mapped[str] = mapped_column(String(16), default="1 day",
                                          comment="1 day / 1 hour / 15 mins …")
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(16), default="ibkr",
                                        comment="来自哪个 API")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    asset: Mapped[Asset] = relationship(back_populates="bars")

    def __repr__(self) -> str:
        return f"<Bar {self.asset_id} {self.ts} {self.close}>"


class IngestLog(Base):
    """数据抓取日志：谁、什么时候、拉了多少行、成功还是失败。"""

    __tablename__ = "ingest_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), default="ibkr")
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    bar_size: Mapped[str] = mapped_column(String(16), default="1 day")
    duration: Mapped[str | None] = mapped_column(String(16), default=None)
    status: Mapped[str] = mapped_column(String(16), default="ok",
                                        comment="ok / empty / error")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)

    def __repr__(self) -> str:
        return f"<IngestLog {self.symbol} {self.status} {self.rows}>"


# ============================================================================
# 2. 特征层（训练集）
# ============================================================================
class FeatureSet(Base):
    """一个训练集的元信息。"""

    __tablename__ = "feature_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    symbols: Mapped[Any] = mapped_column(JSON, default=list)
    feature_names: Mapped[Any] = mapped_column(JSON, default=list)
    label: Mapped[str] = mapped_column(String(64), default="ret_fwd_1",
                                       comment="标签定义，如未来1日收益")
    start: Mapped[str | None] = mapped_column(String(32), default=None)
    end: Mapped[str | None] = mapped_column(String(32), default=None)
    n_rows: Mapped[int] = mapped_column(Integer, default=0)
    n_features: Mapped[int] = mapped_column(Integer, default=0)
    train_ratio: Mapped[float] = mapped_column(Float, default=0.7)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    features: Mapped[list[Feature]] = relationship(
        back_populates="feature_set", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<FeatureSet {self.name} rows={self.n_rows}>"


class Feature(Base):
    """
    特征明细（长表）。

    一行 = (训练集, 标的, 时间戳, 特征名) → 特征值。
    标签也作为一种特殊特征存进来（前缀 __label__），
    这样"取一个完整训练集"就是一次查询 + pivot。
    """

    __tablename__ = "features"
    __table_args__ = (
        Index("ix_features_set_name", "feature_set_id", "feature"),
        Index("ix_features_set_symbol_ts", "feature_set_id", "symbol", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_set_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("feature_sets.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    feature: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[float | None] = mapped_column(Float, default=None)

    feature_set: Mapped[FeatureSet] = relationship(back_populates="features")

    def __repr__(self) -> str:
        return f"<Feature {self.symbol} {self.feature} {self.value}>"


# ============================================================================
# 3. 实验层
# ============================================================================
class Experiment(Base):
    """一次实验。"""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    duration: Mapped[str | None] = mapped_column(String(16), default=None)
    bar_size: Mapped[str | None] = mapped_column(String(16), default=None)
    data_source: Mapped[str | None] = mapped_column(String(16), default=None)
    n_symbols: Mapped[int] = mapped_column(Integer, default=0)
    n_strategies: Mapped[int] = mapped_column(Integer, default=0)
    initial_capital: Mapped[float] = mapped_column(Float, default=1_000_000.0)
    commission_rate: Mapped[float] = mapped_column(Float, default=0.0005)
    slippage_rate: Mapped[float] = mapped_column(Float, default=0.0005)
    runtime_sec: Mapped[float] = mapped_column(Float, default=0.0)
    config_json: Mapped[Any] = mapped_column(JSON, default=dict)
    summary_json: Mapped[Any] = mapped_column(JSON, default=dict)

    runs: Mapped[list[BacktestRun]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Experiment {self.name}>"


class BacktestRun(Base):
    """实验里的一次回测 = (实验, 策略, 标的)。"""

    __tablename__ = "backtest_runs"
    __table_args__ = (
        UniqueConstraint("experiment_id", "strategy", "symbol",
                         name="uq_run_exp_strategy_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("experiments.id", ondelete="CASCADE"))
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    params_json: Mapped[Any] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    experiment: Mapped[Experiment] = relationship(back_populates="runs")
    metrics: Mapped[BacktestMetric | None] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan")
    equity: Mapped[list[EquityPoint]] = relationship(
        back_populates="run", cascade="all, delete-orphan")
    trades: Mapped[list[Trade]] = relationship(
        back_populates="run", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<BacktestRun {self.strategy}@{self.symbol}>"


class BacktestMetric(Base):
    """回测绩效指标：关键列（便于 SQL 排序）+ 全量 JSON（避免加指标就改表）。"""

    __tablename__ = "backtest_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"),
        unique=True, index=True)

    total_return: Mapped[float | None] = mapped_column(Float, default=None)
    annual_return: Mapped[float | None] = mapped_column(Float, default=None)
    annual_vol: Mapped[float | None] = mapped_column(Float, default=None)
    max_drawdown: Mapped[float | None] = mapped_column(Float, default=None)
    sharpe: Mapped[float | None] = mapped_column(Float, default=None)
    sortino: Mapped[float | None] = mapped_column(Float, default=None)
    calmar: Mapped[float | None] = mapped_column(Float, default=None)
    var95: Mapped[float | None] = mapped_column(Float, default=None)
    cvar95: Mapped[float | None] = mapped_column(Float, default=None)
    win_rate: Mapped[float | None] = mapped_column(Float, default=None)
    monthly_win_rate: Mapped[float | None] = mapped_column(Float, default=None)
    profit_factor: Mapped[float | None] = mapped_column(Float, default=None)
    turnover: Mapped[float | None] = mapped_column(Float, default=None)
    exposure: Mapped[float | None] = mapped_column(Float, default=None)
    trade_count: Mapped[float | None] = mapped_column(Float, default=None)
    alpha: Mapped[float | None] = mapped_column(Float, default=None)
    beta: Mapped[float | None] = mapped_column(Float, default=None)
    info_ratio: Mapped[float | None] = mapped_column(Float, default=None)
    excess_annual: Mapped[float | None] = mapped_column(Float, default=None)

    # 统计显著性（做过多重检验修正后的值）
    psr: Mapped[float | None] = mapped_column(Float, default=None)
    dsr: Mapped[float | None] = mapped_column(Float, default=None)
    sharpe_ci_low: Mapped[float | None] = mapped_column(Float, default=None)
    sharpe_ci_high: Mapped[float | None] = mapped_column(Float, default=None)

    metrics_json: Mapped[Any] = mapped_column(JSON, default=dict)

    run: Mapped[BacktestRun] = relationship(back_populates="metrics")

    # 中文指标名 → 英文列名（对应 BacktestResult.metrics 的 key）
    METRIC_MAP: dict[str, str] = {
        "总收益率": "total_return",
        "年化收益率": "annual_return",
        "年化波动率": "annual_vol",
        "最大回撤": "max_drawdown",
        "夏普比率": "sharpe",
        "索提诺比率": "sortino",
        "卡玛比率": "calmar",
        "VaR95(日)": "var95",
        "CVaR95(日)": "cvar95",
        "胜率": "win_rate",
        "月度胜率": "monthly_win_rate",
        "盈亏比": "profit_factor",
        "换手率": "turnover",
        "持仓占比": "exposure",
        "交易次数": "trade_count",
        "年化Alpha": "alpha",
        "Beta": "beta",
        "信息比率": "info_ratio",
        "超额年化": "excess_annual",
        "PSR(>0)": "psr",
        "DSR": "dsr",
        "夏普95%下界": "sharpe_ci_low",
        "夏普95%上界": "sharpe_ci_high",
    }

    @classmethod
    def from_metrics(cls, metrics: dict) -> "BacktestMetric":
        """从中文指标字典构造 ORM 对象（NaN/inf 一律存 NULL）。"""
        obj = cls(metrics_json=_json_safe(metrics))
        for cn, en in cls.METRIC_MAP.items():
            if cn in metrics:
                v = metrics[cn]
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                if v == v and v not in (float("inf"), float("-inf")):
                    setattr(obj, en, v)
        return obj

    def __repr__(self) -> str:
        return f"<BacktestMetric run={self.run_id} sharpe={self.sharpe}>"


class EquityPoint(Base):
    """净值曲线采样点（按 stride 抽样，避免表膨胀）。"""

    __tablename__ = "equity_points"
    __table_args__ = (Index("ix_equity_run_ts", "run_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime)
    equity: Mapped[float] = mapped_column(Float)

    run: Mapped[BacktestRun] = relationship(back_populates="equity")


class Trade(Base):
    """一笔完整交易（进 → 出）。run_id 为空时表示实盘手工单。"""

    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_run_entry", "run_id", "entry_ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), default=None)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    strategy: Mapped[str | None] = mapped_column(String(64), default=None)
    direction: Mapped[float] = mapped_column(Float, default=1.0)
    entry_ts: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    exit_ts: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    entry_price: Mapped[float | None] = mapped_column(Float, default=None)
    exit_price: Mapped[float | None] = mapped_column(Float, default=None)
    ret: Mapped[float | None] = mapped_column(Float, default=None)
    bars: Mapped[float | None] = mapped_column(Float, default=None)

    run: Mapped[BacktestRun | None] = relationship(back_populates="trades")

    def __repr__(self) -> str:
        return f"<Trade {self.symbol} {self.ret}>"


# ============================================================================
# 4. 模型层
# ============================================================================
class ModelRegistry(Base):
    """模型注册表：研究结论的落库。"""

    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), default="PORTFOLIO")
    params_json: Mapped[Any] = mapped_column(JSON, default=dict)
    source_experiment: Mapped[str | None] = mapped_column(String(128), default=None)
    sharpe: Mapped[float | None] = mapped_column(Float, default=None)
    psr: Mapped[float | None] = mapped_column(Float, default=None)
    dsr: Mapped[float | None] = mapped_column(Float, default=None)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def __repr__(self) -> str:
        return f"<ModelRegistry {self.strategy} selected={self.selected}>"


# ============================================================================
# 5. 实盘层
# ============================================================================
class Order(Base):
    """委托记录。dry-run 的计划单也记录，方便复盘与审计。"""

    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_ts_symbol", "ts", "symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    round_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    account: Mapped[str | None] = mapped_column(String(32), default=None)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    order_type: Mapped[str] = mapped_column(String(8), default="LMT")
    limit_price: Mapped[float | None] = mapped_column(Float, default=None)
    ref_price: Mapped[float | None] = mapped_column(Float, default=None)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    fx_rate: Mapped[float | None] = mapped_column(Float, default=None)
    value_hkd: Mapped[float | None] = mapped_column(Float, default=None)
    status: Mapped[str] = mapped_column(String(32), default="DRY-RUN")
    broker_order_id: Mapped[int | None] = mapped_column(Integer, default=None)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    payload_json: Mapped[Any] = mapped_column(JSON, default=dict)

    def __repr__(self) -> str:
        return f"<Order {self.action} {self.symbol} {self.qty} {self.status}>"


class PositionSnapshot(Base):
    """持仓快照。"""

    __tablename__ = "position_snapshots"
    __table_args__ = (Index("ix_pos_ts_account", "ts", "account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    account: Mapped[str | None] = mapped_column(String(32), default=None)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    position: Mapped[float] = mapped_column(Float, default=0.0)
    avg_cost: Mapped[float | None] = mapped_column(Float, default=None)
    market_price: Mapped[float | None] = mapped_column(Float, default=None)
    market_value: Mapped[float | None] = mapped_column(Float, default=None)

    def __repr__(self) -> str:
        return f"<PositionSnapshot {self.symbol} {self.position}>"


class AccountSnapshot(Base):
    """账户净值快照（账户曲线、回撤、熔断判断的数据源）。"""

    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    account: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    net_liquidation: Mapped[float | None] = mapped_column(Float, default=None)
    total_cash: Mapped[float | None] = mapped_column(Float, default=None)
    available_funds: Mapped[float | None] = mapped_column(Float, default=None)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    realized_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    gross_exposure: Mapped[float | None] = mapped_column(Float, default=None)

    def __repr__(self) -> str:
        return f"<AccountSnapshot {self.account} {self.net_liquidation}>"


# ============================================================================
# 表清单（分组顺序 = ER 图上的层次顺序）
# ============================================================================
TABLE_GROUPS: dict[str, list[str]] = {
    "行情数据（API 抓取）": ["assets", "bars", "ingest_log"],
    "训练集（特征工程）": ["feature_sets", "features"],
    "实验与回测": ["experiments", "backtest_runs", "backtest_metrics",
                   "equity_points", "trades"],
    "模型注册表": ["model_registry"],
    "实盘运营": ["orders", "position_snapshots", "account_snapshots"],
}

# 每张表的中文说明（Web「数据库」页与 db-stats 用）
TABLE_COMMENTS: dict[str, str] = {
    "assets": "标的主数据，代码唯一",
    "bars": "OHLCV 行情，按(标的,周期,时间)唯一",
    "ingest_log": "每次调 API 的抓取日志",
    "feature_sets": "训练集元信息（标的/时间/特征清单）",
    "features": "特征明细长表（样本×特征→值）",
    "experiments": "一次实验（策略×标的×假设）",
    "backtest_runs": "一次回测 = (实验,策略,标的)",
    "backtest_metrics": "绩效指标（关键列+全量JSON）",
    "equity_points": "净值曲线采样点",
    "trades": "逐笔交易（进→出）",
    "model_registry": "通过显著性检验、入选实盘的模型",
    "orders": "委托记录（含 dry-run 计划）",
    "position_snapshots": "持仓快照",
    "account_snapshots": "账户净值快照",
}


def table_names() -> list[str]:
    return [t for names in TABLE_GROUPS.values() for t in names]
