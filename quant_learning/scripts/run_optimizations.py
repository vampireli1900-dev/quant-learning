# -*- coding: utf-8 -*-
"""
单独补跑"参数寻优"，把 opt_*.csv 写进已有实验结果目录。
（用于主实验已完成、只想补寻优结果的场景）
"""
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant_platform.backtest import grid_search, overfitting_score   # noqa: E402
from quant_platform.data import get_data                            # noqa: E402
from quant_platform.strategies import create                        # noqa: E402

EXP = "main_compare"
SYMBOLS = ["SPY", "NVDA", "700.HK"]
STRATEGIES = ["双均线交叉", "RSI均值回归", "唐奇安突破", "时间序列动量", "ROC动量阈值"]
METRIC = "夏普比率"

if __name__ == "__main__":
    out = ROOT / "results" / EXP
    out.mkdir(parents=True, exist_ok=True)
    rows_summary = []
    for sym in SYMBOLS:
        df = get_data(sym, duration="5 Y")
        for name in STRATEGIES:
            strat = create(name)
            grid = grid_search(df, name, strat.param_space, metric=METRIC, top_n=5)
            safe = "".join(ch if ch not in '|\\/:*?"<>' else "_" for ch in f"{name}|{sym}")
            grid.to_csv(out / f"opt_{safe}.csv", encoding="utf-8-sig", index=False)
            best = grid.iloc[0]
            bp = strat.normalize_params({k: best[k] for k in strat.param_space if k in grid.columns})
            chk = overfitting_score(df, name, bp, metric=METRIC)
            rows_summary.append({"策略": name, "标的": sym, "最优参数": str(bp),
                                 "样本内最优": chk["best"], "邻域均值": chk["neighbor_mean"],
                                 "稳健度": chk["robustness"]})
            print(f"{name:<10}@{sym:<7} 最优{METRIC}={chk['best']:.2f} "
                  f"邻域={chk['neighbor_mean']:.2f} 稳健度={chk['robustness']:.2f}")
    pd.DataFrame(rows_summary).to_csv(out / "optimization_summary.csv",
                                      encoding="utf-8-sig", index=False)
    print(f"\n已写入 {out / 'optimization_summary.csv'}")
