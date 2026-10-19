# -*- coding: utf-8 -*-
"""
stats —— 统计显著性检验
=======================
防止被随机性和多重检验欺骗：
    块状自助置信区间 / PSR(概率化夏普) / DSR(去膨胀夏普) /
    minTRL(最小样本长度) / 随机信号置换检验
"""

from .significance import (
    sharpe, sharpe_std_err, block_bootstrap, sharpe_ci,
    probabilistic_sharpe, deflated_sharpe, min_track_record,
    random_signal_test, significance_report, batch_significance,
)

__all__ = [
    "sharpe", "sharpe_std_err", "block_bootstrap", "sharpe_ci",
    "probabilistic_sharpe", "deflated_sharpe", "min_track_record",
    "random_signal_test", "significance_report", "batch_significance",
]
