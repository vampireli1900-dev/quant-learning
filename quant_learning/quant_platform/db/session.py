# -*- coding: utf-8 -*-
"""
db/session.py —— 引擎与会话管理
================================
数据库 URL 的优先级：
    1. 显式传入的 url 参数
    2. 环境变量 QP_DATABASE_URL
        sqlite:     sqlite:///D:/path/quant.db
        postgresql: postgresql+psycopg2://user:pwd@host:5432/quant
        mysql:      mysql+pymysql://user:pwd@host:3306/quant
    3. 默认：项目根目录下 data/quant.db（SQLite）

SQLite 下的三项性能优化（很关键，否则多线程 Web 场景会锁库）：
    * WAL 模式         —— 读写并发，读不阻塞写
    * busy_timeout     —— 遇到锁自动等待而不是立刻报错
    * foreign_keys=ON  —— SQLite 默认不启用外键，必须手动开
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import PROJECT_ROOT
from .schema import Base

DEFAULT_DB_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "quant.db"
ENV_URL = "QP_DATABASE_URL"

_engine: Optional[Engine] = None
_SessionFactory: Optional[sessionmaker] = None


def default_url() -> str:
    return f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"


def resolve_url(url: Optional[str] = None) -> str:
    """URL 优先级：显式参数 > 环境变量 QP_DATABASE_URL > CONFIG.db.url > 默认 SQLite。"""
    if url:
        return url
    env = os.getenv(ENV_URL)
    if env:
        return env
    try:
        from ..config import CONFIG
        if getattr(CONFIG, "db", None) and CONFIG.db.url:
            return CONFIG.db.url
    except Exception:                                            # noqa: BLE001
        pass
    return default_url()


def is_sqlite(url: Optional[str] = None) -> bool:
    return resolve_url(url).startswith("sqlite")


def get_engine(url: Optional[str] = None, echo: bool = False) -> Engine:
    """获取全局单例 engine（同一 URL 复用）。"""
    global _engine
    u = resolve_url(url)
    if _engine is None or str(_engine.url) != u:
        DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
        kwargs = {"echo": echo, "future": True}
        if u.startswith("sqlite"):
            # check_same_thread=False：FastAPI 线程池 + 后台守护线程共用 engine
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_pre_ping"] = True       # 长连接被服务端断开后自动重连
            kwargs["pool_recycle"] = 3600
        _engine = create_engine(u, **kwargs)
        if u.startswith("sqlite"):
            _install_sqlite_pragmas(_engine)
        global _SessionFactory
        _SessionFactory = None
    return _engine


def _install_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):        # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=8000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


def get_session_factory(url: Optional[str] = None) -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(url), expire_on_commit=False,
                                       autoflush=False)
    return _SessionFactory


def get_session(url: Optional[str] = None) -> Session:
    """新建一个 Session（调用方负责关闭；Web 请求里建议用 session_scope）。"""
    return get_session_factory(url)()


@contextmanager
def session_scope(url: Optional[str] = None) -> Iterator[Session]:
    """事务上下文：正常提交、异常回滚、结束即关闭。"""
    s = get_session(url)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db(url: Optional[str] = None, echo: bool = False) -> Engine:
    """建库建表（幂等）。"""
    engine = get_engine(url, echo=echo)
    Base.metadata.create_all(engine)
    return engine


def reset_db(url: Optional[str] = None) -> Engine:
    """危险：删掉所有表后重建（仅用于开发）。"""
    engine = get_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine


def db_path_or_dsn(url: Optional[str] = None) -> str:
    """给人看的：SQLite 显示文件路径，其它显示 DSN（隐藏密码）。"""
    u = resolve_url(url)
    if u.startswith("sqlite"):
        return u.split("///")[-1]
    if "@" in u:
        head, tail = u.split("@", 1)
        return f"{head.split('://')[0]}://***@{tail}"
    return u


def db_file_size(url: Optional[str] = None) -> Optional[int]:
    if is_sqlite(url):
        p = Path(resolve_url(url).split("///")[-1])
        return p.stat().st_size if p.exists() else 0
    return None
