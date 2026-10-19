# -*- coding: utf-8 -*-
"""
data/ibkr_source.py —— 盈透(IBKR)数据源
=======================================
基于 ib_insync，负责：
    * 连接复用（一次连接多次请求，避免频繁握手）
    * 合约自动识别（AAPL / SPY / 700.HK / 2800.HK / EURUSD 写法）
    * 历史 K 线拉取（带限频保护 + 重试 + 分段续拉）
    * 行情快照（可能因无订阅而为空，属于正常情况）

注意：本机 TWS 未订阅实时行情，实时 tick 会返回空值；历史 K 线完全可用。
"""

from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from ..config import CONFIG, IBConfig
from .base import DataRequest, DataSource, normalize_ohlcv

try:
    from ib_insync import IB, Stock, Forex, Index, util
    IB_AVAILABLE = True
except Exception:                                     # pragma: no cover
    IB_AVAILABLE = False
    IB = None  # type: ignore


# 港股的交易所/币种映射（按股票代码段判断）
HK_EXCHANGE = "SEHK"


class IBKRSource(DataSource):
    name = "ibkr"

    def __init__(self, cfg: Optional[IBConfig] = None):
        self.cfg = cfg or CONFIG.ib
        self._ib: Optional[IB] = None
        self._last_request_ts = 0.0

    # ---------------- 连接管理 ----------------
    @property
    def ib(self) -> IB:
        if self._ib is None or not self._ib.isConnected():
            if not IB_AVAILABLE:
                raise RuntimeError("未安装 ib_insync，无法使用 IBKR 数据源")
            # 自动挑选一个空闲的 clientId：多进程/多窗口并行时，
            # TWS 会拒绝"已被占用"的 clientId（错误 326）。
            last_err = None
            for offset in range(12):
                cid = self.cfg.client_id + offset
                ib = IB()
                try:
                    ib.connect(self.cfg.host, self.cfg.port,
                               clientId=cid, timeout=self.cfg.timeout)
                    self._ib = ib
                    self.cfg.client_id = cid
                    return self._ib
                except Exception as e:                        # noqa: BLE001
                    last_err = e
                    try:
                        ib.disconnect()
                    except Exception:
                        pass
            raise RuntimeError(f"IB 连接失败（clientId {self.cfg.client_id} 起 12 个均不可用）: {last_err}")
        return self._ib

    def connect(self) -> bool:
        try:
            _ = self.ib
            return True
        except Exception:
            return False

    def disconnect(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            try:
                self._ib.disconnect()
            except Exception:
                pass
        self._ib = None

    def available(self) -> bool:
        if not IB_AVAILABLE:
            return False
        try:
            return self.ib.isConnected()
        except Exception:
            return False

    def account_summary(self) -> dict:
        """返回账户关键字段（用于监控/风控）。"""
        ib = self.ib
        out = {}
        try:
            for acc in ib.managedAccounts():
                out.setdefault("accounts", []).append(acc)
        except Exception:
            pass
        try:
            vals = {v.tag: v.value for v in ib.accountValues()
                    if v.currency in ("BASE", "HKD", "USD")}
            for k in ("NetLiquidation", "TotalCashValue", "BuyingPower",
                      "GrossPositionValue", "AvailableFunds"):
                if k in vals:
                    out[k] = vals[k]
        except Exception:
            pass
        return out

    # ---------------- 合约构造 ----------------
    def make_contract(self, req: DataRequest):
        sym = req.symbol.strip().upper()

        # 港股：700.HK / 0700.HK / 2800.HK
        if sym.endswith(".HK"):
            code = sym[:-3].lstrip("0")
            return Stock(code, HK_EXCHANGE, "HKD")

        # 外汇：EURUSD
        if len(sym) == 6 and sym.isalpha():
            return Forex(sym)

        # 默认美股
        c = Stock(req.symbol.upper(), req.exchange or "SMART", req.currency or "USD")
        if req.primary_exchange:
            c.primaryExchange = req.primary_exchange
        return c

    def qualify(self, req: DataRequest):
        ib = self.ib
        c = self.make_contract(req)
        ib.qualifyContracts(c)
        return c

    # ---------------- 限频与重试 ----------------
    def _throttle(self) -> None:
        gap = time.time() - self._last_request_ts
        wait = self.cfg.min_request_interval - gap
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.time()

    def _request_with_retry(self, contract, req: DataRequest,
                            end_date: str = "") -> list:
        ib = self.ib
        last_err = None
        for attempt in range(self.cfg.max_retry):
            self._throttle()
            try:
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime=end_date,
                    durationStr=req.duration,
                    barSizeSetting=req.bar_size,
                    whatToShow=req.what_to_show,
                    useRTH=req.use_rth,
                    formatDate=1,
                    timeout=max(20, self.cfg.timeout),
                )
                return bars or []
            except Exception as e:            # noqa: BLE001
                last_err = e
                msg = str(e)
                # 限频报错：退避后重试
                backoff = 2.0 * (attempt + 1)
                if "162" in msg or "pacing" in msg.lower():
                    backoff = 10.0 * (attempt + 1)
                time.sleep(backoff)
        raise RuntimeError(f"IB 历史数据请求失败（已重试 {self.cfg.max_retry} 次）: {last_err}")

    # ---------------- 主接口 ----------------
    def fetch(self, req: DataRequest) -> pd.DataFrame:
        ib = self.ib
        contract = self.qualify(req)

        bars = self._request_with_retry(contract, req)
        if not bars:
            return pd.DataFrame(columns=list(("open", "high", "low", "close", "volume")))

        df = util.df(bars)
        if "date" in df.columns:
            df = df.set_index("date")
        df.index = pd.to_datetime(df.index)

        # 1min/5min 等 intraday 数据 IB 返回的是 UTC，转成本地时间更直观
        if req.bar_size != "1 day" and getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_convert(None)        # type: ignore[union-attr]

        return normalize_ohlcv(df)

    def fetch_long(self, req: DataRequest, total_days: int = 1825,
                   chunk_days: int = 365) -> pd.DataFrame:
        """
        分段拉取长历史：IB 单次请求有最大时长限制，这里按 chunk 往前翻页。
        total_days : 要拉的自然日总天数
        chunk_days : 每一段的天数
        """
        ib = self.ib
        contract = self.qualify(req)
        frames = []
        remaining = total_days
        end = ""            # 空字符串 = 从现在往前

        while remaining > 0 and len(frames) * chunk_days < total_days + chunk_days:
            chunk = min(chunk_days, remaining)
            sub = DataRequest(**{**req.__dict__, "duration": f"{chunk} D"})
            bars = self._request_with_retry(contract, sub, end_date=end)
            if not bars:
                break
            d = util.df(bars)
            frames.append(d)
            # 下一段的结束时间 = 本段最早时间
            first_dt = pd.to_datetime(d["date"] if "date" in d.columns else d.index).min()
            end_dt = pd.to_datetime(first_dt)
            if getattr(end_dt, "tzinfo", None) is not None:
                s = end_dt.strftime("%Y%m%d %H:%M:%S")      # type: ignore[union-attr]
            else:
                s = end_dt.strftime("%Y%m%d %H:%M:%S")
            end = s
            remaining -= chunk
            # 防止死循环：如果返回条数明显少于预期（已到历史尽头），就停
            if len(d) <= 1:
                break
            if len(frames) > 40:
                break

        if not frames:
            return pd.DataFrame(columns=list(("open", "high", "low", "close", "volume")))

        df = pd.concat(frames, ignore_index=True)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.drop_duplicates(subset=["date"]).set_index("date").sort_index()
        else:
            df = df.sort_index()
        return normalize_ohlcv(df)

    # ---------------- 行情快照 ----------------
    def snapshot(self, req: DataRequest, market_data_type: int = 3, wait: float = 3.0) -> dict:
        """
        取一次行情快照。
        market_data_type: 1=实时, 2=冻结, 3=延迟, 4=延迟冻结
        本机未订阅行情时会返回全 NaN，属正常。
        """
        ib = self.ib
        contract = self.qualify(req)
        ib.reqMarketDataType(market_data_type)
        tickers = ib.reqTickers(contract)
        ib.sleep(wait)
        if not tickers:
            return {}
        t = tickers[0]
        return {
            "symbol": req.symbol,
            "last": t.last, "bid": t.bid, "ask": t.ask,
            "close": t.close, "volume": t.volume,
            "market_data_type": market_data_type,
        }

    def current_positions(self) -> pd.DataFrame:
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

    def __del__(self):                                   # pragma: no cover
        try:
            self.disconnect()
        except Exception:
            pass
