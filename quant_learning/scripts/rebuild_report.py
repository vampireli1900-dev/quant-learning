# -*- coding: utf-8 -*-
"""
重新生成 HTML 报告（不必重跑昂贵的实验）。

优先从 results/<name>/result.pkl 恢复完整结果；
若没有 pkl，则从已落盘的 CSV 重建一个"够画图/制表"的结果对象。

    python scripts/rebuild_report.py main_compare
"""
import pickle
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant_platform.config import ensure_dirs                      # noqa: E402
from quant_platform.experiment import build_html_report            # noqa: E402
from quant_platform.experiment.runner import ExperimentConfig, ExperimentResult  # noqa: E402

ensure_dirs()


def load_from_csv(name: str) -> ExperimentResult:
    d = ROOT / "results" / name
    res = ExperimentResult(config=ExperimentConfig(name=name))
    if (d / "detail.csv").exists():
        res.detail = pd.read_csv(d / "detail.csv", index_col=0)
    if (d / "by_strategy.csv").exists():
        res.by_strategy = pd.read_csv(d / "by_strategy.csv", index_col=0)
    if (d / "equity_curves.csv").exists():
        eq = pd.read_csv(d / "equity_curves.csv", index_col=0, parse_dates=True)
        res.equity_curves = {c: eq[c].dropna() for c in eq.columns}
    if (d / "benchmark.csv").exists():
        bm = pd.read_csv(d / "benchmark.csv", index_col=0, parse_dates=True)
        res.benchmark_curves = {c: bm[c].dropna() for c in bm.columns}
    for p in d.glob("opt_*.csv"):
        res.optimizations[p.stem[4:]] = pd.read_csv(p)
    res.config.symbols = sorted(res.benchmark_curves.keys())
    return res


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "main_compare"
    pkl = ROOT / "results" / name / "result.pkl"
    if pkl.exists():
        with open(pkl, "rb") as f:
            res = pickle.load(f)
        print("从 result.pkl 恢复完整结果")
    else:
        res = load_from_csv(name)
        print("从 CSV 重建结果（无走前/无 result.pkl 时使用）")
    path = build_html_report(res)
    print(f"报告已生成: {path}")
