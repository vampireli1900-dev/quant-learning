# -*- coding: utf-8 -*-
"""
backtest.py —— 回测引擎 + 绩效指标
==================================
回测(backtest)回答的问题是："如果过去按这套规则交易，能赚多少、风险多大？"

一个"诚实"的回测必须遵守几个原则（否则结果会骗人）：
    1. 不能偷看未来（无 look-ahead bias）：
       今天的仓位必须由"昨天及以前"的信号决定 → 所以要把信号 shift(1)。
    2. 要考虑交易成本：频繁交易会被手续费吃掉收益。
    3. 不能只看总收益，还要看风险（最大回撤、夏普比率）。

本文件实现一个"向量化"回测：全程用 pandas 向量运算，不逐行循环，简洁且快。
"""

import numpy as np
import pandas as pd


def run_backtest(df, signal, initial_capital=100_000.0, commission=0.001):
    """
    核心回测函数。输入 K 线 + 信号，输出绩效指标字典 + 资金曲线。

    参数：
        df              : 含 open/high/low/close 的 DataFrame
        signal          : 每日目标仓位（0/1），与 df 索引对齐
        initial_capital : 初始资金
        commission      : 单边手续费率（默认千分之一，0.001）

    返回：
        dict，包含：
            metrics        : 绩效指标字典
            equity_curve   : 每日净值曲线（Series）
            positions      : 实际每日持仓（Series，已 shift 过）
            trades         : 每笔交易的收益列表（用于算胜率）
    """
    close = df["close"]

    # ---- 1) 目标仓位 → 实际持仓（shift(1) 消除"偷看未来"） ----
    # 今天能看到的信号是昨天收盘算出来的，所以今天的持仓 = 昨天的信号。
    position = signal.shift(1).fillna(0).astype(int)

    # ---- 2) 计算每日收益率 ----
    # 资产本身的日收益率
    asset_returns = close.pct_change().fillna(0)

    # 策略收益 = 持仓 × 资产收益（先不考虑手续费）
    gross_strategy_returns = position * asset_returns

    # ---- 3) 交易成本：仓位变化（换手）时收一次手续费 ----
    turnover = position.diff().abs().fillna(position.abs())
    cost = turnover * commission
    net_strategy_returns = gross_strategy_returns - cost

    # ---- 4) 资金曲线 ----
    equity = (1 + net_strategy_returns).cumprod() * initial_capital

    # ---- 5) 计算各类绩效指标 ----
    metrics = compute_metrics(net_strategy_returns, equity, position, close)

    return {
        "metrics": metrics,
        "equity_curve": equity,
        "positions": position,
        "trades": extract_trades(position, close),
    }


def compute_metrics(strategy_returns, equity, position, close):
    """把一堆绩效指标算出来，统一放字典里，方便打印对比。"""
    n = len(strategy_returns)

    total_return = equity.iloc[-1] / equity.iloc[0] - 1

    # 年化收益率：假设一年 252 个交易日，按复利折算
    annual_return = (1 + total_return) ** (252 / n) - 1

    # 年化波动率
    annual_vol = strategy_returns.std() * np.sqrt(252)

    # 夏普比率 = 年化收益 / 年化波动（这里无风险利率按 0 简化）
    sharpe = annual_return / annual_vol if annual_vol > 0 else np.nan

    # 最大回撤：从历史最高点跌下来的最大幅度
    drawdown = equity / equity.cummax() - 1
    max_drawdown = drawdown.min()

    # 胜率 + 盈亏比，需要先拆出每笔交易
    trades = extract_trades(position, close)
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    win_rate = len(wins) / len(trades) if trades else np.nan
    profit_loss_ratio = (np.mean(wins) / abs(np.mean(losses))) if wins and losses else np.nan

    return {
        "总收益率": total_return,
        "年化收益率": annual_return,
        "年化波动率": annual_vol,
        "夏普比率": sharpe,
        "最大回撤": max_drawdown,
        "交易次数": len(trades),
        "胜率": win_rate,
        "盈亏比": profit_loss_ratio,
    }


def extract_trades(position, close):
    """
    把持仓序列拆成"一笔一笔交易"，返回每笔的收益率。

    一笔交易 = 从买入日到卖出日的一段连续持仓。
    """
    pos = position.fillna(0).astype(int)
    trades = []
    in_trade = False
    entry_price = None

    for i in range(len(pos)):
        if pos.iloc[i] == 1 and not in_trade:
            entry_price = close.iloc[i]   # 开仓
            in_trade = True
        elif pos.iloc[i] == 0 and in_trade:
            trades.append(close.iloc[i] / entry_price - 1)  # 平仓结算
            in_trade = False

    # 如果到最后还持仓，按最后一天收盘价强制平仓
    if in_trade and entry_price is not None:
        trades.append(close.iloc[-1] / entry_price - 1)

    return trades


def print_report(name, result):
    """把某次回测的结果格式化打印成一张小表格。"""
    m = result["metrics"]
    print(f"\n========== {name} ==========")
    print(f"总收益率   : {m['总收益率']:>10.2%}")
    print(f"年化收益率 : {m['年化收益率']:>10.2%}")
    print(f"年化波动率 : {m['年化波动率']:>10.2%}")
    print(f"夏普比率   : {m['夏普比率']:>10.2f}")
    print(f"最大回撤   : {m['最大回撤']:>10.2%}")
    print(f"交易次数   : {m['交易次数']:>10d}")
    print(f"胜率       : {m['胜率']:>10.2%}")
    print(f"盈亏比     : {m['盈亏比']:>10.2f}")


def plot_equity(results):
    """
    用 matplotlib 画资金曲线对比（可选功能，未安装 matplotlib 会静默跳过）。

    想用图表时执行：  pip install matplotlib
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n（未安装 matplotlib，跳过绘图。可运行 `pip install matplotlib` 开启）")
        return

    plt.figure(figsize=(10, 5))
    for name, r in results.items():
        plt.plot(r["equity_curve"].index, r["equity_curve"].values, label=name)
    plt.title("资金曲线对比")
    plt.ylabel("账户权益")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()
