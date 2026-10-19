# -*- coding: utf-8 -*-
"""进阶测试：行情订阅、多周期数据、多标的、订单权限(只读)"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from ib_insync import IB, Stock, util

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=92, timeout=15)
print("connected:", ib.isConnected())
print("serverTime:", ib.reqCurrentTime())

# --- 1. 是否模拟账户 ---
acc = ib.managedAccounts()[0]
print(f"account={acc}  前缀DU={'是模拟账户(Paper)' if acc.startswith('DU') else '未知/真实'}")

# --- 2. 行情订阅状态 ---
c = Stock("AAPL", "SMART", "USD")
ib.qualifyContracts(c)
for mdt, name in [(1, "实时(1)"), (3, "延迟(3)"), (4, "延迟冻结(4)")]:
    try:
        ib.reqMarketDataType(mdt)
        t = ib.reqTickers(c)[0]
        ib.sleep(2)
        print(f"  marketDataType={name}: last={t.last} close={t.close} bid={t.bid}")
    except Exception as e:
        print(f"  marketDataType={name} 失败: {e}")

# --- 3. 多周期历史数据 ---
tests = [
    ("1 Y", "1 day"),
    ("5 Y", "1 day"),
    ("2 Y", "1 hour"),
    ("1 M", "5 mins"),
    ("1 M", "1 min"),
]
for dur, bar in tests:
    try:
        bars = ib.reqHistoricalData(c, endDateTime="", durationStr=dur,
                                    barSizeSetting=bar, whatToShow="TRADES",
                                    useRTH=True, timeout=40)
        print(f"  {dur:>4} / {bar:>7}: {len(bars)} 根")
        time.sleep(1.2)
    except Exception as e:
        print(f"  {dur} / {bar} 失败: {e}")
        time.sleep(1.5)

# --- 4. 多标的 ---
print("多标的日线(2年):")
for sym in ["SPY", "QQQ", "MSFT", "NVDA", "TSLA", "AMD", "META", "GOOGL", "AMZN", "JPM"]:
    try:
        sc = Stock(sym, "SMART", "USD")
        ib.qualifyContracts(sc)
        bars = ib.reqHistoricalData(sc, endDateTime="", durationStr="2 Y",
                                    barSizeSetting="1 day", whatToShow="TRADES",
                                    useRTH=True, timeout=30)
        print(f"  {sym:<6}: {len(bars)} 根  末收={bars[-1].close if bars else 'NA'}")
        time.sleep(1.2)
    except Exception as e:
        print(f"  {sym} 失败: {e}")
        time.sleep(1.5)

# --- 5. 订单权限(只读查询，不下单) ---
try:
    print("openOrders:", len(ib.reqOpenOrders()), " allOpenOrders:", len(ib.reqAllOpenOrders()))
except Exception as e:
    print("订单查询失败:", e)
try:
    print("positions:", [(p.contract.symbol, p.position) for p in ib.positions()][:5])
except Exception as e:
    print("持仓查询失败:", e)

# --- 6. 限频测试：连续快速请求是否会报 pacing ---
print("限频测试(连续5次1min):")
for i in range(5):
    try:
        bars = ib.reqHistoricalData(c, endDateTime="", durationStr="1 D",
                                    barSizeSetting="1 min", whatToShow="TRADES",
                                    useRTH=True, timeout=20)
        print(f"  第{i+1}次: {len(bars)} 根")
    except Exception as e:
        print(f"  第{i+1}次失败: {type(e).__name__}: {str(e)[:80]}")

ib.disconnect()
print("done")
