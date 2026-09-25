"""SQLite 连接与事务 —— 契约《01-共享契约冻结》§4.3 / 10a F3。

设计要点：
- **单文件 SQLite**，不用 ORM，全部显式 SQL。
- 每次建立连接都执行 4 条 PRAGMA（契约 §4.3），业务代码不要自己写 PRAGMA。
- **写操作必须串行化**：SQLite 是单写者模型，仅靠 `busy_timeout` 在并发下仍会
  `database is locked`。因此 `write_tx()` 用**进程级 `threading.Lock`** 兜住，
  锁的粒度是「整个写事务」，不是单条语句。
- 读操作走 `read_conn()`，与写互不阻塞（WAL 模式下读写可并发）。

用法：
    with write_tx() as conn:
        conn.execute("INSERT ...")

    with read_conn() as conn:
        rows = conn.execute("SELECT ...").fetchall()
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.core.config import get_settings

# 进程级写锁 —— 保证同一时刻只有一个写事务（契约 §4.3）
_write_lock = threading.RLock()

# 4 条 PRAGMA，每次连接必执行
_PRAGMAS = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA synchronous = NORMAL",
)


def db_path() -> Path:
    """当前数据库文件路径（来自 .env 的 DB_PATH）。"""
    return get_settings().db_file


def ensure_db_dir() -> None:
    """确保数据库所在目录存在（首次启动时 data/ 可能还不存在）。"""
    db_path().parent.mkdir(parents=True, exist_ok=True)


def get_conn(path: Path | str | None = None) -> sqlite3.Connection:
    """创建一个新连接，并设置 4 条 PRAGMA。

    `isolation_level=None` = 关闭 sqlite3 模块的隐式事务管理，
    由我们自己用显式 `BEGIN` / `COMMIT` 控制事务边界。
    """
    target = Path(path) if path is not None else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(target),
        timeout=5.0,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    return conn


@contextmanager
def write_tx(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """写事务上下文：全局写锁 + `BEGIN IMMEDIATE` + 自动提交/回滚。

    用 `BEGIN IMMEDIATE` 而非 `BEGIN`，让写锁在事务开始时就获取，
    避免升级锁时死锁。
    """
    with _write_lock:
        conn = get_conn(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover - 事务可能已自动回滚
                pass
            raise
        finally:
            conn.close()


@contextmanager
def read_conn(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """只读连接上下文。WAL 模式下读不阻塞写。"""
    conn = get_conn(path)
    try:
        yield conn
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """`sqlite3.Row` → `dict`（API 响应统一用 dict）。"""
    return dict(row) if row is not None else None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def utc_now_iso() -> str:
    """当前 UTC 时间，ISO 8601 且带 `Z`（契约 §4.6）。

    ⚠️ **不要用 `datetime.utcnow()`** —— 它已废弃且不带时区信息。
    全项目时间入库一律走本函数，前端负责转本地时区展示。
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
