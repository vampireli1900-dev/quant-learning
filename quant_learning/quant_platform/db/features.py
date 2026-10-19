# -*- coding: utf-8 -*-
"""
db/features.py —— 训练集：特征工程 → 落库 → 读回
=================================================
课程作业里"训练集"这一环由这里负责：

    1. 从 bars 表（或 data 层）取原始 OHLCV；
    2. 用 indicators.build_features 生成特征矩阵 X；
    3. 用「未来 h 根 K 线收益」生成标签 y（回归）或涨跌方向（分类）；
    4. 落库到 feature_sets / features；
    5. 需要训练时按时间顺序切分 train / valid / test 读回来。

关于未来函数（写报告时务必说清楚）：
    标签 y 用到了 t+1..t+h 的价格，这**只用于监督学习的训练**。
    特征 X 只用截至 t 日收盘可得的信息，所以模型在 t 日做预测时不需要任何未来数据
    （ML 策略的滚动重训正是这个用法）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from ..indicators import build_features
from .repository import load_bars, load_feature_set, save_feature_set

DEFAULT_LOOKBACKS: Tuple[int, ...] = (5, 10, 20, 60)


def make_label(df: pd.DataFrame, horizon: int = 1, mode: str = "ret") -> pd.Series:
    """
    生成监督学习标签。

    horizon : 预测未来多少根 K 线
    mode    : "ret" = 未来收益（回归）；"sign" = 涨跌方向（分类 1/0）
    """
    fwd = df["close"].shift(-horizon) / df["close"] - 1.0
    if mode == "sign":
        return (fwd > 0).astype(float).where(fwd.notna())
    return fwd


def build_training_set(symbols: Sequence[str],
                       name: str = "trainset",
                       duration: str = "5 Y",
                       bar_size: str = "1 day",
                       lookbacks: Sequence[int] = DEFAULT_LOOKBACKS,
                       horizon: int = 1,
                       label_mode: str = "ret",
                       description: str = "",
                       train_ratio: float = 0.7,
                       source: str = "auto",
                       from_db: bool = True) -> Optional[int]:
    """
    构建并保存一个训练集，返回 feature_set_id。

    from_db=True  → 直接从 bars 表读（推荐：保证训练集确实来自数据库）
    from_db=False → 走 data 层（IB / yfinance / 缓存），读完顺带落库
    """
    from ..data import get_data

    frames: Dict[str, pd.DataFrame] = {}
    labels: Dict[str, pd.Series] = {}

    for sym in symbols:
        df = None
        if from_db:
            df = load_bars(sym, bar_size=bar_size)
        if df is None or len(df) == 0:
            df = get_data(sym, bar_size=bar_size, duration=duration, source=source)
            try:
                from .repository import upsert_bars
                upsert_bars(sym, bar_size, df,
                            source="ibkr" if source in ("auto", "ibkr") else source)
            except Exception:                                     # noqa: BLE001
                pass
        if df is None or len(df) < 120:
            print(f"[跳过] {sym}：数据不足")
            continue

        feat = build_features(df, lookbacks=tuple(lookbacks))
        feat = feat.drop(columns=["ret_fwd"], errors="ignore")     # 标签单独存
        frames[sym] = feat
        labels[sym] = make_label(df, horizon=horizon, mode=label_mode)

    if not frames:
        raise RuntimeError("没有可用数据，训练集构建失败")

    # 合并成 MultiIndex(symbol, ts)
    X = pd.concat({s: f for s, f in frames.items()}, names=["symbol", "ts"])
    y = pd.concat({s: l for s, l in labels.items()}, names=["symbol", "ts"])
    y = y.reindex(X.index)
    # 尾部天然没有标签（未来还没发生），丢掉
    keep = y.notna()
    X, y = X[keep], y[keep]

    label_name = f"{'sign' if label_mode == 'sign' else 'ret'}_fwd_{horizon}"
    fs_id = save_feature_set(
        name=name, feat=X, label=y, symbols=list(frames.keys()),
        description=description or f"{bar_size} / {duration}，{len(X.columns)} 个特征",
        train_ratio=train_ratio, label_name=label_name)
    print(f"[训练集] {name} 已保存：{len(X):,} 样本 × {len(X.columns)} 特征"
          f" | 标签 {label_name}")
    return fs_id


def load_training_set(name: str, return_split: bool = False):
    """读回训练集；return_split=True 时按时间切成 train / valid / test。"""
    return load_feature_set(name, return_split=return_split)


def describe_feature_set(name: str) -> pd.DataFrame:
    """某个训练集每个特征的统计描述（缺失率、均值、标准差、分位数）。"""
    X, y = load_feature_set(name)
    if X is None or X.empty:
        return pd.DataFrame()
    desc = X.describe().T
    desc["缺失率"] = X.isna().mean()
    return desc.reset_index().rename(columns={"index": "特征"})
