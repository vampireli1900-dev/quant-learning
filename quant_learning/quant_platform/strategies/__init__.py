# -*- coding: utf-8 -*-
"""策略库：导入本包即完成所有策略注册。"""

from .base import (Strategy, StrategyMeta, REGISTRY, register, create,
                   list_strategies, strategy_table, stateful_position)
from . import trend          # noqa: F401
from . import mean_reversion  # noqa: F401
from . import momentum       # noqa: F401
from . import ensemble       # noqa: F401
from . import ml             # noqa: F401

__all__ = [
    "Strategy", "StrategyMeta", "REGISTRY", "register", "create",
    "list_strategies", "strategy_table", "stateful_position",
]
