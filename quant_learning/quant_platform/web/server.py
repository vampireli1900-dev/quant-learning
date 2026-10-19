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
import threading
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
# 持久 IB 工作线程（见 _run_in_loop）
_IB_WORKER = None
_IB_WORKER_LOCK = threading.Lock()
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
    ib_insync 依赖"创建它的那个线程的事件循环"。之前每次请求都新开线程+新循环，
    导致单例 IB 连接绑定在已关闭的旧循环上，第二次请求报
    "'NoneType' object has no attribute 'send'"。
    改为：一个持久的 IB 工作线程（拥有自己的事件循环），所有 IB 调用都排队
    在这个线程上串行执行 —— 连接自然复用，永不跨循环。
    """
    import queue
    import threading

    global _IB_WORKER
    with _IB_WORKER_LOCK:
        if _IB_WORKER is None or not _IB_WORKER["thread"].is_alive():
            _IB_JOBS = queue.Queue()

            def _worker():
                # ib_insync 导入时就要求当前线程已有事件循环，
                # （"There is no current event loop in thread ..."）
                # 所以 worker 一启动就先把循环建好
                import asyncio
                asyncio.set_event_loop(asyncio.new_event_loop())
                while True:
                    fn_, box_, done_ = _IB_JOBS.get()
                    if fn_ is None:
                        return
                    try:
                        box_["val"] = fn_()
                    except Exception as e:                        # noqa: BLE001
                        box_["err"] = e
                    finally:
                        done_.set()

            t = threading.Thread(target=_worker, daemon=True,
                                 name="ib-worker")
            t.start()
            _IB_WORKER = {"thread": t, "jobs": _IB_JOBS}

    jobs = _IB_WORKER["jobs"]
    box: Dict[str, object] = {}
    done = threading.Event()
    jobs.put((fn, box, done))
    if not done.wait(timeout):
        raise TimeoutError(f"IB 调用超时（>{timeout:.0f}s）")
    if "err" in box:
        raise box["err"]                                      # type: ignore[misc]
    if "val" not in box:
        raise RuntimeError("IB 调用无返回")
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
# 周期 -> (bar_size, 默认 duration)
BARS = {
    "day":  ("1 day", "5 Y", "日线"),
    "week": ("1 week", "5 Y", "周线"),
    "hour": ("1 hour", "2 Y", "1小时"),
    "min15": ("15 mins", "1 M", "15分钟"),
}
INTRADAY = {"hour", "min15"}


@app.get("/api/ohlc")
def ohlc(symbol: str = "SPY", duration: str = "5 Y", bar: str = "day"):
    if bar not in BARS:
        raise HTTPException(400, f"未知周期 {bar}，可选 {list(BARS)}")
    key = f"ohlc|{symbol}|{duration}|{bar}"
    return _cached(key, TTL["ohlc"], lambda: _compute_ohlc(symbol, duration, bar))


def _compute_ohlc(symbol: str, duration: str, bar: str = "day") -> dict:
    from ..data import get_data
    from ..indicators import ema, sma, bollinger

    bar_size = BARS[bar][0]
    try:
        df = _run_in_loop(lambda: get_data(symbol, bar_size=bar_size,
                                           duration=duration))
    except Exception as e:                                     # noqa: BLE001
        raise HTTPException(400, f"取数失败 {symbol}: {str(e)[:120]}")
    if df is None or not len(df):
        raise HTTPException(404, f"无数据 {symbol}")

    d = df.reset_index()
    tcol = d.columns[0]
    tser = pd.to_datetime(d[tcol])
    if bar in INTRADAY:
        # lightweight-charts 的分钟级必须用 UNIX 秒；把盘中时间当 UTC 打包，
        # 前端显示的就是交易所本地原始时间
        times = [int(pd.Timestamp(t).tz_localize("UTC").timestamp())
                 for t in tser]
        tlabel = tser.dt.strftime("%m-%d %H:%M").tolist()
    else:
        times = tser.dt.strftime("%Y-%m-%d").tolist()
        tlabel = times

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
        "symbol": symbol, "bar": bar, "bar_label": BARS[bar][2],
        "bars": bars,
        "volume": vol,
        "ma20": s(sma(close, 20)),
        "ma60": s(sma(close, 60)),
        "ema12": s(ema(close, 12)),
        "bb_upper": s(bollinger(close, 20, 2.0)["upper"]),
        "bb_lower": s(bollinger(close, 20, 2.0)["lower"]),
        "times": times, "time_labels": tlabel,
        "last": round(float(close.iloc[-1]), 4),
        "prev_close": round(float(close.iloc[-2]), 4) if len(close) > 1 else None,
        "start": str(tlabel[0]), "end": str(tlabel[-1]), "count": len(bars),
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


@app.get("/api/portfolio_equity")
def portfolio_equity(strategy: str = "ADX趋势过滤", scheme: str = "equal"):
    """现算一个组合：strategy × 8 标的 × scheme，返回净值/权重/指标。"""
    key = f"pf|{strategy}|{scheme}"
    return _cached(key, 1800, lambda: _compute_portfolio(strategy, scheme))


def _compute_portfolio(strategy: str, scheme: str) -> dict:
    from ..data import get_data
    from ..strategies import create
    from ..portfolio import run_portfolio

    syms = _discover_symbols()
    if len(syms) < 2:
        raise HTTPException(404, "缓存标的不足，无法构建组合")

    def _job():
        data = {}
        for s in syms:
            try:
                data[s] = get_data(s, duration="5 Y")
            except Exception:                                  # noqa: BLE001
                continue
        sig = {s: create(strategy).signals(df) for s, df in data.items()}
        return run_portfolio(data, sig, name=strategy, scheme=scheme)

    try:
        res = _run_in_loop(_job, timeout=240)
    except Exception as e:                                     # noqa: BLE001
        raise HTTPException(400, f"组合计算失败: {str(e)[:160]}")

    eq = res.equity / res.equity.iloc[0] * 100
    times = pd.to_datetime(res.equity.index).strftime("%Y-%m-%d").tolist()
    equity = [{"time": t, "value": round(float(v), 2)} for t, v in zip(times, eq.values)]

    # 基准：等权买入持有（各自归一到 100 后取平均，近似每日再平衡）
    from ..data import get_data as _gd
    bench_df = pd.DataFrame({
        s: (lambda df: df["close"] / df["close"].iloc[0] * 100)(_gd(s, duration="5 Y"))
        for s in res.weights.columns})
    bench_avg = bench_df.mean(axis=1).reindex(res.equity.index).ffill()
    benchmark = [{"time": t, "value": round(float(v), 2)}
                 for t, v in zip(times, bench_avg.values)]

    # 权重：转成逐标的折线序列
    w = res.weights
    w_times = pd.to_datetime(w.index).strftime("%Y-%m-%d").tolist()
    weight_series = []
    palette = ['#2962ff', '#f23645', '#089981', '#e0b64a', '#7b61ff',
               '#00bcd4', '#ff7043', '#9ccc65']
    for i, col in enumerate(w.columns):
        weight_series.append({
            "name": col, "color": palette[i % len(palette)],
            "current": round(float(w[col].iloc[-1]), 4),
            "data": [{"time": t, "value": round(float(v), 4)}
                     for t, v in zip(w_times, w[col].values)],
        })

    metrics = {k: (None if (isinstance(v, float) and np.isnan(v)) else v)
               for k, v in res.metrics.items()}
    contrib = {k: round(float(v), 4) for k, v in res.contributions.items()}

    return {
        "strategy": strategy, "scheme": scheme,
        "times": times, "equity": equity, "benchmark": benchmark,
        "weights": weight_series, "metrics": metrics,
        "contributions": contrib, "symbols": list(res.weights.columns),
    }


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
