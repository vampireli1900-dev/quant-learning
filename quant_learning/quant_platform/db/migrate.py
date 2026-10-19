# -*- coding: utf-8 -*-
"""
db/migrate.py —— 数据库迁移与维护
==================================
三个能力：

    1. import_cache()
       把已有的 data_cache/*.csv（早期版本的磁盘缓存）全量搬到 bars 表。
       这样历史抓取的数据不会浪费，也完成了"文件缓存 → 可查询数据库"的升级。

    2. export_csv()
       把库里的行情导出成 CSV（写报告 / 交给别的工具）。

    3. vacuum()
       SQLite 空间回收 + 统计信息更新（Analyze 让查询规划器更聪明）。

导入时的参数来源优先顺序：
    CSV 同名的 .meta.json（里面存了完整的 DataRequest）> 文件名反推。
    这样 symbol / 周期 / 数据源 不会猜错。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from ..config import CACHE_DIR, RESULTS_DIR
from .repository import bars_exist, upsert_bars
from .session import init_db, session_scope

META_SUFFIX = ".meta.json"

# "1day"/"15mins"/"1hour"/"5min"… → "1 day"/"15 mins"…
_BAR_RE = re.compile(r"^(\d+)\s*(sec|secs|min|mins|hour|hours|day|days|week|weeks)$", re.I)


def unpack_bar_size(s: str) -> str:
    """把文件名里被压掉的空格还原回 IB 的 bar_size 写法。"""
    m = _BAR_RE.match(str(s).strip())
    if not m:
        return str(s)
    return f"{m.group(1)} {m.group(2).lower()}"


def parse_cache_name(stem: str) -> Dict[str, Any]:
    """
    从缓存文件名反推请求参数（meta 缺失时的兜底）。

    文件名格式：{symbol}_{barSize}_{duration}_{whatToShow}_{RTH}
        例：AAPL_1day_5Y_TRADES_RTH
            BRK_B_1day_2Y_TRADES_RTH   （原 symbol 为 BRK.B）
    """
    parts = stem.split("_")
    if len(parts) < 4:
        return {"symbol": stem, "bar_size": "1 day", "duration": "5Y",
                "what_to_show": "TRADES", "use_rth": True}
    rth = parts[-1]
    wts = parts[-2]
    duration = parts[-3]
    bar_size = unpack_bar_size(parts[-4]) if len(parts) >= 5 else "1 day"
    symbol = "_".join(parts[:-4]) if len(parts) > 4 else parts[0]
    return {
        "symbol": symbol.replace("_", "."),
        "bar_size": bar_size,
        "duration": duration,
        "what_to_show": wts,
        "use_rth": str(rth).upper() == "RTH",
    }


def import_cache(cache_dir: Optional[Path] = None, verbose: bool = True,
                 refresh: bool = False) -> pd.DataFrame:
    """
    把 data_cache 下的 CSV 缓存导入 bars 表（幂等，可重复执行）。

    refresh=False → 库里已有该 (标的,周期) 序列则跳过，做增量导入（快）
    refresh=True  → 忽略已有数据，全量重新 upsert

    返回导入明细 DataFrame：文件 / 标的 / 周期 / 状态 / 行数 / 说明
    """
    root = Path(cache_dir or CACHE_DIR)
    init_db()
    rows: List[Dict[str, Any]] = []
    if not root.exists():
        if verbose:
            print(f"缓存目录不存在：{root}")
        return pd.DataFrame()

    files = sorted(root.rglob("*.csv"))
    if verbose:
        print(f"扫描到 {len(files)} 个缓存文件 → 写入 bars 表")

    for p in files:
        meta_p = Path(str(p) + META_SUFFIX)
        source = "ibkr"
        info: Dict[str, Any] = {}
        if meta_p.exists():
            try:
                m = json.loads(meta_p.read_text(encoding="utf-8"))
                info = m.get("request") or {}
                source = str(m.get("source") or "ibkr")
            except Exception:                                    # noqa: BLE001
                info = {}
        if not info:
            info = parse_cache_name(p.stem)

        symbol = str(info.get("symbol") or p.stem).upper()
        bar_size = str(info.get("bar_size") or "1 day")

        if not refresh and bars_exist(symbol, bar_size):
            rows.append({"文件": p.name, "标的": symbol, "周期": bar_size,
                         "状态": "跳过(已存在)", "行数": 0, "说明": source})
            if verbose:
                print(f"  [--] {symbol:<8} {bar_size:<8} 已存在，跳过")
            continue

        try:
            df = pd.read_csv(p, index_col=0, parse_dates=True)
        except Exception as e:                                   # noqa: BLE001
            rows.append({"文件": p.name, "标的": symbol, "周期": bar_size,
                         "状态": "读取失败", "行数": 0, "说明": str(e)[:80]})
            continue
        if df is None or len(df) == 0:
            rows.append({"文件": p.name, "标的": symbol, "周期": bar_size,
                         "状态": "空文件", "行数": 0, "说明": ""})
            continue
        # 目录名就是数据源（data_cache/ibkr/...）
        if not source and p.parent.name:
            source = p.parent.name
        try:
            n = upsert_bars(symbol, bar_size, df, source=source)
            rows.append({"文件": p.name, "标的": symbol, "周期": bar_size,
                         "状态": "已导入", "行数": int(n), "说明": source})
            if verbose:
                print(f"  [OK] {symbol:<8} {bar_size:<8} {n:>6} rows  <- {p.name}")
        except Exception as e:                                   # noqa: BLE001
            rows.append({"文件": p.name, "标的": symbol, "周期": bar_size,
                         "状态": "导入失败", "行数": 0, "说明": str(e)[:80]})
    return pd.DataFrame(rows)


def export_csv(symbol: str, bar_size: str = "1 day",
               out: Optional[Path] = None) -> Optional[Path]:
    """把库里的行情导出成 CSV。"""
    from .repository import load_bars
    df = load_bars(symbol, bar_size=bar_size)
    if df is None or len(df) == 0:
        print(f"库里没有 {symbol} / {bar_size} 的数据")
        return None
    out = Path(out or (RESULTS_DIR / "exports" /
                       f"{symbol.upper()}_{bar_size.replace(' ', '')}.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, encoding="utf-8-sig")
    print(f"导出 {len(df)} 行 → {out}")
    return out


def vacuum() -> None:
    """SQLite 空间回收 + 统计信息更新。"""
    from sqlalchemy import text
    with session_scope() as s:
        for stmt in ("VACUUM", "ANALYZE"):
            try:
                s.execute(text(stmt))
            except Exception:                                     # noqa: BLE001
                pass
    print("已执行 VACUUM / ANALYZE")
