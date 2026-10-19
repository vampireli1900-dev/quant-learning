# -*- coding: utf-8 -*-
"""实验子包：编排、对比、报告。"""

from .runner import ExperimentConfig, ExperimentResult, run_experiment
from .compare import (rank, stability, strategy_correlation, regime_analysis,
                      conclusions)
from .report import build_html_report

__all__ = [
    "ExperimentConfig", "ExperimentResult", "run_experiment",
    "rank", "stability", "strategy_correlation", "regime_analysis",
    "conclusions", "build_html_report",
]
