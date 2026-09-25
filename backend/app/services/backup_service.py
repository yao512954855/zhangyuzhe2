"""数据备份服务 —— WP-BE-08 F6 / F7 / F8（需求 §11.4）。

做法：`VACUUM INTO`（SQLite 3.27+ 原生），**不停止服务**即可得到一份事务一致、
可独立打开的完整副本。相比「复制文件」它不会拷到写了一半的 WAL 状态。

| 能力 | 入口 | 说明 |
|---|---|---|
| 定时备份 | `create_backup(daily=True)` | 每日 02:00，文件名 `app_YYYYMMDD.db` |
| 手动备份 | `create_backup(daily=False)` | 文件名带秒，**不会覆盖当天定时备份** |
| 完整性校验 | `verify_integrity()` | `PRAGMA integrity_check`，跑在**副本**上 |
| 保留策略 | `prune_backups()` | 默认保留 30 份，超出的按文件名（≈时间序）删最旧 |
| 审计 | 内部调用 `write_audit` | `action='backup.create'` / `'backup.failed'` |

### 两条刻意的设计取舍

1. **不提供「自动恢复」接口**（10b F8 明确要求）。
   `VACUUM INTO` 的副本是**另一个完整库文件**，恢复＝停服 + 换文件 + 删 `-wal`/`-shm`。
   做成 HTTP 接口意味着「一个误点的请求就能把线上库换掉」，风险与收益完全不成比例。
   恢复步骤写在 `ops/README.md`，由人在停机窗口执行。

2. **校验失败时删掉这份坏副本**，也不做保留清理。
   否则坏副本会被当成「最新的一份」留下，而好备份被挤掉 —— 那才是真正的灾难。
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.db import write_tx
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 默认保留份数（10b F6：保留 30 天）
DEFAULT_KEEP = 30

#: 备份文件名前缀（`ops/README.md` 与恢复脚本都按它找文件）
FILE_PREFIX = "app_"

#: 文件名后缀
FILE_SUFFIX = ".db"


@dataclass
class BackupOutcome:
    """一次备份的结果。**失败不抛异常**，由调用方决定怎么呈现。"""

    ok: bool
    file: str | None = None
    path: str | None = None
    size_bytes: int = 0
    integrity_ok: bool | None = None
    integrity_detail: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    error: str | None = None
    removed: list[str] = field(default_factory=list)
    kept: int = 0
    daily: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "file": self.file,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "size_human": human_size(self.size_bytes),
            "integrity_ok": self.integrity_ok,
            "integrity_detail": self.integrity_detail,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "removed": self.removed,
            "kept": self.kept,
            "daily": self.daily,
        }


# --------------------------------------------------------------------------- 工具


def human_size(num_bytes: int) -> str:
    """给管理端显示用（`59 MB`）。"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover - 到不了


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def backup_dir() -> Path:
    return get_settings().backup_dir


def backup_file_name(*, daily: bool, now: datetime | None = None) -> str:
    """`daily=True` → `app_YYYYMMDD.db`；否则 `app_YYYYMMDD_HHMMSS.db`。

    手动备份带秒是为了**不覆盖当天的定时备份** —— 两者同名的话，
    早上手动备一次就把凌晨那份定时备份冲掉了，出问题时没有可回退的基准。
    """
    moment = now or datetime.now(timezone.utc)
    if daily:
        return f"{FILE_PREFIX}{moment:%Y%m%d}{FILE_SUFFIX}"
    return f"{FILE_PREFIX}{moment:%Y%m%d_%H%M%S}{FILE_SUFFIX}"


def list_backups() -> list[Path]:
    """列出备份目录里的备份文件，按文件名升序（≈ 时间序）。"""
    directory = backup_dir()
    if not directory.is_dir():
        return []
    return sorted(
        (p for p in directory.glob(f"{FILE_PREFIX}*{FILE_SUFFIX}") if p.is_file()),
        key=lambda p: p.name,
    )


def verify_integrity(path: Path) -> tuple[bool, str]:
    """对**副本**跑 `PRAGMA integrity_check`。返回 `(ok, detail)`。

    ⚠️ 只读方式打开（`mode=ro`），避免校验动作本身改动副本（比如补 WAL）。
    """
    if not path.is_file():
        return False, "备份文件不存在"
    if path.stat().st_size == 0:
        return False, "备份文件为空（0 字节）"

    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"无法打开备份文件：{exc}"

    detail = str(row[0]) if row else "无返回"
    return detail == "ok", detail


def prune_backups(keep: int = DEFAULT_KEEP) -> list[str]:
    """保留最新的 `keep` 份，返回被删除的文件名。

    「最新」按**文件名**排序（`app_YYYYMMDD[_HHMMSS].db` 的字典序 ≈ 时间序），
    而不是 mtime —— 文件被复制/同步过之后 mtime 会失真，名字不会。
    已知边界：同一天的定时备份（`app_20260924.db`）与早于 02:00 的手动备份
    （`app_20260924_010000.db`）字典序与真实时间相反。仅在恰好卡在第 30/31 份
    边界时才可能有影响（两份都是当天的），可接受，此处记录以免日后当成 bug 排查。
    """
    if keep < 1:
        keep = 1
    files = list_backups()
    if len(files) <= keep:
        return []

    removed: list[str] = []
    for old in files[: len(files) - keep]:
        try:
            old.unlink()
            removed.append(old.name)
        except OSError as exc:
            logger.warning("删除过期备份失败 %s：%s", old, exc)
    if removed:
        logger.info("清理过期备份 %d 份：%s", len(removed), ", ".join(removed))
    return removed


