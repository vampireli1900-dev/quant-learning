from ib_insync import *

ib = IB()
ib.connect('127.0.0.1', port=7497, clientId=1)
print("连接状态：", ib.isConnected())

# ========== 账户摘要，打印带中文说明 ==========
print("\n===== 账户信息 ======")
account = ib.accountSummary()
for item in account:
    # 字段映射字典
    tag_map = {
        "AccountCode":"账户编号",
        "NetLiquidation":"账户净清算权益(总资产)",
        "TotalCashValue":"总现金余额",
        "AvailableFunds":"可用资金",
        "BuyingPower":"购买力",
        "EquityWithLoanValue":"含贷款权益",
        "InitialMarginReq":"初始保证金",
        "MaintenanceMarginReq":"维持保证金",
        "UnrealizedPnL":"未实现浮盈浮亏",
        "RealizedPnL":"已实现盈亏",
        "TotalPositions":"总持仓数"
    }
    desc = tag_map.get(item.tag, item.tag)
    print(f"{desc:20} : {item.value}")

# ========== 获取AAPL历史K线 ==========
contract = Stock("AAPL", "SMART", "USD")
bars = ib.reqHistoricalData(
    contract, endDateTime='', durationStr='30 D',
    barSizeSetting='1 hour', whatToShow='TRADES', useRTH=True
)
df = util.df(bars)

print("\n===== AAPL 1小时K线数据 ======")
# 重命名列，输出中文表头
df_chinese = df.rename(columns={
    "date":"时间",
    "open":"开盘",
    "high":"最高",
    "low":"最低",
    "close":"收盘",
    "volume":"成交量",
    "barCount":"成交笔数",
    "average":"平均成交价"
})
print(df_chinese.head())

ib.disconnect()
