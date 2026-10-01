"""轻量后台调度器 —— WP-BE-08 F9 / F13。

**为什么自己写而不引 APScheduler**：契约 §1 要求依赖写死版本、加了就要走变更流程；
而这里只需要「每分钟醒一次，看有没有到点的日任务」，`asyncio.sleep` 循环 30 行就能覆盖，
还顺带把 IP 变化检测（F4）挂在同一个心跳上。少一个依赖 = 少一个部署时踩的坑。

### 承载的任务

| 任务 | 周期 | 归属 |
|---|---|---|
| IP 变化检测（F4） | 60 s | BE-08 |
| 数据备份（F6） | 每日 02:00（北京时间） | BE-08 |
| ASR 用量日聚合（BE-07 F9） | 每日 03:00（北京时间） | BE-07（`tasks/asr_daily_rollup.py`，**延迟导入**） |

> ASR 聚合用**延迟导入**：BE-07 还没落地时调度器要能独立跑（总纲 §1 原则 3「接口解耦」），
> 模块不存在就跳过并记一条 debug 日志，不是错误。

### 时间基准：北京时间
服务器时间存 UTC（契约 §4.6），但「每日 02:00 备份」说的是**本机所在时区的 02:00**。
本项目部署在中国，固定用 `Asia/Shanghai`。

⚠️ Windows 上 `zoneinfo` 需要 IANA 时区数据库，而 Python 官方安装包**不带**它
（除非装了 `tzdata` 包）。因此这里先试 `zoneinfo`，失败就退回**固定 +8 偏移** ——
中国自 1991 年起不再实行夏令时，固定 +8 与 `Asia/Shanghai` 完全等价，不是近似值。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.db import get_conn, read_conn, write_tx
from app.core.logging import get_logger
from app.core.migrate import discover_migrations, get_current_version
from app.services import backup_service
from app.services.discovery import state
from app.services.discovery.udp_responder import responder

logger = get_logger(__name__)

#: 心跳周期（秒）。60 s 是 IP 变化检测的规格值（10b F4）。
TICK_SECONDS = 60

#: 固定 +8 偏移（Windows 缺 tzdata 时的兜底，与 Asia/Shanghai 等价）
_CST_FALLBACK = timezone(timedelta(hours=8), name="CST")

_SHANGHAI: Any = None
_SHANGHAI_RESOLVED = False


def local_tz() -> Any:
    """取 `Asia/Shanghai`；拿不到时退回固定 +8（见模块 docstring）。"""
    global _SHANGHAI, _SHANGHAI_RESOLVED
    if not _SHANGHAI_RESOLVED:
        _SHANGHAI_RESOLVED = True
        try:
            from zoneinfo import ZoneInfo

            _SHANGHAI = ZoneInfo("Asia/Shanghai")
        except Exception:  # noqa: BLE001 - 缺 tzdata / 平台不支持
            logger.info("tzdata 不可用，按固定 +08:00 计算本地时间。")
            _SHANGHAI = _CST_FALLBACK
    return _SHANGHAI


def local_now() -> datetime:
    """当前北京时间。"""
    return datetime.now(local_tz())


# --------------------------------------------------------------------------- 日任务


@dataclass
class DailyJob:
    """一个每日固定时刻执行的任务。"""

    name: str
    hour: int
    minute: int
    run: Callable[[], Any]
    last_run_date: str | None = None
    field_extra: dict = field(default_factory=dict)

    def due(self, now: datetime) -> bool:
        """今天该时刻已到，且今天还没跑过。"""
        if self.last_run_date == now.date().isoformat():
            return False
        return (now.hour, now.minute) >= (self.hour, self.minute)

    async def execute(self, now: datetime) -> Any:
        """执行（同步函数放线程池，避免阻塞事件循环）。"""
        self.last_run_date = now.date().isoformat()
        if asyncio.iscoroutinefunction(self.run):
            return await self.run()
        return await asyncio.to_thread(self.run)


def _daily_backup() -> dict:
    """每日 02:00 —— 定时备份（F6）。"""
    outcome = backup_service.create_backup(daily=True)
    if not outcome.ok:
        state.mark_degraded("backup", f"定时备份失败：{outcome.error}")
    else:
        state.clear_degraded("backup")
    return outcome.to_dict()


def _daily_asr_rollup() -> dict | None:
    """每日 03:00 —— ASR 用量日聚合（BE-07 F9）。模块不在则跳过。"""
    try:
        from app.tasks import asr_daily_rollup  # 延迟导入：BE-07 可能还没落地
    except ImportError:
        # BE-07 已落地（tasks/asr_daily_rollup.py 存在）；此分支仅在模块缺失/导入失败时兜底。
        logger.warning("asr_daily_rollup 模块不可用（导入失败），跳过本次聚合。")
        return None
    return asr_daily_rollup.run_rollup()


def build_jobs() -> list[DailyJob]:
    """构造日任务表。**每次调用返回新实例**，便于测试与手动触发。"""
    return [
        DailyJob(name="backup", hour=2, minute=0, run=_daily_backup),
        DailyJob(name="asr_rollup", hour=3, minute=0, run=_daily_asr_rollup),
    ]


#: 进程内任务表
_jobs: list[DailyJob] = build_jobs()

_loop_task: asyncio.Task | None = None
_started = False


def jobs() -> list[DailyJob]:
    return _jobs


async def run_daily_job_now(name: str) -> Any:
    """立刻执行指定的日任务（手动/测试用），返回其结果。"""
    for job in _jobs:
        if job.name == name:
            return await job.execute(local_now())
    raise KeyError(f"未知的日任务：{name}")


# --------------------------------------------------------------------------- 心跳


async def _tick(now: datetime | None = None) -> None:
    """一次心跳：先跑 IP 检测，再跑到点的日任务。"""
    moment = now or local_now()

    # ---- F4：IP 变化检测
    # ⚠️ 必须放线程池：网卡探测要起 PowerShell（实测 ~2.5 s），同步跑会把
    # 事件循环卡住 —— 全站请求跟着堵 2.5 s（#109，2026-09-30）
    try:
        changed = await asyncio.to_thread(
            lambda: state.refresh(now_ts=time.monotonic())
        )
    except Exception as exc:  # noqa: BLE001 - 检测失败不该打断心跳
        logger.warning("IP 变化检测失败（忽略本次）：%s", exc)
    else:
        if changed:
            logger.warning("检测到本机 IP 变化，新地址：%s", state.current_ip)
            _audit_ip_change()

    # ---- 日任务
    for job in _jobs:
        if not job.due(moment):
            continue
        try:
            await job.execute(moment)
        except Exception as exc:  # noqa: BLE001 - 单个任务失败不影响其他任务
            logger.error("日任务 %s 执行失败：%s", job.name, exc, exc_info=True)


def _audit_ip_change() -> None:
    """IP 变化写审计（10b F4「变化时更新 last_ip_change_at 并写审计」）。

    ⚠️ 审计失败只告警：不能因为审计写不进去就让检测逻辑失败。
    """
    try:
        with write_tx() as conn:
            write_audit(
                conn,
                user_id=None,
                action="server.ip_changed",
                target_type="server",
                target_id=None,
                detail={
                    "current_ip": state.current_ip,
                    "previous_ips": state.previous_ips,
                    "last_ip_change_at": state.last_ip_change_at,
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("IP 变化审计写入失败（忽略）：%s", exc)


async def _ticker() -> None:
    """周期心跳：IP 变化检测 + 到点的日任务。"""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        await _tick()


async def start_background_tasks() -> None:
    """启动后台任务（幂等）。**必须在事件循环内 `await`**。

    顺序是刻意的，而且都是**先做完再返回**：

    1. 启动自检（F13）—— 必须在 UDP 绑定**之前**跑，否则「端口可用性」这项
       会被自己刚绑上的端口误判为不可用；
    2. 绑定 UDP 9527（F2）—— **同步等待绑定结果**。若做成「丢个后台任务去绑」，
       会出现「HTTP 已就绪、但广播还没起来」的窗口期：App 启动瞬间发广播会没人应答，
       而它只会重试 3 轮共 6 秒，运气差就直接判「找不到服务器」。
    3. 最后才创建周期心跳任务。
    """
    global _loop_task, _started
    if _started:
        return
    _started = True

    await run_startup_checks_async()
    if await responder.start():
        logger.info("广播发现已就绪（UDP %s）。", responder.port)
    # 立刻做一次 IP 检测，让管理端首屏就有正确地址（不等 60 s）
    await _tick()

    loop = asyncio.get_running_loop()
    _loop_task = loop.create_task(_ticker(), name="bjsys-scheduler")
    logger.info("后台调度器已启动（心跳 %s 秒）。", TICK_SECONDS)


async def stop_background_tasks() -> None:
    """停止后台任务并释放 UDP 端口（幂等）。"""
    global _loop_task, _started
    _started = False
    task, _loop_task = _loop_task, None
    if task is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
    await responder.stop()
    logger.info("后台调度器已停止。")


# --------------------------------------------------------------------------- F13 启动自检


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def check_db_writable() -> CheckResult:
    """数据库可写。

    做法：开一条连接、`BEGIN IMMEDIATE` 拿到写锁、立刻 `ROLLBACK` ——
    **不改动任何数据**却能真实证明「写得进去」（比 `os.access` 或
    「文件存在」强得多，Windows 上 ACL 与只读属性都会骗过后者）。
    """
    settings = get_settings()
    try:
        conn = get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        return CheckResult("db_writable", False, str(exc))
    return CheckResult("db_writable", True, str(settings.db_file))


def check_migration_version() -> CheckResult:
    """迁移版本与磁盘上的脚本一致。"""
    try:
        expected = max((m.version for m in discover_migrations()), default=0)
        with read_conn() as conn:
            current = get_current_version(conn)
    except Exception as exc:  # noqa: BLE001
        return CheckResult("migration_version", False, f"读取失败：{exc}")
    if current < expected:
        return CheckResult(
            "migration_version", False, f"库内 {current} < 脚本 {expected}（迁移未完成）"
        )
    return CheckResult("migration_version", True, f"version={current}")


def check_backup_dir() -> CheckResult:
    """备份目录可写（F13 明确：失败只告警，不阻止启动）。

    ⚠️ `mkdir` 也必须在 try 里：备份目录的父级可能**是个文件**（用户手滑改了配置），
    这时 `mkdir(parents=True)` 会抛 `NotADirectoryError`/`FileExistsError` ——
    自检函数把异常抛出去，就等于「自检把服务搞挂了」，与本条的目的正好相反。
    """
    directory = backup_service.backup_dir()
    probe = directory / ".selfcheck_probe"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return CheckResult("backup_dir", False, f"备份目录不可写：{exc}")
    return CheckResult("backup_dir", True, str(directory))


def check_udp_port() -> CheckResult:
    """UDP 发现端口可用（只探测，不长期占用）。

    ⚠️ 若应答器**已经**绑上了这个端口，那是「我们自己占着」，
    此时再去 bind 必然失败 —— 必须短路成通过，否则会出现
    「端口工作得好好的，自检却一直报不可用」的假告警。
    """
    import socket

    from app.services.discovery import DISCOVERY_PORT

    if responder.running:
        return CheckResult("udp_port", True, f"UDP {DISCOVERY_PORT} 已由本服务监听")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("0.0.0.0", DISCOVERY_PORT))
    except OSError as exc:
        return CheckResult("udp_port", False, f"UDP {DISCOVERY_PORT} 不可用：{exc}")
    return CheckResult("udp_port", True, f"UDP {DISCOVERY_PORT} 可绑定")


def run_startup_checks() -> list[CheckResult]:
    """跑全部自检（同步，可在任意线程调用）。

    「DB 不可用」是**致命**的（服务起来也没意义），由 `bootstrap()` 负责拒绝启动；
    这里只负责**如实记录并标记降级**，不抛异常。
    """
    results = [
        check_db_writable(),
        check_migration_version(),
        check_backup_dir(),
        check_udp_port(),
    ]
    for r in results:
        if r.ok:
            logger.info("启动自检通过：%s（%s）", r.name, r.detail)
        else:
            logger.warning("启动自检未通过：%s —— %s", r.name, r.detail)
    return results


async def run_startup_checks_async() -> list[CheckResult]:
    """自检是阻塞 IO（起 socket / 建库连接），放线程池跑，避免卡住事件循环首帧。"""
    results = await asyncio.to_thread(run_startup_checks)
    for r in results:
        if r.ok:
            state.clear_degraded(f"selfcheck.{r.name}")
        else:
            state.mark_degraded(f"selfcheck.{r.name}", r.detail)
    return results


def reset_for_tests() -> None:
    """测试用：复位调度器状态（不动 UDP 单例的端口占用）。"""
    global _loop_task, _started, _jobs
    _loop_task = None
    _started = False
    _jobs = build_jobs()


def backup_dir_path() -> Path:  # pragma: no cover - 便捷别名
    return backup_service.backup_dir()
