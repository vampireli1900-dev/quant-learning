# -*- coding: utf-8 -*-
"""测试：下单权限(whatIf 试算，不实际成交)、港美股/ETF/外汇多市场数据可得性"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from ib_insync import IB, Stock, Forex, LimitOrder, MarketOrder

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=93, timeout=15)
print("connected:", ib.isConnected())

# --- 1. whatIf 试算：验证是否有下单权限(不会真实成交) ---
print("\n[下单权限试算 whatIf]")
c = Stock("AAPL", "SMART", "USD")
ib.qualifyContracts(c)
try:
    o = LimitOrder("BUY", 1, 100.0)  # 极低限价，即使真发单也不会成交
    st = ib.whatIfOrder(c, o)
    print(f"  initMargin={st.initMargin}  maintMargin={st.maintMargin}  "
          f"commission={st.commission}  equityWithLoan={st.equityWithLoan}")
    print("  => 具备下单权限(API 非只读)")
except Exception as e:
    print(f"  whatIf 失败: {type(e).__name__}: {str(e)[:150]}")
    print("  => 可能启用了 Read-Only API 或无该品种权限")

# --- 2. 多市场数据可得性 ---
print("\n[多市场历史数据]")
tests = [
    ("美股 ETF", Stock("IWM", "SMART", "USD")),
    ("美股 DIA", Stock("DIA", "SMART", "USD")),
    ("港股 腾讯", Stock("700", "SEHK", "HKD")),
    ("港股 盈富", Stock("2800", "SEHK", "HKD")),
    ("美股 中概", Stock("BABA", "SMART", "USD")),
    ("外汇 EURUSD", Forex("EURUSD")),
]
for name, con in tests:
    try:
        ib.qualifyContracts(con)
        bars = ib.reqHistoricalData(con, endDateTime="", durationStr="6 M",
                                    barSizeSetting="1 day", whatToShow="TRADES",
                                    useRTH=True, timeout=30)
        last = bars[-1].close if bars else None
        print(f"  {name:<12} conId={con.conId:<10} {len(bars):>4} 根  末收={last}")
    except Exception as e:
        print(f"  {name:<12} 失败: {str(e)[:110]}")
    time.sleep(1.3)

# --- 3. 账户净值/持仓详情 ---
print("\n[账户]")
try:
    for av in ib.accountValues()[:0]:
        pass
    vals = {v.tag: v.value for v in ib.accountValues() if v.currency == "BASE"}
    for k in ["NetLiquidation", "TotalCashValue", "BuyingPower", "GrossPositionValue"]:
        print(f"  {k:<20} {vals.get(k, 'NA')} (BASE)")
except Exception as e:
    print("  账户值读取失败:", e)

ib.disconnect()
print("\ndone")
