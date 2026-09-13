# -*- coding: utf-8 -*-
"""
main.py —— 入口：把数据、指标、策略、回测串起来跑一遍
======================================================
这是学习的主入口。看完运行结果，再回头逐个文件读，就能串起
"数据 → 指标 → 策略 → 回测 → 评估" 的完整流程。

运行方式（在 quant_learning 目录下）：
    python main.py
"""

from data import load_data
from strategy import ma_crossover, rsi_mean_reversion, bollinger_breakout
from backtest import run_backtest, print_report, plot_equity


def main():
    # 1) 准备数据（默认假数据；想换真实数据见 data.py 里的 TODO）
    df = load_data(n=500, seed=42)
    close = df["close"]
    print(f"数据：{len(df)} 个交易日，区间 {df.index[0].date()} ~ {df.index[-1].date()}")

    # 2) 定义三套策略信号
    signals = {
        "双均线交叉(20/50)": ma_crossover(close, fast=20, slow=50),
        "RSI均值回归(14)": rsi_mean_reversion(close, window=14),
        "布林带突破(20,2)": bollinger_breakout(close, window=20, num_std=2),
    }

    # 3) 逐套跑回测，收集结果
    results = {}
    for name, sig in signals.items():
        result = run_backtest(df, sig, initial_capital=100_000, commission=0.001)
        results[name] = result
        print_report(name, result)

    # 4) （可选）画资金曲线对比
    plot_equity(results)

    # 5) 顺手打印一个策略的每日持仓，帮助理解 shift(1) 和开平仓
    print("\n===== 双均线策略 前10天信号/持仓对照 =====")
    demo_sig = signals["双均线交叉(20/50)"]
    demo_res = results["双均线交叉(20/50)"]
    view = df[["close"]].copy()
    view["signal(今日算出)"] = demo_sig
    view["position(今日实际持仓)"] = demo_res["positions"]
    print(view.head(10))


if __name__ == "__main__":
    main()
