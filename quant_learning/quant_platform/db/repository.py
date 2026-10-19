# -*- coding: utf-8 -*-
"""
db/repository.py —— 数据访问层（Repository 模式）
==================================================
业务代码只跟这里打交道，不直接写 SQL。好处：
    * 表结构变了只改这一层；
    * 所有写入统一做清洗（NaN/inf → NULL）、去重、事务；
    * 便于在报告里画"数据流向图"：API → repository → 表。

五大类方法：
    行情    upsert_bars / load_bars / bar_coverage / ingest_logged / recent_ingests
    实验    save_experiment / list_experiments / get_ranking / get_detail / get_equity_curve
    模型    register_model / list_models
    训练集  save_feature_set / load_feature_set / list_feature_sets / delete_feature_set
    实盘    save_order / list_orders / save_positions / save_account_snapshot / account_curve
    运维    table_stats / table_schema / run_sql / assert_readonly / example_queries
"""

from __future__ import annotations

import json
import math
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from ..config import CONFIG
from .schema import (
    TABLE_GROUPS, AccountSnapshot, Asset, BacktestMetric, BacktestRun, Bar,
    EquityPoint, Experiment, Feature, FeatureSet, IngestLog, ModelRegistry,
    Order, PositionSnapshot, Trade, _utcnow,
)
from .session import get_engine, session_scope

# 全局总开关：关掉后所有写库操作静默返回，保证无数据库时也能跑实验。
# 初值跟随配置（configs/*.yaml 的 db.enabled，默认 True）。
DB_ENABLED = bool(getattr(getattr(CONFIG, "db", None), "enabled", True))


