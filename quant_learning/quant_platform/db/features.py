# -*- coding: utf-8 -*-
"""
db/features.py —— 训练集（特征工程 → 落库 → 读回）
==================================================
课程作业里"训练集"这一部分由这里负责：

    1. 从 bars 表（或 data 层）取原始 OHLCV；
    2. 用 indicators.build_features 生成特征矩阵 X；
    3. 用"未来 h 日收益"生成标签 y（回归标签）或涨跌方向（分类标签）；
    4. 落库到 feature_sets / features 两张表；
    5. 需要训练时再按时间顺序切分 train / valid / test 读回来。

关于未来函数的说明（重要）：
    标签 y 用到了 t+1..t+h 的价格，这**只用于监督学习训练**。
    特征 X 只用截至 t 日收盘可得的信息，因此训练出的模型在 t 日做预测时
    不需要任何未来数据（ML 策略的滚动训练正是这么用的）。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..config import CONFIG
from ..indicators import build_features
from .repository import (
    list_feature_sets, load_bars, load_feature_set, save_feature_set,
)
from .session import session_scope

DEFAULT_LOOKBACKS = (5, 10, 20, 60)


def make_label(df: pd.DataFrame, horizon: int = 1, mode: str = "ret") -> pd.Series:
    """
    生成监督学习标签。

    horizon: 未来多少根 K 线
    mode:
        "ret"  —— 未来 h 根收益（回归）
        "sign" —— 涨跌方向（分类：1 涨 / 0 跌）
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
    构建并保存一个训练集。

    from_db=True  → 直接从 bars 表读（推荐，保证"训练集来自数据库"）
    from_db=False → 走 data 层（IB/yfinance/缓存）

    返回 feature_set_id。
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
            # 顺带落库，保证下次可直接从库里取
            try:
                from .repository import upsert_bars
                upsert_bars(sym, bar_size, df, source="ibkr" if source == "auto" else source)
            except Exception:                                    # noqa: BLE001
                pass
        if df is None or len(df) < 120:
            print(f"[跳过] {sym}: 数据不足")
            continue

        feat = build_features(df, lookbacks=tuple(lookbacks))
        lab = make_label(df, horizon=horizon, mode=label_mode)
        feat = feat.drop(columns=["ret_fwd"], errors="ignore")     # 标签单独存
        frames[sym] = feat
        labels[sym] = lab

    if not frames:
        raise RuntimeError("没有可用数据，训练集构建失败")

    # 合并成 MultiIndex(symbol, ts)
    X = pd.concat({s: f for s, f in frames.items()}, names=["symbol", "ts"])
    y = pd.concat({s: l for s, l in labels.items()}, names=["symbol", "ts"])
    y = y.reindex(X.index)
    # 丢掉标签为 NaN 的行（序列尾部天然无法标注）
    valid = y.notna()
    X, y = X[valid], y[valid]

    label_name = f"{'sign' if label_mode == 'sign' else 'ret'}_fwd_{horizon}"
    fs_id = save_feature_set(name=name, feat=X, label=y, symbols=list(frames.keys()),
                             description=description or f"{bar_size} / {duration} / 特征"
                                                        f"{len(X.columns)}个",
                             train_ratio=train_ratio, label_name=label_name)
    print(f"[训练集] {name} 已保存：样本 {len(X)} 行 × 特征 {len(X.columns)} 个 "
          f"（{list(frames.keys())}），标签 {label_name}")
    return fs_id


def load_training_set(name: str, return_split: bool = False):
    """读回训练集；return_split=True 时按时间切分三份。"""
    return load_feature_set(name, return_split=return_split)


def feature_set_summary(session=None) -> pd.DataFrame:
    return list_feature_sets()


def describe_feature_set(name: str) -> pd.DataFrame:
    """某个训练集每个特征的统计描述（缺失率/均值/标准差/分位）。"""
    X, y = load_feature_set(name)
    if X.empty:
        return pd.DataFrame()
    desc = X.describe().T
    desc["缺失率"] = X.isna().mean()
    desc = desc.reset_index().rename(columns={"index": "特征"})
    return desc
