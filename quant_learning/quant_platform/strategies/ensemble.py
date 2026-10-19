# -*- coding: utf-8 -*-
"""
strategies/ensemble.py —— 组合 / 自适应类策略
============================================
单一策略总有"吃定一种行情"的宿命：趋势策略死在震荡市，反转策略死在单边市。
这里的三个模型展示三种组合思路：

    1. 投票集成：多个不同类别策略投票，少数服从多数（降低单模型方差）
    2. 状态切换：用 ADX 判断"现在是趋势市还是震荡市"，再切换对应模型
    3. 加权混合：把若干策略的信号按权重线性混合成连续仓位
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import adx
from .base import Strategy, StrategyMeta, create, register


@register
class VotingEnsemble(Strategy):
    """多策略投票：>半数看多才持仓（默认用趋势+动量+ML 三个不同族的模型）。"""
    meta = StrategyMeta("多策略投票", "组合",
                        "多个不同族策略投票，多数表决决定仓位")
    default_params = {
        "members": ["双均线交叉", "时间序列动量", "RSI均值回归"],
        "min_votes": 2,
        "member_params": {},
    }
    param_space = {"min_votes": [1, 2, 3]}
    favored_regime = "全环境（降低方差）"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        votes = []
        for name in self.params["members"]:
            try:
                s = create(name).signals(df)
                votes.append((s > 0).astype(float))
            except Exception:
                continue
        if not votes:
            return pd.Series(0.0, index=df.index)
        v = pd.concat(votes, axis=1).sum(axis=1)
        need = min(self.params["min_votes"], len(votes))
        return (v >= need).astype(float)


@register
class WeightedBlend(Strategy):
    """信号加权混合：把若干策略的 0/1 信号按权重平均成连续仓位。"""
    meta = StrategyMeta("信号加权混合", "组合",
                        "按权重线性混合多个策略信号，输出连续仓位")
    default_params = {
        "members": ["双均线交叉", "RSI均值回归", "ATR移动止损", "波动率目标动量"],
        "weights": [0.3, 0.2, 0.3, 0.2],
    }
    param_space = {}
    favored_regime = "全环境"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        members, weights = self.params["members"], self.params["weights"]
        if len(weights) != len(members):
            weights = [1.0 / len(members)] * len(members)
        total = pd.Series(0.0, index=df.index)
        wsum = 0.0
        for name, w in zip(members, weights):
            try:
                s = create(name).signals(df).fillna(0.0)
                total += w * s
                wsum += w
            except Exception:
                continue
        if wsum == 0:
            return total
        return (total / wsum).clip(0, 1.5)


@register
class RegimeSwitch(Strategy):
    """
    市场状态自适应切换：
        ADX 高（趋势明确）→ 用趋势跟踪模型（双均线）
        ADX 低（震荡）    → 用均值回归模型（布林带回归）
    这是"没有万能策略，但有万能框架"思想的落地。
    """
    meta = StrategyMeta("状态自适应切换", "组合",
                        "ADX高用趋势模型，ADX低用均值回归模型")
    default_params = {"adx_n": 14, "adx_th": 25,
                      "trend_model": "双均线交叉", "trend_params": {"fast": 20, "slow": 50},
                      "range_model": "布林带回归", "range_params": {"window": 20, "k": 2.0}}
    param_space = {"adx_th": [20, 25, 30]}
    favored_regime = "全环境（核心卖点）"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        dx = adx(df, self.params["adx_n"])
        is_trend = (dx > self.params["adx_th"]).fillna(False)

        trend_sig = create(self.params["trend_model"],
                           **(self.params["trend_params"] or {})).signals(df)
        range_sig = create(self.params["range_model"],
                           **(self.params["range_params"] or {})).signals(df)
        out = np.where(is_trend.values, trend_sig.values, range_sig.values)
        return pd.Series(out, index=df.index).fillna(0.0)