def set_enabled(flag: bool) -> None:
    global DB_ENABLED
    DB_ENABLED = bool(flag)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _clean(v: Any) -> Optional[float]:
    """把 pandas/numpy 的值转成可入库的 float；NaN / inf → None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _dt(v: Any) -> Optional[datetime]:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return None
    if isinstance(v, datetime):
        return v
    try:
        return pd.Timestamp(v).to_pydatetime()
    except Exception:                                            # noqa: BLE001
        return None


def _json(obj: Any) -> Any:
    """递归清洗成可 JSON 序列化的结构（NaN/inf → None）。"""
    if isinstance(obj, dict):
        return {str(k): _json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (bool, str)) or obj is None:
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, (pd.Timestamp, datetime)):
        return pd.Timestamp(obj).isoformat()
    return str(obj)


def _dialect_insert(table, session: Session):
    """按方言返回支持 upsert 的 insert 构造器。"""
    name = session.bind.dialect.name if session.bind is not None else "sqlite"
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _ins
    elif name == "mysql":
        from sqlalchemy.dialects.mysql import insert as _ins
    else:
        from sqlalchemy.dialects.sqlite import insert as _ins
    return _ins(table)


def _conflict_target(stmt, cols: Sequence[str]) -> Dict[str, Any]:
    """
    冲突时用于更新的新值。

    不同方言的"新值"引用名不同：
        PostgreSQL / SQLite → stmt.excluded
        MySQL               → stmt.inserted
    """
    src = getattr(stmt, "inserted", None) or getattr(stmt, "excluded")
    return {c: getattr(src, c) for c in cols}


# ===========================================================================
# 行情层
# ===========================================================================
def get_or_create_asset(s: Session, symbol: str, **kw) -> Asset:
    sym = str(symbol).upper()
    a = s.execute(select(Asset).where(Asset.symbol == sym)).scalar_one_or_none()
    if a is None:
        a = Asset(symbol=sym,
                  name=kw.get("name"),
                  sec_type=kw.get("sec_type", "STK"),
                  exchange=kw.get("exchange", "SMART"),
                  currency=kw.get("currency", "USD"))
        s.add(a)
        s.flush()
    return a


def upsert_bars(symbol: str, bar_size: str, df: pd.DataFrame,
                source: str = "ibkr", session: Optional[Session] = None) -> int:
    """
    把 OHLCV 写入 bars 表：同一 (标的, 周期, 时间戳) 已存在则更新，否则插入。

    幂等是刚需——实验反复跑、定时巡检反复拉数据，不能每次都翻倍。

    返回写入/更新的行数。
    """
    if not DB_ENABLED or df is None or len(df) == 0:
        return 0
    if not isinstance(df.index, pd.DatetimeIndex):
        return 0

    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        asset = get_or_create_asset(s, symbol)
        rows: List[Dict[str, Any]] = []
        now = _utcnow()
        for ts, r in df.iterrows():
            t = _dt(ts)
            c = _clean(r.get("close"))
            if t is None or c is None:
                continue
            o = _clean(r.get("open"))
            h = _clean(r.get("high"))
            lo = _clean(r.get("low"))
            rows.append({
                "asset_id": asset.id,
                "bar_size": str(bar_size),
                "ts": t,
                "open": c if o is None else o,
                "high": c if h is None else h,
                "low": c if lo is None else lo,
                "close": c,
                "volume": _clean(r.get("volume")) or 0.0,
                "source": source,
                "ingested_at": now,
            })
        if not rows:
            return 0

        update_cols = ["open", "high", "low", "close", "volume", "source",
                       "ingested_at"]
        for i in range(0, len(rows), 1000):
            stmt = _dialect_insert(Bar, s).values(rows[i:i + 1000])
            if s.bind.dialect.name == "mysql":
                stmt = stmt.on_duplicate_key_update(**_conflict_target(stmt, update_cols))
            else:
                stmt = stmt.on_conflict_do_update(
                    index_elements=["asset_id", "bar_size", "ts"],
                    set_=_conflict_target(stmt, update_cols))
            s.execute(stmt)
        return len(rows)
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def load_bars(symbol: str, bar_size: str = "1 day",
              start: Optional[str] = None, end: Optional[str] = None,
              session: Optional[Session] = None) -> pd.DataFrame:
    """从库里读 OHLCV，返回与 data 模块同格式的 DataFrame。"""
    if not DB_ENABLED:
        return pd.DataFrame()
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        q = (select(Bar.ts, Bar.open, Bar.high, Bar.low, Bar.close, Bar.volume)
             .join(Asset, Asset.id == Bar.asset_id)
             .where(Asset.symbol == str(symbol).upper())
             .where(Bar.bar_size == str(bar_size))
             .order_by(Bar.ts))
        if start:
            q = q.where(Bar.ts >= _dt(start))
        if end:
            q = q.where(Bar.ts <= _dt(end))
        rows = s.execute(q).all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["date", "open", "high", "low",
                                         "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date").astype(float)
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def bars_exist(symbol: str, bar_size: str,
               session: Optional[Session] = None) -> bool:
    """该 (标的, 周期) 序列在库里是否已有数据（用于增量导入时跳过）。"""
    if not DB_ENABLED:
        return False
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        q = (select(Bar.id)
             .join(Asset, Asset.id == Bar.asset_id)
             .where(Asset.symbol == str(symbol).upper())
             .where(Bar.bar_size == str(bar_size))
             .limit(1))
        return s.execute(q).first() is not None
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def bar_coverage(session: Optional[Session] = None) -> pd.DataFrame:
    """数据资产总览：每个 (标的, 周期) 多少根、时间范围、来源。"""
    if not DB_ENABLED:
        return pd.DataFrame()
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        rows = s.execute(text(
            "SELECT a.symbol AS 标的, b.bar_size AS 周期, COUNT(*) AS 行数, "
            "       MIN(b.ts) AS 起始, MAX(b.ts) AS 结束, b.source AS 来源 "
            "FROM bars b JOIN assets a ON a.id = b.asset_id "
            "GROUP BY a.symbol, b.bar_size, b.source "
            "ORDER BY a.symbol, b.bar_size")).all()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=["标的", "周期", "行数", "起始",
                                           "结束", "来源"])
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


@contextmanager
def ingest_logged(source: str, symbol: str, bar_size: str = "1 day",
                  duration: Optional[str] = None):
    """
    记录一次 API 抓取（成功 / 空 / 失败都写 ingest_log）。

    用法：
        with ingest_logged("ibkr", "AAPL") as log:
            df = src.fetch(req)
            log["rows"] = len(df)
    """
    if not DB_ENABLED:
        yield {}
        return

    t0 = time.time()
    rec: Dict[str, Any] = {"rows": 0, "status": "ok", "message": None}
    try:
        yield rec
    except Exception as e:                                        # noqa: BLE001
        rec["status"] = "error"
        rec["message"] = f"{type(e).__name__}: {str(e)[:300]}"
        raise
    finally:
        # 日志写失败绝不能影响取数主流程
        try:
            with session_scope() as s:
                s.add(IngestLog(
                    source=source, symbol=str(symbol).upper(),
                    bar_size=str(bar_size), duration=duration,
                    status=rec.get("status", "ok"),
                    rows=int(rec.get("rows", 0) or 0),
                    message=rec.get("message"),
                    started_at=datetime.fromtimestamp(t0),
                    finished_at=_utcnow(),
                    duration_ms=round((time.time() - t0) * 1000, 1),
                ))
        except Exception:                                        # noqa: BLE001
            pass


def recent_ingests(limit: int = 50, session: Optional[Session] = None) -> pd.DataFrame:
    if not DB_ENABLED:
        return pd.DataFrame()
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        rows = s.execute(select(IngestLog).order_by(
            IngestLog.id.desc()).limit(limit)).scalars().all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([r.to_dict() for r in rows])
        return df[["id", "source", "symbol", "bar_size", "status", "rows",
                   "duration_ms", "finished_at", "message"]]
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


# ===========================================================================
# 实验层
# ===========================================================================
def save_experiment(res, with_equity: bool = True, equity_stride: int = 3,
                    with_trades: bool = True) -> Optional[int]:
    """
    把 ExperimentResult 完整落库：

        experiments → backtest_runs → backtest_metrics / equity_points / trades

    重复跑同名实验时按 (实验, 策略, 标的) 覆盖，不会产生重复行。
    返回 experiment.id；数据库不可用时返回 None。
    """
    if not DB_ENABLED:
        return None
    cfg = res.config
    with session_scope() as s:
        exp = s.execute(select(Experiment).where(
            Experiment.name == cfg.name)).scalar_one_or_none()
        if exp is None:
            exp = Experiment(name=cfg.name)
            s.add(exp)
            s.flush()

        exp.created_at = _utcnow()
        exp.duration = cfg.duration
        exp.bar_size = cfg.bar_size
        exp.data_source = cfg.data_source
        exp.n_symbols = len(cfg.symbols)
        exp.n_strategies = len(cfg.strategies)
        exp.initial_capital = cfg.initial_capital
        exp.commission_rate = cfg.commission_rate
        exp.slippage_rate = cfg.slippage_rate
        exp.runtime_sec = round(float(getattr(res, "runtime_sec", 0.0) or 0.0), 2)
        exp.config_json = _json(getattr(cfg, "__dict__", {}))
        exp.summary_json = _json({
            "data_info": getattr(res, "data_info", {}),
            "symbols": cfg.symbols,
        })
        s.flush()

        detail = getattr(res, "detail", None)
        if detail is None or len(detail) == 0:
            return exp.id

        for _, row in detail.iterrows():
            strategy = str(row.get("策略", ""))
            symbol = str(row.get("标的", "")).upper()
            run = s.execute(select(BacktestRun).where(
                BacktestRun.experiment_id == exp.id,
                BacktestRun.strategy == strategy,
                BacktestRun.symbol == symbol)).scalar_one_or_none()
            if run is None:
                run = BacktestRun(experiment_id=exp.id, strategy=strategy,
                                  symbol=symbol)
                s.add(run)
                s.flush()

            params = row.get("参数")
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except Exception:                                # noqa: BLE001
                    params = {"raw": params}
            run.params_json = _json(params or {})
            run.error = str(row["error"]) if "error" in row and pd.notna(row["error"]) else None
            run.created_at = _utcnow()
            s.flush()

            metrics = {k: v for k, v in row.items()
                       if k not in ("策略", "标的", "参数", "error")}
            m = BacktestMetric.from_metrics(metrics)
            if run.metrics is not None:
                for k, v in m.to_dict().items():
                    if k not in ("id", "run_id"):
                        setattr(run.metrics, k, v)
            else:
                m.run_id = run.id
                s.add(m)
            s.flush()

            key = f"{strategy}|{symbol}"

            # 净值曲线：抽样存储
            if with_equity:
                eq = getattr(res, "equity_curves", {}).get(key)
                if eq is not None and len(eq):
                    s.query(EquityPoint).filter(
                        EquityPoint.run_id == run.id).delete()
                    pts = []
                    for t, v in eq.iloc[::max(1, int(equity_stride))].items():
                        ts_, ev = _dt(t), _clean(v)
                        if ts_ is not None and ev is not None:
                            pts.append({"run_id": run.id, "ts": ts_, "equity": ev})
                    for i in range(0, len(pts), 1000):
                        s.execute(_dialect_insert(EquityPoint, s).values(pts[i:i + 1000]))

            # 交易明细
            if with_trades:
                tr = getattr(res, "trades", {}).get(key)
                if tr is not None and len(tr):
                    s.query(Trade).filter(Trade.run_id == run.id).delete()
                    recs = []
                    for _, t in tr.iterrows():
                        recs.append({
                            "run_id": run.id, "symbol": symbol,
                            "strategy": strategy,
                            "direction": _clean(t.get("direction")) or 1.0,
                            "entry_ts": _dt(t.get("entry_date")),
                            "exit_ts": _dt(t.get("exit_date")),
                            "entry_price": _clean(t.get("entry_price")),
                            "exit_price": _clean(t.get("exit_price")),
                            "ret": _clean(t.get("ret")),
                            "bars": _clean(t.get("bars")),
                        })
                    for i in range(0, len(recs), 1000):
                        s.execute(_dialect_insert(Trade, s).values(recs[i:i + 1000]))

        return exp.id


def list_experiments(session: Optional[Session] = None) -> pd.DataFrame:
    if not DB_ENABLED:
        return pd.DataFrame()
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        rows = s.execute(select(Experiment).order_by(
            Experiment.id.desc())).scalars().all()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([{
            "id": r.id, "实验": r.name, "创建时间": r.created_at,
            "标的数": r.n_symbols, "策略数": r.n_strategies,
            "周期": r.bar_size, "历史": r.duration, "用时(s)": r.runtime_sec,
        } for r in rows])
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def get_ranking(experiment: str, session: Optional[Session] = None) -> pd.DataFrame:
    """按策略跨标的平均绩效排名（SQL GROUP BY 聚合，等价于 by_strategy.csv）。"""
    if not DB_ENABLED:
        return pd.DataFrame()
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        rows = s.execute(text(
            "SELECT r.strategy AS 策略, COUNT(*) AS 标的数, "
            "       AVG(m.sharpe) AS 夏普比率, AVG(m.annual_return) AS 年化收益率, "
            "       AVG(m.max_drawdown) AS 最大回撤, AVG(m.calmar) AS 卡玛比率, "
            "       AVG(m.win_rate) AS 胜率, AVG(m.trade_count) AS 交易次数, "
            "       AVG(m.excess_annual) AS 超额年化 "
            "FROM backtest_runs r "
            "JOIN backtest_metrics m ON m.run_id = r.id "
            "JOIN experiments e ON e.id = r.experiment_id "
            "WHERE e.name = :exp AND m.sharpe IS NOT NULL "
            "GROUP BY r.strategy ORDER BY 夏普比率 DESC"), {"exp": experiment}).all()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=["策略", "标的数", "夏普比率", "年化收益率",
                                           "最大回撤", "卡玛比率", "胜率", "交易次数",
                                           "超额年化"])
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def get_detail(experiment: str, symbol: Optional[str] = None,
               session: Optional[Session] = None) -> pd.DataFrame:
    """实验明细：一行一个 (策略, 标的)。"""
    if not DB_ENABLED:
        return pd.DataFrame()
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        sql = ("SELECT r.strategy AS 策略, r.symbol AS 标的, "
               "       m.annual_return AS 年化收益率, m.sharpe AS 夏普比率, "
               "       m.max_drawdown AS 最大回撤, m.calmar AS 卡玛比率, "
               "       m.win_rate AS 胜率, m.profit_factor AS 盈亏比, "
               "       m.trade_count AS 交易次数, m.excess_annual AS 超额年化, "
               "       r.params_json AS 参数, r.error AS 错误 "
               "FROM backtest_runs r "
               "LEFT JOIN backtest_metrics m ON m.run_id = r.id "
               "JOIN experiments e ON e.id = r.experiment_id "
               "WHERE e.name = :exp")
        params: Dict[str, Any] = {"exp": experiment}
        if symbol:
            sql += " AND r.symbol = :sym"
            params["sym"] = str(symbol).upper()
        sql += " ORDER BY m.sharpe DESC"
        rows = s.execute(text(sql), params).all()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=["策略", "标的", "年化收益率", "夏普比率",
                                           "最大回撤", "卡玛比率", "胜率", "盈亏比",
                                           "交易次数", "超额年化", "参数", "错误"])
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def get_equity_curve(experiment: str, strategy: str, symbol: str,
                     session: Optional[Session] = None) -> pd.Series:
    if not DB_ENABLED:
        return pd.Series(dtype=float)
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        rows = s.execute(text(
            "SELECT p.ts, p.equity FROM equity_points p "
            "JOIN backtest_runs r ON r.id = p.run_id "
            "JOIN experiments e ON e.id = r.experiment_id "
            "WHERE e.name = :exp AND r.strategy = :stg AND r.symbol = :sym "
            "ORDER BY p.ts"),
            {"exp": experiment, "stg": strategy, "sym": str(symbol).upper()}).all()
        if not rows:
            return pd.Series(dtype=float)
        idx = pd.to_datetime([r[0] for r in rows])
        return pd.Series([r[1] for r in rows], index=idx, name=f"{strategy}|{symbol}")
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


# ===========================================================================
# 模型注册表
# ===========================================================================
def register_model(strategy: str, params: Optional[dict] = None,
                   symbol: str = "PORTFOLIO",
                   source_experiment: Optional[str] = None,
                   sharpe: Optional[float] = None,
                   psr: Optional[float] = None, dsr: Optional[float] = None,
                   selected: bool = False,
                   note: Optional[str] = None) -> Optional[int]:
    """登记/更新一个模型（(strategy, symbol) 视为同一条，覆盖更新）。"""
    if not DB_ENABLED:
        return None
    with session_scope() as s:
        m = s.execute(select(ModelRegistry).where(
            ModelRegistry.strategy == strategy,
            ModelRegistry.symbol == str(symbol).upper())).scalar_one_or_none()
        if m is None:
            m = ModelRegistry(strategy=strategy, symbol=str(symbol).upper())
            s.add(m)
            s.flush()
        m.params_json = _json(params or {})
        m.source_experiment = source_experiment
        m.sharpe = _clean(sharpe)
        m.psr = _clean(psr)
        m.dsr = _clean(dsr)
        m.selected = bool(selected)
        m.note = note
        m.created_at = _utcnow()
        s.flush()
        return m.id


def list_models(selected_only: bool = False,
                session: Optional[Session] = None) -> pd.DataFrame:
    if not DB_ENABLED:
        return pd.DataFrame()
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        q = select(ModelRegistry)
        if selected_only:
            q = q.where(ModelRegistry.selected.is_(True))
        rows = s.execute(q.order_by(ModelRegistry.sharpe.desc())).scalars().all()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([{
            "模型": r.strategy, "标的": r.symbol, "夏普": r.sharpe,
            "PSR": r.psr, "DSR": r.dsr, "入选": r.selected,
            "来源实验": r.source_experiment, "备注": r.note,
            "创建时间": r.created_at,
        } for r in rows])
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


# ===========================================================================
# 特征层（训练集）
# ===========================================================================
def save_feature_set(name: str, feat: pd.DataFrame, label: pd.Series,
                     symbols: Sequence[str], description: str = "",
                     train_ratio: float = 0.7,
                     label_name: str = "ret_fwd_1") -> Optional[int]:
    """
    保存一个训练集到数据库（覆盖同名）。

    feat  : 特征矩阵，索引为 DatetimeIndex 或 MultiIndex(symbol, ts)
    label : 标签序列，与 feat 行对齐
    """
    if not DB_ENABLED or feat is None or len(feat) == 0:
        return None

    df = feat.copy()
    lab = pd.Series(label).reindex(df.index) if label is not None else pd.Series(
        np.nan, index=df.index)

    if isinstance(df.index, pd.MultiIndex):
        sym_idx = df.index.get_level_values(0)
        ts_idx = pd.to_datetime(df.index.get_level_values(1))
    else:
        sym_idx = pd.Index([str(symbols[0]).upper()] * len(df))
        ts_idx = pd.to_datetime(df.index)

    with session_scope() as s:
        fs = s.execute(select(FeatureSet).where(
            FeatureSet.name == name)).scalar_one_or_none()
        if fs is None:
            fs = FeatureSet(name=name)
            s.add(fs)
            s.flush()
        else:
            s.query(Feature).filter(Feature.feature_set_id == fs.id).delete()
            s.flush()

        cols = [str(c) for c in df.columns]
        fs.description = description
        fs.symbols = _json([str(x).upper() for x in symbols])
        fs.feature_names = _json(cols)
        fs.label = label_name
        fs.start = str(ts_idx.min().date()) if len(ts_idx) else None
        fs.end = str(ts_idx.max().date()) if len(ts_idx) else None
        fs.n_rows = int(len(df))
        fs.n_features = int(len(cols))
        fs.train_ratio = float(train_ratio)
        fs.created_at = _utcnow()
        s.flush()

        recs: List[Dict[str, Any]] = []
        for i in range(len(df)):
            sym = str(sym_idx[i]).upper()
            ts = ts_idx[i].to_pydatetime()
            for c in cols:
                v = _clean(df.iloc[i][c])
                if v is None:
                    continue
                recs.append({"feature_set_id": fs.id, "symbol": sym, "ts": ts,
                             "feature": c, "value": v})
            lv = _clean(lab.iloc[i])
            if lv is not None:
                recs.append({"feature_set_id": fs.id, "symbol": sym, "ts": ts,
                             "feature": f"__label__{label_name}", "value": lv})
        for i in range(0, len(recs), 2000):
            s.execute(_dialect_insert(Feature, s).values(recs[i:i + 2000]))
        return fs.id


def list_feature_sets(session: Optional[Session] = None) -> pd.DataFrame:
    if not DB_ENABLED:
        return pd.DataFrame()
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        rows = s.execute(select(FeatureSet).order_by(
            FeatureSet.id.desc())).scalars().all()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([{
            "id": r.id, "训练集": r.name,
            "标的": ",".join(r.symbols or []),
            "样本数": r.n_rows, "特征数": r.n_features,
            "标签": r.label, "时间范围": f"{r.start} ~ {r.end}",
            "创建时间": r.created_at,
        } for r in rows])
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def load_feature_set(name: str, return_split: bool = False,
                     session: Optional[Session] = None):
    """
    读回训练集。

    return_split=False → (X, y)，索引为 MultiIndex(symbol, ts)
    return_split=True  → (X_tr, y_tr, X_va, y_va, X_te, y_te)，按时间顺序切分
    """
    if not DB_ENABLED:
        return pd.DataFrame(), pd.Series(dtype=float)
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        fs = s.execute(select(FeatureSet).where(
            FeatureSet.name == name)).scalar_one_or_none()
        if fs is None:
            return pd.DataFrame(), pd.Series(dtype=float)
        rows = s.execute(select(Feature.symbol, Feature.ts, Feature.feature,
                                Feature.value).where(
            Feature.feature_set_id == fs.id)).all()
        if not rows:
            return pd.DataFrame(), pd.Series(dtype=float)
        df = pd.DataFrame(rows, columns=["symbol", "ts", "feature", "value"])
        wide = df.pivot_table(index=["symbol", "ts"], columns="feature",
                              values="value", aggfunc="last")
        wide = wide.sort_index(level=["symbol", "ts"])
        label_col = f"__label__{fs.label}"
        if label_col not in wide.columns:
            return pd.DataFrame(), pd.Series(dtype=float)
        y = wide[label_col]
        X = wide.drop(columns=[label_col])
        X = X[sorted(X.columns)]
        if not return_split:
            return X, y
        ratio = float(fs.train_ratio or 0.7)
        n = len(X)
        n_tr = int(n * ratio)
        n_va = int(n * (ratio + (1 - ratio) / 2))
        return (X.iloc[:n_tr], y.iloc[:n_tr],
                X.iloc[n_tr:n_va], y.iloc[n_tr:n_va],
                X.iloc[n_va:], y.iloc[n_va:])
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def delete_feature_set(name: str) -> bool:
    if not DB_ENABLED:
        return False
    with session_scope() as s:
        fs = s.execute(select(FeatureSet).where(
            FeatureSet.name == name)).scalar_one_or_none()
        if fs is None:
            return False
        s.delete(fs)
        return True


# ===========================================================================
# 实盘层
# ===========================================================================
def save_order(order: dict) -> Optional[int]:
    """保存一条委托记录。"""
    if not DB_ENABLED:
        return None
    with session_scope() as s:
        o = Order(
            ts=_dt(order.get("时间")) or _utcnow(),
            round_id=order.get("round_id"),
            account=order.get("账户"),
            symbol=str(order.get("标的", "")).upper(),
            action=str(order.get("动作", "")).upper(),
            qty=_clean(order.get("股数")) or 0.0,
            order_type=str(order.get("订单类型", "LMT")).upper(),
            limit_price=_clean(order.get("限价(USD)")),
            ref_price=_clean(order.get("参考价(USD)")),
            currency=order.get("币种") or "USD",
            fx_rate=_clean(order.get("汇率USD.HKD")),
            value_hkd=_clean(order.get("金额(HKD)")),
            status=str(order.get("执行") or "DRY-RUN"),
            broker_order_id=order.get("订单号"),
            note=order.get("备注"),
            payload_json=_json(order),
        )
        s.add(o)
        s.flush()
        return o.id


def list_orders(limit: int = 100, symbol: Optional[str] = None,
                session: Optional[Session] = None) -> pd.DataFrame:
    if not DB_ENABLED:
        return pd.DataFrame()
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        q = select(Order)
        if symbol:
            q = q.where(Order.symbol == str(symbol).upper())
        rows = s.execute(q.order_by(Order.id.desc()).limit(limit)).scalars().all()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([{
            "时间": r.ts, "账户": r.account, "标的": r.symbol,
            "动作": r.action, "股数": r.qty, "类型": r.order_type,
            "限价": r.limit_price, "状态": r.status,
            "订单号": r.broker_order_id, "金额(HKD)": r.value_hkd,
        } for r in rows])
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def save_positions(ts: datetime, account: Optional[str],
                   positions: pd.DataFrame) -> int:
    """
    保存一批持仓快照。

    positions 需含 symbol / position 列，可选 avgCost / marketPrice / marketValue。
    """
    if not DB_ENABLED or positions is None or len(positions) == 0:
        return 0
    with session_scope() as s:
        recs = []
        for _, r in positions.iterrows():
            recs.append(PositionSnapshot(
                ts=ts, account=account,
                symbol=str(r.get("symbol", "")).upper(),
                position=_clean(r.get("position")) or 0.0,
                avg_cost=_clean(r.get("avgCost", r.get("avg_cost"))),
                market_price=_clean(r.get("marketPrice", r.get("market_price"))),
                market_value=_clean(r.get("marketValue", r.get("market_value"))),
            ))
        s.add_all(recs)
        s.flush()
        return len(recs)


def save_account_snapshot(ts: datetime, account: Optional[str],
                          summary: dict) -> Optional[int]:
    """保存账户净值快照。"""
    if not DB_ENABLED:
        return None
    with session_scope() as s:
        a = AccountSnapshot(
            ts=ts, account=account,
            net_liquidation=_clean(summary.get("NetLiquidation")),
            total_cash=_clean(summary.get("TotalCashValue")),
            available_funds=_clean(summary.get("AvailableFunds")),
            unrealized_pnl=_clean(summary.get("UnrealizedPnL")),
            realized_pnl=_clean(summary.get("RealizedPnL")),
            gross_exposure=_clean(summary.get("GrossPositionValue")),
        )
        s.add(a)
        s.flush()
        return a.id


def account_curve(account: Optional[str] = None, limit: int = 500,
                  session: Optional[Session] = None) -> pd.DataFrame:
    if not DB_ENABLED:
        return pd.DataFrame()
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        q = select(AccountSnapshot)
        if account:
            q = q.where(AccountSnapshot.account == account)
        rows = s.execute(q.order_by(AccountSnapshot.ts.asc()).limit(limit)).scalars().all()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([{
            "时间": r.ts, "账户": r.account, "净值": r.net_liquidation,
            "现金": r.total_cash, "可用资金": r.available_funds,
            "浮动盈亏": r.unrealized_pnl, "已实现盈亏": r.realized_pnl,
        } for r in rows])
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


# ===========================================================================
# 运维 / 查询
# ===========================================================================
def table_stats(session: Optional[Session] = None) -> pd.DataFrame:
    """各表行数。"""
    if not DB_ENABLED:
        return pd.DataFrame()
    ctx = session_scope() if session is None else None
    s = session or (ctx.__enter__() if ctx else None)          # type: ignore[arg-type]
    try:
        insp = inspect(s.bind)
        quote = "`" if s.bind.dialect.name == "mysql" else '"'
        existing = set(insp.get_table_names())
        rows = []
        for group, names in TABLE_GROUPS.items():
            for t in names:
                if t not in existing:
                    continue
                n = s.execute(text(f"SELECT COUNT(*) FROM {quote}{t}{quote}")).scalar() or 0
                rows.append({"分组": group, "表": t, "行数": int(n)})
        return pd.DataFrame(rows)
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


# 允许出现在 SQL 里的只读语句开头
READONLY_PREFIX = ("select", "with", "explain", "pragma")
# 出现即拒绝（防注入/误删）
FORBIDDEN = ("insert", "update", "delete", "drop", "alter", "create", "replace",
             "attach", "detach", "vacuum", "reindex", "begin", "commit", "truncate")


def assert_readonly(sql: str) -> None:
    """只允许只读查询，防止误删库 / SQL 注入。"""
    s = " ".join(str(sql).strip().lower().split())
    if not s:
        raise ValueError("SQL 为空")
    if s.count(";") > 1:
        raise ValueError("不支持多语句执行")
    s2 = s.rstrip(";").strip()
    if not s2.startswith(READONLY_PREFIX):
        raise ValueError("只允许 SELECT / WITH / EXPLAIN / PRAGMA 开头的只读语句")
    head = f" {s2[:60]} "
    for w in FORBIDDEN:
        if f" {w} " in head or s2.startswith(w + " "):
            raise ValueError(f"只允许只读查询，检测到关键字：{w}")


def run_sql(sql: str, limit: int = 500) -> Dict[str, Any]:
    """
    执行只读 SQL，返回 {columns, rows, rowcount, limited, elapsed_ms}。

    结果做了清洗：NaN/inf → None，numpy 标量 → Python 标量，
    保证前端 JSON 序列化不会炸。
    """
    assert_readonly(sql)
    limit = max(1, min(int(limit), 5000))
    t0 = time.time()
    with session_scope() as s:
        res = s.execute(text(sql))
        cols = list(res.keys())
        raw = res.fetchmany(limit)
        out: List[List[Any]] = []
        for r in raw:
            row: List[Any] = []
            for v in r:
                if isinstance(v, (np.integer,)):
                    v = int(v)
                elif isinstance(v, (np.floating,)):
                    f = float(v)
                    v = f if math.isfinite(f) else None
                elif isinstance(v, (np.bool_,)):
                    v = bool(v)
                elif isinstance(v, float) and not math.isfinite(v):
                    v = None
                elif isinstance(v, (pd.Timestamp, datetime)):
                    v = pd.Timestamp(v).isoformat()
                elif v is not None and not isinstance(v, (int, float, str, bool)):
                    v = str(v)
                row.append(v)
            out.append(row)
        return {
            "columns": cols,
            "rows": out,
            "rowcount": len(out),
            "limited": limit,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }


def table_schema(table: str) -> pd.DataFrame:
    """返回某张表的字段定义。"""
    if not DB_ENABLED:
        return pd.DataFrame()
    with session_scope() as s:
        insp = inspect(s.bind)
        if table not in insp.get_table_names():
            return pd.DataFrame()
        cols = insp.get_columns(table)
        pk = set(insp.get_pk_constraint(table).get("constrained_columns") or [])
        return pd.DataFrame([{
            "字段": c["name"],
            "类型": str(c["type"]),
            "主键": c["name"] in pk,
            "可空": bool(c.get("nullable", True)),
        } for c in cols])


def example_queries() -> List[Dict[str, str]]:
    """Web 端与 CLI 共用的示例查询（也是课程演示脚本）。"""
    return [
        {"名称": "数据资产总览（每个标的多少根K线）",
         "sql": "SELECT a.symbol AS 标的, b.bar_size AS 周期, COUNT(*) AS 行数,\n"
                "       MIN(b.ts) AS 起始, MAX(b.ts) AS 结束, b.source AS 来源\n"
                "FROM bars b JOIN assets a ON a.id = b.asset_id\n"
                "GROUP BY a.symbol, b.bar_size, b.source\n"
                "ORDER BY 行数 DESC"},
        {"名称": "策略排行榜（跨标的平均夏普）",
         "sql": "SELECT r.strategy AS 策略, COUNT(*) AS 标的数,\n"
                "       ROUND(AVG(m.sharpe), 3) AS 平均夏普,\n"
                "       ROUND(AVG(m.annual_return), 4) AS 平均年化,\n"
                "       ROUND(AVG(m.max_drawdown), 4) AS 平均最大回撤\n"
                "FROM backtest_runs r JOIN backtest_metrics m ON m.run_id = r.id\n"
                "WHERE m.sharpe IS NOT NULL\n"
                "GROUP BY r.strategy ORDER BY 平均夏普 DESC"},
        {"名称": "显著模型（PSR>0.95 且 DSR>0.95）",
         "sql": "SELECT r.strategy AS 策略, r.symbol AS 标的,\n"
                "       ROUND(m.sharpe, 3) AS 夏普, ROUND(m.psr, 3) AS PSR,\n"
                "       ROUND(m.dsr, 3) AS DSR\n"
                "FROM backtest_runs r JOIN backtest_metrics m ON m.run_id = r.id\n"
                "WHERE m.psr > 0.95 AND m.dsr > 0.95\n"
                "ORDER BY m.dsr DESC"},
        {"名称": "入选实盘的模型",
         "sql": "SELECT strategy AS 模型, symbol AS 标的, sharpe AS 夏普,\n"
                "       psr, dsr, note AS 备注\n"
                "FROM model_registry WHERE selected = 1 ORDER BY sharpe DESC"},
        {"名称": "最近 20 笔委托记录",
         "sql": "SELECT ts AS 时间, account AS 账户, symbol AS 标的, action AS 动作,\n"
                "       qty AS 股数, order_type AS 类型, limit_price AS 限价,\n"
                "       status AS 状态, broker_order_id AS 订单号\n"
                "FROM orders ORDER BY id DESC LIMIT 20"},
        {"名称": "各标的盈亏笔数统计",
         "sql": "SELECT symbol AS 标的, COUNT(*) AS 交易笔数,\n"
                "       SUM(CASE WHEN ret > 0 THEN 1 ELSE 0 END) AS 盈利笔数,\n"
                "       ROUND(AVG(ret), 5) AS 平均每笔收益\n"
                "FROM trades GROUP BY symbol ORDER BY 平均每笔收益 DESC"},
        {"名称": "训练集清单",
         "sql": "SELECT name AS 训练集, n_rows AS 样本数, n_features AS 特征数,\n"
                "       label AS 标签, start AS 起始, end AS 结束\n"
                "FROM feature_sets ORDER BY id DESC"},
        {"名称": "API 抓取日志（最近 20 次）",
         "sql": "SELECT source AS 数据源, symbol AS 标的, status AS 状态,\n"
                "       rows AS 行数, duration_ms AS 耗时毫秒, finished_at AS 完成时间\n"
                "FROM ingest_log ORDER BY id DESC LIMIT 20"},
        {"名称": "账户净值曲线",
         "sql": "SELECT ts AS 时间, account AS 账户, net_liquidation AS 净值,\n"
                "       total_cash AS 现金, unrealized_pnl AS 浮动盈亏\n"
                "FROM account_snapshots ORDER BY ts DESC LIMIT 50"},
    ]
