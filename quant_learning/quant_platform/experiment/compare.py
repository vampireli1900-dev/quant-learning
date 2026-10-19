# -*- coding: utf-8 -*-
"""
experiment/compare.py —— 对比、排名与稳健性诊断
==============================================
回答三个层次的问题：

    1. 谁最好？        综合评分排名（风险调整后收益为主）
    2. 好得稳定吗？    跨标的、跨时间段的一致性（单标的好看不算数）
    3. 是不是一回事？  策略间收益相关性（避免"以为分散了，其实都是同一类赌注"）
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..backtest.metrics import rank_strategies
from ..config import CONFIG
from .runner import ExperimentResult


# ---------------------------------------------------------------------------
# 1) 排名
# ---------------------------------------------------------------------------
def rank(result: ExperimentResult, weights: Optional[dict] = None) -> pd.DataFrame:
    """对"策略×标的平均表现"做综合评分排名。"""
    bs = result.by_strategy
    if not len(bs):
        return pd.DataFrame()
    # rank_strategies 期望"指标为行、策略为列"
    mat = bs.drop(columns=[c for c in ("标的数",) if c in bs.columns], errors="ignore").T
    ranked = rank_strategies(mat, weights)
    return ranked


# ---------------------------------------------------------------------------
# 2) 稳健性
# ---------------------------------------------------------------------------
def stability(result: ExperimentResult) -> pd.DataFrame:
    """
    稳健性诊断：
        sharpe_std    跨标的夏普标准差（越小越稳）
        sharpe_min    最差标的上的夏普（下限思维）
        positive_rate 有多少比例的标的上夏普>0
        worst_drawdown 跨标的最大回撤极值
    """
    d = result.detail
    if not len(d) or "夏普比率" not in d.columns:
        return pd.DataFrame()
    g = d.groupby("策略")
    out = pd.DataFrame({
        "夏普均值": g["夏普比率"].mean(),
        "夏普标准差": g["夏普比率"].std(),
        "夏普最差": g["夏普比率"].min(),
        "夏普最好": g["夏普比率"].max(),
        "正收益标的占比": g["夏普比率"].apply(lambda s: float((s > 0).mean())),
    })
    if "最大回撤" in d.columns:
        out["最差回撤"] = g["最大回撤"].min()
    if "年化收益率" in d.columns:
        out["年化均值"] = g["年化收益率"].mean()
        out["年化最差"] = g["年化收益率"].min()
    return out.sort_values("夏普均值", ascending=False)


# ---------------------------------------------------------------------------
# 3) 相关性
# ---------------------------------------------------------------------------
def strategy_correlation(result: ExperimentResult,
                         method: str = "pearson") -> pd.DataFrame:
    """策略间日收益相关性矩阵（在所有标的上拼接后计算）。"""
    if not result.equity_curves:
        return pd.DataFrame()
    rets = {}
    for key, eq in result.equity_curves.items():
        name = key.split("|")[0]
        r = eq.pct_change().dropna()
        if len(r):
            rets.setdefault(name, []).append(r)
    merged = {}
    for name, parts in rets.items():
        merged[name] = pd.concat(parts).dropna()
    df = pd.DataFrame(merged)
    return df.corr(method=method)


# ---------------------------------------------------------------------------
# 4) 市场状态（regime）分解
# ---------------------------------------------------------------------------
def regime_analysis(result: ExperimentResult,
                    benchmark_curves: Optional[Dict[str, pd.Series]] = None) -> pd.DataFrame:
    """
    把每个标的的历史按"基准 6 个月收益"分成 上涨/震荡/下跌 三种状态，
    看策略在不同状态下的表现——用于判断策略到底赚的是什么钱。
    """
    curves = benchmark_curves or result.benchmark_curves
    if not curves or not result.equity_curves:
        return pd.DataFrame()

    rows = []
    for key, eq in result.equity_curves.items():
        name, sym = key.split("|", 1)
        bench = curves.get(sym)
        if bench is None:
            continue
        common = eq.index.intersection(bench.index)
        if len(common) < 60:
            continue
        b_ret = bench.reindex(common).pct_change().fillna(0)
        roll = bench.reindex(common)
        window = 126
        # 用过去 window 天的基准收益划分状态
        state = (roll / roll.shift(window) - 1).shift(1)
        buckets = {
            "上涨(基准6月>10%)": state > 0.10,
            "震荡(-10%~10%)": (state >= -0.10) & (state <= 0.10),
            "下跌(基准6月<-10%)": state < -0.10,
        }
        r = eq.reindex(common).pct_change().fillna(0)
        for label, mask in buckets.items():
            sub = r[mask.fillna(False)]
            if len(sub) < 20:
                continue
            ann = (1 + sub).prod() ** (252 / len(sub)) - 1
            rows.append({"策略": name, "标的": sym, "市场状态": label,
                         "年化收益": ann, "天数": len(sub),
                         "夏普": sub.mean() / sub.std() * np.sqrt(252) if sub.std() > 0 else np.nan})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.groupby(["策略", "市场状态"])[["年化收益", "夏普"]].mean().reset_index()


# ---------------------------------------------------------------------------
# 5) 自动结论
# ---------------------------------------------------------------------------
def conclusions(result: ExperimentResult, top_k: int = 3) -> List[str]:
    """根据实验结果自动生成可读结论（用于报告顶部）。"""
    out: List[str] = []
    bs = result.by_strategy
    if not len(bs):
        return ["实验结果为空，无法给出结论。"]

    def _best(col: str, ascending: bool = False):
        if col not in bs.columns:
            return None
        s = bs[col].dropna()
        if not len(s):
            return None
        return s.idxmax() if not ascending else s.idxmin()

    best = _best("夏普比率")
    if best is not None:
        out.append(f"**综合夏普最高**：{best}（平均夏普 {bs.loc[best, '夏普比率']:.2f}）")
    cal = _best("卡玛比率")
    if cal is not None:
        out.append(f"**风险调整(卡玛)最优**：{cal}（卡玛 {bs.loc[cal, '卡玛比率']:.2f}）")
    dd = _best("最大回撤")           # 回撤为负，最大=最浅
    if dd is not None:
        out.append(f"**回撤控制最好**：{dd}（平均最大回撤 {bs.loc[dd, '最大回撤']:.2%}）")

    st = stability(result)
    if len(st) and "夏普标准差" in st.columns:
        sstd = st["夏普标准差"].dropna()
        if len(sstd):
            steady = sstd.idxmin()
            out.append(f"**跨标的最稳定**：{steady}（夏普标准差 {sstd.loc[steady]:.2f}）")

    # 是否跑赢基准
    d = result.detail
    if "超额年化" in d.columns:
        beat = d[d["超额年化"] > 0].groupby("策略").size()
        total = d.groupby("策略").size()
        ratio = (beat / total).dropna().sort_values(ascending=False)
        if len(ratio):
            s = "、".join(f"{k}({v:.0%})" for k, v in ratio.head(3).items())
            out.append(f"**跑赢买入持有比例最高**：{s}")

    # 过拟合警告
    if result.walkforward:
        degraded = []
        for (name, sym), wf in result.walkforward.items():
            ism = np.nanmean(wf.in_sample_scores) if wf.in_sample_scores else np.nan
            osm = np.nanmean(wf.out_sample_scores) if wf.out_sample_scores else np.nan
            if ism == ism and osm == osm and ism > 0.2 and osm < 0.5 * ism:
                degraded.append(f"{name}@{sym}")
        if degraded:
            out.append("⚠️ **疑似过拟合**（样本外夏普 < 样本内一半）：" + "、".join(degraded[:6]))
        else:
            out.append("✅ 走前分析中，未发现样本外表现大幅劣化的明显过拟合信号")

    out.append(
        "> 提示：本平台所有结果均基于历史数据回测，回测≠实盘。"
        "样本外/走前验证只能降低过拟合风险，不能保证未来有效。")
    return out