def _dir_writable(directory: Path) -> tuple[bool, str]:
    """真正写一个探针文件再删掉 —— `os.access` 在 Windows 上对 ACL 判定不准。"""
    probe = directory / ".backup_write_probe"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"probe")
        probe.unlink()
        return True, ""
    except OSError as exc:
        return False, f"备份目录不可写：{exc}"


# --------------------------------------------------------------------------- 主流程


def create_backup(
    *,
    daily: bool = False,
    operator_id: int | None = None,
    ip: str | None = None,
    keep: int = DEFAULT_KEEP,
    now: datetime | None = None,
    audit: bool = True,
) -> BackupOutcome:
    """执行一次备份 + 校验 + 保留清理 + 审计。**任何失败都返回 `outcome.ok=False`**。

    Args:
        daily: 定时备份（固定文件名）还是手动备份（带秒）。
        operator_id: 手动备份时是操作人；定时备份传 `None`（系统动作）。
        audit: 是否写 `audit_logs`。测试里造大量文件时可关掉。
    """
    started = _utc_now()
    started_ts = datetime.now(timezone.utc)
    directory = backup_dir()
    outcome = BackupOutcome(ok=False, started_at=started, daily=daily)

    def _fail(reason: str, *, delete_partial: Path | None = None) -> BackupOutcome:
        outcome.error = reason
        outcome.finished_at = _utc_now()
        outcome.duration_ms = int(
            (datetime.now(timezone.utc) - started_ts).total_seconds() * 1000
        )
        if delete_partial is not None:
            try:
                delete_partial.unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                pass
        logger.error("备份失败：%s", reason)
        if audit:
            _audit(success=False, outcome=outcome, operator_id=operator_id, ip=ip)
        return outcome

    writable, why = _dir_writable(directory)
    if not writable:
        return _fail(why)

    source = get_settings().db_file
    if not source.is_file():
        return _fail(f"数据库文件不存在：{source}")

    target = directory / backup_file_name(daily=daily, now=now)
    if target.exists():
        # `VACUUM INTO` 要求目标不存在；同名即覆盖（当日重跑定时备份）
        try:
            target.unlink()
        except OSError as exc:
            return _fail(f"无法覆盖已存在的备份文件：{exc}")

    try:
        conn = sqlite3.connect(str(source), timeout=15.0)
        try:
            conn.execute("VACUUM INTO ?", (str(target),))
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _fail(f"VACUUM INTO 失败：{exc}", delete_partial=target)

    outcome.path = str(target)
    outcome.file = target.name

    ok, detail = verify_integrity(target)
    outcome.integrity_ok = ok
    outcome.integrity_detail = detail
    if not ok:
        return _fail(f"副本完整性校验未通过：{detail}", delete_partial=target)

    try:
        outcome.size_bytes = target.stat().st_size
    except OSError:  # pragma: no cover
        outcome.size_bytes = 0

    outcome.removed = prune_backups(keep)
    outcome.kept = len(list_backups())
    outcome.ok = True
    outcome.finished_at = _utc_now()
    outcome.duration_ms = int(
        (datetime.now(timezone.utc) - started_ts).total_seconds() * 1000
    )
    logger.info(
        "备份完成：%s（%s，校验通过，保留 %d 份）",
        outcome.file,
        human_size(outcome.size_bytes),
        outcome.kept,
    )
    if audit:
        _audit(success=True, outcome=outcome, operator_id=operator_id, ip=ip)
    return outcome


def _audit(
    *, success: bool, outcome: BackupOutcome, operator_id: int | None, ip: str | None
) -> None:
    """写审计。**审计失败不影响备份结论**（备份已经从磁盘层面成功了）。"""
    try:
        with write_tx() as conn:
            write_audit(
                conn,
                user_id=operator_id,
                action="backup.create" if success else "backup.failed",
                target_type="backup",
                target_id=None,
                detail=outcome.to_dict(),
                ip=ip,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("备份审计写入失败（忽略）：%s", exc)


def latest_backup() -> dict | None:
    """最近一份备份的摘要（供 `/api/server/info` 展示）。"""
    files = list_backups()
    if not files:
        return None
    newest = files[-1]
    try:
        stat = newest.stat()
    except OSError:  # pragma: no cover
        return None
    return {
        "file": newest.name,
        "path": str(newest),
        "size_bytes": stat.st_size,
        "size_human": human_size(stat.st_size),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }


def summary() -> dict:
    """备份总览：目录、份数、最近一份、总占用、保留策略。"""
    files = list_backups()
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:  # pragma: no cover
            continue
    directory = backup_dir()
    writable, why = (True, "") if not directory.exists() else _dir_writable(directory)
    return {
        "dir": str(directory),
        "writable": writable,
        "writable_error": why or None,
        "count": len(files),
        "keep": DEFAULT_KEEP,
        "total_bytes": total,
        "total_human": human_size(total),
        "latest": latest_backup(),
    }


def disk_free_bytes(path: Path | None = None) -> int | None:
    """磁盘剩余空间（备份前够不够的判断依据）。取不到返回 `None`。"""
    try:
        return shutil.disk_usage(str(path or backup_dir())).free
    except OSError:
        return None
