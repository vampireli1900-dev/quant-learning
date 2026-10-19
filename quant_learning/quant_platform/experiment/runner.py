# -*- coding: utf-8 -*-
"""
experiment/runner.py —— 实验编排
================================
一次实验 = 一组策略 × 一组标的 × 一段历史 × 一组回测假设。

产出：
    * 明细表：每一行是 (策略, 标的) 的一次完整回测
    * 汇总表：每个策略跨标的的平均表现
    * （可选）走前分析结果：每个 (策略, 标的) 的样本外净值曲线
    * （可选）参数寻优与过拟合体检

所有结果都可序列化（CSV/JSON），保证可复现、可追溯。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from ..backtest.engine import BacktestResult, backtest_buy_hold, run_backtest
from ..backtest.metrics import compute_metrics, rank_strategies
from ..backtest.optimizer import grid_search, overfitting_score
from ..backtest.walkforward import WalkForwardResult, walk_forward
from ..config import CONFIG, RESULTS_DIR, ensure_dirs
from ..data import get_data
from ..strategies import create, list_strategies

ensure_dirs()


@dataclass
class ExperimentConfig:
    """实验配置（也可由 YAML 生成）。"""
    name: str = "experiment"
    symbols: List[str] = field(default_factory=lambda: ["SPY"])
    strategies: List[Union[str, Dict[str, Any]]] = field(default_factory=list)
    duration: str = "5 Y"
    bar_size: str = "1 day"
    data_source: str = "auto"
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.0005
    slippage_rate: float = 0.0005
    include_benchmark: bool = True
    run_walkforward: bool = False
    wf_train_bars: int = 756
    wf_test_bars: int = 126
    wf_strategies: List[str] = field(default_factory=list)   # 为空=全部（除基准）
    run_optimization: bool = False
    opt_metric: str = "夏普比率"
    opt_top_n: int = 5
    opt_strategies: List[str] = field(default_factory=list)  # 为空=全部有参数空间者
    opt_symbols: List[str] = field(default_factory=list)     # 为空=全部标的
    output_dir: str = str(RESULTS_DIR)

    # ---- YAML ----
    @classmethod
    def from_yaml(cls, path: str | Path) -> "ExperimentConfig":
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


@dataclass
class ExperimentResult:
    config: ExperimentConfig
    detail: pd.DataFrame = field(default_factory=pd.DataFrame)   # (策略,标的) 明细
    by_strategy: pd.DataFrame = field(default_factory=pd.DataFrame)  # 策略跨标的汇总
    by_symbol: pd.DataFrame = field(default_factory=pd.DataFrame)
    equity_curves: Dict[str, pd.Series] = field(default_factory=dict)  # key: 策略|标的
    benchmark_curves: Dict[str, pd.Series] = field(default_factory=dict)
    trades: Dict[str, pd.DataFrame] = field(default_factory=dict)     # key: 策略|标的
    walkforward: Dict[Tuple[str, str], WalkForwardResult] = field(default_factory=dict)
    optimizations: Dict[str, pd.DataFrame] = field(default_factory=dict)
    data_info: Dict[str, dict] = field(default_factory=dict)
    runtime_sec: float = 0.0

    @property
    def output_dir(self) -> Path:
        p = Path(self.config.output_dir) / self.config.name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def save(self) -> Path:
        """落盘：CSV + JSON 摘要。"""
        out = self.output_dir
        if len(self.detail):
            self.detail.to_csv(out / "detail.csv", encoding="utf-8-sig")
        if len(self.by_strategy):
            self.by_strategy.to_csv(out / "by_strategy.csv", encoding="utf-8-sig")
        if self.equity_curves:
            eq = pd.DataFrame(self.equity_curves)
            eq.to_csv(out / "equity_curves.csv", encoding="utf-8-sig")
        if self.benchmark_curves:
            pd.DataFrame(self.benchmark_curves).to_csv(out / "benchmark.csv", encoding="utf-8-sig")
        if self.walkforward:
            rows = []
            for (s, sym), wf in self.walkforward.items():
                rows.append({
                    "策略": s, "标的": sym, "段数": len(wf.segments),
                    "样本内均夏普": np.nanmean(wf.in_sample_scores) if wf.in_sample_scores else np.nan,
                    "样本外均夏普": np.nanmean(wf.out_sample_scores) if wf.out_sample_scores else np.nan,
                    **{f"样本外{k}": v for k, v in wf.metrics.items()
                       if k in ("年化收益率", "夏普比率", "最大回撤", "卡玛比率")},
                })
            pd.DataFrame(rows).to_csv(out / "walkforward.csv", encoding="utf-8-sig", index=False)
            # 样本外净值
            oos_eq = {f"{s}|{sym}": wf.oos_equity for (s, sym), wf in self.walkforward.items()
                      if wf.oos_equity is not None}
            if oos_eq:
                pd.DataFrame(oos_eq).to_csv(out / "walkforward_equity.csv", encoding="utf-8-sig")
        for k, df in self.optimizations.items():
            # Windows 文件名不允许 | \ / : * ? " < >，需清洗
            safe = "".join(ch if ch not in '|\\/:*?"<>' else "_" for ch in str(k))
            try:
                df.to_csv(out / f"opt_{safe}.csv", encoding="utf-8-sig", index=False)
            except Exception as e:                           # noqa: BLE001
                print(f"[保存失败] 寻优结果 {k}: {e}")

        # 结果对象整体序列化：便于以后不重跑实验就重新生成/调整报告
        try:
            import pickle
            with open(out / "result.pkl", "wb") as f:
                pickle.dump(self, f)
        except Exception:
            pass

        summary = {
            "experiment": self.config.name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "runtime_sec": round(self.runtime_sec, 2),
            "symbols": self.config.symbols,
            "strategies": [s if isinstance(s, str) else s.get("name") for s in self.config.strategies],
            "data_info": self.data_info,
            "assumptions": {
                "initial_capital": self.config.initial_capital,
                "commission_rate": self.config.commission_rate,
                "slippage_rate": self.config.slippage_rate,
            },
        }
        # ---- 落库到 SQL 数据库（旁路：失败不影响文件产出） ----
        try:
            dbcfg = getattr(CONFIG, "db", None)
            if dbcfg and dbcfg.enabled and dbcfg.persist_experiments:
                from ..db.repository import save_experiment
                eid = save_experiment(self, equity_stride=max(1, int(dbcfg.equity_stride)))
                if eid:
                    summary["db_experiment_id"] = eid
        except Exception as e:                                    # noqa: BLE001
            print(f"[db] 实验落库失败（文件已保存）: {type(e).__name__}: {str(e)[:80]}")

        with open(out / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
        return out


def _strategy_spec(item: Union[str, Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """支持 "策略名" 或 {"name": ..., "params": {...}} 两种写法。"""
    if isinstance(item, str):
        return item, {}
    name = item.get("name")
    if not name:
        raise ValueError(f"策略配置缺少 name: {item}")
    return name, item.get("params", {}) or {}


def run_experiment(cfg: ExperimentConfig, verbose: bool = True) -> ExperimentResult:
    """执行一次完整实验。"""
    t0 = time.time()
    res = ExperimentResult(config=cfg)
    strategies = [_strategy_spec(s) for s in cfg.strategies] or [(n, {}) for n in list_strategies()]

    bt_kwargs = dict(initial_capital=cfg.initial_capital,
                     commission_rate=cfg.commission_rate,
                     slippage_rate=cfg.slippage_rate)

    # ---------- 1) 取数 ----------
    data: Dict[str, pd.DataFrame] = {}
    for sym in cfg.symbols:
        try:
            df = get_data(sym, bar_size=cfg.bar_size, duration=cfg.duration,
                          source=cfg.data_source)
            if df is None or len(df) < 120:
                print(f"[跳过] {sym} 数据不足（{0 if df is None else len(df)} 根）")
                continue
            data[sym] = df
            res.data_info[sym] = {
                "bars": int(len(df)),
                "start": str(df.index[0].date()),
                "end": str(df.index[-1].date()),
                "last_close": float(df["close"].iloc[-1]),
            }
            if verbose:
                print(f"[数据] {sym}: {len(df)} 根 {df.index[0].date()} ~ {df.index[-1].date()}")
        except Exception as e:                                  # noqa: BLE001
            print(f"[数据失败] {sym}: {e}")

    if not data:
        raise RuntimeError("没有任何可用数据，实验终止")

    # ---------- 2) 回测：策略 × 标的 ----------
    rows = []
    for sym, df in data.items():
        # 基准
        bh = backtest_buy_hold(df, name="买入持有", initial_capital=cfg.initial_capital)
        res.benchmark_curves[sym] = bh.equity
        metric_keys = None

        for name, params in strategies:
            try:
                strat = create(name, **params)
                sig = strat.signals(df)
                r = run_backtest(df, sig, name=name, symbol=sym,
                                 params={**strat.params}, **bt_kwargs)
                key = f"{name}|{sym}"
                res.equity_curves[key] = r.equity
                if r.trades is not None and len(r.trades):
                    res.trades[key] = r.trades
                row = {"策略": name, "标的": sym,
                       "参数": json.dumps(strat.params, ensure_ascii=False),
                       **r.metrics}
                # 相对基准
                if "年化收益率" in r.metrics:
                    row["超额年化"] = r.metrics["年化收益率"] - bh.metrics.get("年化收益率", np.nan)
                rows.append(row)
                if verbose:
                    print(f"  ✓ {name:<14} @ {sym:<6} 夏普={r.metrics.get('夏普比率', float('nan')):>6.2f} "
                          f"年化={r.metrics.get('年化收益率', float('nan')):>7.2%} "
                          f"回撤={r.metrics.get('最大回撤', float('nan')):>7.2%}")
            except Exception as e:                              # noqa: BLE001
                print(f"  ✗ {name} @ {sym} 失败: {type(e).__name__}: {str(e)[:90]}")
                rows.append({"策略": name, "标的": sym, "参数": json.dumps(params, ensure_ascii=False),
                             "error": str(e)[:120]})

    res.detail = pd.DataFrame(rows)

    # ---------- 3) 汇总：按策略跨标的平均 ----------
    if len(res.detail):
        num_cols = [c for c in res.detail.columns
                    if c not in ("策略", "标的", "参数", "error")
                    and pd.api.types.is_numeric_dtype(res.detail[c])]
        agg = res.detail.groupby("策略")[num_cols].mean(numeric_only=True)
        agg["标的数"] = res.detail.groupby("策略")["标的"].nunique()
        # 跑赢基准（年化收益 > 基准年化）的标的占比
        if "超额年化" in res.detail.columns:
            win = res.detail.groupby("策略")["超额年化"].apply(lambda s: float((s > 0).mean()))
            agg["跑赢基准占比"] = win
        res.by_strategy = agg.sort_values("夏普比率", ascending=False) if "夏普比率" in agg else agg

        res.by_symbol = res.detail.pivot_table(index="策略", columns="标的",
                                               values="夏普比率", aggfunc="mean")

    # ---------- 4) 走前分析 ----------
    if cfg.run_walkforward:
        wf_names = cfg.wf_strategies or None
        for sym, df in data.items():
            for name, params in strategies:
                if name in ("买入持有",):
                    continue
                if wf_names and name not in wf_names:
                    continue
                try:
                    wf = walk_forward(df, name, train_bars=cfg.wf_train_bars,
                                      test_bars=cfg.wf_test_bars,
                                      metric=cfg.opt_metric, **bt_kwargs)
                    res.walkforward[(name, sym)] = wf
                    if verbose:
                        ism = np.nanmean(wf.in_sample_scores) if wf.in_sample_scores else np.nan
                        osm = np.nanmean(wf.out_sample_scores) if wf.out_sample_scores else np.nan
                        print(f"  [WF] {name:<14} @ {sym:<6} 样本内={ism:>6.2f} 样本外={osm:>6.2f}")
                except Exception as e:                          # noqa: BLE001
                    print(f"  [WF失败] {name} @ {sym}: {str(e)[:80]}")

    # ---------- 5) 参数寻优 + 过拟合体检 ----------
    if cfg.run_optimization:
        opt_syms = cfg.opt_symbols or list(data.keys())
        opt_names = cfg.opt_strategies or None
        for sym, df in data.items():
            if sym not in opt_syms:
                continue
            for name, params in strategies:
                if opt_names and name not in opt_names:
                    continue
                strat = create(name, **params)
                if not strat.param_space:
                    continue
                try:
                    grid = grid_search(df, name, strat.param_space, metric=cfg.opt_metric,
                                       top_n=cfg.opt_top_n, **bt_kwargs)
                    res.optimizations[f"{name}|{sym}"] = grid
                    if verbose and len(grid):
                        best = grid.iloc[0]
                        print(f"  [OPT] {name:<14} @ {sym:<6} 最优{cfg.opt_metric}="
                              f"{best.get('_score', float('nan')):.2f}")
                except Exception as e:                          # noqa: BLE001
                    print(f"  [OPT失败] {name} @ {sym}: {str(e)[:80]}")

    res.runtime_sec = time.time() - t0
    if verbose:
        print(f"\n实验完成，用时 {res.runtime_sec:.1f}s")
    return res
