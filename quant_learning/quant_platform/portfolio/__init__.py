# -*- coding: utf-8 -*-
"""
portfolio —— 多标的组合回测
===========================
engine.py 提供 5 种资金分配方案（等权/信号加权/波动倒数/风险平价/组合波动目标），
并输出相关性矩阵、逐标的贡献分解、分散化收益对比。
"""

from .engine import (
    PortfolioResult, run_portfolio, compute_weights,
    diversification_benefit, compare_schemes, SCHEMES,
)

__all__ = [
    "PortfolioResult", "run_portfolio", "compute_weights",
    "diversification_benefit", "compare_schemes", "SCHEMES",
]
