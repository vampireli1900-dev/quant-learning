# -*- coding: utf-8 -*-
"""
stats/significance.py —— 统计显著性检验
=======================================
回测里最容易骗自己的三件事：

    1. 夏普 1.0 但是只有 100 根 K 线 → 可能纯属运气
    2. 试了 500 组参数，最好的那组夏普 1.5 → 多重检验偏差，本来就该出个高分
    3. 收益分布尖峰厚尾 → 正态分布假设下的夏普被系统性高估

本模块给出四件武器（均来自 Bailey & López de Prado、Andrew Lo 的学术工作）：
    block_bootstrap      块状自助法：保留自相关，给出夏普的置信区间
    probabilistic_sharpe PSR：考虑偏度峰度后，"真实夏普 > 目标值"的概率
    deflated_sharpe       DSR：扣除"试了 N 次"带来的选择偏差后的夏普
    min_track_record      minTRL：要证明夏普显著，最少需要多少根 K 线
    random_signal_test    随机信号置换检验：策略是否只是随机漫步的镜像

判据（经验阈值）：
    PSR > 0.95  → 可以相信这个夏普不是运气
    DSR < 0.95  → 扣除多重检验后不显著，本质是过拟合
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats as sps

TRADING_DAYS = 252
EULER = 0.5772156649015329     # 欧拉-马歇罗尼常数，DSR 公式用到


# ---------------------------------------------------------------------------
# 基础：夏普与其分布特征
# ---------------------------------------------------------------------------
def sharpe(returns: pd.Series, periods: int = TRADING_DAYS,
           risk_free: float = 0.0) -> float:
    r = pd.Series(returns).dropna()
    if len(r) < 2:
        return float("nan")
    sd = r.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return float("nan")
    return float((r.mean() - risk_free / periods) / sd * np.sqrt(periods))


def _moments(r: pd.Series) -> tuple:
    """返回 (n, skew, raw_kurtosis)。pandas 的 kurt() 是超额峰度，这里还原成原始峰度。"""
    x = pd.Series(r).dropna()
    n = len(x)
    if n < 4:
        return n, 0.0, 3.0
    return n, float(x.skew()), float(x.kurt()) + 3.0


def sharpe_std_err(sr: float, n: int, skew: float = 0.0,
                   kurtosis: float = 3.0) -> float:
    """
    Lo(2002) 的夏普估计量标准误（考虑非正态）：
        σ(SR) = sqrt( [1 - γ3·SR + (γ4-1)/4·SR²] / (n-1) )
    """
    if n <= 1:
        return float("nan")
    var = (1 - skew * sr + (kurtosis - 1) / 4.0 * sr ** 2) / (n - 1)
    return float(np.sqrt(max(var, 1e-12)))


# ---------------------------------------------------------------------------
# 1) 块状自助法（保留自相关）
# ---------------------------------------------------------------------------
def block_bootstrap(returns: pd.Series,
                    stat: Callable[[np.ndarray], float] = None,
                    n_boot: int = 2000,
                    block: int = 20,
                    ci: float = 0.95,
                    seed: int = 42) -> Dict[str, float]:
    """
    对统计量做块状自助法，返回点估计、置信区间、标准误。

    为什么用块状而不是普通 bootstrap？
        金融收益有自相关（波动聚集），逐点重采样会破坏时间序列结构，
        低估波动 → 高估夏普。块状重采样能保留局部结构。
    """
    r = np.asarray(pd.Series(returns).dropna(), dtype=float)
    n = len(r)
    if n < block * 2:
        return {"点估计": float("nan"), "下界": float("nan"),
                "上界": float("nan"), "标准误": float("nan")}
    stat = stat or (lambda x: sharpe(pd.Series(x)))
    rng = np.random.default_rng(seed)

    n_blocks = int(np.ceil(n / block))
    idx_starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))

    vals = np.empty(n_boot)
    for b in range(n_boot):
        sample = np.concatenate([r[s:s + block] for s in idx_starts[b]])[:n]
        vals[b] = stat(sample)

    point = stat(r)
    lo, hi = np.percentile(vals, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
    return {
        "点估计": float(point),
        "下界": float(lo),
        "上界": float(hi),
        "标准误": float(np.std(vals, ddof=1)),
        "P(>0)": float((vals > 0).mean()),
    }


def sharpe_ci(returns: pd.Series, ci: float = 0.95,
              n_boot: int = 2000, block: int = 20, seed: int = 42) -> Dict[str, float]:
    """夏普比率的块状自助置信区间。"""
    return block_bootstrap(returns, stat=lambda x: sharpe(pd.Series(x)),
                           n_boot=n_boot, block=block, ci=ci, seed=seed)


# ---------------------------------------------------------------------------
# 2) PSR —— 概率化夏普比率
# ---------------------------------------------------------------------------
def probabilistic_sharpe(returns: pd.Series,
                         target_sr: float = 0.0,
                         risk_free: float = 0.0) -> float:
    """
    PSR = P(真实夏普 > target_sr)。
    判据：PSR > 0.95 才算"显著不为运气"。
    """
    r = pd.Series(returns).dropna()
    n, skew, kurt = _moments(r)
    sr = sharpe(r, risk_free=risk_free)
    if n < 10 or np.isnan(sr):
        return float("nan")
    sr = sr / np.sqrt(TRADING_DAYS)          # 转成"每期夏普"
    target = target_sr / np.sqrt(TRADING_DAYS)
    se = sharpe_std_err(sr, n, skew, kurt)
    z = (sr - target) / se
    return float(sps.norm.cdf(z))


# ---------------------------------------------------------------------------
# 3) DSR —— 去膨胀夏普比率（扣掉"试了很多次"的偏差）
# ---------------------------------------------------------------------------
def deflated_sharpe(returns: pd.Series,
                    n_trials: int,
                    sharpe_variance: Optional[float] = None,
                    all_sharpes: Optional[Sequence[float]] = None,
                    risk_free: float = 0.0) -> Dict[str, float]:
    """
    做过 N 次试验（参数网格/策略池）后，最好那次的结果必然虚高。
    DSR 先算出"纯靠试次数能刷出的期望最大夏普 SR0"，再用 PSR 检验是否显著超过它。

    n_trials       : 试验次数（参数组合数 / 策略数）
    all_sharpes    : 若提供了全部试验的夏普，直接算方差（推荐）
    sharpe_variance: 否则手动给夏普的方差
    """
    r = pd.Series(returns).dropna()
    n, skew, kurt = _moments(r)
    sr = sharpe(r, risk_free=risk_free)

    if all_sharpes is not None and len(all_sharpes) > 1:
        sharpe_variance = float(np.nanvar(np.asarray(all_sharpes, dtype=float), ddof=1))
        n_trials = max(n_trials, len(all_sharpes))
    if sharpe_variance is None:
        # 没有试验分布时，退化为用该收益序列自身的夏普标准误平方
        sharpe_variance = sharpe_std_err(sr / np.sqrt(TRADING_DAYS), n, skew, kurt) ** 2

    if n_trials < 2 or n < 10:
        return {"夏普": sr, "试验次数": n_trials, "SR0": float("nan"),
                "DSR": float("nan"), "结论": "样本不足"}

    sd = float(np.sqrt(max(sharpe_variance, 1e-12)))
    # 多次试验下的"期望最大夏普"基准（Bailey & López de Prado 2014）
    z1 = sps.norm.ppf(1 - 1.0 / n_trials)
    z2 = sps.norm.ppf(1 - 1.0 / (n_trials * np.e))
    sr0 = sd * ((1 - EULER) * z1 + EULER * z2)

    # 用 SR0 作为门槛算 PSR
    sr_p = sr / np.sqrt(TRADING_DAYS)
    se = sharpe_std_err(sr_p, n, skew, kurt)
    dsr = float(sps.norm.cdf((sr_p - sr0 / np.sqrt(TRADING_DAYS)) / se)) if se > 0 else float("nan")

    return {
        "夏普": float(sr),
        "试验次数": int(n_trials),
        "夏普方差": sharpe_variance,
        "SR0(期望虚高线)": float(sr0),
        "DSR": dsr,
        "结论": "显著" if (dsr == dsr and dsr > 0.95) else "不显著（疑似过拟合）",
    }


# ---------------------------------------------------------------------------
# 4) minTRL —— 最小样本长度
# ---------------------------------------------------------------------------
def min_track_record(target_sr: float, observed_sr: float,
                     skew: float = 0.0, kurtosis: float = 3.0,
                     alpha: float = 0.05) -> float:
    """
    要证明"真实夏普 > observed_sr"（在 α 显著性下），最少需要多少根 K 线。
    返回值若大于你实际样本量，说明**数据不够，结论不可信**。
    """
    sr_p = observed_sr / np.sqrt(TRADING_DAYS)
    tgt = target_sr / np.sqrt(TRADING_DAYS)
    if observed_sr <= target_sr:
        return float("inf")
    z = sps.norm.ppf(1 - alpha)
    num = (1 - skew * sr_p + (kurtosis - 1) / 4.0 * sr_p ** 2) * (z ** 2)
    return float(1 + num / ((sr_p - tgt) ** 2))


# ---------------------------------------------------------------------------
# 5) 随机信号置换检验
# ---------------------------------------------------------------------------
def random_signal_test(df: pd.DataFrame, signal: pd.Series,
                       n_iter: int = 500, seed: int = 42) -> Dict[str, float]:
    """
    保持"持仓比例"和"持仓段长度分布"不变，随机打乱进场时点，看能跑出多好的夏普。
    如果策略的真实夏普排不进随机分布的前 5%，说明它跟掷骰子没区别。

    做法：把信号按连续持仓段(block)切分，随机平移这些段的位置。

    注意：随机平移会让段与段重叠合并，总暴露度通常与真实信号不同，
    所以结果里同时给出"真实持仓占比 vs 随机持仓占比"——如果随机组暴露度
    明显更高，它的夏普占便宜，此时 p 值偏保守（宁可判不显著）。
    """
    from ..backtest import run_backtest

    real = run_backtest(df, signal, name="real")
    real_sr = real.metrics.get("夏普比率", np.nan)
    if np.isnan(real_sr):
        return {"真实夏普": float("nan")}

    pos = signal.fillna(0).values.astype(float)
    n = len(pos)
    rng = np.random.default_rng(seed)

    # 抽取连续持仓段
    segs = []
    i = 0
    while i < n:
        if abs(pos[i]) > 1e-9:
            j = i
            while j + 1 < n and abs(pos[j + 1] - pos[i]) < 1e-9:
                j += 1
            segs.append((i, j, pos[i]))
            i = j + 1
        else:
            i += 1
    if not segs:
        return {"真实夏普": real_sr, "随机夏普均值": float("nan"),
                "p值": float("nan"), "结论": "无持仓段"}

    rand_sr = []
    rand_exp = []
    for _ in range(n_iter):
        p = np.zeros(n)
        for (a, b, v) in segs:
            L = b - a + 1
            start = rng.integers(0, max(1, n - L + 1))
            p[start:start + L] = v
        r = run_backtest(df, pd.Series(p, index=df.index), name="rand")
        s = r.metrics.get("夏普比率", np.nan)
        if not np.isnan(s):
            rand_sr.append(s)
            rand_exp.append(r.metrics.get("持仓占比", np.nan))

    if not rand_sr:
        return {"真实夏普": real_sr}
    arr = np.asarray(rand_sr)
    p_val = float((arr >= real_sr).mean())
    real_exp = real.metrics.get("持仓占比", np.nan)
    return {
        "真实夏普": float(real_sr),
        "随机夏普均值": float(np.mean(arr)),
        "随机夏普95分位": float(np.percentile(arr, 95)),
        "真实持仓占比": float(real_exp),
        "随机持仓占比": float(np.nanmean(rand_exp)),
        "p值": p_val,
        "结论": "显著优于随机" if p_val < 0.05 else "与随机无异",
    }


# ---------------------------------------------------------------------------
# 汇总：给一个策略结果做全套体检
# ---------------------------------------------------------------------------
def significance_report(returns: pd.Series,
                        n_trials: int = 1,
                        all_sharpes: Optional[Sequence[float]] = None,
                        risk_free: float = 0.0,
                        do_bootstrap: bool = True) -> Dict[str, float]:
    """一次性产出所有显著性指标，方便直接塞进对比表。"""
    r = pd.Series(returns).dropna()
    n, skew, kurt = _moments(r)
    sr = sharpe(r, risk_free=risk_free)
    out = {
        "样本数": float(n),
        "夏普比率": sr,
        "偏度": skew,
        "峰度(原始)": kurt,
        "夏普标准误(年化)": sharpe_std_err(sr / np.sqrt(TRADING_DAYS), n, skew, kurt) * np.sqrt(TRADING_DAYS),
        "PSR(>0)": probabilistic_sharpe(r, target_sr=0.0, risk_free=risk_free),
        "PSR(>1)": probabilistic_sharpe(r, target_sr=1.0, risk_free=risk_free),
    }
    if do_bootstrap and n >= 40:
        ci = sharpe_ci(r)
        out["夏普95%下界"] = ci["下界"]
        out["夏普95%上界"] = ci["上界"]
    d = deflated_sharpe(r, n_trials=n_trials, all_sharpes=all_sharpes, risk_free=risk_free)
    out["试验次数"] = d.get("试验次数", n_trials)
    out["SR0(期望虚高线)"] = d.get("SR0(期望虚高线)", float("nan"))
    out["DSR"] = d.get("DSR", float("nan"))
    out["最小样本量(证明夏普>0)"] = min_track_record(0.0, sr, skew, kurt)
    return out


def batch_significance(results: Dict[str, pd.Series],
                       risk_free: float = 0.0) -> pd.DataFrame:
    """对多个策略的收益序列批量做显著性体检（互相作为试验次数）。"""
    sharpes = [sharpe(v, risk_free=risk_free) for v in results.values()]
    rows = []
    for k, v in results.items():
        rep = significance_report(v, n_trials=len(results),
                                  all_sharpes=sharpes, risk_free=risk_free)
        rep["策略"] = k
        rows.append(rep)
    out = pd.DataFrame(rows)
    front = ["策略", "夏普比率", "PSR(>0)", "DSR", "夏普95%下界", "夏普95%上界",
             "SR0(期望虚高线)", "样本数", "最小样本量(证明夏普>0)"]
    cols = [c for c in front if c in out.columns] + \
           [c for c in out.columns if c not in front]
    out = out[cols].sort_values("夏普比率", ascending=False)
    return out
