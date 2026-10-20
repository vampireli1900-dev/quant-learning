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


def _to_f(v) -> float:
    """把可能是 None / NaN / 字符串的值安全转成 float（失败返回 NaN）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


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

    # 落库：把这次显著性体检的结论写进 model_registry
    try:
        from .db import register_model
        for _, r in b.iterrows():
            psr = _to_f(r.get("PSR(>0)"))
            dsr = _to_f(r.get("DSR"))
            register_model(strategy=str(r.get("策略")),
                           symbol=str(args.symbol).upper(),
                           source_experiment=f"significance/{args.symbol}",
                           sharpe=_to_f(r.get("夏普比率")), psr=psr, dsr=dsr,
                           selected=bool(psr > 0.95 and dsr > 0.95),
                           note="来自 CLI significance 显著性体检")
        print("  → 已写入数据库 model_registry")
    except Exception as e:                                     # noqa: BLE001
        print(f"  [db] 写入 model_registry 失败：{str(e)[:80]}")
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


# ---------------------------------------------------------------------------
# 数据库（SQL）子命令
# ---------------------------------------------------------------------------
def cmd_db_init(args):
    """建库建表 / 重置 / 查看连接信息。"""
    from .db import db_location, db_size_bytes, init_db, reset_db, is_sqlite
    from .db.schema import TABLE_GROUPS

    if args.reset:
        if not args.yes:
            print("危险：--reset 会清空所有数据！确认请加 --yes")
            return
        engine = reset_db()
        print(f"已重置数据库：{db_location()}")
    else:
        engine = init_db()

    print(f"数据库位置 : {db_location()}")
    print(f"方言       : {engine.dialect.name}")
    size = db_size_bytes()
    if size is not None:
        print(f"文件大小   : {size / 1024 / 1024:.2f} MB")
    print("\n表结构：")
    for group, names in TABLE_GROUPS.items():
        print(f"  {group}")
        for n in names:
            print(f"    - {n}")
    print("\n提示：设置环境变量 QP_DATABASE_URL 即可切换到 PostgreSQL / MySQL，"
          "无需改动任何业务代码。")


def cmd_db_stats(args):
    """各表行数统计。"""
    from .db import table_stats
    df = table_stats()
    if not len(df):
        print("（数据库为空或不可用，先运行 db-init）")
        return
    _print_df(df)
    print(f"\n合计 {int(df['行数'].sum()):,} 行")


def cmd_db_query(args):
    """执行只读 SQL；不给 --sql 时进入交互模式。"""
    from .db import example_queries, run_sql

    def _run(sql: str):
        try:
            r = run_sql(sql, limit=args.limit)
        except Exception as e:                                   # noqa: BLE001
            print(f"[错误] {e}")
            return
        if not r["columns"]:
            print("（无结果）")
            return
        df = pd.DataFrame(r["rows"], columns=r["columns"])
        print(df.to_string())
        print(f"\n{r['rowcount']} 行 | {r['elapsed_ms']} ms")

    if args.list:
        for i, q in enumerate(example_queries(), 1):
            print(f"[{i}] {q['名称']}\n    {q['sql']}\n")
        return

    if args.example:
        qs = example_queries()
        if not (1 <= args.example <= len(qs)):
            print(f"示例编号需在 1..{len(qs)}")
            return
        print(f"-- {qs[args.example - 1]['名称']}")
        _run(qs[args.example - 1]["sql"])
        return

    if args.sql:
        _run(args.sql)
        return

    print("进入 SQL 交互模式（只读；输入 exit 退出，sample 查看示例）")
    while True:
        try:
            line = input("sql> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", "q"):
            break
        if line.lower() == "sample":
            for i, q in enumerate(example_queries(), 1):
                print(f"[{i}] {q['名称']}")
            continue
        if line.lower().startswith("run "):
            try:
                line = example_queries()[int(line.split()[1]) - 1]["sql"]
            except Exception:                                    # noqa: BLE001
                print("用法：run <示例编号>")
                continue
        _run(line)


def cmd_db_import(args):
    """把 data_cache 下的 CSV 缓存导入 bars 表。"""
    from pathlib import Path
    from .db.migrate import import_cache
    df = import_cache(Path(args.dir) if args.dir else None,
                      refresh=args.refresh)
    if not len(df):
        print("没有导入任何文件")
        return
    ok = df[df["状态"] == "已导入"]
    print(f"\n导入完成：{len(ok)}/{len(df)} 个文件，"
          f"共 {int(ok['行数'].sum()):,} 行行情写入数据库")


def cmd_db_coverage(args):
    """行情覆盖情况：每个标的/周期多少根、起止时间。"""
    from .db import bar_coverage
    _print_df(bar_coverage())


def cmd_db_ingests(args):
    """API 抓取日志。"""
    from .db import recent_ingests
    _print_df(recent_ingests(limit=args.limit))


def cmd_db_features(args):
    """构建/查看训练集（落库到 feature_sets / features）。"""
    from .db.features import build_training_set, describe_feature_set
    from .db import list_feature_sets

    if args.describe:
        _print_df(describe_feature_set(args.describe))
        return
    if args.list:
        _print_df(list_feature_sets())
        return

    build_training_set(symbols=args.symbols, name=args.name,
                       duration=args.duration, bar_size=args.bar_size,
                       horizon=args.horizon, label_mode=args.label,
                       description=args.desc or "",
                       train_ratio=args.train_ratio,
                       from_db=not args.no_from_db)


def cmd_db_register(args):
    """
    把实验结论同步进 model_registry（自动按 PSR/DSR 门槛标记入选）。

    这一步是"研究 → 决策"的落库闭环：回测指标留在 backtest_metrics，
    最终选用哪些模型、为什么选，记在 model_registry，供实盘脚本读取。
    """
    from .db import list_models, sync_model_registry

    df = sync_model_registry(experiment=args.experiment,
                             psr_min=args.psr_min, dsr_min=args.dsr_min,
                             select=not args.no_select)
    if not len(df):
        print("（库里还没有回测指标，先跑一次 experiment）")
        return
    _print_df(df)
    n = int(df["入选"].sum()) if "入选" in df else 0
    print(f"\n共 {len(df)} 个模型，其中 {n} 个达标入选 "
          f"(PSR>{args.psr_min} 且 DSR>{args.dsr_min})")
    if args.show:
        print("\n当前模型注册表：")
        _print_df(list_models())


def cmd_db_orders(args):
    """查看委托记录 / 账户与持仓快照。"""
    from .db import list_orders, account_curve

    if args.account:
        _print_df(account_curve(limit=args.limit))
        return
    _print_df(list_orders(limit=args.limit, symbol=args.symbol))


def cmd_db_export(args):
    """把库里的行情导出成 CSV。"""
    from pathlib import Path
    from .db.migrate import export_csv
    export_csv(args.symbol, bar_size=args.bar_size,
               out=Path(args.out) if args.out else None)


def cmd_db_dump(args):
    """导出建表 DDL（可直接写入作业报告 / 在别的库执行）。"""
    from pathlib import Path
    from sqlalchemy.schema import CreateIndex, CreateTable
    from .db import TABLE_COMMENTS, TABLE_GROUPS, Base, db_location, get_engine

    eng = get_engine()
    out = Path(args.out)
    if out.parent != Path(""):
        out.parent.mkdir(parents=True, exist_ok=True)

    group_of = {t: g for g, names in TABLE_GROUPS.items() for t in names}

    buf = [
        "-- ============================================================",
        "-- quant_learning · 量化交易模型实验平台 —— 数据库结构（DDL）",
        f"-- 由 ORM 自动生成，请勿手工编辑；方言：{eng.dialect.name}",
        f"-- 当前库位置：{db_location()}",
        "-- ============================================================",
        "",
    ]

    tables = Base.metadata.sorted_tables      # 已按外键依赖拓扑排序
    by_name = {t.name: t for t in tables}
    ordered: list = []
    for g, names in TABLE_GROUPS.items():     # 按业务分层输出，便于阅读
        ordered += [by_name[n] for n in names if n in by_name]
    ordered += [t for t in tables if t not in ordered]

    current_group = None
    for t in ordered:
        g = group_of.get(t.name, "其它")
        if g != current_group:
            current_group = g
            buf += [f"-- ---------------- {g} ----------------", ""]
        if t.name in TABLE_COMMENTS:
            buf.append(f"-- {TABLE_COMMENTS[t.name]}")
        buf.append(str(CreateTable(t).compile(eng)).strip().rstrip(";") + ";")
        for idx in sorted(t.indexes, key=lambda i: i.name or ""):
            buf.append(str(CreateIndex(idx).compile(eng)).strip().rstrip(";") + ";")
        buf.append("")

    out.write_text("\n".join(buf), encoding="utf-8")
    print(f"已导出 DDL → {out}（{len(tables)} 张表，{out.stat().st_size} 字节）")


def cmd_board(args):
    """终端打印账户实时看板：净值 / 现金 / 可用 / 持仓估值 / 盈亏。"""
    from .live.dashboard import account_board

    d = account_board(record=not args.no_record, use_cache=False)
    if not d.get("ok"):
        print(f"未连接：{d.get('error')}")
        return

    t = d.get("totals") or {}
    cur = t.get("基准币种") or ""

    def n(k, dec=0):
        v = t.get(k)
        return "—" if v is None else f"{v:,.{dec}f}"

    def s(k, dec=0):
        v = t.get(k)
        if v is None:
            return "—"
        return ("+" if v > 0 else "") + f"{v:,.{dec}f}"

    def pc(v, dec=2):
        if v is None:
            return "—"
        return ("+" if v > 0 else "") + f"{v * 100:.{dec}f}%"

    print("=" * 58)
    print(f"  账户实时看板   {(d.get('account') or {}).get('accounts', '—')}")
    print(f"  {d.get('ts', '')}")
    print("=" * 58)
    print(f"  账户净值   {n('净值')} {cur}")
    print(f"  账面现金   {n('现金')} {cur}   (占净值 {pc(t.get('现金占比'), 1)})")
    print(f"  可用资金   {n('可用资金')} {cur}")
    print(f"  购买力     {n('购买力')} {cur}")
    print(f"  持仓市值   {n('持仓市值')} {cur}   ({t.get('持仓数', 0)} 个持仓)")
    print(f"  浮动盈亏   {s('持仓盈亏')} {cur}   (按成本价)")
    print(f"  当日盈亏   {s('当日盈亏')} {cur}")
    print(f"  已实现盈亏 {s('已实现盈亏')} {cur}")
    print(f"  杠杆       {n('杠杆', 2)}×     汇率 USD/{cur} {n('汇率', 4)}")
    print("-" * 58)

    rows = d.get("rows") or []
    if rows:
        print(f"  {'标的':<8}{'方向':<5}{'股数':>8}{'成本价':>10}{'现价':>10}"
              f"{'市值':>12}{'浮动盈亏':>12}{'盈亏%':>9}{'权重':>8}")
        for r in rows:
            print(f"  {r['标的']:<8}{r['方向']:<5}{r['股数']:>8.0f}"
                  f"{r['成本价']:>10.2f}{r['现价']:>10.2f}{r['市值']:>12.2f}"
                  f"{r['浮动盈亏']:>12.2f}{pc(r['盈亏%']):>9}{pc(r['权重%'], 1):>8}")
    else:
        print("  当前无持仓")
    print("=" * 58)


def cmd_web(args):
    """启动 TradingView 风格的 Web 展示终端（桌面 / 与移动端 /m 同端口）。"""
    from .web import run_server
    print(f"启动 Web 终端： http://{args.host}:{args.port}")
    if args.host == "0.0.0.0":
        # 手机要访问，就得绑 0.0.0.0；把局域网地址打出来方便直接扫
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan = s.getsockname()[0]
            s.close()
        except OSError:
            lan = "<本机IP>"
        print(f"  桌面版： http://{lan}:{args.port}/")
        print(f"  移动版： http://{lan}:{args.port}/m   （手机同 Wi-Fi 直接打开）")
    else:
        print(f"  移动版： http://{args.host}:{args.port}/m")
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

    # ---------------- 数据库 ----------------
    d = sub.add_parser("db-init", help="初始化数据库（建表）")
    d.add_argument("--reset", action="store_true", help="清空并重建（危险）")
    d.add_argument("--yes", action="store_true")
    d.set_defaults(func=cmd_db_init)

    ds = sub.add_parser("db-stats", help="各表行数统计")
    ds.set_defaults(func=cmd_db_stats)

    dq = sub.add_parser("db-query", help="执行只读 SQL（不给 --sql 则进入交互模式）")
    dq.add_argument("--sql", default=None)
    dq.add_argument("--limit", type=int, default=500)
    dq.add_argument("--list", action="store_true", help="列出示例查询")
    dq.add_argument("--example", type=int, default=None, help="执行第 N 个示例查询")
    dq.set_defaults(func=cmd_db_query)

    di = sub.add_parser("db-import", help="把 CSV 缓存导入数据库")
    di.add_argument("--dir", default=None, help="缓存目录，默认 data_cache/")
    di.add_argument("--refresh", action="store_true")
    di.set_defaults(func=cmd_db_import)

    dc = sub.add_parser("db-coverage", help="行情覆盖情况")
    dc.set_defaults(func=cmd_db_coverage)

    dl = sub.add_parser("db-ingests", help="API 抓取日志")
    dl.add_argument("--limit", type=int, default=50)
    dl.set_defaults(func=cmd_db_ingests)

    df_ = sub.add_parser("db-features", help="构建/查看训练集")
    df_.add_argument("--name", default="trainset")
    df_.add_argument("--symbols", nargs="*",
                     default=["SPY", "QQQ", "AAPL", "MSFT", "NVDA"])
    df_.add_argument("--duration", default="5 Y")
    df_.add_argument("--bar-size", default="1 day")
    df_.add_argument("--horizon", type=int, default=1, help="标签预测未来几根K线")
    df_.add_argument("--label", choices=["ret", "sign"], default="ret")
    df_.add_argument("--train-ratio", type=float, default=0.7)
    df_.add_argument("--desc", default="")
    df_.add_argument("--no-from-db", action="store_true",
                     help="不从数据库读，直接走数据层（IB/yfinance）")
    df_.add_argument("--list", action="store_true", help="列出已有训练集")
    df_.add_argument("--describe", default=None, help="查看某训练集的字段统计")
    df_.set_defaults(func=cmd_db_features)

    dr = sub.add_parser("db-register",
                        help="把实验结论同步进模型注册表（按 PSR/DSR 门槛标记入选）")
    dr.add_argument("--experiment", default=None,
                    help="实验名；不填则汇总全部实验")
    dr.add_argument("--psr-min", type=float, default=0.95)
    dr.add_argument("--dsr-min", type=float, default=0.95)
    dr.add_argument("--no-select", action="store_true",
                    help="只同步指标，不自动标记入选")
    dr.add_argument("--show", action="store_true", help="同步后打印完整注册表")
    dr.set_defaults(func=cmd_db_register)

    do = sub.add_parser("db-orders", help="委托记录 / 账户快照")
    do.add_argument("--limit", type=int, default=100)
    do.add_argument("--symbol", default=None)
    do.add_argument("--account", action="store_true", help="查看账户净值曲线")
    do.set_defaults(func=cmd_db_orders)

    de = sub.add_parser("db-export", help="导出库里行情到 CSV")
    de.add_argument("symbol")
    de.add_argument("--bar-size", default="1 day")
    de.add_argument("--out", default=None)
    de.set_defaults(func=cmd_db_export)

    dd = sub.add_parser("db-dump", help="导出建表 DDL 到 .sql 文件")
    dd.add_argument("--out", default="docs/schema.sql")
    dd.set_defaults(func=cmd_db_dump)

    ab = sub.add_parser("board", help="打印账户实时看板（持仓/余额/估值）")
    ab.add_argument("--no-record", action="store_true",
                    help="本次不写数据库快照")
    ab.set_defaults(func=cmd_board)

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
