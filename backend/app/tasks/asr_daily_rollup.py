"""ASR 用量日聚合 —— WP-BE-07 F9。

**每天 03:00** 由 `app/tasks/scheduler.py` 的调度器调用（延迟导入）。

职责一句话：把超出保留期的 `asr_logs` 明细**先汇总进 `asr_daily_stats`，再删除明细**。
保留最近 `KEEP_DAYS`（90）天明细，更早的只留汇总。

---

### 为什么必须有这个任务

`asr_logs` 是**每答一题就写一条**的表。一个 40 人的班，每人每天练 20 题 ×
2 个 step（说词 + 拼读）= 1600 条/天，一学期 100 天就是 16 万条。
SQLite 单文件撑得住，但 `GET /api/stats/asr` 这类「按天聚合」的查询会随明细增长
越来越慢。而**月度/学期的用量报表**根本不需要逐条明细 —— 只需要日汇总。

### 两个刻意的设计点

1. **先聚合、后删除，同一个事务**（`write_tx`）。
   分开做的话，一旦中间崩了，就会出现「明细删了、汇总没写」——数据凭空消失。
   放在一个事务里，要么都成，要么都不成。

2. **阈值按天算、不按「条数」算**。
   用条数阈值（比如保留最近 10 万条）在天数上是不确定的：假期没数据就不删，
   开学猛练一下又突然删掉一大片。按天算保证「能查到的历史深度」是稳定的 90 天。

### `avg_latency_ms` 的合并是**近似**（已在代码里标注）

`asr_daily_stats` 没存「延迟样本数」，所以把新一天的数据合并进已存在的
`(stat_date, provider)` 行时，只能**用 `ok_count` 当权重**做加权平均。
正常情况下同一天不会被聚合两次（聚合即删除），所以这条分支极少走到；
真走到了误差也可忽略。如果哪天要精确，得给表加一列 `latency_n`。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.core.db import read_conn, write_tx
from app.core.logging import get_logger

# 北京时间与切天位移（原 stats_service 已随 WP-F 删除，这里内联最小实现）
_BJ = "+8 hours"


def beijing_day(offset_days: int = 0) -> str:
    """北京时间某天（`offset_days=0` 今天，`-1` 昨天）的 `YYYY-MM-DD`。"""
    from datetime import timedelta

    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).date()
    return (today + timedelta(days=offset_days)).isoformat()


logger = get_logger(__name__)

#: 明细保留天数（F9 明文：保留最近 90 天）
KEEP_DAYS = 90


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_rollup(*, keep_days: int = KEEP_DAYS, dry_run: bool = False) -> dict[str, Any]:
    """执行一次日聚合。**可重复执行**（第二次没有符合条件的数据，就是空跑）。

    Args:
        keep_days: 明细保留天数，早于「北京时间今天 − keep_days」的明细会被聚合删除。
        dry_run: 只统计不落库（排障用；`dry_run=True` 时不会删除任何明细）。

    Returns:
        `{cutoff, kept_days, rolled_days, rolled_rows, deleted_rows, details}`。
    """
    keep_days = max(int(keep_days), 1)
    cutoff = beijing_day(-keep_days)

    if dry_run:
        with read_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n,
                       COUNT(DISTINCT date(datetime(created_at, ?))) AS days
                FROM asr_logs
                WHERE date(datetime(created_at, ?)) < ?
                """,
                (_BJ, _BJ, cutoff),
            ).fetchone()
        return {
            "cutoff": cutoff,
            "keep_days": keep_days,
            "rolled_days": int(row["days"] or 0),
            "rolled_rows": int(row["n"] or 0),
            "deleted_rows": 0,
            "dry_run": True,
            "details": [],
        }

    with write_tx() as conn:
        # ---- 1) 聚合（按天 × 厂商）----
        aggregates = conn.execute(
            """
            SELECT date(datetime(created_at, ?))                      AS stat_date,
                   provider,
                   COUNT(*)                                           AS call_count,
                   SUM(audio_ms)                                      AS total_audio_ms,
                   SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END)      AS ok_count,
                   SUM(CASE WHEN status = 'empty' THEN 1 ELSE 0 END)   AS empty_count,
                   SUM(CASE WHEN status IN ('error','timeout')
                            THEN 1 ELSE 0 END)                        AS error_count,
                   SUM(CASE WHEN status = 'ok' AND latency_ms IS NOT NULL
                            THEN latency_ms ELSE 0 END)                AS latency_sum
            FROM asr_logs
            WHERE date(datetime(created_at, ?)) < ?
            GROUP BY stat_date, provider
            ORDER BY stat_date, provider
            """,
            (_BJ, _BJ, cutoff),
        ).fetchall()

        details: list[dict[str, Any]] = []
        for r in aggregates:
            calls = int(r["call_count"] or 0)
            ok = int(r["ok_count"] or 0)
            avg_latency = (
                int(round(int(r["latency_sum"] or 0) / ok)) if ok and r["latency_sum"] else None
            )
            conn.execute(
                """
                INSERT INTO asr_daily_stats
                    (stat_date, provider, call_count, total_audio_ms, avg_latency_ms,
                     ok_count, empty_count, error_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stat_date, provider) DO UPDATE SET
                    call_count     = call_count + excluded.call_count,
                    total_audio_ms = total_audio_ms + excluded.total_audio_ms,
                    ok_count       = ok_count + excluded.ok_count,
                    empty_count    = empty_count + excluded.empty_count,
                    error_count    = error_count + excluded.error_count,
                    -- 以 ok_count 为权重近似合并（见模块 docstring 的设计点 3）：
                    -- 用**旧值**的平均乘旧权重 + 新平均乘新权重，再除以总权重。
                    avg_latency_ms = CAST(ROUND(
                        (COALESCE(avg_latency_ms, 0) * COALESCE(ok_count, 0)
                         + COALESCE(excluded.avg_latency_ms, 0) * excluded.ok_count)
                        / NULLIF(COALESCE(ok_count, 0) + excluded.ok_count, 0)
                    ) AS INTEGER)
                """,
                (
                    str(r["stat_date"]),
                    r["provider"],
                    calls,
                    int(r["total_audio_ms"] or 0),
                    avg_latency,
                    ok,
                    int(r["empty_count"] or 0),
                    int(r["error_count"] or 0),
                ),
            )
            details.append(
                {
                    "stat_date": str(r["stat_date"]),
                    "provider": r["provider"],
                    "call_count": calls,
                    "ok_count": ok,
                    "empty_count": int(r["empty_count"] or 0),
                    "error_count": int(r["error_count"] or 0),
                    "avg_latency_ms": avg_latency,
                }
            )

        # ---- 2) 删除明细（同一事务，先聚合后删）----
        cur = conn.execute(
            "DELETE FROM asr_logs WHERE date(datetime(created_at, ?)) < ?",
            (_BJ, cutoff),
        )
        deleted = int(cur.rowcount or 0)

    result = {
        "cutoff": cutoff,
        "keep_days": keep_days,
        "rolled_days": len(aggregates),
        "rolled_rows": sum(int(r["call_count"] or 0) for r in aggregates),
        "deleted_rows": deleted,
        "dry_run": False,
        "at": _utc_now(),
        "details": details,
    }
    if deleted:
        logger.info(
            "ASR 日聚合完成：%d 天 / %d 家厂商，删除明细 %d 条（保留最近 %d 天）",
            len(aggregates),
            len({d["provider"] for d in details}),
            deleted,
            keep_days,
        )
    else:
        logger.debug("ASR 日聚合：无超出保留期的明细，跳过。")
    return result


def detail_retention() -> dict[str, Any]:
    """明细保留现状（供管理端/排障查看当前最早一条明细是哪天）。"""
    with read_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n, MIN(created_at) AS earliest, MAX(created_at) AS latest
            FROM asr_logs
            """
        ).fetchone()
        rolled = conn.execute(
            """
            SELECT COUNT(*) AS days, MIN(stat_date) AS earliest, MAX(stat_date) AS latest
            FROM asr_daily_stats
            """
        ).fetchone()
    return {
        "keep_days": KEEP_DAYS,
        "detail_rows": int(row["n"] or 0),
        "detail_earliest": row["earliest"],
        "detail_latest": row["latest"],
        "rolled_days": int(rolled["days"] or 0),
        "rolled_earliest": rolled["earliest"],
        "rolled_latest": rolled["latest"],
    }
