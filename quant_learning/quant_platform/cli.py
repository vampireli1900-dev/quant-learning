# -*- coding: utf-8 -*-
"""
cli.py —— 命令行入口
====================
常用命令：

    python -m quant_platform.cli list                        列出全部策略
    python -m quant_platform.cli fetch SPY --duration "5 Y"  拉取并缓存数据
    python -m quant_platform.cli backtest SPY --strategy 双均线交叉
    python -m quant_platform.cli optimize SPY --strategy 双均线交叉
    python -m quant_platform.cli walkforward SPY --strategy 时间序列动量
    python -m quant_platform.cli experiment --config configs/demo.yaml
    python -m quant_platform.cli account                     查看 IB 账户
    python -m quant_platform.cli signal SPY AAPL             今日信号扫描
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 40)


def _print_df(df: pd.DataFrame):
    if df is None or not len(df):
        print("（无数据）")
        return
    print(df.to_string())


def cmd_list(args):
    from .strategies import strategy_table
    _print_df(strategy_table())


def cmd_fetch(args):
    from .data import get_data
    for sym in args.symbols:
        df = get_data(sym, duration=args.duration, bar_size=args.bar_size,
                      refresh=args.refresh)
        print(f"{sym}: {len(df)} 根 {df.index[0].date()} ~ {df.index[-1].date()} "
              f"最新收盘 {df['close'].iloc[-1]:.2f}")


def cmd_backtest(args):
    from .backtest import backtest_buy_hold, run_backtest
    from .data import get_data
    from .strategies import create
    df = get_data(args.symbol, duration=args.duration, bar_size=args.bar_size)
    print(f"数据 {len(df)} 根：{df.index[0].date()} ~ {df.index[-1].date()}")
    bh = backtest_buy_hold(df, initial_capital=args.capital)
    print(bh.summary())
    for name in args.strategy:
        s = create(name)
        r = run_backtest(df, s.signals(df), name=name, symbol=args.symbol,
                         initial_capital=args.capital)
        print(r.summary())


def cmd_optimize(args):
    from .backtest import grid_search, overfitting_score, random_search
    from .data import get_data
    from .strategies import create
    df = get_data(args.symbol, duration=args.duration)
    strat = create(args.strategy[0])
    print(f"参数空间：{strat.param_space}")
    if args.mode == "grid":
        out = grid_search(df, args.strategy[0], metric=args.metric, top_n=args.top)
    else:
        out = random_search(df, args.strategy[0], metric=args.metric, n_iter=args.top)
    _print_df(out)
    if len(out):
        best = out.iloc[0]
        best_params = {k: best[k] for k in strat.param_space if k in out.columns}
        chk = overfitting_score(df, args.strategy[0], best_params, metric=args.metric)
        print("\n过拟合体检（>0.7 稳健，<0.5 危险）:")
        for k, v in chk.items():
            print(f"  {k}: {v}")


def cmd_walkforward(args):
    from .backtest import walk_forward
    from .data import get_data
    df = get_data(args.symbol, duration=args.duration)
    wf = walk_forward(df, args.strategy[0], train_bars=args.train, test_bars=args.test,
                      metric=args.metric, anchored=args.anchored)
    print(wf.summary())
    if wf.benchmark:
        print(f"  同期买入持有年化: {wf.benchmark.get('年化收益率', float('nan')):.2%}")


def cmd_experiment(args):
    from .experiment import ExperimentConfig, build_html_report, run_experiment
    cfg = ExperimentConfig.from_yaml(args.config)
    if args.name:
        cfg.name = args.name
    res = run_experiment(cfg, verbose=True)
    res.save()
    path = build_html_report(res)
    print(f"\n结果目录: {res.output_dir}")
    print(f"HTML 报告: {path}")


def cmd_riskab(args):
    """风控 A/B 对比：同一信号套不同风控方案。"""
    from .data import get_data
    from .risk import overlay_ab_test, PRESETS
    df = get_data(args.symbol, duration=args.duration)
    print(f"数据 {len(df)} 根：{df.index[0].date()} ~ {df.index[-1].date()}")
    ovs = args.overlay or list(PRESETS)
    cols = ["策略", "风控方案", "夏普比率", "年化收益率", "最大回撤",
            "卡玛比率", "交易次数", "持仓占比"]
    all_rows = []
    for s in args.strategy:
        ab = overlay_ab_test(df, s, overlays=ovs, symbol=args.symbol)
        ab = ab.sort_values(args.metric, ascending=False)
        print(f"\n===== {s} =====")
        _print_df(ab[[c for c in cols if c in ab.columns]])
        all_rows.append(ab)
    if len(all_rows) > 1 and args.save:
        out = pd.concat(all_rows, ignore_index=True)
        p = Path("results") / f"riskab_{args.symbol}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(p, encoding="utf-8-sig", index=False)
        print(f"\n已保存: {p}")


def cmd_account(args):
    from .live import IBBroker
    bk = IBBroker(dry_run=True)
    bk.connect()
    acc = bk.account()
    for k, v in acc.items():
        print(f"{k:<22} {v}")
    pos = bk.positions()
    print("\n持仓:" if len(pos) else "\n持仓: 无")
    _print_df(pos)
    bk.disconnect()


def cmd_signal(args):
    from .live import SignalMonitor
    mon = SignalMonitor(strategies=args.strategy or ["双均线交叉", "RSI均值回归",
                                                     "时间序列动量", "ATR移动止损"],
                        symbols=args.symbols, duration=args.duration)
    _print_df(mon.scan())


def cmd_order(args):
    """下单（默认 dry-run）。"""
    from .live import IBBroker
    bk = IBBroker(dry_run=not args.execute)
    bk.connect()
    if args.execute and not args.yes:
        print("危险操作：这会真实发单！请加 --yes 确认。")
        return
    info = bk.place_order(args.symbol, args.action, args.qty,
                          order_type="LMT" if args.limit else "MKT",
                          limit_price=args.limit)
    for k, v in info.items():
        print(f"{k}: {v}")
    bk.disconnect()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quant_platform", description="量化交易模型实验平台")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出所有策略").set_defaults(func=cmd_list)

    f = sub.add_parser("fetch", help="拉取并缓存数据")
    f.add_argument("symbols", nargs="+")
    f.add_argument("--duration", default="5 Y")
    f.add_argument("--bar-size", default="1 day")
    f.add_argument("--refresh", action="store_true")
    f.set_defaults(func=cmd_fetch)

    b = sub.add_parser("backtest", help="单标的回测")
    b.add_argument("symbol")
    b.add_argument("--strategy", nargs="+", default=["双均线交叉"])
    b.add_argument("--duration", default="5 Y")
    b.add_argument("--bar-size", default="1 day")
    b.add_argument("--capital", type=float, default=1_000_000)
    b.set_defaults(func=cmd_backtest)

    o = sub.add_parser("optimize", help="参数寻优")
    o.add_argument("symbol")
    o.add_argument("--strategy", nargs="+", default=["双均线交叉"])
    o.add_argument("--mode", choices=["grid", "random"], default="grid")
    o.add_argument("--metric", default="夏普比率")
    o.add_argument("--top", type=int, default=8)
    o.add_argument("--duration", default="5 Y")
    o.set_defaults(func=cmd_optimize)

    w = sub.add_parser("walkforward", help="走前分析")
    w.add_argument("symbol")
    w.add_argument("--strategy", nargs="+", default=["双均线交叉"])
    w.add_argument("--train", type=int, default=756)
    w.add_argument("--test", type=int, default=126)
    w.add_argument("--metric", default="夏普比率")
    w.add_argument("--anchored", action="store_true")
    w.add_argument("--duration", default="5 Y")
    w.set_defaults(func=cmd_walkforward)

    e = sub.add_parser("experiment", help="运行实验配置")
    e.add_argument("--config", default="configs/demo.yaml")
    e.add_argument("--name", default=None)
    e.set_defaults(func=cmd_experiment)

    ra = sub.add_parser("riskab", help="风控 A/B 对比")
    ra.add_argument("symbol")
    ra.add_argument("--strategy", nargs="+", default=["双均线交叉"])
    ra.add_argument("--overlay", nargs="*", default=None,
                    help="风控方案名（默认全部）：无风控/ATR移动止损/固定止损8%/"
                         "ATR止损+止盈/波动率目标15%/波动目标+ATR止损/全套风控")
    ra.add_argument("--metric", default="夏普比率")
    ra.add_argument("--duration", default="5 Y")
    ra.add_argument("--save", action="store_true")
    ra.set_defaults(func=cmd_riskab)

    sub.add_parser("account", help="查看 IB 账户与持仓").set_defaults(func=cmd_account)

    s = sub.add_parser("signal", help="今日信号扫描")
    s.add_argument("symbols", nargs="+")
    s.add_argument("--strategy", nargs="+", default=None)
    s.add_argument("--duration", default="2 Y")
    s.set_defaults(func=cmd_signal)

    od = sub.add_parser("order", help="下单（默认 dry-run）")
    od.add_argument("symbol")
    od.add_argument("action", choices=["BUY", "SELL"])
    od.add_argument("qty", type=float)
    od.add_argument("--limit", type=float, default=None)
    od.add_argument("--execute", action="store_true", help="真实发单（默认试算）")
    od.add_argument("--yes", action="store_true")
    od.set_defaults(func=cmd_order)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
