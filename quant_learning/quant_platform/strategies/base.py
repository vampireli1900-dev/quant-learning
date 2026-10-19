# -*- coding: utf-8 -*-
"""
strategies/base.py —— 策略基类与注册表
======================================
统一接口是"多模型对比平台"的地基：任何策略只要实现 signals(df) 返回
一个与 df 索引对齐的仓位 Series，就能被回测、寻优、走前验证、横向排名。

约定：
    signals(df, **params) -> pd.Series
        1 /  0.5  = 做多（数值=仓位权重）
        0          = 空仓
        -1 / -0.5  = 做空（需回测引擎 allow_short=True）

参数空间 param_space 用于参数寻优与过拟合检测：
    {"fast": [5, 10, 20], "slow": [30, 60, 120]}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd
from itertools import product


@dataclass
class StrategyMeta:
    """策略元信息（用于报告展示）。"""
    name: str
    category: str                 # 趋势跟踪 / 均值回归 / 动量 / 突破 / 机器学习 / 组合
    description: str = ""
    reference: str = ""           # 出处或经典文献


class Strategy(ABC):
    """策略基类。子类只需实现 signals() 并定义 meta / default_params / param_space。"""

    meta: StrategyMeta
    default_params: Dict[str, Any] = {}
    param_space: Dict[str, List[Any]] = {}
    # 该策略默认偏好什么市场环境（用于结果解读，非交易逻辑）
    favored_regime: str = "—"

    def __init__(self, **params):
        self.params = {**self.default_params, **params}

    @abstractmethod
    def signals(self, df: pd.DataFrame) -> pd.Series:
        raise NotImplementedError

    # ---- 便捷 ----
    def run(self, df: pd.DataFrame) -> pd.Series:
        return self.signals(df)

    def param_grid(self) -> List[Dict[str, Any]]:
        """展开参数空间为笛卡尔积列表。"""
        if not self.param_space:
            return [dict(self.params)]
        keys = list(self.param_space.keys())
        combos = []
        for values in product(*[self.param_space[k] for k in keys]):
            p = dict(self.params)
            p.update(dict(zip(keys, values)))
            combos.append(p)
        return combos

    def with_params(self, **params) -> "Strategy":
        return self.__class__(**{**self.params, **params})

    def normalize_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        把参数值还原成"默认参数的类型"。

        为什么需要：从寻优结果 DataFrame 里取出的参数会被 pandas 统一转成
        float（因为同一张表里还有浮点指标列），例如 entry_n=20.0。
        直接传给 rolling(20.0) 会抛 "min_periods must be an integer"。
        """
        out = dict(params)
        for k, v in out.items():
            ref = self.default_params.get(k, None)
            if ref is None and k in self.param_space and self.param_space[k]:
                ref = self.param_space[k][0]
            try:
                if isinstance(ref, bool):
                    out[k] = bool(v)
                elif isinstance(ref, int):
                    out[k] = int(round(float(v)))
                elif isinstance(ref, float):
                    out[k] = float(v)
            except (TypeError, ValueError):
                continue
        return out

    def __repr__(self) -> str:
        ps = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"<{self.meta.name}({ps})>"


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
REGISTRY: Dict[str, type] = {}


def register(cls: type) -> type:
    """装饰器：把策略类注册进全局表。"""
    inst = cls()
    REGISTRY[inst.meta.name] = cls
    return cls


def create(name: str, **params) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"未注册的策略: {name}；可用：{list_strategies()}")
    return REGISTRY[name](**params)


def list_strategies(category: Optional[str] = None) -> List[str]:
    out = []
    for name, cls in REGISTRY.items():
        if category is None or cls.meta.category == category:
            out.append(name)
    return sorted(out)


def strategy_table() -> pd.DataFrame:
    rows = []
    for name, cls in REGISTRY.items():
        inst = cls()
        rows.append({
            "策略": name,
            "类别": inst.meta.category,
            "默认参数": ", ".join(f"{k}={v}" for k, v in inst.params.items()),
            "适合行情": inst.favored_regime,
            "说明": inst.meta.description,
        })
    return pd.DataFrame(rows).sort_values(["类别", "策略"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 工具：把"买入/卖出条件"转成持仓状态机（很多策略都要用到）
# ---------------------------------------------------------------------------
def stateful_position(enter: pd.Series, exit_: pd.Series,
                      index: pd.Index, allow_reverse: bool = False) -> pd.Series:
    """
    空仓时等买入信号，持仓时等卖出信号。
    allow_reverse=True 时允许由多转空（用于可做空的策略）。
    """
    enter = enter.fillna(False).astype(bool)
    exit_ = exit_.fillna(False).astype(bool)
    holding = 0
    out = []
    for e, x in zip(enter.values, exit_.values):
        if holding == 0:
            if e:
                holding = 1
            elif allow_reverse and x:
                holding = -1
        elif holding == 1:
            if x:
                holding = -1 if allow_reverse else 0
        elif holding == -1:
            if e:
                holding = 1 if allow_reverse else 0
        out.append(holding)
    return pd.Series(out, index=index, dtype=float)
