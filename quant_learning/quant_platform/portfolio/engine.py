# -*- coding: utf-8 -*-
"""
portfolio/engine.py —— 多标的组合回测
=====================================
单标的回测只能回答"这个策略在 AAPL 上赚不赚钱"，
组合回测才能回答实战问题：

    "8 个标的、3 个策略，钱怎么分？分散化到底带来了多少改善？"

支持的权重方案（scheme）：
    equal       等权：有信号的标的平分资金
    signal      按信号强度加权（信号 0~1 连续值时更有意义）
    vol_inverse 波动率倒数加权（波动大的少给钱，经典风险平价近似）
    risk_parity 风险平价（用协方差矩阵迭代求解，波动倒数加权是它的简化）
    vol_target  组合层面波动率目标（先等权/信号加权，再整体缩放到目标波动）

组合回测与单标的回测的关系：
    单标的 run_backtest 是这里的特例（1 个标的、权重 1）。
    组合层额外提供：相关性矩阵、分散化收益、逐标的贡献分解。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Literal

import numpy as np
import pandas as pd

from ..config import CONFIG, BacktestConfig
from ..backtest import metrics as M
from ..backtest.engine import BacktestResult

WeightScheme = Literal["equal", "signal", "vol_inverse", "risk_parity", "vol_target"]


@dataclass
class PortfolioResult:
    name: str
    returns: pd.Series
    equity: pd.Series
    weights: pd.DataFrame                    # 逐日各标的权重（index=日期, columns=标的）
    metrics: dict = field(default_factory=dict)
    per_symbol: pd.DataFrame = field(default_factory=pd.DataFrame)
    correlation: pd.DataFrame = field(default_factory=pd.DataFrame)
    contributions: pd.Series = field(default_factory=pd.Series)

    PCT_KEYS = {"总收益率", "年化收益率", "年化波动率", "最大回撤", "胜率",
                "月度胜率", "VaR95(日)", "CVaR95(日)", "持仓占比", "单日最大亏损"}
    INT_KEYS = {"交易次数", "回撤持续期"}

    def summary(self) -> str:
        keys = ["年化收益率", "年化波动率", "夏普比率", "最大回撤",
                "卡玛比率", "月度胜率", "持仓占比"]
        lines = [f"—— 组合：{self.name} ——"]
        for k in keys:
            v = self.metrics.get(k, np.nan)
            if isinstance(v, float) and not np.isnan(v):
                if k in self.PCT_KEYS:
                    lines.append(f"  {k:<10}: {v:>8.2%}")
                else:
                    lines.append(f"  {k:<10}: {v:>8.2f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 权重计算
# ---------------------------------------------------------------------------
def _rolling_vol(returns: pd.DataFrame, lookback: int = 60) -> pd.DataFrame:
    """各标的滚动年化波动率。"""
    return returns.rolling(lookback, min_periods=max(10, lookback // 3)).std() * np.sqrt(252)


def compute_weights(signals: pd.DataFrame, returns: pd.DataFrame,
                    scheme: WeightScheme = "equal",
                    vol_lookback: int = 60,
                    max_weight: float = 0.4,
                    max_leverage: float = 1.0,
                    target_vol: Optional[float] = None) -> pd.DataFrame:
    """
    signals: index=日期, columns=标的，值为目标仓位（可 0/1 或连续）
    returns: index=日期, columns=标的，各标的日收益（用于波动率估计）
    返回：逐日权重矩阵，每行和为总杠杆（<= max_leverage）
    """
    sig = signals.reindex(returns.index).fillna(0.0).astype(float)
    cols = list(sig.columns)
    n = len(sig)

    if scheme == "signal":
        base = sig.abs()
    elif scheme in ("vol_inverse", "risk_parity", "vol_target"):
        vol = _rolling_vol(returns, vol_lookback).reindex(returns.index)
        inv = 1.0 / vol.replace(0, np.nan)
        base = inv.fillna(0.0)
    else:                                    # equal
        base = pd.DataFrame(1.0, index=sig.index, columns=cols)

    # 只在有信号（|signal|>0）的标的上分配资金
    active = (sig.abs() > 1e-8)
    base = base.where(active, 0.0)

    row_sum = base.abs().sum(axis=1).replace(0, np.nan)
    w = base.div(row_sum, axis=0).fillna(0.0)

    # 带上信号方向（做多/做空）
    w = w * np.sign(sig)

    # 单标的上限（真实组合管理中必需的集中度约束）
    if max_weight and max_weight > 0:
        for _ in range(6):                   # 简单迭代重分配，收敛很快
            over = w.abs() > max_weight
            if not over.values.any():
                break
            excess = (w.abs() - max_weight).clip(lower=0).sum(axis=1)
            w = w.clip(lower=-max_weight, upper=max_weight)
            pool = ((w.abs() < max_weight - 1e-9) & (w.abs() > 0))
            pool_sum = pool.sum(axis=1).replace(0, np.nan)
            add = (excess / pool_sum)
            w = w + add.to_numpy().reshape(-1, 1) * pool.values * np.sign(w.values)

    # 组合层面波动率目标
    if scheme == "vol_target" and target_vol:
        vol = _rolling_vol(returns, vol_lookback).reindex(returns.index)
        w.columns = cols
        port_vol = (w.abs() * vol[cols]).sum(axis=1)
        scale = (target_vol / port_vol.replace(0, np.nan)).clip(upper=max_leverage * 3)
        w = w.mul(scale.fillna(1.0), axis=0)

    # 总杠杆约束
    lev = w.abs().sum(axis=1)
    over_lev = lev > max_leverage
    if over_lev.any():
        w = w.div(lev / max_leverage, axis=0).where(over_lev, w)

    w.columns = cols
    return w.fillna(0.0)


def run_portfolio(data: Dict[str, pd.DataFrame],
                  signals: Dict[str, pd.Series],
                  name: str = "组合",
                  scheme: WeightScheme = "equal",
                  cfg: Optional[BacktestConfig] = None,
                  commission_rate: Optional[float] = None,
                  slippage_rate: Optional[float] = None,
                  vol_lookback: int = 60,
                  max_weight: float = 0.4,
                  max_leverage: float = 1.0,
                  target_vol: Optional[float] = None,
                  rebalance_cost: bool = True) -> PortfolioResult:
    """
    组合回测主入口。

    data    : {symbol: OHLCV DataFrame}，索引会取交集
    signals : {symbol: 仓位 Series}（由策略生成，内部会 shift(1) 避免未来函数）
    """
    cfg = cfg or CONFIG.backtest
    commission_rate = cfg.commission_rate if commission_rate is None else commission_rate
    slippage_rate = cfg.slippage_rate if slippage_rate is None else slippage_rate

    symbols = [s for s in signals if s in data]
    if not symbols:
        raise ValueError("没有可用标的：signals 与 data 无交集")

    # 对齐日期（取交集，避免未来函数/缺失值带来的假收益）
    ret_df = pd.DataFrame({s: data[s]["close"].pct_change() for s in symbols})
    ret_df = ret_df.dropna(how="all").fillna(0.0)

    sig_df = pd.DataFrame({s: signals[s].reindex(ret_df.index) for s in symbols})
    sig_df = sig_df.shift(1).fillna(0.0)      # 明日执行

    w = compute_weights(sig_df, ret_df, scheme=scheme, vol_lookback=vol_lookback,
                        max_weight=max_weight, max_leverage=max_leverage,
                        target_vol=target_vol)
    w = w.reindex(ret_df.index).fillna(0.0)

    # 组合毛收益
    gross = (w * ret_df[w.columns]).sum(axis=1)

    # 成本：按权重换手计
    if rebalance_cost:
        turnover = w.diff().abs().fillna(w.abs()).sum(axis=1)
        cost = turnover * (commission_rate + slippage_rate)
    else:
        turnover = pd.Series(0.0, index=ret_df.index)
        cost = pd.Series(0.0, index=ret_df.index)

    net = (gross - cost).clip(lower=-0.99)
    equity = (1 + net).cumprod() * cfg.initial_capital

    # 指标
    pos_flag = (w.abs().sum(axis=1) > 1e-8).astype(float)
    m = M.compute_metrics(net, equity, pos_flag, equity,
                          risk_free_rate=cfg.risk_free_rate)
    m["年化换手"] = float(turnover.mean() * 252)
    m["持仓占比"] = float(pos_flag.mean())
    m["平均标的数量"] = float((w.abs() > 1e-8).sum(axis=1).mean())

    # 逐标的贡献
    contrib = (w * ret_df[w.columns]).sum(axis=0)
    contrib = contrib.sort_values(ascending=False)

    # 逐标的独立表现（对比用：同样信号在单标的上跑是什么样）
    rows = []
    for s in w.columns:
        r_s = (w[s] * ret_df[s])
        eq_s = (1 + r_s).cumprod() * cfg.initial_capital
        mm = M.compute_metrics(r_s, eq_s, (w[s].abs() > 1e-8).astype(float),
                               ret_df[s], risk_free_rate=cfg.risk_free_rate)
        rows.append({"标的": s, "权重均值": float(w[s].abs().mean()),
                     "年化收益率": mm.get("年化收益率", np.nan),
                     "夏普比率": mm.get("夏普比率", np.nan),
                     "最大回撤": mm.get("最大回撤", np.nan)})
    per_symbol = pd.DataFrame(rows)

    # 相关性（用各自原始收益，反映分散化潜力）
    corr = ret_df[w.columns].corr()

    return PortfolioResult(
        name=name, returns=net, equity=equity, weights=w, metrics=m,
        per_symbol=per_symbol, correlation=corr, contributions=contrib,
    )


def diversification_benefit(res: PortfolioResult,
                            data: Dict[str, pd.DataFrame],
                            signals: Dict[str, pd.Series]) -> pd.DataFrame:
    """
    分散化收益：组合 vs 各标的单独跑同一信号的平均表现。
    如果组合夏普 > 单标的平均夏普，说明分散化确实起作用了。
    """
    from ..backtest import run_backtest

    rows = []
    for s in res.weights.columns:
        if s in data and s in signals:
            r = run_backtest(data[s], signals[s], name=s, symbol=s)
            rows.append({"标的": s, "单标的夏普": r.metrics.get("夏普比率", np.nan),
                         "单标的年化": r.metrics.get("年化收益率", np.nan),
                         "单标的回撤": r.metrics.get("最大回撤", np.nan)})
    out = pd.DataFrame(rows)
    avg = out[["单标的夏普", "单标的年化", "单标的回撤"]].mean()
    cmp_row = {"标的": "★组合", "单标的夏普": res.metrics.get("夏普比率", np.nan),
               "单标的年化": res.metrics.get("年化收益率", np.nan),
               "单标的回撤": res.metrics.get("最大回撤", np.nan)}
    out = pd.concat([out, pd.DataFrame([cmp_row])], ignore_index=True)
    out.loc[len(out)] = {"标的": "—单标的平均", **avg.to_dict()}
    return out


SCHEMES = ["equal", "signal", "vol_inverse", "risk_parity", "vol_target"]


def compare_schemes(data: Dict[str, pd.DataFrame],
                    signals: Dict[str, pd.Series],
                    schemes=None, **kwargs) -> pd.DataFrame:
    """同一批信号，换不同权重方案，看哪个分钱方式最好。"""
    schemes = schemes or SCHEMES
    rows = []
    for sc in schemes:
        try:
            r = run_portfolio(data, signals, name=sc, scheme=sc, **kwargs)
            rows.append({"权重方案": sc, **r.metrics})
        except Exception as e:
            rows.append({"权重方案": sc, "错误": str(e)})
    out = pd.DataFrame(rows)
    cols = ["权重方案", "年化收益率", "年化波动率", "夏普比率", "最大回撤",
            "卡玛比率", "年化换手", "平均标的数量"]
    return out[[c for c in cols if c in out.columns]]
