# -*- coding: utf-8 -*-
"""
live/dashboard.py —— 账户实时看板聚合层
========================================
把「账户摘要 + 持仓 + 最新价 + 估值 + 盈亏 + 数据库历史」拼成一份
前端可以直接渲染的 JSON。

为什么不直接写在 web/server.py：
    1. 看板要给 Web、CLI、定时任务三处复用，不能绑死在 FastAPI 上。
    2. 行情/账户取数慢且有副作用（连 IB），集中一处才能统一做缓存与降级。

**币种口径（这里最容易出错，单独说明）**
    账户基准币种是 HKD，而持仓标的是美股（USD）。两边直接相加会串币种，
    所以统一约定：
        · 逐行（标的维度）：成本价/现价/市值/浮动盈亏 一律用**标的自身币种**，
          这是看盘的习惯，也和 TWS 一致。
        · 汇总（账户维度）：用 IB 给的账户币种数值；需要把标的折算过来时，
          乘汇率换算到账户基准币种。
        · 权重 = |市值| / Σ|市值|，是比值，与币种无关。

设计：
    * 全程**旁路**：IB 连不上 / DB 写不进，只降级不抛错——看板是给人看的，
      不能因为看板挂了影响下单主流程。
    * 快照节流写入（默认 60s 一条），避免前端 10s 轮询把 account_snapshots
      撑爆，同时又能自然长出净值曲线。
    * 进程内缓存（默认 5s），多个面板同时请求不会重复打 IB。

用法：
    from quant_platform.live import account_board
    board = account_board()          # 返回 dict
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import CONFIG

# ---------------------------------------------------------------------------
# 缓存 / 节流
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_BOARD_CACHE: Optional[tuple] = None          # (expire_ts, board)
_LAST_SNAPSHOT_TS = 0.0

BOARD_TTL = 5.0            # 看板缓存秒数
SNAPSHOT_INTERVAL = 60.0   # 账户快照最小写入间隔（秒）
FALLBACK_FX = 7.8          # USD.HKD 兜底汇率

# 持仓表展示列
POS_COLUMNS = ["标的", "币种", "股数", "成本价", "现价", "市值",
               "成本", "浮动盈亏", "盈亏%", "权重%"]


def _f(v, default: float = 0.0) -> float:
    """安全转 float：IB 返回的都是字符串，且有可能是空串 / 'N/A'。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if np.isnan(f) or np.isinf(f) else f


def _nan_to_none(df: pd.DataFrame) -> pd.DataFrame:
    """NaN / ±Inf → None，否则 JSON 序列化会直接报错。"""
    d = df.copy()
    num = d.select_dtypes(include=[np.number])
    if len(num.columns):
        num = num.replace([np.inf, -np.inf], np.nan)
        d[num.columns] = num.astype(object).where(num.notna(), None)
    return d.astype(object).where(pd.notnull(d), None)


def _json_safe(obj):
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if (np.isnan(f) or np.isinf(f)) else f
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return obj


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------
def _fetch_from_ib() -> dict:
    """
    连 IB 取账户与持仓，并补最新价算估值。

    返回统一结构 {ok, error, ts, account, rows, columns, totals, market, fx}。
    连不上时返回 ok=False + error，不抛异常。
    """
    import random
    from .broker import IBBroker, RiskLimits, fx_rate
    from ..config import IBConfig

    # 数据层也会建 IB 连接，这里用随机高位 clientId 避免抢号（IB 错误 326）
    bcfg = IBConfig(host=CONFIG.ib.host, port=CONFIG.ib.port,
                    client_id=random.randint(400, 900),
                    timeout=CONFIG.ib.timeout)
    broker = IBBroker(cfg=bcfg, dry_run=True, limits=RiskLimits())
    try:
        if not broker.connect():
            return {"ok": False, "error": "无法连接 TWS / Gateway"}
        acc = broker.account()
        if acc.get("error") and not acc.get("NetLiquidation"):
            return {"ok": False, "error": acc["error"]}

        try:
            pos = broker.positions()
        except Exception as e:                                 # noqa: BLE001
            pos = pd.DataFrame()
            acc["positions_error"] = str(e)[:80]

        raw = [] if not len(pos) else pos.to_dict(orient="records")
        syms = [str(r.get("symbol", "")).upper() for r in raw]
        px = broker.mark_prices(syms) if syms else {}

        # 账户基准币种：港股账号是 HKD。换算只在这一处做，别的地方都用比值。
        base = acc.get("base_currency") or acc.get("currency") or "HKD"
        fx = 1.0 if base == "USD" else fx_rate(broker, "USD", base)

        return _assemble(acc, raw, px, fx=fx, base=base)
    except Exception as e:                                     # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    finally:
        try:
            broker.disconnect()
        except Exception:                                      # noqa: BLE001
            pass


