# -*- coding: utf-8 -*-
"""
scripts/account_daemon.py —— 账户快照常驻采样器
================================================
定时把模拟账户的净值 / 现金 / 可用资金 / 持仓估值写进数据库。

为什么要单独一个进程：
    看板只在**有人打开页面时**才写快照，关掉浏览器曲线就断了。
    想拿到连续的日内净值曲线，就需要一个常驻进程按固定节拍采样。

落库节流在 dashboard._record_snapshot 里（默认 60s 一条），
所以这里 --interval 设得比 60 小也没意义，不会写得更密。

用法：
    python scripts/account_daemon.py --interval 60
    python scripts/account_daemon.py --interval 300 --quiet   # 只写库不打印
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from quant_platform.live.dashboard import account_board   # noqa: E402


def _log(msg: str):
    print(msg, flush=True)


def _fmt(d: dict) -> str:
    t = d.get("totals") or {}
    cur = t.get("基准币种") or ""

    def n(k, dec=0):
        v = t.get(k)
        return "—" if v is None else f"{v:,.{dec}f}"

    def signed(k, dec=0):
        v = t.get(k)
        if v is None:
            return "—"
        return ("+" if v > 0 else "") + f"{v:,.{dec}f}"

    return (f"{d.get('ts','')} | 净值 {n('净值')} {cur} | 现金 {n('现金')} "
            f"| 可用 {n('可用资金')} | 持仓市值 {n('持仓市值')} "
            f"| 盈亏 {signed('持仓盈亏')} | 当日 {signed('当日盈亏')} "
            f"| 杠杆 {n('杠杆', 2)}×")


def run(interval: int, quiet: bool = False):
    _log(f"账户快照采样器启动：每 {interval}s 采样一次（Ctrl+C 停止）")
    n = 0
    while True:
        n += 1
        try:
            d = account_board(record=True, use_cache=False)
            if d.get("ok"):
                if not quiet:
                    _log(f"[{n}] {_fmt(d)}")
            else:
                _log(f"[{n}] {datetime.now():%H:%M:%S} 采样失败：{d.get('error')}")
        except KeyboardInterrupt:
            _log("[停止] 收到中断")
            break
        except Exception as e:                                # noqa: BLE001
            _log(f"[{n}] [错误] {type(e).__name__}: {str(e)[:200]}")
        time.sleep(max(5, interval))


def main():
    ap = argparse.ArgumentParser(description="账户快照常驻采样")
    ap.add_argument("--interval", type=int, default=60,
                    help="采样间隔（秒）；落库最密 60s 一条")
    ap.add_argument("--quiet", action="store_true", help="不打印每次采样结果")
    ap.add_argument("--once", action="store_true", help="只采样一次")
    a = ap.parse_args()

    if a.once:
        d = account_board(record=True, use_cache=False)
        if d.get("ok"):
            _log(_fmt(d))
        else:
            _log(f"采样失败：{d.get('error')}")
        return
    run(a.interval, quiet=a.quiet)


if __name__ == "__main__":
    main()
