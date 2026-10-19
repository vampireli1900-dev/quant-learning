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


def cmd_significance(args):
    """统计显著性体检：夏普置信区间 / PSR / DSR / 最小样本量 / 随机信号检验。"""
    from .data import get_data
    from .strategies import create
    from .backtest import run_backtest
    from .stats import batch_significance, random_signal_test
    df = get_data(args.symbol, duration=args.duration)
    names = args.strategy or ["双均线交叉", "RSI均值回归", "唐奇安突破",
                              "ZScore回归", "ADX趋势过滤", "时间序列动量"]
    rets, sigs = {}, {}
    for n in names:
        s = create(n).signals(df)
        sigs[n] = s
        rets[n] = run_backtest(df, s, name=n, symbol=args.symbol).returns
    b = batch_significance(rets)
    cols = ["策略", "夏普比率", "PSR(>0)", "DSR", "夏普95%下界", "夏普95%上界",
            "SR0(期望虚高线)", "样本数", "最小样本量(证明夏普>0)"]
    print(f"=== {args.symbol} 显著性体检（{len(names)} 个策略互为试验次数）===")
    _print_df(b[[c for c in cols if c in b.columns]])
    print("\n判据：PSR>0.95 且 DSR>0.95 才算真的显著；"
          "最小样本量 > 实际样本数 = 数据不够，结论不可信")
    if args.random:
        print("\n=== 随机信号置换检验 ===")
        rows = []
        for n in args.random:
            if n in sigs:
                r = random_signal_test(df, sigs[n], n_iter=args.iter)
                r["策略"] = n
                rows.append(r)
        if rows:
            out = pd.DataFrame(rows)
            front = ["策略", "真实夏普", "随机夏普均值", "随机夏普95分位",
                     "真实持仓占比", "随机持仓占比", "p值", "结论"]
            _print_df(out[[c for c in front if c in out.columns]])


def cmd_portfolio(args):
    """多标的组合回测。"""
    from .data import get_data
    from .strategies import create
    from .portfolio import run_portfolio, compare_schemes, diversification_benefit
    data = {s: get_data(s, duration=args.duration) for s in args.symbols}
    strat = create(args.strategy) if args.strategy != "买入持有" else None
    if strat is None:
        signals = {s: pd.Series(1.0, index=d.index) for s, d in data.items()}
        label = "买入持有"
    else:
        signals = {s: strat.signals(d) for s, d in data.items()}
        label = args.strategy
    print(f"标的 {len(data)} 个 | 策略 {label}")
    cmp_df = compare_schemes(data, signals)
    _print_df(cmp_df)
    best = cmp_df.sort_values("夏普比率", ascending=False).iloc[0]["权重方案"]
    print(f"\n最优权重方案：{best}")
    res = run_portfolio(data, signals, name=f"{label}/{best}", scheme=str(best))
    print()
    print(res.summary())
    print("\n逐标的贡献:")
    _print_df(res.contributions.to_frame("收益贡献"))
    print("\n分散化收益（组合 vs 单标的平均）:")
    _print_df(diversification_benefit(res, data, signals))
    if args.save:
        p = Path("results") / f"portfolio_{label}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        res.weights.to_csv(p, encoding="utf-8-sig")
        print(f"\n权重已保存: {p}")


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


def cmd_web(args):
    """启动 TradingView 风格的 Web 展示终端。"""
    from .web import run_server
    print(f"启动 Web 终端： http://{args.host}:{args.port}")
    print("（Ctrl+C 停止）")
    run_server(host=args.host, port=args.port)


def cmd_paper(args):
    """模拟盘自动交易监控（默认 dry-run，绝不真发单）。"""
    from .live import PaperConfig, PaperTrader, default_paper_config
    from .risk import RiskOverlay, PRESETS

    if args.preset:
        cfg = default_paper_config(args.name or "adx_paper")
        if args.symbols:
            cfg.symbols = args.symbols
        if args.strategy:
            cfg.strategy = args.strategy
    else:
        cfg = PaperConfig(
            name=args.name or "paper",
            symbols=args.symbols or ["SPY", "QQQ"],
            strategy=args.strategy or "ADX趋势过滤",
            scheme=args.scheme,
            capital=args.capital,
            dry_run=not args.execute,
        )
    if args.overlay and args.overlay != "无风控":
        cfg.overlay = PRESETS.get(args.overlay, RiskOverlay(name=args.overlay))

    dry = not args.execute
    if args.execute and not args.yes:
        print("危险：这会真实发单！请加 --yes 确认，或去掉 --execute 先看计划。")
        return

    trader = PaperTrader(cfg)
    print(f"配置: 策略={cfg.strategy} 标的={cfg.symbols} 资金={cfg.capital:,.0f} "
          f"权重={cfg.scheme} 风控={cfg.overlay.describe() if cfg.overlay else '无'} "
          f"dry_run={dry}")
    if args.daemon:
        trader.run_daemon(interval=args.interval, max_rounds=args.rounds,
                          ignore_hours=args.ignore_hours, execute=args.execute)
    else:
        r = trader.check(execute=args.execute)
        print(PaperTrader.format(r))
    print(f"\n状态目录: {trader.dir}")


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

    sg = sub.add_parser("significance", help="统计显著性体检")
    sg.add_argument("symbol")
    sg.add_argument("--strategy", nargs="+", default=None)
    sg.add_argument("--random", nargs="*", default=None,
                    help="对指定策略做随机信号置换检验")
    sg.add_argument("--iter", type=int, default=300)
    sg.add_argument("--duration", default="5 Y")
    sg.set_defaults(func=cmd_significance)

    pf = sub.add_parser("portfolio", help="多标的组合回测")
    pf.add_argument("symbols", nargs="+")
    pf.add_argument("--strategy", default="ADX趋势过滤")
    pf.add_argument("--duration", default="5 Y")
    pf.add_argument("--save", action="store_true")
    pf.set_defaults(func=cmd_portfolio)

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

    w = sub.add_parser("web", help="启动 Web 展示终端")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8770)
    w.set_defaults(func=cmd_web)

    pp = sub.add_parser("paper", help="模拟盘自动交易监控（默认 dry-run）")
    pp.add_argument("--name", default=None)
    pp.add_argument("--symbols", nargs="*", default=None)
    pp.add_argument("--strategy", default=None)
    pp.add_argument("--scheme", default="equal")
    pp.add_argument("--capital", type=float, default=100_000)
    pp.add_argument("--overlay", default=None,
                    help="风控方案：无风控/ATR移动止损/固定止损8%/ATR止损+止盈/"
                         "波动率目标15%/波动目标+ATR止损/全套风控")
    pp.add_argument("--preset", action="store_true",
                    help="用回测结论驱动的默认配置（ADX趋势过滤+ATR止损，8标的）")
    pp.add_argument("--daemon", action="store_true", help="常驻循环")
    pp.add_argument("--interval", type=int, default=3600)
    pp.add_argument("--rounds", type=int, default=None)
    pp.add_argument("--ignore-hours", action="store_true", help="忽略交易时段限制")
    pp.add_argument("--execute", action="store_true", help="真实发单（默认只出计划）")
    pp.add_argument("--yes", action="store_true")
    pp.set_defaults(func=cmd_paper)

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
