# -*- coding: utf-8 -*-
"""
scripts/run_enhanced_experiments.py —— 增强实验：风控 × 组合 × 显著性
=====================================================================
在"单标的 21 模型对比"之上，回答三个更实战的问题：

    A. 风控 A/B   ：同一信号挂不同风控，夏普/回撤改善多少？
    B. 组合层     ：8 个标的怎么分钱？分散化带来多少提升？
    C. 显著性     ：这些夏普是真实 alpha 还是运气/过拟合？

产出：results/enhanced/ 下的 CSV + report.html
"""

from __future__ import annotations

import sys
import io
import base64
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant_platform.data import get_data
from quant_platform.strategies import create
from quant_platform.risk import overlay_ab_test, PRESETS, apply_overlay, RiskOverlay
from quant_platform.portfolio import run_portfolio, compare_schemes
from quant_platform.stats import batch_significance
from quant_platform.experiment.report import setup_matplotlib, _fig_to_base64

SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META"]
# 从上一轮主实验里挑出有代表性的 6 个（覆盖各策略族）
STRATEGIES = ["双均线交叉", "唐奇安突破", "RSI均值回归",
              "ZScore回归", "ADX趋势过滤", "时间序列动量"]
DURATION = "5 Y"
OUT = Path("results/enhanced")


def log(*a):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]", *a, flush=True)


# ---------------------------------------------------------------------------
# A. 风控 A/B
# ---------------------------------------------------------------------------
def run_risk_ab(data):
    log("A. 风控 A/B 对比：6 策略 × 7 风控方案 × 8 标的")
    frames = []
    for name in STRATEGIES:
        for sym, df in data.items():
            ab = overlay_ab_test(df, name, symbol=sym)
            frames.append(ab)
        log(f"   {name} 完成")
    out = pd.concat(frames, ignore_index=True)
    g = out.groupby(["策略", "风控方案"]).agg(
        夏普比率=("夏普比率", "mean"),
        年化收益率=("年化收益率", "mean"),
        最大回撤=("最大回撤", "mean"),
        卡玛比率=("卡玛比率", "mean"),
    ).reset_index()

    # 相对"无风控"的改善
    base = g[g["风控方案"] == "无风控"].set_index("策略")
    g["夏普改善"] = g.apply(
        lambda r: r["夏普比率"] - base.loc[r["策略"], "夏普比率"]
        if r["策略"] in base.index else np.nan, axis=1)
    g["回撤改善"] = g.apply(
        lambda r: r["最大回撤"] - base.loc[r["策略"], "最大回撤"]
        if r["策略"] in base.index else np.nan, axis=1)
    return out, g.sort_values(["策略", "夏普比率"], ascending=[True, False])


# ---------------------------------------------------------------------------
# B. 组合层
# ---------------------------------------------------------------------------
def run_portfolio_layer(data):
    log("B. 组合层：6 策略 × 5 权重方案 × 8 标的")
    scheme_rows, port_equity, contrib_rows = [], {}, []
    for name in STRATEGIES:
        sigs = {s: create(name).signals(d) for s, d in data.items()}
        cmp_df = compare_schemes(data, sigs)
        cmp_df.insert(0, "策略", name)
        scheme_rows.append(cmp_df)

        best = cmp_df.sort_values("夏普比率", ascending=False).iloc[0]["权重方案"]
        res = run_portfolio(data, sigs, name=f"{name}/{best}", scheme=best)
        port_equity[name] = res.equity
        c = res.contributions.to_frame("收益贡献")
        c["策略"] = name
        contrib_rows.append(c.reset_index().rename(columns={"index": "标的"}))
        log(f"   {name} → 最优权重 {best}，组合夏普 "
            f"{res.metrics.get('夏普比率', float('nan')):.2f}")
    return (pd.concat(scheme_rows, ignore_index=True),
            port_equity,
            pd.concat(contrib_rows, ignore_index=True))


# ---------------------------------------------------------------------------
# C. 显著性
# ---------------------------------------------------------------------------
def run_significance(port_equity):
    log("C. 显著性检验（组合层收益序列）")
    rets = {k: v.pct_change().dropna() for k, v in port_equity.items()}
    b = batch_significance(rets)
    return b


