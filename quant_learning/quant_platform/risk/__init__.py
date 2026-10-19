# -*- coding: utf-8 -*-
"""
risk —— 风险管理层
==================
overlay.py 提供可插拔风控叠加层（波动率目标 / ATR 移动止损 / 固定止损止盈 /
最长持仓 / 回撤熔断），可与任意策略信号组合，用于"加风控 vs 不加风控"的 A/B 对比。
"""

from .overlay import (
    RiskOverlay, PRESETS, preset, overlay_grid,
    apply_overlay, apply_atr_stop, apply_drawdown_circuit, vol_target_scale,
)
from .compare import run_with_overlay, overlay_ab_test, summarize_overlay_effect

__all__ = [
    "RiskOverlay", "PRESETS", "preset", "overlay_grid",
    "apply_overlay", "apply_atr_stop", "apply_drawdown_circuit", "vol_target_scale",
    "run_with_overlay", "overlay_ab_test", "summarize_overlay_effect",
]
