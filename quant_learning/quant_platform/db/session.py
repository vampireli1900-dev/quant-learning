# -*- coding: utf-8 -*-
"""
db/session.py —— 引擎与会话管理
================================
URL 解析优先级：
    1. 显式传入的 url 参数
    2. 环境变量 QP_DATABASE_URL
    3. 默认 SQLite（项目根目录 data/quant.db）

为什么默认 SQLite：
    课程作业/演示要能一键跑起来 —— 单文件、零服务、无需装数据库。
    但业务代码里**没有一句 SQLite 专有语法**，全部由 SQLAlchemy 生成标准 SQL，
    因此把 DSN 换成 PostgreSQL / MySQL 就能整体迁移，连建表语句都不用手写。

三个容易踩坑、这里已经处理好的点：
    1. check_same_thread=False
       —— FastAPI 的工作线程和后台守护线程会共用同一个 engine，
          不关掉这个检查就会报 "SQLite objects created in a thread ..."。
    2. WAL 日志模式
       —— 默认模式下「写」会阻塞「读」，Web 刷新页面会卡住；WAL 让读写并发。
    3. busy_timeout
       —— 遇到写锁自动等待 8 秒，而不是立刻抛 "database is locked"。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import CONFIG, DEFAULT_DB_PATH
from .schema import Base

ENV_URL = "QP_DATABASE_URL"

_engine: Optional[Engine] = None
_factory: Optional[sessionmaker] = None


def default_url() -> str:
    """默认连接串。注意 Windows 盘符路径：sqlite:///D:/xxx 的写法。"""
    return f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"


def resolve_url(url: Optional[str] = None) -> str:
    if url:
        return url
    if CONFIG.db.url:
        return CONFIG.db.url
    env = os.getenv(ENV_URL)
    if env:
        return env
    return default_url()


def is_sqlite(url: Optional[str] = None) -> bool:
    return resolve_url(url).startswith("sqlite")


def _install_sqlite_pragmas(engine: Engine) -> None:
    """SQLite 建连时自动执行的一组 PRAGMA（每个新连接都会跑）。"""

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, _connection_record):               # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")     # 读写并发
        cur.execute("PRAGMA busy_timeout=8000")   # 遇锁等待 8s
        cur.execute("PRAGMA foreign_keys=ON")    # SQLite 默认不开外键
        cur.execute("PRAGMA synchronous=NORMAL")  # 兼顾安全与速度
        cur.close()


def get_engine(url: Optional[str] = None, echo: Optional[bool] = None) -> Engine:
    """获取全局单例 engine（URL 变化时自动重建）。"""
    global _engine, _factory
    u = resolve_url(url)
    echo = CONFIG.db.echo if echo is None else echo
    if _engine is None or str(_engine.url) != u:
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {"echo": bool(echo), "future": True}
        if u.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_pre_ping"] = True        # 长连接被服务端踢掉后自动重连
            kwargs["pool_recycle"] = 3600
        _engine = create_engine(u, **kwargs)
        if u.startswith("sqlite"):
            _install_sqlite_pragmas(_engine)
        _factory = None
    return _engine


def get_session_factory(url: Optional[str] = None) -> sessionmaker:
    global _factory
    if _factory is None:
        _factory = sessionmaker(bind=get_engine(url), expire_on_commit=False,
                                autoflush=False)
    return _factory


def get_session(url: Optional[str] = None) -> Session:
    """新建一个 Session（调用方负责关闭）。"""
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


def init_db(url: Optional[str] = None, echo: Optional[bool] = None) -> Engine:
    """建库建表（幂等，表已存在则跳过）。"""
    engine = get_engine(url, echo=echo)
    Base.metadata.create_all(engine)
    return engine


def reset_db(url: Optional[str] = None) -> Engine:
    """危险：删掉所有表后重建（仅开发用）。"""
    engine = get_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine


def db_location(url: Optional[str] = None) -> str:
    """给人看的库位置：SQLite 显示文件路径，其它显示 DSN（隐去密码）。"""
    u = resolve_url(url)
    if u.startswith("sqlite"):
        return u.split("///")[-1]
    if "@" in u:
        scheme, rest = u.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return u


def db_size_bytes(url: Optional[str] = None) -> Optional[int]:
    """SQLite 返回数据文件字节数；其它数据库返回 None。"""
    if not is_sqlite(url):
        return None
    # 只有使用默认路径时才知道文件在哪
    if url is None and not CONFIG.db.url:
        return DEFAULT_DB_PATH.stat().st_size if DEFAULT_DB_PATH.exists() else 0
    p = url or CONFIG.db.url
    return DEFAULT_DB_PATH.stat().st_size if DEFAULT_DB_PATH.exists() and p.startswith("sqlite") else 0
