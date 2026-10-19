# -*- coding: utf-8 -*-
"""预拉取实验用数据到本地缓存（避免实验过程中被限频拖慢）。"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quant_platform.data import get_data

SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "700.HK"]
DURATION = "5 Y"

if __name__ == "__main__":
    for s in SYMBOLS:
        t0 = time.time()
        try:
            df = get_data(s, duration=DURATION, bar_size="1 day", refresh=True)
            print(f"{s:<8} {len(df):>5} 根  {df.index[0].date()} ~ {df.index[-1].date()}  "
                  f"({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"{s:<8} 失败: {e}")