# ---------------------------------------------------------------------------
# 图
# ---------------------------------------------------------------------------
def fig_overlay_heatmap(g):
    plt = setup_matplotlib()
    piv = g.pivot(index="策略", columns="风控方案", values="夏普比率")
    piv = piv[[c for c in PRESETS if c in piv.columns]]
    fig, ax = plt.subplots(figsize=(11, 4.2))
    im = ax.imshow(piv.values, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(len(piv.columns)))
    ax.set_xticklabels(piv.columns, rotation=25, ha="right", fontsize=9)
    ax.set_yticks(range(len(piv.index)))
    ax.set_yticklabels(piv.index, fontsize=9)
    for i in range(len(piv.index)):
        for j in range(len(piv.columns)):
            v = piv.values[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=8)
    ax.set_title("风控方案 × 策略：平均夏普（跨8标的）", fontsize=12)
    fig.colorbar(im, ax=ax, shrink=0.8)
    return _fig_to_base64(fig)


def fig_overlay_delta(g):
    plt = setup_matplotlib()
    sub = g[g["风控方案"] != "无风控"]
    piv = sub.pivot(index="策略", columns="风控方案", values="夏普改善")
    fig, ax = plt.subplots(figsize=(11, 4.2))
    x = np.arange(len(piv.index))
    width = 0.8 / len(piv.columns)
    for j, col in enumerate(piv.columns):
        vals = piv[col].values
        ax.bar(x + j * width - 0.4 + width / 2, vals, width, label=col)
    ax.axhline(0, color="#333", linewidth=1)
    ax.set_xticks(x)
    ax.set_xticklabels(piv.index, fontsize=9)
    ax.set_ylabel("夏普变化（相对无风控）")
    ax.set_title("叠加风控后夏普变化（柱体向上=风控有效）", fontsize=12)
    ax.legend(fontsize=8, ncol=3)
    return _fig_to_base64(fig)


def fig_portfolio_equity(port_equity, data):
    plt = setup_matplotlib()
    fig, ax = plt.subplots(figsize=(11, 5))
    # 基准：8 标的等权买入持有
    ret = pd.DataFrame({s: d["close"].pct_change() for s, d in data.items()})
    ret = ret.dropna(how="all").fillna(0.0)
    bh = (1 + ret.mean(axis=1)).cumprod() * 100
    ax.plot(bh.index, bh.values, label="等权买入持有(基准)",
            color="#888888", linewidth=2.2, linestyle="--")
    colors = plt.cm.tab10(np.linspace(0, 1, len(port_equity)))
    for c, (name, eq) in zip(colors, port_equity.items()):
        ax.plot(eq.index, eq.values / eq.iloc[0] * 100, label=name, linewidth=1.5, color=c)
    ax.set_title("组合层资金曲线（起点=100，各策略取最优权重方案）", fontsize=12)
    ax.set_ylabel("归一化净值")
    ax.legend(fontsize=8, ncol=2)
    return _fig_to_base64(fig)


def fig_significance(b):
    plt = setup_matplotlib()
    sub = b.sort_values("夏普比率")
    fig, ax = plt.subplots(figsize=(10, 4.2))
    x = np.arange(len(sub))
    ax.barh(x - 0.2, sub["夏普比率"], 0.4, label="夏普比率", color="#4C72B0")
    ax.barh(x + 0.2, sub["PSR(>0)"], 0.4, label="PSR(真夏普>0的概率)", color="#55A868")
    ax.axvline(0.95, color="#C44E52", linestyle="--", linewidth=1.2)
    ax.text(0.95, len(sub) - 0.5, "PSR=0.95 显著线", color="#C44E52", fontsize=8)
    ax.set_yticks(x)
    ax.set_yticklabels(sub["策略"], fontsize=9)
    ax.set_xlabel("数值")
    ax.set_title("组合层夏普 vs 统计显著性 PSR", fontsize=12)
    ax.legend(fontsize=8)
    return _fig_to_base64(fig)


def df_html(df, float_cols=None, fmt="{:.3f}"):
    d = df.copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else fmt.format(v))
    return d.to_html(index=False, escape=False, border=0,
                     classes="tbl", justify="left")


