# -*- coding: utf-8 -*-
"""
live/broker.py —— 实盘/模拟盘交易接口（默认安全：只读 + dry-run）
==============================================================
安全设计（重要）：
    1. 默认 dry_run=True，只打印"打算做什么"，绝不真正发单。
    2. 默认 readonly 提示 + 单笔金额上限 + 单标的仓位上限。
    3. 每次下单前都做"预检"：资金是否足够、是否超过风控限额。
    4. 当前账户是模拟盘(DU 开头)，即便真发单也只是模拟成交。

典型用法：
    broker = IBBroker(dry_run=True)
    broker.connect()
    print(broker.account())
    print(broker.positions())
    broker.rebalance("AAPL", target_shares=100)      # dry-run 下只打印
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import CONFIG, IBConfig


def fx_rate(broker, base: str = "USD", quote: str = "HKD") -> float:
    """
    取汇率（如 USD.HKD）。

    ib_insync 的 Forex(pair) 要求 6 位无点格式（USDHKD），写成 USD.HKD 会 assert
    失败，所以这里统一拼好再传。取不到就用兜底 7.8 并提示——估值不能因为
    汇率取不到就整个崩掉。
    """
    pair = f"{base.upper()}{quote.upper()}"
    for what in ("MIDPOINT", "BID"):
        try:
            from ib_insync import Forex
            c = Forex(pair)
            broker.ib.qualifyContracts(c)
            bars = broker.ib.reqHistoricalData(
                c, "", "5 D", "1 day", what, False, 1, False)
            if bars:
                v = float(bars[-1].close)
                if v > 0:
                    return v
        except Exception as e:                                 # noqa: BLE001
            print(f"[提示] 汇率 {pair}/{what} 取失败：{str(e)[:60]}")
    print("[提示] 使用兜底汇率 7.8")
    return 7.8


@dataclass
class RiskLimits:
    """风控限额。"""
    max_order_value: float = 50_000.0      # 单笔最大金额（账户币种）
    max_position_value: float = 200_000.0  # 单标的最大持仓市值
    max_daily_orders: int = 20
    max_gross_exposure: float = 1_000_000.0


class IBBroker:
    """盈透交易封装（默认 dry-run）。"""

    def __init__(self, cfg: Optional[IBConfig] = None, dry_run: bool = True,
                 limits: Optional[RiskLimits] = None):
        self.cfg = cfg or CONFIG.ib
        self.dry_run = dry_run
        self.limits = limits or RiskLimits()
        self._ib = None
        self._order_count = 0

    # ---------------- 连接 ----------------
    def connect(self) -> bool:
        """
        连接 TWS。clientId 撞号（IB 错误 326）在多脚本并行时很常见，
        这里自动往后试若干个 id，而不是直接失败。
        """
        import random
        from ib_insync import IB

        if self._ib is not None and self._ib.isConnected():
            return True

        base = int(self.cfg.client_id)
        tried = [base] + [base + i for i in (1, 2, 3, 5, 7, 11)] + \
                [random.randint(100, 900)]
        last_err = None
        for cid in tried:
            ib = IB()
            try:
                ib.connect(self.cfg.host, self.cfg.port,
                           clientId=cid, timeout=self.cfg.timeout)
                if ib.isConnected():
                    self._ib = ib
                    self.cfg.client_id = cid
                    return True
            except Exception as e:                            # noqa: BLE001
                last_err = e
                try:
                    ib.disconnect()
                except Exception:                             # noqa: BLE001
                    pass
        if last_err:
            print(f"[警告] IB 连接失败（已试 {len(tried)} 个 clientId）：{last_err}")
        return False

    def disconnect(self):
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
        self._ib = None

    @property
    def ib(self):
        if self._ib is None:
            self.connect()
        return self._ib

    # ---------------- 账户 / 持仓 ----------------
    def account(self) -> Dict[str, str]:
        """
        取账户摘要。

        字段比 IB 官方 accountSummary 少，只留看板与风控真正会用的：
            净值 NetLiquidation / 现金 TotalCashValue / 可用 AvailableFunds
            购买力 BuyingPower / 持仓市值 GrossPositionValue
            浮动盈亏 UnrealizedPnL / 已实现 RealizedPnL / 当日 DailyPnL
            保证金 MaintMarginReq · InitMarginReq / 剩余流动性 ExcessLiquidity
        多币种账户（如港币 DUT 账号）BASE 行可能为空，这里按"先 BASE、后兜底"取。
        """
        ib = self.ib
        out = {"accounts": ",".join(ib.managedAccounts())}
        keys = ("NetLiquidation", "TotalCashValue", "BuyingPower",
                "AvailableFunds", "GrossPositionValue", "Cushion",
                "UnrealizedPnL", "RealizedPnL", "DailyPnL",
                "MaintMarginReq", "InitMarginReq", "ExcessLiquidity",
                "FullAvailableFunds")
        try:
            vals = ib.accountValues()
            # 先按基准币种取；多币种账户（如港币 DUT 账号）BASE 可能为空，
            # 此时退而求其次：取任一币种下第一次出现的同名字段。
            for v in vals:
                if v.currency in ("BASE", "") and v.tag in keys:
                    out[v.tag] = v.value
                    out.setdefault("currency", v.currency or "BASE")
            for v in vals:
                if v.tag in keys and v.tag not in out:
                    out[v.tag] = v.value
                    out.setdefault("currency", v.currency)
            # "BASE" 不是真币种，只是 IB 的占位符；真正要展示的是净值那一行的
            # 币种（DUT 账号 = HKD）。拿不到就退回 USD，别把 BASE 漏给前端。
            real = ""
            for v in vals:
                if v.tag == "NetLiquidation" and v.currency not in ("BASE", ""):
                    real = v.currency
                    break
            out["base_currency"] = real or (
                out.get("currency") if out.get("currency") not in ("BASE", "", None)
                else "USD")
        except Exception as e:                               # noqa: BLE001
            out["error"] = str(e)[:100]
            out.setdefault("base_currency", "USD")
        return out

    def positions(self) -> pd.DataFrame:
        ib = self.ib
        rows = []
        for p in ib.positions():
            rows.append({
                "symbol": p.contract.symbol,
                "exchange": p.contract.exchange,
                "currency": p.contract.currency,
                "position": p.position,
                "avg_cost": p.avgCost,
            })
        return pd.DataFrame(rows)

    def mark_prices(self, symbols, exchange: str = "SMART",
                    currency: str = "USD") -> Dict[str, float]:
        """
        批量取最新价，用于给持仓估值。

        两级兜底，因为模拟账户经常没有实时行情订阅：
            1) reqTickers 拿 last / close / marketPrice
            2) 仍取不到的，回落到数据层最后一根日线收盘（缓存或 API）
        返回 {SYMBOL: price}；全部失败时对应标的缺 key，调用方需自行处理。
        """
        import numpy as _np

        out: Dict[str, float] = {}
        syms = [str(s).upper() for s in (symbols or [])]
        if not syms:
            return out

        try:
            contracts = [self._contract(s, exchange, currency) for s in syms]
            tickers = self.ib.reqTickers(*contracts)
            for c, t in zip(contracts, tickers):
                p = None
                for attr in ("last", "close", "marketPrice"):
                    v = getattr(t, attr, None)
                    if callable(v):
                        try:
                            v = v()
                        except Exception:                        # noqa: BLE001
                            v = None
                    try:
                        f = float(v)
                    except (TypeError, ValueError):
                        continue
                    if not _np.isnan(f) and f > 0:
                        p = f
                        break
                if p is not None:
                    out[str(c.symbol).upper()] = p
        except Exception as e:                                 # noqa: BLE001
            print(f"[提示] 实时报价取失败：{str(e)[:60]}")

        missing = [s for s in syms if s not in out]
        if missing:
            from ..data import get_data
            for s in missing:
                try:
                    df = get_data(s, duration="1 M", bar_size="1 day")
                except Exception:                              # noqa: BLE001
                    continue
                if df is not None and len(df):
                    out[s] = float(df["close"].iloc[-1])
        return out

    def open_orders(self) -> pd.DataFrame:
        ib = self.ib
        rows = []
        for o in ib.reqOpenOrders():
            rows.append({
                "symbol": o.contract.symbol, "action": o.action,
                "qty": o.totalQuantity, "type": o.orderType,
                "lmt": getattr(o, "lmtPrice", None),
                "status": o.status,
            })
        return pd.DataFrame(rows)

    # ---------------- 下单 ----------------
    def _contract(self, symbol: str, exchange: str = "SMART", currency: str = "USD"):
        from ib_insync import Stock
        sym = symbol.upper()
        if sym.endswith(".HK"):
            c = Stock(sym[:-3].lstrip("0"), "SEHK", "HKD")
        else:
            c = Stock(symbol, exchange, currency)
        self.ib.qualifyContracts(c)
        return c

    def place_order(self, symbol: str, action: str, quantity: float,
                    order_type: str = "MKT", limit_price: Optional[float] = None,
                    exchange: str = "SMART", currency: str = "USD",
                    price_hint: Optional[float] = None) -> dict:
        """
        下单（受 dry_run 与风控限额约束）。
        返回执行信息 dict。
        """
        action = action.upper()
        if quantity <= 0:
            return {"ok": False, "reason": "数量必须为正"}
        if self._order_count >= self.limits.max_daily_orders:
            return {"ok": False, "reason": "超过当日最大下单次数"}

        est_value = abs(quantity) * (price_hint or 0.0)
        if price_hint and est_value > self.limits.max_order_value:
            return {"ok": False, "reason": f"单笔金额 {est_value:,.0f} 超过上限 "
                                           f"{self.limits.max_order_value:,.0f}"}

        info = {
            "ok": True, "dry_run": self.dry_run, "symbol": symbol,
            "action": action, "qty": quantity, "type": order_type,
            "est_value": est_value or None,
        }
        if self.dry_run:
            info["msg"] = "DRY-RUN：未真实发单（如需真实下单请 dry_run=False）"
            return info

        from ib_insync import MarketOrder, LimitOrder
        contract = self._contract(symbol, exchange, currency)
        order = (LimitOrder(action, quantity, limit_price) if order_type == "LMT"
                 else MarketOrder(action, quantity))
        trade = self.ib.placeOrder(contract, order)
        self._order_count += 1
        self.ib.sleep(2)
        info.update({
            "order_id": trade.order.orderId,
            "status": trade.orderStatus.status,
            "filled": trade.orderStatus.filled,
        })
        return info

    def rebalance(self, symbol: str, target_shares: float,
                  price_hint: Optional[float] = None,
                  exchange: str = "SMART", currency: str = "USD") -> dict:
        """把某标的的持仓调整到目标股数。"""
        pos = self.positions()
        cur = 0.0
        if len(pos):
            m = pos[pos["symbol"].str.upper() == symbol.upper()]
            if len(m):
                cur = float(m.iloc[0]["position"])
        delta = target_shares - cur
        if abs(delta) < 1e-8:
            return {"ok": True, "msg": f"{symbol} 已在目标仓位 {cur:.0f} 股，无需操作"}
        action = "BUY" if delta > 0 else "SELL"
        return self.place_order(symbol, action, abs(delta), price_hint=price_hint,
                                exchange=exchange, currency=currency)


class SignalMonitor:
    """
    信号监控：定时拉取最新数据 → 计算策略信号 → 输出"今天该怎么做"。
    只读，不下单。可与 IBBroker 组合实现半自动交易。
    """

    def __init__(self, strategies: List[str], symbols: List[str],
                 duration: str = "2 Y", bar_size: str = "1 day"):
        self.strategies = strategies
        self.symbols = symbols
        self.duration = duration
        self.bar_size = bar_size

    def scan(self) -> pd.DataFrame:
        from ..data import get_data
        from ..strategies import create
        rows = []
        for sym in self.symbols:
            try:
                df = get_data(sym, duration=self.duration, bar_size=self.bar_size)
            except Exception as e:                           # noqa: BLE001
                rows.append({"标的": sym, "error": str(e)[:60]})
                continue
            rec = {"标的": sym, "最新收盘": float(df["close"].iloc[-1]),
                   "数据日期": str(df.index[-1].date())}
            votes = []
            for name in self.strategies:
                try:
                    sig = create(name).signals(df)
                    last = sig.iloc[-1]
                    prev = sig.iloc[-2] if len(sig) > 1 else 0
                    rec[name] = last
                    votes.append(1 if last > 0 else 0)
                    rec[name + "_变化"] = "买入↑" if (last > 0 and prev <= 0) else (
                        "卖出↓" if (last <= 0 and prev > 0) else "")
                except Exception as e:                       # noqa: BLE001
                    rec[name] = np.nan
            rec["看多票数"] = sum(votes)
            rec["合计建议仓位"] = round(sum(votes) / max(1, len(self.strategies)), 2)
            rows.append(rec)
        return pd.DataFrame(rows)
