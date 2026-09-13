# -*- coding: utf-8 -*-
"""
strategy.py —— 交易策略
=======================
策略的本质：给定历史 K 线，输出"每天该持多少仓位"的信号序列。

约定（全项目统一）：
    signal 是一个 pd.Series，取值含义：
        1  = 满仓持有（买入/持仓）
        0  = 空仓（不持有）
    与日期索引对齐，长度和输入数据一致。

本文件用三种经典策略演示"从指标到信号"的转化，从易到难：
    1. 双均线交叉（金叉/死叉）—— 趋势跟随，最经典的入门策略
    2. RSI 均值回归 —— 超卖买、超买卖，赌"涨多了会跌、跌多了会涨"
    3. 布林带突破 —— 顺势突破上轨买入，回落到中轨卖出
"""

import pandas as pd

from indicators import sma, rsi, bollinger


def ma_crossover(close, fast=20, slow=50):
    """
    双均线交叉（金叉/死叉）策略。

    规则：
        快线上穿慢线（金叉）→ 趋势转多 → 买入持有
        快线下穿慢线（死叉）→ 趋势转空 → 卖出空仓

    实现方式很"向量化"：直接用 (快线 > 慢线) 生成 0/1，
    因为"快线在慢线上方"这个状态本身就等价于"处于多头持仓"。
    """
    fast_ma = sma(close, fast)
    slow_ma = sma(close, slow)
    signal = (fast_ma > slow_ma).astype(int)   # True→1，False→0
    return signal


def rsi_mean_reversion(close, window=14, oversold=30, overbought=70):
    """
    RSI 均值回归策略。

    规则：
        RSI 跌破 oversold(30) → 超卖，买入
        RSI 涨破 overbought(70) → 超买，卖出

    和均线策略不同，这里是"逢低买、逢高卖"的状态机逻辑：
    只在满足条件的那一刻切换仓位，所以用 _stateful_position 逐步推演。
    """
    r = rsi(close, window)
    enter = r < oversold       # 买入条件：超卖
    exit_ = r > overbought     # 卖出条件：超买
    return _stateful_position(enter, exit_, close.index)


def bollinger_breakout(close, window=20, num_std=2):
    """
    布林带突破策略。

    规则：
        收盘价突破上轨 → 强势，买入
        收盘价回落到中轨(20日均线)以下 → 动能减弱，卖出
    """
    bb = bollinger(close, window, num_std)
    enter = close > bb["upper"]   # 突破上轨
    exit_ = close < bb["mid"]     # 跌破中轨
    return _stateful_position(enter, exit_, close.index)


def _stateful_position(enter_cond, exit_cond, index):
    """
    工具函数：把"买入条件/卖出条件"两个布尔序列，转成"持有状态"序列。

    逻辑：空仓时只等买入信号；持仓时只等卖出信号。这样不会在连续满足
    条件时反复开平仓，更接近真实交易状态。

    参数：
        enter_cond : 买入条件（布尔 Series）
        exit_cond  : 卖出条件（布尔 Series）
        index      : 目标输出的日期索引
    """
    holding = False
    position = []
    for enter, exit_ in zip(enter_cond, exit_cond):
        if not holding and enter:        # 空仓 + 买入信号 → 开仓
            holding = True
        elif holding and exit_:          # 持仓 + 卖出信号 → 平仓
            holding = False
        position.append(1 if holding else 0)

    return pd.Series(position, index=index, dtype=int)


if __name__ == "__main__":
    from data import load_data
    df = load_data(n=200)
    close = df["close"]

    sig_ma = ma_crossover(close)
    sig_rsi = rsi_mean_reversion(close)
    sig_bb = bollinger_breakout(close)

    print("双均线持仓天数:", sig_ma.sum())
    print("RSI 持仓天数:", sig_rsi.sum())
    print("布林带持仓天数:", sig_bb.sum())
