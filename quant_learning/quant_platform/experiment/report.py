# -*- coding: utf-8 -*-
"""
experiment/report.py —— 实验报告生成（HTML + 图表）
==================================================
把实验结果变成一份"能拿去讨论"的报告：
    * 关键结论（自动生成）
    * 综合排名表
    * 稳健性诊断表
    * 策略相关性矩阵
    * 资金曲线图 / 风险收益散点图 / 市场状态分解图
    * 走前分析的样本内 vs 样本外

图表用 matplotlib 生成后以 base64 内嵌，单个 HTML 文件即可分享。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import RESULTS_DIR
from .compare import conclusions, regime_analysis, stability, strategy_correlation
from .runner import ExperimentResult

# ---------------------------------------------------------------------------
# 中文字体
# ---------------------------------------------------------------------------
def setup_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXian",
                                       "Microsoft JhengHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 110
    plt.rcParams["savefig.dpi"] = 110
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.3
    plt.rcParams["axes.spines.top"] = False
    plt.rcParams["axes.spines.right"] = False
    return plt


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode("ascii")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return f"data:image/png;base64,{data}"


# ---------------------------------------------------------------------------
# 图 1：资金曲线
# ---------------------------------------------------------------------------
def plot_equity(result: ExperimentResult, symbol: str,
                top_n: int = 6, include_benchmark: bool = True) -> Optional[str]:
    keys = [k for k in result.equity_curves if k.endswith(f"|{symbol}")]
    if not keys:
        return None
    # 挑该标的上夏普最高的前 top_n 个策略
    d = result.detail
    if len(d) and "夏普比率" in d.columns:
        sub = d[d["标的"] == symbol].sort_values("夏普比率", ascending=False)
        keep = [f"{r['策略']}|{symbol}" for _, r in sub.head(top_n).iterrows()]
        keys = [k for k in keys if k in keep] or keys[:top_n]

    plt = setup_matplotlib()
    fig, ax = plt.subplots(figsize=(11, 5))
    if include_benchmark and symbol in result.benchmark_curves:
        b = result.benchmark_curves[symbol]
        ax.plot(b.index, b.values / b.iloc[0] * 100, label="买入持有(基准)",
                color="#888888", linewidth=2.2, linestyle="--")
    colors = plt.cm.tab10(np.linspace(0, 1, len(keys)))
    for c, k in zip(colors, keys):
        eq = result.equity_curves[k]
        ax.plot(eq.index, eq.values / eq.iloc[0] * 100, label=k.split("|")[0],
                linewidth=1.5, color=c)
    ax.set_title(f"{symbol} 资金曲线对比（起点=100）", fontsize=13)
    ax.set_ylabel("归一化净值")
    ax.legend(fontsize=8, ncol=2, framealpha=0.9)
    ax.grid(alpha=0.3)
    return _fig_to_base64(fig)


# ---------------------------------------------------------------------------
# 图 2：风险收益散点
# ---------------------------------------------------------------------------
def plot_risk_return(result: ExperimentResult) -> Optional[str]:
    bs = result.by_strategy
    if not len(bs) or "年化波动率" not in bs.columns or "年化收益率" not in bs.columns:
        return None
    plt = setup_matplotlib()
    fig, ax = plt.subplots(figsize=(9.5, 6))
    x = bs["年化波动率"] * 100
    y = bs["年化收益率"] * 100
    sharpe = bs.get("夏普比率", pd.Series(np.nan, index=bs.index))
    sc = ax.scatter(x, y, c=sharpe, cmap="RdYlGn", s=90, edgecolor="#333333",
                    linewidth=0.6, alpha=0.9)
    for name, xi, yi in zip(bs.index, x, y):
        ax.annotate(name, (xi, yi), fontsize=8, xytext=(4, 4),
                    textcoords="offset points")
    ax.axhline(0, color="#999999", linewidth=1)
    ax.set_xlabel("年化波动率 (%)")
    ax.set_ylabel("年化收益率 (%)")
    ax.set_title("风险-收益分布（颜色=夏普比率）", fontsize=13)
    fig.colorbar(sc, ax=ax, label="夏普比率")
    return _fig_to_base64(fig)


# ---------------------------------------------------------------------------
# 图 3：关键指标柱状图
# ---------------------------------------------------------------------------
def plot_metric_bars(result: ExperimentResult, metrics=("夏普比率", "卡玛比率",
                                                        "年化收益率", "最大回撤")) -> Optional[str]:
    bs = result.by_strategy
    if not len(bs):
        return None
    avail = [m for m in metrics if m in bs.columns]
    if not avail:
        return None
    plt = setup_matplotlib()
    n = len(avail)
    fig, axes = plt.subplots(1, n, figsize=(4.2 * n, 5.2))
    if n == 1:
        axes = [axes]
    for ax, m in zip(axes, avail):
        vals = bs[m].sort_values(ascending=(m == "最大回撤"))
        colors = ["#2e7d32" if (v > 0 if m != "最大回撤" else v < 0) else "#c62828"
                  for v in vals.values]
        ax.barh(range(len(vals)), vals.values, color=colors, alpha=0.85)
        ax.set_yticks(range(len(vals)))
        ax.set_yticklabels(vals.index, fontsize=8)
        ax.set_title(m, fontsize=11)
        ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    return _fig_to_base64(fig)


# ---------------------------------------------------------------------------
# 图 4：走前分析（样本外净值）
# ---------------------------------------------------------------------------
def plot_walkforward(result: ExperimentResult, symbol: Optional[str] = None) -> Optional[str]:
    if not result.walkforward:
        return None
    items = [(k, v) for k, v in result.walkforward.items()
             if v.oos_equity is not None and (symbol is None or k[1] == symbol)]
    if not items:
        return None
    plt = setup_matplotlib()
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8))

    # 左：样本外净值
    ax = axes[0]
    for (name, sym), wf in items[:8]:
        eq = wf.oos_equity
        ax.plot(eq.index, eq.values / eq.iloc[0] * 100,
                label=f"{name}@{sym}", linewidth=1.4)
    ax.set_title("走前分析：样本外净值（拼接）", fontsize=12)
    ax.set_ylabel("归一化净值")
    ax.legend(fontsize=7, ncol=2)

    # 右：样本内 vs 样本外夏普
    ax2 = axes[1]
    labels, isv, osv = [], [], []
    for (name, sym), wf in items:
        if wf.in_sample_scores and wf.out_sample_scores:
            labels.append(f"{name}@{sym}")
            isv.append(np.nanmean(wf.in_sample_scores))
            osv.append(np.nanmean(wf.out_sample_scores))
    if labels:
        x = np.arange(len(labels))
        w = 0.38
        ax2.bar(x - w / 2, isv, w, label="样本内", color="#1976d2", alpha=0.85)
        ax2.bar(x + w / 2, osv, w, label="样本外", color="#f57c00", alpha=0.85)
        ax2.set_xticks(x)
        ax2.set_xticklabels(labels, rotation=35, ha="right", fontsize=7)
        ax2.legend(fontsize=9)
        ax2.set_ylabel("平均夏普比率")
        ax2.set_title("样本内 vs 样本外（差距越大越可能过拟合）", fontsize=12)
    fig.tight_layout()
    return _fig_to_base64(fig)


# ---------------------------------------------------------------------------
# 图 5：市场状态分解
# ---------------------------------------------------------------------------
def plot_regime(result: ExperimentResult) -> Optional[str]:
    rg = regime_analysis(result)
    if not len(rg):
        return None
    piv = rg.pivot(index="策略", columns="市场状态", values="年化收益")
    if not len(piv):
        return None
    plt = setup_matplotlib()
    fig, ax = plt.subplots(figsize=(10, max(4, 0.35 * len(piv))))
    piv = piv.reindex(piv.mean(axis=1).sort_values(ascending=False).index)
    x = np.arange(len(piv))
    w = 0.8 / max(1, len(piv.columns))
    colors = ["#c62828", "#9e9e9e", "#2e7d32"]     # 涨红/震荡灰/跌绿 — 注意中国市场习惯
    for i, col in enumerate(piv.columns):
        vals = piv[col].values * 100
        ax.bar(x + i * w, vals, w, label=col, alpha=0.9,
               color=colors[i % len(colors)])
    ax.set_xticks(x + w * (len(piv.columns) - 1) / 2)
    ax.set_xticklabels(piv.index, rotation=35, ha="right", fontsize=8)
    ax.axhline(0, color="#333333", linewidth=0.8)
    ax.set_ylabel("年化收益 (%)")
    ax.set_title("不同市场状态下的表现（看策略到底赚什么钱）", fontsize=12)
    ax.legend(fontsize=8)
    fig.tight_layout()
    return _fig_to_base64(fig)


# ---------------------------------------------------------------------------
# 图 6：策略 × 标的 夏普热力图
# ---------------------------------------------------------------------------
def plot_heatmap(result: ExperimentResult, value: str = "夏普比率") -> Optional[str]:
    d = result.detail
    if not len(d) or value not in d.columns or "标的" not in d.columns:
        return None
    piv = d.pivot_table(index="策略", columns="标的", values=value, aggfunc="mean")
    if not len(piv):
        return None
    piv = piv.reindex(piv.mean(axis=1).sort_values(ascending=False).index)
    plt = setup_matplotlib()
    fig, ax = plt.subplots(figsize=(1.15 * len(piv.columns) + 4, 0.42 * len(piv) + 2.4))
    data = piv.values.astype(float)
    vmax = np.nanmax(np.abs(data)) if np.isfinite(np.nanmax(np.abs(data))) else 1.0
    im = ax.imshow(data, cmap="RdYlGn", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(piv.columns)))
    ax.set_xticklabels(piv.columns, fontsize=9)
    ax.set_yticks(range(len(piv.index)))
    ax.set_yticklabels(piv.index, fontsize=9)
    for i in range(len(piv.index)):
        for j in range(len(piv.columns)):
            v = data[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=8,
                        color="#111111")
    ax.set_title(f"策略 × 标的 {value} 热力图", fontsize=12)
    ax.grid(False)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    return _fig_to_base64(fig)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def _df_to_html(df: pd.DataFrame, float_fmt: str = "{:.2f}") -> str:
    if not len(df):
        return "<p><i>（无数据）</i></p>"
    styled = df.copy()
    for c in styled.columns:
        if pd.api.types.is_float_dtype(styled[c]):
            if "率" in str(c) and c not in ("夏普比率", "索提诺比率", "卡玛比率", "信息比率",
                                            "盈亏比", "Omega", "Beta", "年化换手"):
                styled[c] = styled[c].map(lambda v: "—" if pd.isna(v) else f"{v:.2%}")
            else:
                styled[c] = styled[c].map(lambda v: "—" if pd.isna(v) else float_fmt.format(v))
    return styled.to_html(escape=False, classes="tbl", border=0)


def build_html_report(result: ExperimentResult,
                      output_path: Optional[str] = None,
                      symbol_focus: Optional[str] = None) -> Path:
    """生成完整 HTML 报告，返回文件路径。"""
    out_dir = result.output_dir
    out_path = Path(output_path) if output_path else out_dir / "report.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    focus = symbol_focus or (result.config.symbols[0] if result.config.symbols else None)
    eq_img = plot_equity(result, focus) if focus else None
    rr_img = plot_risk_return(result)
    bar_img = plot_metric_bars(result)
    wf_img = plot_walkforward(result, focus)
    rg_img = plot_regime(result)
    hm_img = plot_heatmap(result)

    rank_tbl = result.by_strategy
    stab_tbl = stability(result)
    corr_tbl = strategy_correlation(result)
    concl = conclusions(result)

    data_rows = "\n".join(
        f"<tr><td>{k}</td><td>{v['bars']}</td><td>{v['start']}</td><td>{v['end']}</td>"
        f"<td>{v['last_close']:.2f}</td></tr>"
        for k, v in result.data_info.items())

    concl_html = "\n".join(f"<li>{c}</li>" for c in concl)

    # 走前汇总表
    wf_rows = ""
    if result.walkforward:
        wf_rows = "<h3>走前分析（Walk-Forward）</h3><table class='tbl'><tr><th>策略</th><th>标的</th><th>段数</th><th>样本内夏普</th><th>样本外夏普</th><th>外/内</th></tr>"
        for (name, sym), wf in result.walkforward.items():
            ism = np.nanmean(wf.in_sample_scores) if wf.in_sample_scores else np.nan
            osm = np.nanmean(wf.out_sample_scores) if wf.out_sample_scores else np.nan
            ratio = osm / ism if (ism and ism == ism and abs(ism) > 1e-9) else np.nan
            wf_rows += (f"<tr><td>{name}</td><td>{sym}</td><td>{len(wf.segments)}</td>"
                        f"<td>{ism:.2f}</td><td>{osm:.2f}</td><td>{ratio:.2f}</td></tr>")
        wf_rows += "</table>"

    # 参数寻优表
    opt_html = ""
    if result.optimizations:
        opt_html = "<h3>参数寻优结果（每种取前 5）</h3>"
        for k, df in result.optimizations.items():
            opt_html += f"<h4>{k}</h4>" + _df_to_html(df.head(5))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>量化模型实验报告 - {result.config.name}</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", -apple-system, sans-serif;
         margin: 32px auto; max-width: 1180px; color: #222; background: #fbfbfc; line-height: 1.65; }}
  h1 {{ border-bottom: 3px solid #1976d2; padding-bottom: 10px; }}
  h2 {{ margin-top: 34px; color: #0d47a1; border-left: 5px solid #1976d2; padding-left: 10px; }}
  h3 {{ margin-top: 24px; color: #333; }}
  table.tbl {{ border-collapse: collapse; width: 100%; font-size: 12.5px; background: #fff; }}
  table.tbl th, table.tbl td {{ border: 1px solid #e3e6ea; padding: 6px 9px; text-align: right; }}
  table.tbl th {{ background: #eef3fa; text-align: center; font-weight: 600; }}
  table.tbl td:first-child, table.tbl th:first-child {{ text-align: left; }}
  table.tbl tbody tr:hover {{ background: #f5f9ff; }}
  img {{ max-width: 100%; border: 1px solid #e3e6ea; border-radius: 6px; margin: 10px 0; background:#fff; }}
  .meta {{ color: #666; font-size: 13px; }}
  .card {{ background: #fff; border: 1px solid #e3e6ea; border-radius: 8px; padding: 16px 20px; margin: 14px 0; }}
  .warn {{ background: #fff8e1; border-left: 4px solid #ffb300; padding: 10px 14px; }}
  code {{ background: #f2f4f7; padding: 1px 5px; border-radius: 3px; }}
</style>
</head>
<body>
<h1>量化交易模型实验报告</h1>
<div class="meta">
  实验名称：<b>{result.config.name}</b> ｜ 生成时间：{pd.Timestamp.now():%Y-%m-%d %H:%M} ｜
  用时：{result.runtime_sec:.1f}s ｜
  标的：{', '.join(result.config.symbols)} ｜
  策略数：{len(result.config.strategies)}
</div>

<div class="card warn">
<h3 style="margin-top:0">核心结论</h3>
<ul>{concl_html}</ul>
</div>

<h2>一、数据说明</h2>
<table class="tbl"><tr><th>标的</th><th>K线数</th><th>起始</th><th>结束</th><th>最新收盘</th></tr>{data_rows}</table>
<p class="meta">数据源：盈透 IBKR（历史K线，本地缓存）；成本假设：手续费 {result.config.commission_rate:.4%} 单边、
滑点 {result.config.slippage_rate:.4%} 单边；信号延迟 1 根K线执行（无未来函数）。</p>

<h2>二、综合排名（跨标的平均）</h2>
{_df_to_html(rank_tbl)}

<h2>三、稳健性诊断</h2>
{_df_to_html(stab_tbl)}
<p class="meta">「夏普标准差」衡量策略在不同标的上表现的一致性；越小说明策略不是靠单一标的运气好。</p>

<h2>四、图表</h2>
{f'<h3>资金曲线（{focus}）</h3><img src="{eq_img}">' if eq_img else ''}
{f'<h3>风险-收益分布</h3><img src="{rr_img}">' if rr_img else ''}
{f'<h3>关键指标对比</h3><img src="{bar_img}">' if bar_img else ''}
{f'<h3>市场状态分解</h3><img src="{rg_img}">' if rg_img else ''}
{f'<h3>策略 × 标的 表现热力图</h3><img src="{hm_img}">' if hm_img else ''}
{f'<h3>走前分析</h3><img src="{wf_img}">' if wf_img else ''}

{wf_rows}

<h2>五、策略相关性</h2>
{_df_to_html(corr_tbl)}
<p class="meta">相关性高说明这些策略本质在赌同一件事，组合起来并不会真正分散风险。</p>

{opt_html}

<h2>六、明细数据</h2>
{_df_to_html(result.detail)}

<p class="meta" style="margin-top:26px">
免责声明：本报告由量化实验平台自动生成，全部结果基于历史回测，不构成任何投资建议。
</p>
</body>
</html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
