# -*- coding: utf-8 -*-
"""
strategies/ml.py —— 机器学习策略（严格走前训练，杜绝未来函数）
==============================================================
这是全平台最容易"作弊"的地方，所以实现上做了三道防线：

    1. 特征只用截至 t 日收盘可得的数据；标签是 t→t+1 的收益。
    2. 训练窗口永远在预测日之前：预测第 t 天用的模型，只用 [0, t-1] 的数据训练。
    3. 滚动重训（每 refit_every 根 K 线重训一次），模拟真实上线后的模型更新。

模型可选：随机森林(rf) / 逻辑回归(logreg) / 梯度提升(gbdt)。
信号：预测上涨概率 > 阈值 → 持仓 1，否则 0（也可用概率做仓位权重）。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..indicators import build_features
from .base import Strategy, StrategyMeta, register


def _make_model(kind: str, seed: int = 42):
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler

    if kind == "rf":
        est = RandomForestClassifier(n_estimators=200, max_depth=4,
                                     min_samples_leaf=20, random_state=seed,
                                     n_jobs=-1)
    elif kind == "gbdt":
        est = GradientBoostingClassifier(n_estimators=100, max_depth=3,
                                         learning_rate=0.05, random_state=seed)
    else:
        est = LogisticRegression(max_iter=1000, C=0.1)
    return Pipeline([("imputer", SimpleImputer(strategy="median")),
                     ("scaler", StandardScaler()),
                     ("model", est)])


@register
class MLClassifier(Strategy):
    """滚动重训的机器学习方向预测策略。"""
    meta = StrategyMeta("机器学习方向预测", "机器学习",
                        "用价量特征滚动训练分类器，预测次日涨跌并择时",
                        "Walk-forward，严格防未来函数")
    default_params = {
        "model": "rf",            # rf / logreg / gbdt
        "threshold": 0.55,        # 上涨概率阈值
        "refit_every": 21,        # 每多少根K线重训
        "train_window": 750,      # 训练窗口长度（0 表示全历史扩展窗口）
        "min_train": 250,         # 至少多少样本才开始预测
        "use_prob_weight": False, # True 时用概率做连续仓位
    }
    param_space = {"model": ["rf", "logreg"], "threshold": [0.52, 0.55, 0.60],
                   "refit_every": [21, 42]}
    favored_regime = "数据充足的活跃标的"

    def __init__(self, **params):
        super().__init__(**params)
        self._last_model_info: dict = {}

    def signals(self, df: pd.DataFrame, verbose: bool = False) -> pd.Series:
        p = self.params
        feat = build_features(df)
        feature_cols = [c for c in feat.columns if c != "ret_fwd"]
        X_all = feat[feature_cols]
        y_all = (feat["ret_fwd"] > 0).astype(int)
        y_all[feat["ret_fwd"].isna()] = np.nan

        n = len(X_all)
        sig = np.zeros(n)
        model = None
        last_fit = -10 ** 9
        min_train = min(p["min_train"], max(50, n // 3))

        for t in range(min_train, n):
            need_refit = model is None or (t - last_fit) >= p["refit_every"]
            if need_refit:
                start = 0 if not p["train_window"] else max(0, t - p["train_window"])
                Xtr = X_all.iloc[start:t]              # 不含 t（无未来函数）
                ytr = y_all.iloc[start:t]
                mask = ytr.notna()
                Xtr, ytr = Xtr[mask], ytr[mask]
                if len(ytr) < 60 or ytr.nunique() < 2:
                    sig[t] = 0.0
                    continue
                model = _make_model(p["model"])
                try:
                    model.fit(Xtr, ytr)
                    last_fit = t
                except Exception:
                    model = None
                    continue

            xt = X_all.iloc[[t]]
            if xt.isna().all().all():
                sig[t] = 0.0
                continue
            try:
                prob = model.predict_proba(xt)[0, 1]
            except Exception:
                sig[t] = 0.0
                continue

            if p["use_prob_weight"]:
                # 概率转成仓位：以 0.5 为中心线性放大，截到 [0,1]
                sig[t] = float(np.clip((prob - 0.5) * 4, 0, 1))
            else:
                sig[t] = 1.0 if prob >= p["threshold"] else 0.0

        self._last_model_info = {"n_samples": n, "n_features": len(feature_cols),
                                 "predicted_from": min_train}
        return pd.Series(sig, index=df.index)


@register
class MLWithTrendFilter(Strategy):
    """
    ML 预测 + 趋势过滤：
    只有当 ML 看多 且 价格在 200 日均线上方（大趋势向上）时才持仓。
    展示"模型信号 + 硬性风控/过滤条件"的组合思路。
    """
    meta = StrategyMeta("ML+趋势过滤", "机器学习",
                        "ML看多 且 价格>长期均线 才持仓")
    default_params = {"model": "rf", "threshold": 0.55, "refit_every": 21,
                      "train_window": 750, "min_train": 250, "trend_ma": 200}
    param_space = {"threshold": [0.52, 0.55], "trend_ma": [150, 200]}
    favored_regime = "牛市"

    def signals(self, df: pd.DataFrame) -> pd.Series:
        from ..indicators import sma
        ml_sig = MLClassifier(**{k: v for k, v in self.params.items()
                                 if k in MLClassifier.default_params}).signals(df)
        trend_up = df["close"] > sma(df["close"], self.params["trend_ma"])
        return (ml_sig * trend_up.fillna(False)).astype(float)
