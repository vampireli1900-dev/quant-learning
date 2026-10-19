# -*- coding: utf-8 -*-
"""
web/server.py —— 量化实验平台 Web 终端后端
==========================================
提供一套只读 REST API + 静态资源，前端是 TradingView 风格的暗色交易终端。

设计原则：
    1. **只读优先**：除 /api/paper/run 需要显式参数外，所有接口只读。
    2. **容错**：IB 连接失败、结果文件缺失都返回空数组而不是 500。
    3. **缓存**：K 线与信号计算较慢，进程内按 key 缓存（带 TTL）。

启动：
    python -m quant_platform.web.server --port 8770
    # 或
    python -m quant_platform.cli web --port 8770
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
PROJECT_ROOT = WEB_DIR.parent.parent
RESULTS_DIR = PROJECT_ROOT / "results"

# 简单进程内缓存：key -> (expire_ts, value)
_CACHE: Dict[str, tuple] = {}
TTL = {
    "ohlc": 300,
    "signals": 300,
    "ranking": 600,
    "watchlist": 120,
    "account": 30,
}


def _cached(key: str, ttl: float, fn):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = fn()
    _CACHE[key] = (now + ttl, val)
    return val


def _read_csv(p: Path) -> pd.DataFrame:
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p, encoding="utf-8-sig")
    except Exception:                                          # noqa: BLE001
        return pd.DataFrame()


def _run_in_loop(fn, timeout: float = 120):
    """
    ib_insync 内部依赖 asyncio 事件循环，而 FastAPI 的同步端点跑在线程池里，
    那个线程没有 current event loop（会报 "There is no current event loop"）。
    这里单独开一个线程，给它造一个专用事件循环再执行同步的 IB 代码。
    """
    import asyncio
    import threading

    box: Dict[str, object] = {}

    def _target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            box["val"] = fn()
        except Exception as e:                                # noqa: BLE001
            box["err"] = e
        finally:
            try:
                loop.close()
            except Exception:                                 # noqa: BLE001
                pass

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if "err" in box:
        raise box["err"]                                      # type: ignore[misc]
    if "val" not in box:
        raise TimeoutError(f"调用超时（>{timeout:.0f}s）")
    return box["val"]


def _json_safe(df: pd.DataFrame) -> pd.DataFrame:
    """
    把 NaN / ±Inf 换成 None。
    否则 json.dumps 会抛 "Out of range float values are not JSON compliant"
    （回测里盈亏比、最小样本量这些指标经常算出 inf）。
    """
    d = df.copy()
    num = d.select_dtypes(include=[np.number])
    if len(num.columns):
        num = num.replace([np.inf, -np.inf], np.nan)
        d[num.columns] = num.astype(object).where(num.notna(), None)
    d = d.astype(object).where(pd.notnull(d), None)
    return d


app = FastAPI(title="QuantLab Web Terminal", version="0.2.0")


# ---------------------------------------------------------------------------
# 静态
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# 元信息
# ---------------------------------------------------------------------------
@app.get("/api/bootstrap")
def bootstrap():
    from ..strategies import list_strategies, strategy_table

    symbols = _discover_symbols()
    return {
        "strategies": strategy_table().to_dict(orient="records"),
        "strategy_names": list_strategies(),
        "symbols": symbols,
        "experiments": _discover_experiments(),
        "build": "0.2.0",
    }


def _discover_symbols() -> List[str]:
    """从缓存目录推断可用标的。"""
    d = PROJECT_ROOT / "data_cache" / "ibkr"
    if not d.exists():
        return ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META"]
    syms = set()
    for f in d.glob("*_5Y_*.csv"):
        syms.add(f.name.split("_")[0])
    for f in d.glob("*_2Y_*.csv"):
        syms.add(f.name.split("_")[0])
    out = sorted(syms)
    # 港股缓存命名为 700_HK，还原展示名
    return out or ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META"]


def _discover_experiments() -> List[str]:
    if not RESULTS_DIR.exists():
        return []
    return sorted([p.name for p in RESULTS_DIR.iterdir()
                   if p.is_dir() and (p / "report.html").exists()])


# ---------------------------------------------------------------------------
# K 线 + 成交量 + 指标
# ---------------------------------------------------------------------------
@app.get("/api/ohlc")
def ohlc(symbol: str = "SPY", duration: str = "5 Y"):
    key = f"ohlc|{symbol}|{duration}"
    return _cached(key, TTL["ohlc"], lambda: _compute_ohlc(symbol, duration))


def _compute_ohlc(symbol: str, duration: str) -> dict:
    from ..data import get_data
    from ..indicators import ema, sma, bollinger

    try:
        df = _run_in_loop(lambda: get_data(symbol, duration=duration))
    except Exception as e:                                     # noqa: BLE001
        raise HTTPException(400, f"取数失败 {symbol}: {str(e)[:120]}")
    if df is None or not len(df):
        raise HTTPException(404, f"无数据 {symbol}")

    d = df.reset_index()
    tcol = d.columns[0]
    times = pd.to_datetime(d[tcol]).dt.strftime("%Y-%m-%d").tolist()

    bars = [{
        "time": t,
        "open": round(float(o), 4), "high": round(float(h), 4),
        "low": round(float(l), 4), "close": round(float(c), 4),
    } for t, o, h, l, c in zip(times, d["open"], d["high"], d["low"], d["close"])]

    close = df["close"]
    def s(series):
        return [None if pd.isna(v) else round(float(v), 4) for v in series]

    vol = [{
        "time": t,
        "value": float(v),
        "color": "rgba(242,54,69,0.45)" if c >= o else "rgba(8,153,129,0.45)",
    } for t, v, c, o in zip(times, df["volume"], d["close"], d["open"])]

    return {
        "symbol": symbol,
        "bars": bars,
        "volume": vol,
        "ma20": s(sma(close, 20)),
        "ma60": s(sma(close, 60)),
        "ema12": s(ema(close, 12)),
        "bb_upper": s(bollinger(close, 20, 2.0)["upper"]),
        "bb_lower": s(bollinger(close, 20, 2.0)["lower"]),
        "times": times,
        "last": round(float(close.iloc[-1]), 4),
        "prev_close": round(float(close.iloc[-2]), 4) if len(close) > 1 else None,
        "start": times[0], "end": times[-1], "count": len(bars),
    }


# ---------------------------------------------------------------------------
# 策略信号（买卖标记）
# ---------------------------------------------------------------------------
@app.get("/api/signals")
def signals(symbol: str = "SPY", strategy: str = "ADX趋势过滤",
            duration: str = "5 Y"):
    key = f"sig|{symbol}|{strategy}|{duration}"
    return _cached(key, TTL["signals"],
                   lambda: _compute_signals(symbol, strategy, duration))


def _compute_signals(symbol: str, strategy: str, duration: str) -> dict:
    from ..data import get_data
    from ..strategies import create
    from ..backtest import run_backtest, backtest_buy_hold
    from ..stats import significance_report

    df = _run_in_loop(lambda: get_data(symbol, duration=duration))
    strat = create(strategy)
    sig = strat.signals(df)
    res = run_backtest(df, sig, name=strategy, symbol=symbol)
    bh = backtest_buy_hold(df)

    times = pd.to_datetime(df.index).strftime("%Y-%m-%d").tolist()
    pos = sig.fillna(0).values
    markers = []
    for i in range(1, len(pos)):
        if pos[i] > 0 and pos[i - 1] <= 0:
            markers.append({"time": times[i], "position": "belowBar",
                            "color": "#f23645", "shape": "arrowUp",
                            "text": "B"})
        elif pos[i] <= 0 and pos[i - 1] > 0:
            markers.append({"time": times[i], "position": "aboveBar",
                            "color": "#089981", "shape": "arrowDown",
                            "text": "S"})
    markers = markers[-300:]          # 太多标记会拖慢前端

    eq = res.equity
    equity = [{"time": t, "value": round(float(v), 2)}
              for t, v in zip(times, eq.values / eq.iloc[0] * 100)]
    bench = bh.equity
    bench_eq = [{"time": t, "value": round(float(v), 2)}
                for t, v in zip(times, bench.values / bench.iloc[0] * 100)]

    sigrep = significance_report(res.returns)

    return {
        "symbol": symbol, "strategy": strategy,
        "markers": markers,
        "equity": equity,
        "benchmark": bench_eq,
        "metrics": {k: (None if (isinstance(v, float) and np.isnan(v)) else v)
                    for k, v in res.metrics.items()},
        "benchmark_metrics": {k: (None if (isinstance(v, float) and np.isnan(v)) else v)
                              for k, v in bh.metrics.items()},
        "significance": {k: (None if (isinstance(v, float) and np.isnan(v)) else v)
                         for k, v in sigrep.items()},
        "params": strat.params,
    }


# ---------------------------------------------------------------------------
# 实验结果
# ---------------------------------------------------------------------------
@app.get("/api/ranking")
def ranking(experiment: str = "main_compare"):
    key = f"rank|{experiment}"
    def _load():
        df = _read_csv(RESULTS_DIR / experiment / "by_strategy.csv")
        if not len(df):
            return {"rows": [], "columns": []}
        d = _json_safe(df)
        return {"rows": d.to_dict(orient="records"), "columns": list(d.columns)}
    return _cached(key, TTL["ranking"], _load)


@app.get("/api/detail")
def detail(experiment: str = "main_compare", symbol: Optional[str] = None):
    df = _read_csv(RESULTS_DIR / experiment / "detail.csv")
    if not len(df):
        return {"rows": [], "columns": []}
    if symbol and "标的" in df.columns:
        df = df[df["标的"] == symbol]
    d = _json_safe(df)
    return {"rows": d.to_dict(orient="records"), "columns": list(d.columns)}


@app.get("/api/equity_curves")
def equity_curves(experiment: str = "main_compare", symbol: str = "SPY",
                  top_n: int = 8):
    p = RESULTS_DIR / experiment / "equity_curves.csv"
    if not p.exists():
        return {"times": [], "series": []}
    df = _read_csv(p)
    if not len(df):
        return {"times": [], "series": []}
    c0 = df.columns[0]
    times = pd.to_datetime(df[c0]).dt.strftime("%Y-%m-%d").tolist()
    cols = [c for c in df.columns if c.endswith(f"|{symbol}")]
    # 用 by_strategy 的夏普挑前 top_n
    rank = _read_csv(RESULTS_DIR / experiment / "by_strategy.csv")
    order = []
    if len(rank) and "策略" in rank.columns and "夏普比率" in rank.columns:
        order = rank.sort_values("夏普比率", ascending=False)["策略"].tolist()
    cols.sort(key=lambda c: order.index(c.split("|")[0])
              if c.split("|")[0] in order else 999)
    cols = cols[:top_n]
    series = []
    for c in cols:
        v = df[c].astype(float)
        base = v.dropna()
        if not len(base):
            continue
        series.append({
            "name": c.split("|")[0],
            "data": [{"time": t, "value": None if pd.isna(x) else round(float(x), 2)}
                     for t, x in zip(times, v)],
        })
    return {"times": times, "series": series, "symbol": symbol}


@app.get("/api/significance")
def significance():
    df = _read_csv(RESULTS_DIR / "enhanced" / "significance.csv")
    if not len(df):
        return {"rows": [], "columns": []}
    d = _json_safe(df)
    return {"rows": d.to_dict(orient="records"), "columns": list(d.columns)}


@app.get("/api/riskab")
def riskab():
    df = _read_csv(RESULTS_DIR / "enhanced" / "risk_ab_summary.csv")
    if not len(df):
        return {"rows": [], "columns": []}
    d = _json_safe(df)
    return {"rows": d.to_dict(orient="records"), "columns": list(d.columns)}


@app.get("/api/portfolio_schemes")
def portfolio_schemes():
    df = _read_csv(RESULTS_DIR / "enhanced" / "portfolio_schemes.csv")
    if not len(df):
        return {"rows": [], "columns": []}
    d = _json_safe(df)
    return {"rows": d.to_dict(orient="records"), "columns": list(d.columns)}


@app.get("/api/walkforward")
def walkforward():
    df = _read_csv(RESULTS_DIR / "walkforward_validation" / "walkforward.csv")
    if not len(df):
        return {"rows": [], "columns": []}
    d = _json_safe(df)
    return {"rows": d.to_dict(orient="records"), "columns": list(d.columns)}


# ---------------------------------------------------------------------------
# 实时：自选股扫描 + 账户
# ---------------------------------------------------------------------------
@app.get("/api/watchlist")
def watchlist(symbols: str = "SPY,QQQ,AAPL,MSFT,NVDA,AMD,TSLA,META"):
    key = f"wl|{symbols}"

    def _load():
        from ..live import SignalMonitor
        syms = [s.strip() for s in symbols.split(",") if s.strip()]
        mon = SignalMonitor(strategies=["ADX趋势过滤", "ZScore回归",
                                        "时间序列动量", "双均线交叉"],
                            symbols=syms, duration="2 Y")
        try:
            df = _run_in_loop(mon.scan, timeout=180)
        except Exception as e:                                 # noqa: BLE001
            return {"rows": [], "columns": [], "error": str(e)[:120]}
        d = _json_safe(df)
        return {"rows": d.to_dict(orient="records"), "columns": list(d.columns)}
    return _cached(key, TTL["watchlist"], _load)


@app.get("/api/account")
def account():
    def _load():
        from ..live import IBBroker

        def _job():
            bk = IBBroker(dry_run=True)
            bk.connect()
            a = bk.account()
            p = bk.positions()
            bk.disconnect()
            return a, p

        try:
            acc, pos = _run_in_loop(_job, timeout=40)
        except Exception as e:                                 # noqa: BLE001
            return {"ok": False, "error": str(e)[:120]}
        return {"ok": True, "account": acc,
                "positions": [] if not len(pos)
                else pos.to_dict(orient="records")}
    return _cached("account", TTL["account"], _load)


@app.get("/api/paper/state")
def paper_state():
    p = RESULTS_DIR / "paper"
    if not p.exists():
        return {"ok": False, "error": "尚无模拟盘记录"}
    out = []
    for d in p.iterdir():
        f = d / "state.json"
        if f.exists():
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:                                  # noqa: BLE001
                continue
    return {"ok": True, "states": out}


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
def run_server(host: str = "127.0.0.1", port: int = 8770, reload: bool = False):
    import uvicorn
    print(f"QuantLab Web Terminal: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    a = ap.parse_args()
    run_server(a.host, a.port)
