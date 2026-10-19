# -*- coding: utf-8 -*-
"""
db/migrate.py —— 数据库迁移与维护工具
======================================
三个能力：
    1. import_cache()  把已有的 data_cache/*.csv（API 历史抓取的落盘缓存）
                       全量导入 bars 表——把"文件缓存"升级为"可查询的数据库"
    2. export_csv()    把库里的行情导出成 CSV（做报告/给别的工具用）
    3. vacuum()        回收空间（SQLite）

优先读取 CSV 同名的 .meta.json（里面记了完整的 DataRequest），
读不到才回退去解析文件名，保证 symbol/周期/数据源 不会猜错。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from ..config import CACHE_DIR, RESULTS_DIR
from .repository import upsert_bars
from .session import init_db, session_scope

META_SUFFIX = ".meta.json"

# 形如 "1day" / "15mins" / "1hour" / "5min" → "1 day" / "15 mins" …
_BS_RE = re.compile(r"^(\d+)\s*(sec|secs|min|mins|hour|hours|day|days|week|weeks)$", re.I)


def _unpack_bar_size(s: str) -> str:
    m = _BS_RE.match(str(s).strip())
    if not m:
        return str(s)
    return f"{m.group(1)} {m.group(2).lower()}"


def _parse_from_filename(stem: str) -> Dict[str, str]:
    """回退方案：从缓存文件名反推请求参数。"""
    parts = stem.split("_")
    # 末尾固定为: ..._barSize_duration_whatToShow_RTH[extra]
    wts, rth = "TRADES", "RTH"
    if len(parts) >= 2:
        rth = parts[-1]
        wts = parts[-2]
        rest = parts[:-2]
    else:
        rest = parts
    duration = rest[-1] if rest else "5Y"
    bar_size = _unpack_bar_size(rest[-2]) if len(rest) >= 2 else "1 day"
    symbol = "_".join(rest[:-2]) if len(rest) > 2 else (rest[0] if rest else stem)
    return {"symbol": symbol.replace("_", "."), "bar_size": bar_size,
            "duration": duration, "what_to_show": wts,
            "use_rth": str(rth).upper() == "RTH"}


def import_cache(cache_dir: Optional[Path] = None, verbose: bool = True,
                 refresh: bool = False) -> pd.DataFrame:
    """
    把 data_cache 下的 CSV 缓存导入数据库。

    refresh=False 时，若库里已有该序列数据则跳过（增量导入）。
    返回导入结果明细表。
    """
    root = Path(cache_dir or CACHE_DIR)
    init_db()
    if not root.exists():
        if verbose:
            print(f"缓存目录不存在：{root}")
        return pd.DataFrame()

    rows: List[Dict] = []
    files = sorted(root.rglob("*.csv"))
    for p in files:
        meta_p = Path(str(p) + META_SUFFIX)
        info: Dict[str, object] = {}
        if meta_p.exists():
            try:
                m = json.loads(meta_p.read_text(encoding="utf-8"))
                info = m.get("request") or {}
                source = str(m.get("source") or "ibkr")
            except Exception:                                    # noqa: BLE001
                info, source = {}, "ibkr"
        else:
            info = _parse_from_filename(p.stem)
            source = "ibkr"

        symbol = str(info.get("symbol") or p.stem).upper()
        bar_size = str(info.get("bar_size") or "1 day")
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

        try:
            n = upsert_bars(symbol, bar_size, df, source=source)
            rows.append({"文件": p.name, "标的": symbol, "周期": bar_size,
                         "状态": "已导入", "行数": int(n), "说明": source})
            if verbose:
                print(f"  ✓ {symbol:<8} {bar_size:<8} {n:>6} 行  ← {p.name}")
        except Exception as e:                                   # noqa: BLE001
            rows.append({"文件": p.name, "标的": symbol, "周期": bar_size,
                         "状态": "导入失败", "行数": 0, "说明": str(e)[:80]})
            if verbose:
                print(f"  ✗ {symbol:<8} {str(e)[:60]}")
    return pd.DataFrame(rows)


def export_csv(symbol: str, bar_size: str = "1 day",
               out: Optional[Path] = None) -> Optional[Path]:
    """把库里的行情导出成 CSV。"""
    from .repository import load_bars
    df = load_bars(symbol, bar_size=bar_size)
    if df is None or len(df) == 0:
        print(f"库里没有 {symbol} / {bar_size} 的数据")
        return None
    out = Path(out or (RESULTS_DIR / "exports" / f"{symbol.upper()}_{bar_size.replace(' ', '')}.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, encoding="utf-8-sig")
    print(f"已导出 {len(df)} 行 → {out}")
    return out


def vacuum() -> None:
    """SQLite 空间回收 + 统计信息更新。"""
    from sqlalchemy import text as _text
    with session_scope() as s:
        for stmt in ("VACUUM", "ANALYZE"):
            try:
                s.execute(_text(stmt))
            except Exception:                                    # noqa: BLE001
                pass
    print("已执行 VACUUM / ANALYZE")
