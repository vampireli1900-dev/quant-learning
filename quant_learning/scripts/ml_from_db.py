# -*- coding: utf-8 -*-
"""
scripts/ml_from_db.py —— 从 SQL 数据库读取训练集，训练并评估机器学习模型
======================================================================
演示数据库在"训练集"环节的完整用法：

    bars（API 原始数据）
        └─ db-features 构建特征 → feature_sets / features（训练集落库）
                                      └─ 本脚本：从库里读回训练集 → 训练 → 评估
                                              └─ 结论写回 model_registry

与 strategies/ml.py 的分工：
    * ml.py   ：滚动重训，严格防未来函数，用于**回测**（可信度优先）
    * 本脚本  ：一次性在完整训练集上训练，用于**建模研究与教学演示**

两者都把结论写进 model_registry，便于对比。

用法：
    python -m quant_platform.cli db-features --name demo_trainset --symbols SPY QQQ AAPL
    python -m quant_platform.cli db-features --list
    python scripts/ml_from_db.py --name demo_trainset
    python scripts/ml_from_db.py --name demo_trainset --models rf logreg gbdt
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant_platform.config import RESULTS_DIR                    # noqa: E402
from quant_platform.db import (                                   # noqa: E402
    list_feature_sets, list_models, load_training_set, register_model,
)


def _make_model(kind: str, seed: int = 42):
    from sklearn.ensemble import (GradientBoostingClassifier,
                                  RandomForestClassifier)
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    if kind == "rf":
        est = RandomForestClassifier(n_estimators=300, max_depth=4,
                                     min_samples_leaf=20, random_state=seed,
                                     n_jobs=-1)
    elif kind == "gbdt":
        est = GradientBoostingClassifier(n_estimators=150, max_depth=3,
                                         learning_rate=0.05, random_state=seed)
    else:
        est = LogisticRegression(max_iter=2000, C=0.1)
    return Pipeline([("imputer", SimpleImputer(strategy="median")),
                     ("scaler", StandardScaler()),
                     ("model", est)])


def evaluate(y_true, prob) -> Dict[str, float]:
    from sklearn.metrics import accuracy_score, roc_auc_score

    out = {"AUC": float("nan"), "准确率": float("nan"), "IC": float("nan")}
    try:
        if len(pd.unique(y_true)) > 1:
            out["AUC"] = float(roc_auc_score(y_true, prob))
    except Exception:                                            # noqa: BLE001
        pass
    try:
        out["准确率"] = float(accuracy_score(
        y_true, (np.asarray(prob) >= 0.5).astype(int)))
    except Exception:                                            # noqa: BLE001
        pass
    try:
        out["IC"] = float(pd.Series(prob).corr(
        pd.Series(y_true).astype(float), method="spearman"))
    except Exception:                                            # noqa: BLE001
        pass
    return out


def main():
    ap = argparse.ArgumentParser(description="从数据库读训练集并训练模型")
    ap.add_argument("--name", default="demo_trainset", help="训练集名称")
    ap.add_argument("--models", nargs="*", default=["rf", "logreg", "gbdt"],
                    choices=["rf", "logreg", "gbdt"])
    ap.add_argument("--threshold", type=float, default=0.5)
    a = ap.parse_args()

    Xtr, ytr, Xva, yva, Xte, yte = load_training_set(a.name, return_split=True)
    if Xtr is None or len(Xtr) == 0:
        print("训练集不存在或为空。先构建一个：")
        print("  python -m quant_platform.cli db-features --name demo_trainset "
              "--symbols SPY QQQ AAPL")
        return

    def to_cls(y) -> np.ndarray:
        """标签可能是 0/1 分类 或 未来收益（回归），统一转成涨跌方向。"""
        s = pd.Series(y).astype(float)
        if set(s.dropna().unique().tolist()).issubset({0.0, 1.0}):
            return s.fillna(0).astype(int).values
        return (s > 0).astype(int).values

    y_bin = pd.Series(to_cls(ytr))
    print(f"  train {Xtr.shape} | valid {Xva.shape} | test {Xte.shape}")

    NAMES = {"rf": "随机森林", "logreg": "逻辑回归", "gbdt": "梯度提升"}
    rows = []
    for kind in a.models:
        try:
            m = _make_model(kind)
            m.fit(Xtr, y_bin.values)
            p_va = m.predict_proba(Xva)[:, 1]
            p_te = m.predict_proba(Xte)[:, 1]
            yva_b = to_cls(yva)
            yte_b = to_cls(yte)
            ev = evaluate(yte_b, p_te)
            ev_v = evaluate(yva_b, p_va)
            rows.append({
                "模型": NAMES.get(kind, kind),
                "验证集AUC": round(ev_v["AUC"], 4),
                "测试集AUC": round(ev["AUC"], 4),
                "测试集准确率": round(ev["准确率"], 4),
                "测试集IC": round(ev["IC"], 4),
            })
            register_model(
                strategy=f"ML({kind})",
                symbol=f"FEATURESET:{a.name}",
                source_experiment=a.name,
                sharpe=None, psr=None, dsr=None, selected=False,
                note=(f"训练集 {a.name} | 测试集AUC={ev['AUC']:.4f} "
                      f"准确率={ev['准确率']:.4f} IC={ev['IC']:.4f} "
                      f"(n_train={len(Xtr)}, n_test={len(Xte)})"),
            )
        except Exception as e:                                   # noqa: BLE001
            print(f"  [失败] {kind}: {str(e)[:100]}")

    if rows:
        df = pd.DataFrame(rows).sort_values("测试集AUC", ascending=False)
        print("\n=== 模型评估（训练集来自数据库） ===")
        print(df.to_string(index=False))

        out = Path(RESULTS_DIR) / "ml_from_db"
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "summary.json", "w", encoding="utf-8") as f:
            json.dump({"训练集": a.name,
                       "时间": datetime.now().isoformat(timespec="seconds"),
                       "结果": rows}, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n已写入 model_registry，摘要落盘：{out / 'summary.json'}")
    else:
        print("没有可评估的模型")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
