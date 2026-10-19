# -*- coding: utf-8 -*-
"""
web —— TradingView 风格的 Web 展示终端
======================================
server.py 提供只读 REST API + 静态资源，前端用 TradingView 官方开源的
Lightweight Charts 渲染 K 线，暗色主题、简洁高级。

启动：python -m quant_platform.cli web --port 8770
"""

from .server import app, run_server

__all__ = ["app", "run_server"]