def build_report(paths: dict, tables: dict, figs: dict, meta: dict):
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<title>量化模型增强实验报告</title><style>",
        "body{font-family:'Microsoft YaHei',sans-serif;margin:24px;color:#222;background:#fff;line-height:1.6}",
        "h1{border-bottom:3px solid #4C72B0;padding-bottom:8px}",
        "h2{margin-top:32px;color:#4C72B0;border-left:4px solid #4C72B0;padding-left:10px}",
        "h3{margin-top:20px;color:#333}",
        "table.tbl{border-collapse:collapse;font-size:12.5px;margin:10px 0}",
        "table.tbl th{background:#4C72B0;color:#fff;padding:6px 10px;text-align:left}",
        "table.tbl td{padding:5px 10px;border-bottom:1px solid #e5e5e5}",
        "table.tbl tr:nth-child(even){background:#f7f9fc}",
        "img{max-width:100%;border:1px solid #e5e5e5;border-radius:6px;margin:10px 0}",
        ".note{background:#fff8e1;border-left:4px solid #f0ad4e;padding:10px 14px;margin:12px 0}",
        ".ok{background:#e8f5e9;border-left:4px solid #4caf50;padding:10px 14px;margin:12px 0}",
        ".bad{background:#fdecea;border-left:4px solid #e53935;padding:10px 14px;margin:12px 0}",
        ".kv{color:#666;font-size:13px}",
        "</style></head><body>",
        f"<h1>量化交易模型实验平台 · 增强实验报告</h1>",
        f"<p class='kv'>生成时间：{meta['生成时间']}　|　"
        f"标的：{meta['标的数']} 个　|　策略：{meta['策略数']} 个　|　"
        f"数据：{meta['数据源']}　|　样本：{meta['样本区间']}</p>",
    ]

    parts.append("<h2>一、结论速览</h2>")
    for c in meta["结论"]:
        cls = "bad" if c.startswith("⚠") else ("ok" if c.startswith("✅") else "note")
        parts.append(f"<div class='{cls}'>{c}</div>")

    parts.append("<h2>二、风控 A/B：加风控到底有没有用</h2>")
    parts.append("<h3>2.1 各策略 × 各风控方案的平均夏普（跨 8 标的）</h3>")
    parts.append(f"<img src='{figs['overlay_heat']}'>")
    parts.append(df_html(tables["risk_ab_summary"]))
    parts.append("<h3>2.2 相对不加风控的夏普变化</h3>")
    parts.append(f"<img src='{figs['overlay_delta']}'>")
    parts.append("<p class='kv'>正值=风控有效。注意：波动率目标类方案对 0/1 长仓信号"
                 "只能减仓不能加仓，往往会拉低收益但压平回撤。</p>")

    parts.append("<h2>三、组合层：分散化带来多少提升</h2>")
    parts.append("<h3>3.1 权重方案对比（各策略）</h3>")
    parts.append(df_html(tables["scheme"]))
    parts.append("<h3>3.2 组合资金曲线（各策略取最优权重方案）</h3>")
    parts.append(f"<img src='{figs['port_equity']}'>")
    parts.append("<h3>3.3 逐标的收益贡献（最优方案下）</h3>")
    parts.append(df_html(tables["contrib"]))

    parts.append("<h2>四、统计显著性：这些夏普可信吗</h2>")
    parts.append(f"<img src='{figs['significance']}'>")
    parts.append(df_html(tables["significance"]))
    parts.append("<p class='kv'>PSR = 真实夏普 &gt; 0 的概率（&gt;0.95 才算显著）；"
                 "DSR = 扣除「试了很多次」的选择偏差后的夏普；"
                 "SR0 = 纯靠试次数能刷出的期望虚高线；"
                 "最小样本量 = 要证明显著所需的 K 线数量。</p>")

    parts.append("<h2>五、附件</h2><ul>")
    for k, v in paths.items():
        parts.append(f"<li>{k}：<code>{v}</code></li>")
    parts.append("</ul></body></html>")
    return "\n".join(parts)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    log("加载数据...")
    data = {}
    for s in SYMBOLS:
        try:
            df = get_data(s, duration=DURATION)
            if df is not None and len(df) > 300:
                data[s] = df
        except Exception as e:                                # noqa: BLE001
            log(f"  [跳过] {s}: {str(e)[:60]}")
    log(f"可用标的 {len(data)} 个")

    ab_raw, ab_sum = run_risk_ab(data)
    scheme_df, port_equity, contrib = run_portfolio_layer(data)
    sig_df = run_significance(port_equity)

    log("生成图表...")
    figs = {
        "overlay_heat": fig_overlay_heatmap(ab_sum),
        "overlay_delta": fig_overlay_delta(ab_sum),
        "port_equity": fig_portfolio_equity(port_equity, data),
        "significance": fig_significance(sig_df),
    }

    # 落盘 CSV
    p_ab = OUT / "risk_ab_detail.csv"
    p_abs = OUT / "risk_ab_summary.csv"
    p_sc = OUT / "portfolio_schemes.csv"
    p_ct = OUT / "portfolio_contributions.csv"
    p_sg = OUT / "significance.csv"
    ab_raw.to_csv(p_ab, encoding="utf-8-sig", index=False)
    ab_sum.to_csv(p_abs, encoding="utf-8-sig", index=False)
    scheme_df.to_csv(p_sc, encoding="utf-8-sig", index=False)
    contrib.to_csv(p_ct, encoding="utf-8-sig", index=False)
    sig_df.to_csv(p_sg, encoding="utf-8-sig", index=False)

    # 结论
    best_ov = ab_sum[ab_sum["风控方案"] != "无风控"].sort_values("夏普改善", ascending=False)
    best_ov_line = (f"✅ 最有效的风控：<b>{best_ov.iloc[0]['风控方案']}</b>"
                    f"（在 {best_ov.iloc[0]['策略']} 上夏普 +{best_ov.iloc[0]['夏普改善']:.2f}）"
                    if len(best_ov) else "（无）")
    best_pf = scheme_df.sort_values("夏普比率", ascending=False).iloc[0]
    avg_single = ab_sum[ab_sum["风控方案"] == "无风控"]["夏普比率"].mean()
    best_port_sr = sig_df["夏普比率"].max()
    sig_ok = sig_df[sig_df["PSR(>0)"] > 0.95]
    if len(sig_ok):
        sig_line = f"✅ 达到统计显著（PSR>0.95）的策略：{'、'.join(sig_ok['策略'])}"
    else:
        sig_line = ("⚠ 没有任何策略在组合层达到 PSR>0.95 —— "
                    "现有样本长度下这些夏普都还不能与运气区分开，"
                    "需要更长历史或更高频数据才能下结论")

    meta = {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "标的数": len(data),
        "策略数": len(STRATEGIES),
        "数据源": "IBKR 真实日线（本地缓存）",
        "样本区间": f"{min(d.index[0].date() for d in data.values())} ~ "
                    f"{max(d.index[-1].date() for d in data.values())}",
        "结论": [
            best_ov_line,
            f"✅ 分散化效果：单标的平均夏普 {avg_single:.2f} → "
            f"最优组合夏普 {best_port_sr:.2f}"
            f"（{best_pf['策略']} × {best_pf['权重方案']}）",
            sig_line,
            "⚠ 机器学习类策略在上一轮主实验中垫底（换手过高被成本吃光），本轮未纳入",
        ],
    }

    html = build_report(
        {"风控A/B明细": str(p_ab), "风控A/B汇总": str(p_abs),
         "组合权重方案": str(p_sc), "逐标的贡献": str(p_ct),
         "显著性检验": str(p_sg)},
        {"risk_ab_summary": ab_sum[["策略", "风控方案", "夏普比率", "年化收益率",
                                    "最大回撤", "卡玛比率", "夏普改善", "回撤改善"]],
         "scheme": scheme_df[["策略", "权重方案", "年化收益率", "夏普比率",
                              "最大回撤", "卡玛比率", "平均标的数量"]],
         "contrib": contrib.pivot(index="标的", columns="策略",
                                  values="收益贡献").reset_index(),
         "significance": sig_df[["策略", "夏普比率", "PSR(>0)", "DSR",
                                 "夏普95%下界", "夏普95%上界",
                                 "SR0(期望虚高线)", "样本数",
                                 "最小样本量(证明夏普>0)"]]},
        figs, meta,
    )
    rp = OUT / "report.html"
    rp.write_text(html, encoding="utf-8")
    log(f"报告已生成: {rp}")

    print("\n=== 风控 A/B 汇总（跨 8 标的平均夏普）===")
    print(ab_sum.pivot(index="策略", columns="风控方案",
                       values="夏普比率").to_string(float_format=lambda x: f"{x:.2f}"))
    print("\n=== 组合层（各策略最优权重方案）===")
    print(sig_df[["策略", "夏普比率", "PSR(>0)", "DSR"]]
          .to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(f"\n报告: {rp}")


if __name__ == "__main__":
    main()
