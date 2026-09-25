"""迁移执行器 —— 契约《01-共享契约冻结》§4.4 / 10a F4。

规则：
- 扫描 `app/migrations/*.sql`，按编号升序排列。
- 读取 `schema_version` 的最大版本号，只执行未应用的脚本。
- **每个脚本在单事务内执行**；失败则回滚并抛出（调用方拒绝启动）。
- 迁移脚本**不允许包含业务数据**，只允许结构 + settings 种子值。
- 支持手工执行：`python -m app.core.migrate`。

SQLite 的 DDL 支持事务，因此把脚本与版本号写入包在同一个 `BEGIN IMMEDIATE ... COMMIT`
里执行 —— 这样中途失败不会留下"一半结构"。
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import NamedTuple

from app.core.config import get_settings
from app.core.db import ensure_db_dir, get_conn

_MIGRATION_RE = re.compile(r"^(\d+)_([A-Za-z0-9_\-]+)\.sql$")

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    description TEXT
)
"""


class Migration(NamedTuple):
    version: int
    name: str
    path: Path


class MigrationError(RuntimeError):
    """迁移失败。启动流程捕获后应拒绝启动。"""


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    """扫描迁移目录，返回按版本号升序的列表。"""
    target = directory or get_settings().migrations_dir
    if not target.is_dir():
        raise MigrationError(f"迁移目录不存在：{target}")

    found: list[Migration] = []
    for path in sorted(target.glob("*.sql")):
        m = _MIGRATION_RE.match(path.name)
        if not m:
            continue
        found.append(Migration(version=int(m.group(1)), name=m.group(2), path=path))

    found.sort(key=lambda x: x.version)

    # 编号不得重复 / 跳号复用（契约 §4.4）
    versions = [m.version for m in found]
    if len(versions) != len(set(versions)):
        raise MigrationError(f"迁移脚本编号重复：{versions}")

    return found


def ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_VERSION_DDL)


def get_current_version(conn: sqlite3.Connection) -> int:
    """当前已应用的最大版本号；无记录返回 0。"""
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None or row["v"] is None:
        return 0
    return int(row["v"])


def _apply_one(conn: sqlite3.Connection, mig: Migration) -> None:
    """在单事务内执行一个迁移脚本并记录版本号。"""
    sql = mig.path.read_text(encoding="utf-8")
    # 文件名来自本地目录，仍需转义单引号以防万一
    description = mig.name.replace("'", "''")

    script = (
        "BEGIN IMMEDIATE;\n"
        f"{sql}\n"
        f"INSERT INTO schema_version (version, description) "
        f"VALUES ({mig.version}, '{description}');\n"
        "COMMIT;\n"
    )

    try:
        conn.executescript(script)
    except Exception as exc:  # noqa: BLE001 - 需统一包装后向上抛
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise MigrationError(
            f"迁移 {mig.path.name} 执行失败，已回滚：{exc}"
        ) from exc


def run_migrations() -> list[int]:
    """应用所有未执行的迁移，返回本次执行的版本号列表。

    幂等：已应用的版本会跳过（10a U2 —— 二次启动不重复执行）。
    """
    ensure_db_dir()
    conn = get_conn()
    applied: list[int] = []
    try:
        ensure_schema_version_table(conn)
        current = get_current_version(conn)

        for mig in discover_migrations():
            if mig.version <= current:
                continue
            _apply_one(conn, mig)
            applied.append(mig.version)
            current = mig.version
    finally:
        conn.close()

    return applied


def main() -> int:
    """`python -m app.core.migrate` 手工执行入口。"""
    from app.core.config import get_settings as _gs
    from app.core.logging import get_logger, setup_logging

    setup_logging(_gs().LOG_LEVEL)
    logger = get_logger("app.core.migrate")

    try:
        applied = run_migrations()
    except MigrationError as exc:
        logger.error("迁移失败：%s", exc)
        return 1

    if applied:
        logger.info("迁移完成，本次应用版本：%s", applied)
    else:
        logger.info("数据库已是最新，无需迁移。")
    logger.info("数据库文件：%s", get_settings().db_file)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
