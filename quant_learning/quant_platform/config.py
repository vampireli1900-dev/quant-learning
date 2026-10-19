# -*- coding: utf-8 -*-
"""
config.py —— 全局配置与路径管理
================================
集中定义：根目录、缓存目录、结果目录、IB 连接参数、回测默认成本假设。

为什么要集中配置？
    实验平台里"参数到处硬编码"是灾难：改一次手续费要翻十个文件。
    这里用一个 Config 数据类 + YAML 覆盖机制解决。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

# ----------------------------------------------------------------------------
# 路径
# ----------------------------------------------------------------------------
# __file__ = .../quant_learning/quant_platform/config.py
PACKAGE_DIR = Path(__file__).resolve().parent          # quant_platform/
PROJECT_ROOT = PACKAGE_DIR.parent                       # quant_learning/
CACHE_DIR = PROJECT_ROOT / "data_cache"
RESULTS_DIR = PROJECT_ROOT / "results"
CONFIG_DIR = PROJECT_ROOT / "configs"
LOG_DIR = PROJECT_ROOT / "logs"


def ensure_dirs() -> None:
    """创建平台需要的目录（幂等）。"""
    for d in (CACHE_DIR, RESULTS_DIR, CONFIG_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------------
# 配置数据类
# ----------------------------------------------------------------------------
@dataclass
class IBConfig:
    """盈透连接配置。"""
    host: str = "127.0.0.1"
    port: int = 7497            # TWS 纸面/实盘默认 7497；IB Gateway 纸面 4002 / 实盘 4001
    # 默认 clientId 带进程随机偏移，避免多个脚本并行时撞号（错误 326）
    client_id: int = field(default_factory=lambda: 20 + (os.getpid() % 60))
    timeout: float = 30.0
    readonly: bool = True       # 安全默认：只读，不下单
    account: str = ""           # 留空则自动取 managedAccounts()[0]
    # 历史数据限频保护：两次请求之间最小间隔（秒）
    min_request_interval: float = 1.2
    max_retry: int = 3


@dataclass
class BacktestConfig:
    """回测的默认市场假设。"""
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.0005     # 单边手续费率（万五，接近 IB 美股阶梯定价）
    slippage_rate: float = 0.0005       # 单边滑点（按成交额比例）
    min_commission: float = 1.0         # 每笔最低佣金（美元）
    allow_short: bool = False           # 策略默认只做多
    trading_days_per_year: int = 252
    risk_free_rate: float = 0.0         # 无风险利率（年化），可自行调整


@dataclass
class DBConfig:
    """数据库配置（SQL 持久化）。"""
    # 留空 → 默认 SQLite：项目根目录 data/quant.db
    # PostgreSQL: postgresql+psycopg2://user:pwd@host:5432/quant
    # MySQL     : mysql+pymysql://user:pwd@host:3306/quant
    url: str = ""
    enabled: bool = True          # 总开关；关掉后所有写库静默跳过
    echo: bool = False            # 打印 SQL（调试用）
    persist_bars: bool = True     # API 行情落库
    persist_experiments: bool = True   # 实验/回测结果落库
    persist_orders: bool = True   # 委托与账户快照落库
    equity_stride: int = 3        # 净值曲线抽样步长（1=不抽样）


@dataclass
class Config:
    """平台总配置。"""
    ib: IBConfig = field(default_factory=IBConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    db: DBConfig = field(default_factory=DBConfig)
    cache_enabled: bool = True
    cache_dir: str = str(CACHE_DIR)
    results_dir: str = str(RESULTS_DIR)
    log_level: str = "INFO"

    # ---------------- YAML ----------------
    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Config":
        cfg = cls()
        if "ib" in raw:
            cfg.ib = IBConfig(**{**asdict(cfg.ib), **raw["ib"]})
        if "backtest" in raw:
            cfg.backtest = BacktestConfig(**{**asdict(cfg.backtest), **raw["backtest"]})
        if "db" in raw:
            cfg.db = DBConfig(**{**asdict(cfg.db), **raw["db"]})
        for k in ("cache_enabled", "cache_dir", "results_dir", "log_level"):
            if k in raw:
                setattr(cfg, k, raw[k])
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ib": asdict(self.ib),
            "backtest": asdict(self.backtest),
            "db": asdict(self.db),
            "cache_enabled": self.cache_enabled,
            "cache_dir": self.cache_dir,
            "results_dir": self.results_dir,
            "log_level": self.log_level,
        }


# 全局单例（可直接 from quant_platform.config import CONFIG）
CONFIG = Config()

# 可被环境变量覆盖（方便 CI / 不同机器）
if os.getenv("QP_IB_PORT"):
    CONFIG.ib.port = int(os.environ["QP_IB_PORT"])
if os.getenv("QP_IB_HOST"):
    CONFIG.ib.host = os.environ["QP_IB_HOST"]


def load_config(path: Optional[str | Path] = None) -> Config:
    """加载配置；path 为空时返回默认配置。"""
    ensure_dirs()
    if path is None:
        return CONFIG
    return Config.from_yaml(path)
