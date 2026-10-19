# -*- coding: utf-8 -*-
"""
backtest/walkforward.py —— 走前分析（Walk-Forward Analysis）
==========================================================
这是识别过拟合最有力的工具，也是本平台的"诚实验证"核心。

做法（滚动窗口）：
    |--- 训练段(样本内) ---|--- 测试段(样本外) ---|
                          |--- 训练 ---|--- 测试 ---|
                                       |--- 训练 ---|--- 测试 ---|

    * 每个训练段里用网格搜索挑最优参数
    * 只把这个参数用到紧随其后的测试段，记录样本外表现
    * 把所有测试段首尾相接 = 一条"样本外净值曲线"

如果"样本内表现"远好于"样本外表现"，说明过拟合。
业界经验：样本外/样本内 夏普比 > 0.5 才勉强可用，> 0.7 才算稳健。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from ..strategies.base import create
from .engine import BacktestResult, backtest_buy_hold, run_backtest
from .metrics import compute_metrics
from .optimizer import grid_search


@dataclass
class WalkForwardResult:
    name: str = ""
    symbol: str = ""
    oos_equity: Optional[pd.Series] = None      # 样本外拼接净值
    oos_returns: Optional[pd.Series] = None
    in_sample_scores: List[float] = field(default_factory=list)
    out_sample_scores: List[float] = field(default_factory=list)
    chosen_params: List[Dict[str, Any]] = field(default_factory=list)
    segments: List[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    benchmark: Optional[dict] = None

    def summary(self) -> str:
        is_mean = np.nanmean(self.in_sample_scores) if self.in_sample_scores else np.nan
        oos_mean = np.nanmean(self.out_sample_scores) if self.out_sample_scores else np.nan
        lines = [
            f"—— {self.name} @ {self.symbol} 走前分析 ——",
            f"  段数            : {len(self.segments)}",
            f"  样本内平均夏普   : {is_mean:.2f}" if is_mean == is_mean else "  样本内平均夏普: NA",
            f"  样本外平均夏普   : {oos_mean:.2f}" if oos_mean == oos_mean else "  样本外平均夏普: NA",
            f"  样本外/样本内    : {(oos_mean/is_mean):.2f}"
            if (is_mean and is_mean == is_mean and abs(is_mean) > 1e-9) else "  样本外/样本内: NA",
        ]
        for k in ("年化收益率", "夏普比率", "最大回撤", "卡玛比率"):
            if k in self.metrics:
                v = self.metrics[k]
                lines.append(f"  样本外{k:<8}: {v:.2%}" if k in ("年化收益率", "最大回撤") else f"  样本外{k:<8}: {v:.2f}")
        return "\n".join(lines)


def walk_forward(df: pd.DataFrame, strategy_name: str,
                 param_space: Optional[Dict[str, Sequence]] = None,
                 train_bars: int = 756,      # 约 3 年日线
                 test_bars: int = 126,       # 约 6 个月
                 metric: str = "夏普比率",
                 anchored: bool = False,     # True=锚定（训练起点固定）
                 step: Optional[int] = None,
                 initial_capital: float = 1_000_000.0,
                 **bt_kwargs) -> WalkForwardResult:
    """
    走前分析主函数。

    anchored=True 时训练窗口起点固定在数据开头（扩展窗口）；
    否则为滚动窗口（更严格，也更贴近"忘掉旧数据"的实盘做法）。
    """
    n = len(df)
    step = step or test_bars
    res = WalkForwardResult(name=strategy_name)

    if n < train_bars + test_bars + 10:
        res.metrics = {"error": f"数据不足：需要 {train_bars + test_bars} 根，实际 {n} 根"}
        return res

    oos_parts: List[pd.Series] = []
    start = 0
    while start + train_bars + test_bars <= n:
        train_start = 0 if anchored else start       # 锚定=扩展窗口，否则滚动窗口
        train = df.iloc[train_start: start + train_bars]
        test = df.iloc[start + train_bars: start + train_bars + test_bars]

        # 1) 训练段内寻优
        try:
            grid = grid_search(train, strategy_name, param_space,
                               metric=metric, top_n=1, **bt_kwargs)
            if len(grid) == 0:
                start += step
                continue
            strat = create(strategy_name)
            best_row = grid.iloc[0]
            raw_params = {k: best_row[k] for k in strat.param_space.keys() if k in best_row}
            best_params = strat.normalize_params(raw_params)
        except Exception:
            start += step
            continue

        # 2) 用最优参数跑样本内（记录）
        try:
            is_res = run_backtest(train, create(strategy_name, **best_params).signals(train),
                                  name=strategy_name, params=best_params,
                                  initial_capital=initial_capital, **bt_kwargs)
            res.in_sample_scores.append(is_res.metrics.get(metric, np.nan))
        except Exception:
            res.in_sample_scores.append(np.nan)

        # 3) 用同一参数跑样本外
        try:
            oos_res = run_backtest(test, create(strategy_name, **best_params).signals(test),
                                   name=strategy_name, params=best_params,
                                   initial_capital=initial_capital, **bt_kwargs)
            res.out_sample_scores.append(oos_res.metrics.get(metric, np.nan))
            # 分段净值：每段独立以 initial_capital 起算，按段起始净值缩放拼接
            seg_eq = oos_res.equity
            if oos_parts:
                scale = oos_parts[-1].iloc[-1] / seg_eq.iloc[0]
                seg_eq = seg_eq * scale
            oos_parts.append(seg_eq)
            res.segments.append({
                "train_start": train.index[0], "train_end": train.index[-1],
                "test_start": test.index[0], "test_end": test.index[-1],
                "params": best_params,
                "is_score": is_res.metrics.get(metric, np.nan),
                "oos_score": oos_res.metrics.get(metric, np.nan),
                "oos_return": oos_res.metrics.get("总收益率", np.nan),
            })
        except Exception:
            res.out_sample_scores.append(np.nan)

        res.chosen_params.append(best_params)
        start += step
        if step <= 0:                 # 防止死循环
            break

    if not oos_parts:
        res.metrics = {"error": "没有任何有效的走前分段"}
        return res

    oos_eq = pd.concat(oos_parts)
    oos_eq = oos_eq[~oos_eq.index.duplicated(keep="last")].sort_index()
    oos_ret = oos_eq.pct_change().fillna(0.0)
    res.oos_equity = oos_eq
    res.oos_returns = oos_ret
    res.metrics = compute_metrics(oos_ret, oos_eq)

    # 同期买入持有基准
    bh = backtest_buy_hold(df.loc[oos_eq.index[0]: oos_eq.index[-1]],
                           initial_capital=initial_capital)
    res.benchmark = bh.metrics
    return res
