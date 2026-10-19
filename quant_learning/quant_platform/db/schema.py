# -*- coding: utf-8 -*-
"""
db/schema.py —— 数据库表结构（ORM 模型）
========================================
本平台所有持久化数据都落在这 14 张表里，分为五层：

    【1 行情层】                     —— API 抓回来的原始数据
        assets              标的主数据（一次写入，长期不变）
        bars                OHLCV 行情（核心大表，按 (标的,周期,时间) 唯一）
        ingest_log          抓取日志（谁、什么时候、拉了多少行、成功还是失败）

    【2 特征层】                     —— 训练集
        feature_sets        一个训练集的元信息（名字、标的、时间范围、特征清单）
        features            特征明细（长表：一行 = 一个"样本×特征"的值）

    【3 实验层】                     —— 模型检测与对比
        experiments         一次实验（策略×标的×历史×成本假设）
        backtest_runs       实验里的一次回测（策略, 标的）
        backtest_metrics    该次回测的绩效指标（宽表，便于 SQL 排序筛选）
        equity_points       净值曲线采样点
        trades              回测产生的逐笔交易

    【4 模型层】
        model_registry      模型注册表：哪些模型通过了显著性检验、被选入实盘

    【5 实盘层】
        orders              真实委托记录（含 dry-run 计划）
        position_snapshots  持仓快照
        account_snapshots   账户净值快照

设计取舍（写给课程报告看的三点）：
    1. bars 用 (asset_id, bar_size, ts) 做唯一约束，抓取重复执行也不会产生脏数据，
       配合 upsert 实现"增量更新"，这是行情库的标准做法。
    2. features 用长表而不是宽表：特征数量会随研究推进变化，长表无需改表结构
       （宽表加一个特征就要 ALTER TABLE）。代价是行数多，但用 (feature_set_id, feature)
       复合索引后，取一个训练集的查询仍在毫秒级。
    3. 指标表同时存"关键指标列 + 完整 JSON"：关键列方便 SQL 直接排序比较，
       JSON 保留全量信息，避免以后加指标要迁移表。

数据库选型：默认 SQLite（零配置、单文件、标准 SQL 方言），
通过环境变量 QP_DATABASE_URL 可无缝切到 PostgreSQL / MySQL，无需改任何业务代码。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    def to_dict(self) -> dict:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


def _now() -> datetime:
    return datetime.now()


# ============================================================================
# 1. 行情层
# ============================================================================
class Asset(Base):
    """标的主数据。"""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True,
                                        comment="代码，如 AAPL")
    name: Mapped[Optional[str]] = mapped_column(String(128), comment="显示名")
    sec_type: Mapped[str] = mapped_column(String(16), default="STK")
    exchange: Mapped[str] = mapped_column(String(16), default="SMART")
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    bars: Mapped[list["Bar"]] = relationship(back_populates="asset",
                                             cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Asset {self.symbol}>"


class Bar(Base):
    """OHLCV 行情。一张表装所有周期，用 bar_size 区分。"""

    __tablename__ = "bars"
    __table_args__ = (
        UniqueConstraint("asset_id", "bar_size", "ts", name="uq_bars_series"),
        Index("ix_bars_asset_size_ts", "asset_id", "bar_size", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    bar_size: Mapped[str] = mapped_column(String(16), default="1 day",
                                          comment="1 day / 1 hour / 15 mins …")
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(16), default="ibkr",
                                        comment="数据来自哪个 API")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    asset: Mapped["Asset"] = relationship(back_populates="bars")

    def __repr__(self) -> str:
        return f"<Bar {self.asset_id} {self.ts} {self.close}>"


class IngestLog(Base):
    """数据抓取日志：可审计——每次调 API 拉了什么、结果如何。"""

    __tablename__ = "ingest_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), default="ibkr")
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    bar_size: Mapped[str] = mapped_column(String(16), default="1 day")
    duration: Mapped[Optional[str]] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="ok",
                                        comment="ok / empty / error")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
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
    description: Mapped[Optional[str]] = mapped_column(Text)
    symbols: Mapped[Any] = mapped_column(JSON, default=list)
    feature_names: Mapped[Any] = mapped_column(JSON, default=list)
    label: Mapped[str] = mapped_column(String(64), default="ret_fwd_1",
                                       comment="标签定义，如未来1日收益方向")
    start: Mapped[Optional[str]] = mapped_column(String(32))
    end: Mapped[Optional[str]] = mapped_column(String(32))
    n_rows: Mapped[int] = mapped_column(Integer, default=0)
    n_features: Mapped[int] = mapped_column(Integer, default=0)
    train_ratio: Mapped[float] = mapped_column(Float, default=0.7,
                                               comment="训练集/验证集/测试集切分比例")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    features: Mapped[list["Feature"]] = relationship(
        back_populates="feature_set", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<FeatureSet {self.name} rows={self.n_rows}>"


class Feature(Base):
    """
    特征明细（长表）。

    一行 = (训练集, 标的, 时间戳, 特征名) → 特征值。
    label 也作为一种特殊 feature 名存进来（见 features.py 里的 LABEL_KEY），
    这样"取一个完整训练集"就是一次简单的查询 + pivot。
    """

    __tablename__ = "features"
    __table_args__ = (
        Index("ix_features_set_name", "feature_set_id", "feature"),
        Index("ix_features_set_symbol_ts", "feature_set_id", "symbol", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_set_id: Mapped[int] = mapped_column(
        ForeignKey("feature_sets.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    feature: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[Optional[float]] = mapped_column(Float)

    feature_set: Mapped["FeatureSet"] = relationship(back_populates="features")

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    duration: Mapped[Optional[str]] = mapped_column(String(16))
    bar_size: Mapped[Optional[str]] = mapped_column(String(16))
    data_source: Mapped[Optional[str]] = mapped_column(String(16))
    n_symbols: Mapped[int] = mapped_column(Integer, default=0)
    n_strategies: Mapped[int] = mapped_column(Integer, default=0)
    initial_capital: Mapped[float] = mapped_column(Float, default=1_000_000.0)
    commission_rate: Mapped[float] = mapped_column(Float, default=0.0005)
    slippage_rate: Mapped[float] = mapped_column(Float, default=0.0005)
    runtime_sec: Mapped[float] = mapped_column(Float, default=0.0)
    config_json: Mapped[Any] = mapped_column(JSON, default=dict)
    summary_json: Mapped[Any] = mapped_column(JSON, default=dict)

    runs: Mapped[list["BacktestRun"]] = relationship(
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
        ForeignKey("experiments.id", ondelete="CASCADE"))
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    params_json: Mapped[Any] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    experiment: Mapped["Experiment"] = relationship(back_populates="runs")
    metrics: Mapped[Optional["BacktestMetric"]] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan")
    equity: Mapped[list["EquityPoint"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")
    trades: Mapped[list["Trade"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<BacktestRun {self.strategy}@{self.symbol}>"


class BacktestMetric(Base):
    """回测绩效指标：关键列 + 全量 JSON。"""

    __tablename__ = "backtest_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), unique=True, index=True)

    total_return: Mapped[Optional[float]] = mapped_column(Float)      # 总收益率
    annual_return: Mapped[Optional[float]] = mapped_column(Float)     # 年化收益率
    annual_vol: Mapped[Optional[float]] = mapped_column(Float)        # 年化波动率
    max_drawdown: Mapped[Optional[float]] = mapped_column(Float)      # 最大回撤
    sharpe: Mapped[Optional[float]] = mapped_column(Float)            # 夏普比率
    sortino: Mapped[Optional[float]] = mapped_column(Float)           # 索提诺比率
    calmar: Mapped[Optional[float]] = mapped_column(Float)            # 卡玛比率
    var95: Mapped[Optional[float]] = mapped_column(Float)             # VaR95(日)
    cvar95: Mapped[Optional[float]] = mapped_column(Float)           # CVaR95(日)
    win_rate: Mapped[Optional[float]] = mapped_column(Float)          # 胜率
    monthly_win_rate: Mapped[Optional[float]] = mapped_column(Float)  # 月度胜率
    profit_factor: Mapped[Optional[float]] = mapped_column(Float)     # 盈亏比
    turnover: Mapped[Optional[float]] = mapped_column(Float)          # 换手率
    exposure: Mapped[Optional[float]] = mapped_column(Float)          # 持仓占比
    trade_count: Mapped[Optional[float]] = mapped_column(Float)       # 交易次数
    alpha: Mapped[Optional[float]] = mapped_column(Float)             # 年化Alpha
    beta: Mapped[Optional[float]] = mapped_column(Float)              # Beta
    info_ratio: Mapped[Optional[float]] = mapped_column(Float)        # 信息比率
    excess_annual: Mapped[Optional[float]] = mapped_column(Float)     # 超额年化

    # 统计显著性（若有）
    psr: Mapped[Optional[float]] = mapped_column(Float)
    dsr: Mapped[Optional[float]] = mapped_column(Float)
    sharpe_ci_low: Mapped[Optional[float]] = mapped_column(Float)
    sharpe_ci_high: Mapped[Optional[float]] = mapped_column(Float)

    metrics_json: Mapped[Any] = mapped_column(JSON, default=dict)

    run: Mapped["BacktestRun"] = relationship(back_populates="metrics")

    # 中文指标名 → 英文列名（与 BacktestResult.metrics 的 key 对应）
    METRIC_MAP = {
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
        """从中文指标字典构造 ORM 对象。"""
        obj = cls(metrics_json=_json_safe(metrics))
        for cn, en in cls.METRIC_MAP.items():
            if cn in metrics:
                v = metrics[cn]
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                if v != v or v in (float("inf"), float("-inf")):   # NaN / inf
                    continue
                setattr(obj, en, v)
        return obj

    def __repr__(self) -> str:
        return f"<BacktestMetric run={self.run_id} sharpe={self.sharpe}>"


class EquityPoint(Base):
    """净值曲线采样点（为避免表过大，默认按 stride 抽样存储）。"""

    __tablename__ = "equity_points"
    __table_args__ = (Index("ix_equity_run_ts", "run_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime)
    equity: Mapped[float] = mapped_column(Float)

    run: Mapped["BacktestRun"] = relationship(back_populates="equity")


class Trade(Base):
    """回测/实盘产生的一笔完整交易（进→出）。"""

    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_run_entry", "run_id", "entry_ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    strategy: Mapped[Optional[str]] = mapped_column(String(64))
    direction: Mapped[float] = mapped_column(Float, default=1.0)
    entry_ts: Mapped[Optional[datetime]] = mapped_column(DateTime)
    exit_ts: Mapped[Optional[datetime]] = mapped_column(DateTime)
    entry_price: Mapped[Optional[float]] = mapped_column(Float)
    exit_price: Mapped[Optional[float]] = mapped_column(Float)
    ret: Mapped[Optional[float]] = mapped_column(Float)
    bars: Mapped[Optional[float]] = mapped_column(Float)

    run: Mapped[Optional["BacktestRun"]] = relationship(back_populates="trades")


# ============================================================================
# 4. 模型层
# ============================================================================
class ModelRegistry(Base):
    """模型注册表：研究结论的落库——谁入选、为什么入选。"""

    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), default="PORTFOLIO")
    params_json: Mapped[Any] = mapped_column(JSON, default=dict)
    source_experiment: Mapped[Optional[str]] = mapped_column(String(128))
    sharpe: Mapped[Optional[float]] = mapped_column(Float)
    psr: Mapped[Optional[float]] = mapped_column(Float)
    dsr: Mapped[Optional[float]] = mapped_column(Float)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    def __repr__(self) -> str:
        return f"<ModelRegistry {self.strategy} selected={self.selected}>"


# ============================================================================
# 5. 实盘层
# ============================================================================
class Order(Base):
    """委托记录（真实下单与 dry-run 计划都记，便于审计与复盘）。"""

    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_ts_symbol", "ts", "symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    round_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    account: Mapped[Optional[str]] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    order_type: Mapped[str] = mapped_column(String(8), default="LMT")
    limit_price: Mapped[Optional[float]] = mapped_column(Float)
    ref_price: Mapped[Optional[float]] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    fx_rate: Mapped[Optional[float]] = mapped_column(Float)
    value_hkd: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="DRY-RUN")
    broker_order_id: Mapped[Optional[int]] = mapped_column(Integer)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[Optional[float]] = mapped_column(Float)
    note: Mapped[Optional[str]] = mapped_column(Text)
    payload_json: Mapped[Any] = mapped_column(JSON, default=dict)

    def __repr__(self) -> str:
        return f"<Order {self.action} {self.symbol} {self.qty} {self.status}>"


class PositionSnapshot(Base):
    """持仓快照。"""

    __tablename__ = "position_snapshots"
    __table_args__ = (Index("ix_pos_ts_account", "ts", "account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    account: Mapped[Optional[str]] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    position: Mapped[float] = mapped_column(Float)
    avg_cost: Mapped[Optional[float]] = mapped_column(Float)
    market_price: Mapped[Optional[float]] = mapped_column(Float)
    market_value: Mapped[Optional[float]] = mapped_column(Float)


class AccountSnapshot(Base):
    """账户净值快照（用于画账户曲线、算回撤与熔断）。"""

    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    account: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    net_liquidation: Mapped[Optional[float]] = mapped_column(Float)
    total_cash: Mapped[Optional[float]] = mapped_column(Float)
    available_funds: Mapped[Optional[float]] = mapped_column(Float)
    unrealized_pnl: Mapped[Optional[float]] = mapped_column(Float)
    realized_pnl: Mapped[Optional[float]] = mapped_column(Float)
    gross_exposure: Mapped[Optional[float]] = mapped_column(Float)


def _json_safe(obj: Any) -> Any:
    """把 NaN/inf/非 JSON 类型清洗成可存储的值。"""
    import math

    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (int, bool, str)) or obj is None:
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return str(obj)


# 表清单（用于统计/展示，顺序即 ER 图上的分组顺序）
TABLE_GROUPS = {
    "行情数据（API 抓取）": ["assets", "bars", "ingest_log"],
    "训练集（特征工程）": ["feature_sets", "features"],
    "实验与回测": ["experiments", "backtest_runs", "backtest_metrics",
                   "equity_points", "trades"],
    "模型注册表": ["model_registry"],
    "实盘运营": ["orders", "position_snapshots", "account_snapshots"],
}


def table_names() -> list:
    return [t for names in TABLE_GROUPS.values() for t in names]