def _assemble(acc: dict, raw: List[dict], px: Dict[str, float],
              fx: float = 1.0, base: str = "HKD") -> dict:
    """
    把账户摘要与持仓行拼成看板结构。

    逐行口径（标的自身币种，通常 USD）：
        市值 = 股数 × 现价
        成本 = 股数 × 成本均价        （IB 的 avgCost 是**每股**成本）
        浮动盈亏 = 市值 − 成本
    权重 = |市值| / Σ|市值|（比值，不受币种影响）
    """
    ts = datetime.now()
    rows = []
    for r in raw:
        sym = str(r.get("symbol", "")).upper()
        qty = _f(r.get("position"))
        avg = _f(r.get("avg_cost"))
        cur = str(r.get("currency", "USD")).upper()
        last = px.get(sym)
        last = _f(last) if last is not None else np.nan

        mv = qty * last if not np.isnan(last) else np.nan
        cost = qty * avg
        pnl = (mv - cost) if not np.isnan(mv) else np.nan
        pnl_pct = (pnl / cost) if (cost and not np.isnan(pnl)) else np.nan

        rows.append({
            "标的": sym, "币种": cur, "股数": qty,
            "成本价": avg, "现价": last,
            "市值": mv, "成本": cost,
            "浮动盈亏": pnl, "盈亏%": pnl_pct,
            "权重%": np.nan,              # 下面按总市值统一回填
            "方向": "多" if qty > 0 else ("空" if qty < 0 else "平"),
        })

    abs_mv = [abs(r["市值"]) for r in rows if not np.isnan(r["市值"])]
    denom = float(np.nansum(abs_mv)) if abs_mv else 0.0
    for r in rows:
        v = abs(r["市值"]) if not np.isnan(r["市值"]) else np.nan
        r["权重%"] = (v / denom) if (denom and not np.isnan(v)) else np.nan

    # ---- 账户维度（基准币种） ----
    netliq = _f(acc.get("NetLiquidation"), np.nan)
    cash = _f(acc.get("TotalCashValue"), np.nan)
    gross_ib = _f(acc.get("GrossPositionValue"), np.nan)
    gross_usd = float(np.nansum([r["市值"] for r in rows])) if rows else 0.0
    gross_hkd = gross_usd * fx

    if np.isnan(netliq):
        netliq = (cash if not np.isnan(cash) else 0.0) + gross_hkd
    if np.isnan(gross_ib):
        gross_ib = gross_hkd

    # 浮动盈亏：以**成本价**为准（表格口径），换算到账户币种
    pnl_hkd = float(np.nansum([r["浮动盈亏"] for r in rows])) * fx if rows else np.nan
    ib_unreal = _f(acc.get("UnrealizedPnL"), np.nan)

    cash_pct = (cash / netliq) if (netliq and not np.isnan(cash)) else np.nan

    totals = {
        "净值": netliq,
        "现金": cash,
        "现金占比": cash_pct,
        "可用资金": _f(acc.get("AvailableFunds"), np.nan),
        "购买力": _f(acc.get("BuyingPower"), np.nan),
        "持仓市值": gross_ib,
        "持仓盈亏": pnl_hkd,
        "IB浮动盈亏": ib_unreal,
        "已实现盈亏": _f(acc.get("RealizedPnL"), np.nan),
        "当日盈亏": _f(acc.get("DailyPnL"), np.nan),
        "维持保证金": _f(acc.get("MaintMarginReq"), np.nan),
        "剩余流动性": _f(acc.get("ExcessLiquidity"), np.nan),
        "持仓数": len(rows),
        "杠杆": (gross_ib / netliq) if netliq else np.nan,
        "基准币种": base,
        "汇率": fx,
    }

    return {
        "ok": True,
        "ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "account": acc,
        "raw_positions": raw,
        "market": px,
        "columns": POS_COLUMNS,
        "rows": rows,
        "totals": totals,
        "fx": fx,
        "base": base,
    }


