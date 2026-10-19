# -*- coding: utf-8 -*-
"""
backtest/metrics.py —— 绩效评价指标库
=====================================
一个策略好不好，绝不能只看收益率。这里提供完整的多维评估：

    收益维度   总收益 / 年化收益 / 累计净值
    风险维度   年化波动 / 最大回撤 / 回撤持续期 / VaR / CVaR / 下行波动
    风险调整   夏普 / 索提诺 / Calmar / 信息比率 / Omega
    交易维度   交易次数 / 胜率 / 盈亏比 / 平均持仓天数 / 换手率 / 暴露度
    相对基准   Alpha / Beta / 超额收益 / 跟踪误差
    稳健性     月度胜率 / 最差年度 / 收益偏度峰度

所有函数输入为「日收益率序列」或「净值曲线」，输出 dict，方便直接进对比表。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _annualize_factor(index: pd.DatetimeIndex | pd.Index) -> float:
    """根据实际数据频率推断年化因子（日线=252，小时线≈252*6.5 等）。"""
    if isinstance(index, pd.DatetimeIndex) and len(index) > 3:
        deltas = np.diff(index.values).astype("timedelta64[ns]").astype(np.int64)
        med_days = np.median(deltas) / (1e9 * 86400)
        if med_days <= 0:
            return TRADING_DAYS
        if med_days >= 0.9:            # 日线
            return TRADING_DAYS
        minutes = med_days * 24 * 60
        if minutes >= 1:               # 分钟线/小时线：按每年 252 个交易日的交易分钟折算
            per_day = max(1.0, 390.0 / minutes)
            return TRADING_DAYS * per_day
    return TRADING_DAYS


def max_drawdown(equity: pd.Series) -> float:
    """最大回撤（负数）。"""
    if len(equity) == 0:
        return np.nan
    return float((equity / equity.cummax() - 1).min())


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1


def drawdown_duration(equity: pd.Series) -> int:
    """最长回撤持续期（多少个 bar 未创新高）。"""
    if len(equity) == 0:
        return 0
    peak = equity.cummax()
    underwater = (equity < peak).astype(int)
    # 最长连续 1 的长度
    best = cur = 0
    for v in underwater.values:
        cur = cur + 1 if v == 1 else 0
        best = max(best, cur)
    return int(best)


def cagr(equity: pd.Series, periods_per_year: float) -> float:
    """年化复合增长率。"""
    if len(equity) < 2:
        return np.nan
    years = len(equity) / periods_per_year
    if years <= 0:
        return np.nan
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)


def value_at_risk(returns: pd.Series, level: float = 0.95) -> float:
    """历史 VaR（负数，表示 95% 置信下日最大损失）。"""
    if len(returns.dropna()) == 0:
        return np.nan
    return float(np.nanpercentile(returns, (1 - level) * 100))


def conditional_var(returns: pd.Series, level: float = 0.95) -> float:
    """CVaR / 期望缺口：超过 VaR 那部分损失的平均。"""
    r = returns.dropna()
    if len(r) == 0:
        return np.nan
    thr = value_at_risk(r, level)
    tail = r[r <= thr]
    return float(tail.mean()) if len(tail) else np.nan


def omega_ratio(returns: pd.Series, threshold: float = 0.0) -> float:
    """Omega 比率：收益高于阈值的部分 / 低于阈值的部分。"""
    r = returns.dropna()
    if len(r) == 0:
        return np.nan
    gain = r[r > threshold].sum() - threshold * (r > threshold).sum()
    loss = threshold * (r <= threshold).sum() - r[r <= threshold].sum()
    return float(gain / loss) if loss > 0 else np.inf


def extract_trades(position: pd.Series, price: pd.Series) -> pd.DataFrame:
    """
    把持仓序列拆成一笔笔交易。
    返回 DataFrame：entry_date / exit_date / entry_price / exit_price / ret / bars
    """
    pos = position.fillna(0).astype(int)
    trades, entry_i, entry_p = [], None, None
    vals = pos.values
    px = price.values
    idx = pos.index

    for i in range(len(vals)):
        if vals[i] != 0 and entry_i is None:
            entry_i, entry_p = i, px[i]
        elif vals[i] == 0 and entry_i is not None:
            trades.append({
                "entry_date": idx[entry_i], "exit_date": idx[i],
                "entry_price": entry_p, "exit_price": px[i],
                "ret": (px[i] / entry_p - 1) * np.sign(vals[entry_i]) if entry_p else np.nan,
                "bars": i - entry_i,
                "direction": np.sign(vals[entry_i]),
            })
            entry_i, entry_p = None, None
    if entry_i is not None:
        trades.append({
            "entry_date": idx[entry_i], "exit_date": idx[-1],
            "entry_price": entry_p, "exit_price": px[-1],
            "ret": (px[-1] / entry_p - 1) * np.sign(vals[entry_i]) if entry_p else np.nan,
            "bars": len(vals) - 1 - entry_i,
            "direction": np.sign(vals[entry_i]),
        })
    return pd.DataFrame(trades)


def trade_stats(trades: pd.DataFrame) -> dict:
    """从交易记录算胜率/盈亏比/平均持仓等。"""
    if trades is None or len(trades) == 0:
        return {"trades": 0, "win_rate": np.nan, "profit_loss_ratio": np.nan,
                "avg_bars": np.nan, "avg_trade": np.nan, "best": np.nan, "worst": np.nan,
                "expectancy": np.nan}
    r = trades["ret"].dropna()
    wins, losses = r[r > 0], r[r <= 0]
    return {
        "trades": int(len(r)),
        "win_rate": float(len(wins) / len(r)) if len(r) else np.nan,
        "profit_loss_ratio": (float(wins.mean() / abs(losses.mean()))
                              if len(wins) and len(losses) and losses.mean() != 0 else np.nan),
        "avg_bars": float(trades["bars"].mean()) if len(trades) else np.nan,
        "avg_trade": float(r.mean()),
        "best": float(r.max()),
        "worst": float(r.min()),
        "expectancy": float(r.mean()),
    }


def compute_metrics(returns: pd.Series,
                    equity: Optional[pd.Series] = None,
                    position: Optional[pd.Series] = None,
                    price: Optional[pd.Series] = None,
                    benchmark_returns: Optional[pd.Series] = None,
                    periods_per_year: Optional[float] = None,
                    risk_free_rate: float = 0.0) -> dict:
    """
    核心：一次性算出全部指标。

    参数：
        returns           策略日收益率（已扣费）
        equity            净值曲线（不给则按 returns 累乘构造）
        position          持仓序列（用于交易统计，可为空）
        price             价格序列（配合 position 算交易）
        benchmark_returns 基准收益（买入持有），用于 Alpha/Beta/信息比率
    """
    r = returns.dropna()
    if len(r) == 0:
        return {}
    if equity is None:
        equity = (1 + r).cumprod()
    if periods_per_year is None:
        periods_per_year = _annualize_factor(r.index)

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    ann_ret = cagr(equity, periods_per_year)
    ann_vol = float(r.std(ddof=1) * np.sqrt(periods_per_year))
    downside = r[r < 0]
    down_vol = float(downside.std(ddof=1) * np.sqrt(periods_per_year)) if len(downside) > 1 else np.nan

    rf_period = risk_free_rate / periods_per_year
    excess = r - rf_period
    sharpe = float(excess.mean() / excess.std(ddof=1) * np.sqrt(periods_per_year)) if excess.std(ddof=1) > 0 else np.nan
    sortino = float(excess.mean() / down_vol) if down_vol and down_vol > 0 else np.nan

    mdd = max_drawdown(equity)
    calmar = float(ann_ret / abs(mdd)) if mdd and mdd < 0 else np.nan

    # 月度/年度胜率
    monthly = equity.resample("ME").last().pct_change().dropna() if isinstance(equity.index, pd.DatetimeIndex) else pd.Series(dtype=float)
    # 注意：空仓月份收益恒为 0，若直接计入会把"没交易"误判成"没赚钱"，
    # 让低暴露策略的月度胜率被系统性低估。这里只统计真正有波动的月份。
    active_months = monthly[monthly.abs() > 1e-9]
    monthly_win = float((active_months > 0).mean()) if len(active_months) else np.nan

    m = {
        # 收益
        "总收益率": total_return,
        "年化收益率": ann_ret,
        # 风险
        "年化波动率": ann_vol,
        "下行波动率": down_vol,
        "最大回撤": mdd,
        "回撤持续期": drawdown_duration(equity),
        "VaR95(日)": value_at_risk(r, 0.95),
        "CVaR95(日)": conditional_var(r, 0.95),
        # 风险调整
        "夏普比率": sharpe,
        "索提诺比率": sortino,
        "卡玛比率": calmar,
        "Omega": omega_ratio(r),
        # 分布特征
        "偏度": float(r.skew()) if len(r) > 2 else np.nan,
        "峰度": float(r.kurt()) if len(r) > 3 else np.nan,
        "月度胜率": monthly_win,
        "单日最大亏损": float(r.min()),
        # 稳定性
        "最长连亏(月)": _max_consecutive_negative(active_months) if len(active_months) else np.nan,
        "有效月份数": float(len(active_months)),
    }

    # 交易统计
    if position is not None and price is not None:
        trades = extract_trades(position, price)
        ts = trade_stats(trades)
        m.update({
            "交易次数": ts["trades"],
            "胜率": ts["win_rate"],
            "盈亏比": ts["profit_loss_ratio"],
            "平均持仓天数": ts["avg_bars"],
            "单笔均值": ts["avg_trade"],
            "最好一笔": ts["best"],
            "最差一笔": ts["worst"],
        })
        turnover = position.diff().abs().fillna(position.abs())
        m["年化换手"] = float(turnover.sum() / len(position) * periods_per_year)
        m["持仓占比"] = float((position != 0).mean())

    # 相对基准
    if benchmark_returns is not None:
        b = benchmark_returns.reindex(r.index).dropna()
        common = r.index.intersection(b.index)
        if len(common) > 10:
            rr, bb = r.loc[common], b.loc[common]
            cov = np.cov(rr, bb)
            beta = float(cov[0, 1] / cov[1, 1]) if cov[1, 1] > 0 else np.nan
            alpha = float((rr.mean() - beta * bb.mean()) * periods_per_year)
            active = rr - bb
            te = float(active.std(ddof=1) * np.sqrt(periods_per_year))
            m.update({
                "Beta": beta,
                "年化Alpha": alpha,
                "跟踪误差": te,
                "信息比率": float(active.mean() / active.std(ddof=1) * np.sqrt(periods_per_year))
                if active.std(ddof=1) > 0 else np.nan,
                "相对基准超额": float((1 + rr).prod() - (1 + bb).prod()),
            })
    return m


def _max_consecutive_negative(s: pd.Series) -> int:
    best = cur = 0
    for v in s.values:
        cur = cur + 1 if v <= 0 else 0
        best = max(best, cur)
    return int(best)


def metrics_to_frame(results: dict[str, dict]) -> pd.DataFrame:
    """把 {策略名: metrics} 转成对比 DataFrame（指标为行，策略为列）。"""
    df = pd.DataFrame(results)
    return df


def rank_strategies(df_metrics: pd.DataFrame,
                    weights: Optional[dict] = None) -> pd.DataFrame:
    """
    综合评分排名。默认权重更偏向"风险调整后收益"，而非纯收益率。
    """
    weights = weights or {
        "夏普比率": 0.30,
        "卡玛比率": 0.20,
        "年化收益率": 0.20,
        "最大回撤": 0.15,        # 回撤越小越好（内部取反）
        "胜率": 0.05,
        "月度胜率": 0.10,
    }
    score = pd.Series(0.0, index=df_metrics.columns)
    for k, w in weights.items():
        if k not in df_metrics.index:
            continue
        row = pd.to_numeric(df_metrics.loc[k], errors="coerce")
        # z-score 标准化后加权；缺失填 0 分
        z = (row - row.mean()) / row.std(ddof=0) if row.std(ddof=0) else row * 0
        z = z.fillna(0.0)
        if k in ("最大回撤", "年化波动率", "VaR95(日)"):
            z = -z                       # 越小越好
        score += w * z
    out = df_metrics.T.copy()
    out["综合评分"] = score
    out = out.sort_values("综合评分", ascending=False)
    return out.T
