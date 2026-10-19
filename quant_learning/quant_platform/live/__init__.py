# -*- coding: utf-8 -*-
"""实盘/模拟盘子包。"""

from .broker import IBBroker, SignalMonitor, RiskLimits
from .paper import PaperTrader, PaperConfig, default_paper_config, is_us_market_open


def __getattr__(name):
    """
    延迟导入 dashboard。

    dashboard 会经 db 包间接拉起 pandas / sqlalchemy，而 live 包被 CLI 的高频
    路径引用；做成惰性加载后，不碰看板就不会付这份导入开销，也避免与 db 包
    在初始化顺序上互相牵扯。
    """
    if name in ("account_board", "board_frame"):
        from .dashboard import account_board, board_frame
        return {"account_board": account_board,
                "board_frame": board_frame}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["IBBroker", "SignalMonitor", "RiskLimits",
           "PaperTrader", "PaperConfig", "default_paper_config", "is_us_market_open",
           "account_board", "board_frame"]