# ---------------------------------------------------------------------------
# 落库（旁路 + 节流）
# ---------------------------------------------------------------------------
def _record_snapshot(board: dict) -> bool:
    """
    把账户与持仓快照写进数据库。

    节流：距上次写入不足 SNAPSHOT_INTERVAL 就跳过，这样前端可以放心地
    10s 轮询，而 account_snapshots 只会按分钟级增长。
    """
    global _LAST_SNAPSHOT_TS

    if not board or not board.get("ok"):
        return False
    try:
        from ..db.repository import DB_ENABLED
        if not DB_ENABLED:
            return False
    except Exception:                                          # noqa: BLE001
        return False

    if time.time() - _LAST_SNAPSHOT_TS < SNAPSHOT_INTERVAL:
        return False

    with _LOCK:
        if time.time() - _LAST_SNAPSHOT_TS < SNAPSHOT_INTERVAL:   # 并发下再查一次
            return False
        _LAST_SNAPSHOT_TS = time.time()
        try:
            from ..db import save_account_snapshot, save_positions
            ts = datetime.now()
            acc = board.get("account") or {}
            acct = acc.get("accounts") or None
            save_account_snapshot(ts, acct, acc)
            rows = board.get("rows") or []
            if rows:
                df = pd.DataFrame([{
                    "symbol": r["标的"], "position": r["股数"],
                    "avgCost": r["成本价"], "marketPrice": r["现价"],
                    "marketValue": r["市值"],
                } for r in rows])
                save_positions(ts, acct, df)
            return True
        except Exception:                                      # noqa: BLE001
            return False


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def account_board(record: bool = True, use_cache: bool = True,
                  history_limit: int = 500) -> dict:
    """
    生成账户实时看板数据。

    record=True 时把本次快照节流写入 DB（用于净值曲线）。
    返回 dict：{ok, error, ts, totals, rows, columns, market, history}
    """
    global _BOARD_CACHE

    board: Optional[dict] = None
    if use_cache:
        with _LOCK:
            hit = _BOARD_CACHE
        if hit and hit[0] > time.time():
            board = hit[1]

    if board is None:
        board = _fetch_from_ib()
        with _LOCK:
            _BOARD_CACHE = (time.time() + BOARD_TTL, board)

    out = dict(board or {})
    if record:
        out["_recorded"] = _record_snapshot(out)

    hist = _load_history(limit=history_limit)
    out["history"] = hist
    _fill_daily_pnl(out, hist)
    return _json_safe(out)


def _fill_daily_pnl(board: dict, hist: dict):
    """
    补算当日盈亏。

    模拟账户的 accountValues 常常不返回 DailyPnL，与其显示"—"，
    不如用**当天第一条快照**当基线（等价于「今日开盘净值」）自己算：
        当日盈亏 = 当前净值 − 当日首条快照净值
    只有一条快照时无从比较，保持 None，前端显示"—"。
    """
    totals = board.get("totals") or {}
    # 注意：这里还是原始值，缺失可能是 nan 而不一定是 None
    # （_json_safe 把 nan 换成 None 是在返回前才做的），两种都要判掉。
    cur = totals.get("当日盈亏")
    if cur is not None and not (isinstance(cur, float) and np.isnan(cur)):
        return
    times = (hist or {}).get("times") or []
    vals = (hist or {}).get("净值") or []
    if len(times) < 2:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    base = None
    for t, v in zip(times, vals):
        if str(t).startswith(today) and v:
            base = float(v)
            break
    if base is None:
        return
    netliq = totals.get("净值")
    if netliq is None or (isinstance(netliq, float) and np.isnan(netliq)):
        return
    totals["当日盈亏"] = float(netliq) - base
    totals["当日盈亏基线"] = base


def _load_history(limit: int = 500) -> dict:
    """读取账户快照历史（DB 不可用则返回空结构）。"""
    empty = {"times": [], "净值": [], "现金": [], "可用资金": [], "points": 0}
    try:
        from ..db import account_curve
        df = account_curve(limit=limit)
    except Exception:                                          # noqa: BLE001
        return empty
    if df is None or not len(df):
        return empty
    if "净值" in df.columns:
        df = df.dropna(subset=["净值"])
    if not len(df):
        return empty

    times = pd.to_datetime(df["时间"]).dt.strftime("%Y-%m-%d %H:%M").tolist()

    def col(name):
        if name not in df.columns:
            return [0.0] * len(times)
        return [round(float(v), 2) for v in pd.to_numeric(
            df[name], errors="coerce").fillna(0.0)]

    return {
        "times": times,
        "净值": col("净值"),
        "现金": col("现金"),
        "可用资金": col("可用资金"),
        "points": len(times),
    }


def board_frame(max_rows: int = 50) -> pd.DataFrame:
    """
    返回看板的持仓 DataFrame（CLI / 测试用）。
    与 account_board 同源，避免两套口径。
    """
    b = _fetch_from_ib()
    if not b.get("ok"):
        return pd.DataFrame()
    df = pd.DataFrame(b.get("rows") or [])
    if not len(df):
        return df
    return _nan_to_none(df).head(max_rows)
