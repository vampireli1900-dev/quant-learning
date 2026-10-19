# -*- coding: utf-8 -*-
"""
backtest/optimizer.py —— 参数寻优与过拟合诊断
============================================
参数寻优是把双刃剑：找到"最优参数"很容易，难的是判断它是不是噪声。

本模块提供：
    1. grid_search   网格搜索（全组合）
    2. random_search 随机搜索（组合爆炸时用）
    3. param_sensitivity 参数敏感性分析：
       看最优参数周边的表现是否平滑——如果"改一点点参数结果就崩"，
       那这个最优参数几乎肯定是过拟合（这叫参数高原 vs 参数尖峰）。

判据（经验法则）：
    邻域平均表现 / 最优表现 > 0.7  → 参数高原，稳健
    < 0.5                          → 参数尖峰，高度疑似过拟合
"""

from __future__ import annotations

from itertools import product
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from ..strategies.base import Strategy, create
from .engine import run_backtest


def _evaluate(df: pd.DataFrame, strategy_name: str, params: Dict[str, Any],
              metric: str, **bt_kwargs) -> dict:
    strat = create(strategy_name)
    params = strat.normalize_params(params)
    strat = create(strategy_name, **params)
    sig = strat.signals(df)
    res = run_backtest(df, sig, name=strategy_name, params=params, **bt_kwargs)
    return {**res.metrics, **params}


def grid_search(df: pd.DataFrame, strategy_name: str,
                param_space: Optional[Dict[str, Sequence]] = None,
                metric: str = "夏普比率",
                top_n: int = 10,
                **bt_kwargs) -> pd.DataFrame:
    """
    网格搜索：返回按 metric 排序的结果表（每行一组参数 + 全部指标）。
    """
    strat = create(strategy_name)
    space = param_space or strat.param_space
    if not space:
        return pd.DataFrame([_evaluate(df, strategy_name, dict(strat.params), metric, **bt_kwargs)])

    keys = list(space.keys())
    rows = []
    for values in product(*[space[k] for k in keys]):
        params = {**strat.params, **dict(zip(keys, values))}
        try:
            row = _evaluate(df, strategy_name, params, metric, **bt_kwargs)
            row["_score"] = row.get(metric, np.nan)
            rows.append(row)
        except Exception as e:                       # noqa: BLE001
            rows.append({**params, "_score": np.nan, "_error": str(e)[:60]})
    out = pd.DataFrame(rows)
    out = out.sort_values("_score", ascending=False).head(top_n).reset_index(drop=True)
    return out


def random_search(df: pd.DataFrame, strategy_name: str,
                  param_space: Optional[Dict[str, Sequence]] = None,
                  n_iter: int = 30, metric: str = "夏普比率",
                  seed: int = 0, **bt_kwargs) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    strat = create(strategy_name)
    space = param_space or strat.param_space
    if not space:
        return pd.DataFrame([_evaluate(df, strategy_name, dict(strat.params), metric, **bt_kwargs)])
    rows = []
    for _ in range(n_iter):
        params = {**strat.params}
        for k, v in space.items():
            params[k] = v[int(rng.integers(0, len(v)))]
        try:
            row = _evaluate(df, strategy_name, params, metric, **bt_kwargs)
            row["_score"] = row.get(metric, np.nan)
            rows.append(row)
        except Exception as e:                       # noqa: BLE001
            rows.append({**params, "_score": np.nan})
    return pd.DataFrame(rows).sort_values("_score", ascending=False).reset_index(drop=True)


def param_sensitivity(df: pd.DataFrame, strategy_name: str,
                      base_params: Dict[str, Any],
                      vary_key: str,
                      vary_values: Sequence,
                      metric: str = "夏普比率",
                      **bt_kwargs) -> pd.DataFrame:
    """
    单参数敏感性：固定其他参数，扫描 vary_key 的不同取值，看 metric 变化。
    用于画"参数高原"图。
    """
    rows = []
    for v in vary_values:
        params = {**base_params, vary_key: v}
        try:
            row = _evaluate(df, strategy_name, params, metric, **bt_kwargs)
        except Exception as e:                       # noqa: BLE001
            row = {**params, metric: np.nan}
        row[vary_key] = v
        rows.append(row)
    return pd.DataFrame(rows)


def overfitting_score(df: pd.DataFrame, strategy_name: str,
                      best_params: Dict[str, Any],
                      metric: str = "夏普比率",
                      **bt_kwargs) -> dict:
    """
    过拟合体检：把最优参数的每个维度 ±1 档（邻居）都跑一遍，
    比较"邻居平均表现"与"最优表现"的比值。

    返回：
        best       最优参数得分
        neighbor_mean 邻居平均得分
        neighbor_worst 邻居最差得分
        robustness  neighbor_mean / best（>0.7 稳健，<0.5 危险）
    """
    strat = create(strategy_name)
    space = strat.param_space
    best = _evaluate(df, strategy_name, best_params, metric, **bt_kwargs).get(metric, np.nan)

    neighbors = []
    for k, values in space.items():
        if k not in best_params or len(values) < 2:
            continue
        cur = best_params[k]
        if cur not in values:
            continue
        i = list(values).index(cur)
        for j in (i - 1, i + 1):
            if 0 <= j < len(values):
                p = {**best_params, k: values[j]}
                try:
                    neighbors.append(_evaluate(df, strategy_name, p, metric, **bt_kwargs).get(metric, np.nan))
                except Exception:
                    pass
    if not neighbors:
        return {"best": best, "neighbor_mean": np.nan, "neighbor_worst": np.nan,
                "robustness": np.nan, "n_neighbors": 0}

    nb = np.array([x for x in neighbors if not (isinstance(x, float) and np.isnan(x))])
    if len(nb) == 0:
        return {"best": best, "neighbor_mean": np.nan, "neighbor_worst": np.nan,
                "robustness": np.nan, "n_neighbors": 0}
    mean_ = float(nb.mean())
    worst = float(nb.min())
    denom = abs(best) if best and not np.isnan(best) and abs(best) > 1e-9 else 1.0
    return {
        "best": float(best) if best == best else np.nan,
        "neighbor_mean": mean_,
        "neighbor_worst": worst,
        "robustness": float(mean_ / denom),
        "n_neighbors": int(len(nb)),
    }
