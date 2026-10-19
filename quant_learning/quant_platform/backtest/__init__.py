# -*- coding: utf-8 -*-
"""回测子包：引擎、指标、参数寻优、走前验证。"""

from .engine import (BacktestResult, run_backtest, backtest_buy_hold,
                     apply_stops, volatility_target)
from . import metrics as metrics_module
from .metrics import (compute_metrics, max_drawdown, drawdown_series,
                      extract_trades, trade_stats, metrics_to_frame,
                      rank_strategies, value_at_risk, conditional_var)
from .optimizer import (grid_search, random_search, param_sensitivity,
                        overfitting_score)
from .walkforward import walk_forward, WalkForwardResult

__all__ = [
    "BacktestResult", "run_backtest", "backtest_buy_hold", "apply_stops",
    "volatility_target", "metrics", "compute_metrics", "max_drawdown",
    "drawdown_series", "extract_trades", "trade_stats", "metrics_to_frame",
    "rank_strategies", "value_at_risk", "conditional_var",
    "grid_search", "random_search", "param_sensitivity", "overfitting_score",
    "walk_forward", "WalkForwardResult",
]
