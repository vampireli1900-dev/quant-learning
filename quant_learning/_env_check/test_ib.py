# -*- coding: utf-8 -*-
"""测试盈透 IB API 连通性：连接、账户、历史K线、实时行情"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from ib_insync import IB, Stock, util

HOST = "127.0.0.1"
PORT = 7497
CLIENT_ID = 91

ib = IB()
print(f"[1] 尝试连接 {HOST}:{PORT} clientId={CLIENT_ID} ...")
t0 = time.time()
try:
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=15)
except Exception as e:
    print(f"    连接失败: {type(e).__name__}: {e}")
    sys.exit(1)
print(f"    连接成功耗时 {time.time()-t0:.2f}s, isConnected={ib.isConnected()}")

print("[2] 账户信息:")
try:
    for acc in ib.managedAccounts():
        print(f"    账号: {acc}")
    ib.reqAccountSummaryAsync  # noqa
    summary = ib.accountSummary()
    for s in summary[:12]:
        print(f"    {s.tag:<28} {s.value:<20} {s.currency}")
except Exception as e:
    print(f"    账户信息失败: {e}")

print("[3] 合约资格检查 (AAPL / SPY / MSFT):")
contracts = {}
for sym in ["AAPL", "SPY", "MSFT"]:
    try:
        c = Stock(sym, "SMART", "USD")
        ib.qualifyContracts(c)
        contracts[sym] = c
        print(f"    {sym} -> conId={c.conId} ok")
    except Exception as e:
        print(f"    {sym} 失败: {e}")

print("[4] 拉取历史日K线 (AAPL 1年):")
df = None
if "AAPL" in contracts:
    try:
        bars = ib.reqHistoricalData(
            contracts["AAPL"], endDateTime="", durationStr="1 Y",
            barSizeSetting="1 day", whatToShow="TRADES", useRTH=True,
            timeout=30,
        )
        df = util.df(bars)
        print(f"    拿到 {len(df)} 根K线")
        print(df.tail(3).to_string())
    except Exception as e:
        print(f"    历史数据失败: {e}")

print("[5] 实时行情快照 (AAPL delayed/live):")
if "AAPL" in contracts:
    try:
        ib.reqMarketDataType(3)  # 3=延迟, 1=实时, 4=延迟冻结
        t = ib.reqTickers(contracts["AAPL"])
        ib.sleep(3)
        for tk in t:
            print(f"    last={tk.last} bid={tk.bid} ask={tk.ask} close={tk.close}")
    except Exception as e:
        print(f"    行情失败: {e}")

print("[6] 订单权限/只读检查:")
try:
    print(f"    clientId={ib.client.clientId}")
    print(f"    managedAccounts={ib.managedAccounts()}")
except Exception as e:
    print(f"    {e}")

ib.disconnect()
print("完成，已断开")
