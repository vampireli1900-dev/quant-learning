# -*- coding: utf-8 -*-
"""
risk/compare.py —— 风控 A/B 对比
=================================
回答实战中最关键的问题：策略不行，是**逻辑**不行还是**风控**没做好？

做法：同一个信号，套上不同的风控方案分别回测，横向对比。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd

from ..backtest import run_backtest
from ..strategies import create
from .overlay import RiskOverlay, PRESETS, apply_overlay


def run_with_overlay(df: pd.DataFrame, signal: pd.Series,
                     ov: RiskOverlay, name: str = "strategy",
                     symbol: str = "", **bt_kwargs) -> dict:
    """套上风控后回测，返回 metrics + 风控描述。"""
    adj = apply_overlay(df, signal, ov)
    res = run_backtest(df, adj, name=name, symbol=symbol, **bt_kwargs)
    out = dict(res.metrics)
    out["风控方案"] = ov.name
    out["风控描述"] = ov.describe()
    return out


def overlay_ab_test(df: pd.DataFrame,
                    strategy: Union[str, pd.Series],
                    overlays: Optional[Sequence[Union[str, RiskOverlay]]] = None,
                    symbol: str = "",
                    strategy_params: Optional[dict] = None,
                    **bt_kwargs) -> pd.DataFrame:
    """
    对同一策略信号跑多套风控方案。

    strategy: 策略名（str）或直接给信号 Series
    overlays: 风控方案名列表 / RiskOverlay 列表；默认跑全部预置方案
    """
    if isinstance(strategy, str):
        strat = create(strategy, **(strategy_params or {}))
        signal = strat.signals(df)
        sname = strategy
    else:
        signal = strategy
        sname = "自定义信号"

    if overlays is None:
        overlays = list(PRESETS.values())
    ovs = [PRESETS[o] if isinstance(o, str) else o for o in overlays]

    rows = []
    for ov in ovs:
        try:
            r = run_with_overlay(df, signal, ov, name=sname, symbol=symbol, **bt_kwargs)
            r["策略"] = sname
            rows.append(r)
        except Exception as e:                       # 单个方案失败不拖垮整体
            rows.append({"策略": sname, "风控方案": ov.name,
                         "风控描述": ov.describe(), "错误": str(e)})
    out = pd.DataFrame(rows)
    front = ["策略", "风控方案", "风控描述"]
    cols = front + [c for c in out.columns if c not in front]
    return out[cols]


def summarize_overlay_effect(ab: pd.DataFrame,
                             metric: str = "夏普比率") -> pd.DataFrame:
    """把 A/B 结果压成"相对无风控的改善"。"""
    if "风控方案" not in ab.columns or metric not in ab.columns:
        return pd.DataFrame()
    base = ab[ab["风控方案"] == "无风控"]
    if not len(base):
        return pd.DataFrame()
    b = base[metric].iloc[0]
    out = ab[["风控方案", "风控描述", metric, "年化收益率", "最大回撤"]].copy()
    out[f"{metric}变化"] = out[metric] - b
    return out
