"""SRS 间隔重复服务 —— WP-2F（二期，契约《01-共享契约冻结》§15.5）。

**`srs_review_plan` 表的唯一写入方**（契约 §15.1 / §15.6）。

算法：标准 **SM-2**（SuperMemo 2），参数按契约 §15 取默认：
- `ease` 默认 `2.5`；
- 初始间隔 `1` 天；
- `ease` 下限 `1.3`（防止记忆难度系数塌缩）。

> ⚠️ 所有算法参数必须写成**具名常量**（见下方 `SRS_*`），
> 不得把魔法数字散落在 `WHERE`/`UPDATE` 里 —— 将来调参只改这一处。

### 两个服务方法（签名冻结，见 §15.5）

```
due_items(conn, student_id, limit)  -> list[SrsItem]
review(conn, student_id, word_id, grade) -> SrsResult
```

- `due` 只回**当前学生**已到期（`due_at <= 现在`）的计划，按 `due_at` 升序；
  没有任何到期计划时返回**空列表**，**不报错**（契约 §15.2）。
- `review` 推进一条已存在的计划；**找不到计划 → `SRS_PLAN_NOT_FOUND (70004)`**
  （越权与「不存在」同码，不暴露资源存在性，契约 §15.2 / §15.4 安全口径）。

### 关于「计划从哪来」

本包只负责「复习推进」，不负责「首次建计划」。计划由**首次练会某词**的链路写入
（归属 BE-04 的答题落库流程，按契约 §15.6 写入方唯一性调用本服务建计划）。
本文件**不**提供建计划接口 —— `review` 找不到计划必须按契约返回 `70004`，
这是验收硬条件，不能改成「顺手建一个」。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.db import utc_now_iso
from app.core.errors import BizError, ErrorCode

# ===========================================================================
# SM-2 算法参数（具名常量 —— 调参只改这里）
# ===========================================================================

#: 默认记忆难度系数（SM-2 标准初值）。
SRS_DEFAULT_EASE = 2.5

#: 首次复习（reps==0）后的间隔天数。
SRS_INITIAL_INTERVAL_DAYS = 1

#: 第二次复习（reps==1）后的间隔天数（SM-2 固定为 6）。
SRS_SECOND_INTERVAL_DAYS = 6

#: 记忆难度系数下限（低于此会塌缩，导致间隔永远不再增长）。
SRS_MIN_EASE = 1.3

#: quality（grade）达到该值视为「答对」，否则视为「遗忘 / lapse」。
SRS_PASS_THRESHOLD = 3

#: grade 合法取值范围（SM-2 的 quality 是 0–5 的整数）。
SRS_MIN_GRADE = 0
SRS_MAX_GRADE = 5

#: 遗忘（lapse）后间隔重置到的天数（回到初始间隔）。
SRS_LAPSE_INTERVAL_DAYS = 1


# ===========================================================================
# 数据结构
# ===========================================================================


@dataclass(frozen=True)
class SrsItem:
    """一条到期的复习计划（含展示用词条信息，避免前端二次请求）。"""

    word_id: int
    unit_id: int
    due_at: str
    interval_days: int
    ease: float
    reps: int
    lapses: int
    # —— 展示字段（JOIN words / units 得到，可选）——
    en: str | None = None
    cn: str | None = None
    word_type: str | None = None
    unit_name: str | None = None


@dataclass(frozen=True)
class SrsResult:
    """一次复习推进后的结果。"""

    next_due: str
    interval_days: int
    reps: int
    ease: float
    lapses: int


# ===========================================================================
# 纯函数：SM-2 状态推进（无副作用，便于单测）
# ===========================================================================


def apply_sm2(
    *,
    quality: int,
    interval_days: int,
    ease: float,
    reps: int,
    lapses: int,
) -> tuple[int, float, int, int]:
    """按 SM-2 推进一条计划的状态。

    Args:
        quality: 本次复习质量（grade），0–5。
        interval_days: 推进前的间隔天数。
        ease: 推进前的记忆难度系数。
        reps: 推进前的连续答对次数。
        lapses: 推进前的遗忘次数。

    Returns:
        `(new_interval_days, new_ease, new_reps, new_lapses)`。

    规则（标准 SM-2）：
    - 答对（quality >= 3）：
        - reps==0 → 间隔 1 天；
        - reps==1 → 间隔 6 天；
        - 其余   → 间隔 = round(旧间隔 × ease)。
        reps +1，lapses 不变。
    - 答错（quality < 3）：间隔重置为 1 天，reps 归零，lapses +1。
    - ease 每次都按 SM-2 公式更新，并夹在 `SRS_MIN_EASE` 之上。
    """
    if quality >= SRS_PASS_THRESHOLD:
        if reps == 0:
            new_interval = SRS_INITIAL_INTERVAL_DAYS
        elif reps == 1:
            new_interval = SRS_SECOND_INTERVAL_DAYS
        else:
            new_interval = max(1, int(round(interval_days * ease)))
        new_reps = reps + 1
        new_lapses = lapses
    else:
        new_interval = SRS_LAPSE_INTERVAL_DAYS
        new_reps = 0
        new_lapses = lapses + 1

    # SM-2 的 ease 更新公式（与答对 / 答错无关，每次都算）。
    delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    new_ease = max(SRS_MIN_EASE, ease + delta)

    return new_interval, round(new_ease, 4), new_reps, new_lapses


def _next_due_iso(interval_days: int) -> str:
    """计算下次到期时间（ISO 8601 UTC，带 Z，契约 §4.6）。"""
    when = datetime.now(timezone.utc) + timedelta(days=interval_days)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


# ===========================================================================
# 服务方法（签名冻结，见 §15.5）
# ===========================================================================


class SrsService:
    """无状态服务；方法为 `classmethod`（与 `MasteryService` 同风格）。

    `conn` 由调用方传入：
    - `due_items` 用只读连接；
    - `review` 必须在一个已 `BEGIN` 的写事务里调用（本方法不自开事务）。
    """

    @classmethod
    def due_items(
        cls,
        conn: Any,
        student_id: int,
        limit: int,
    ) -> list[SrsItem]:
        """返回 `student_id` **已到期**的复习计划（按 `due_at` 升序）。

        只回自己的数据（`WHERE student_id = ?`）；没有任何到期计划时返回空列表，
        **不报错**（契约 §15.2）。

        `due_at <= 现在` 才算到期；未来的计划不出现。
        """
        now = utc_now_iso()
        rows = conn.execute(
            """
            SELECT p.word_id, p.unit_id, p.due_at, p.interval_days, p.ease,
                   p.reps, p.lapses,
                   w.en, w.cn, w.type AS word_type, u.name AS unit_name
            FROM srs_review_plan p
            LEFT JOIN words w  ON w.id = p.word_id
            LEFT JOIN units u  ON u.id = p.unit_id
            WHERE p.student_id = ? AND p.due_at <= ?
            ORDER BY p.due_at ASC, p.word_id ASC
            LIMIT ?
            """,
            (int(student_id), now, int(limit)),
        ).fetchall()

        return [
            SrsItem(
                word_id=int(r["word_id"]),
                unit_id=int(r["unit_id"]),
                due_at=str(r["due_at"]),
                interval_days=int(r["interval_days"] or 1),
                ease=float(r["ease"] if r["ease"] is not None else SRS_DEFAULT_EASE),
                reps=int(r["reps"] or 0),
                lapses=int(r["lapses"] or 0),
                en=r["en"],
                cn=r["cn"],
                word_type=r["word_type"],
                unit_name=r["unit_name"],
            )
            for r in rows
        ]

    @classmethod
    def review(
        cls,
        conn: Any,
        *,
        student_id: int,
        word_id: int,
        grade: int,
    ) -> SrsResult:
        """推进一条已存在的复习计划，写回新状态并返回结果。

        找不到该学生的这条计划 → 抛 `SRS_PLAN_NOT_FOUND (70004)`
        （越权与「不存在」同码，不暴露资源存在性）。

        Args:
            conn: 调用方传入的、已在写事务中的连接。
            student_id / word_id: 定位计划（UNIQUE(student_id, word_id)）。
            grade: SM-2 质量分 0–5；非法值由 API 层在调用前校验。
        """
        row = conn.execute(
            """
            SELECT id, interval_days, ease, reps, lapses
            FROM srs_review_plan
            WHERE student_id = ? AND word_id = ?
            """,
            (int(student_id), int(word_id)),
        ).fetchone()

        if row is None:
            # 越权（这条计划属于别人）与「不存在」同码，防止探测他人记录。
            raise BizError(ErrorCode.SRS_PLAN_NOT_FOUND)

        interval_days = int(row["interval_days"] or 1)
        ease = float(row["ease"] if row["ease"] is not None else SRS_DEFAULT_EASE)
        reps = int(row["reps"] or 0)
        lapses = int(row["lapses"] or 0)

        new_interval, new_ease, new_reps, new_lapses = apply_sm2(
            quality=grade,
            interval_days=interval_days,
            ease=ease,
            reps=reps,
            lapses=lapses,
        )
        next_due = _next_due_iso(new_interval)
        now = utc_now_iso()

        conn.execute(
            """
            UPDATE srs_review_plan
               SET due_at = ?, interval_days = ?, ease = ?, reps = ?,
                   lapses = ?, last_reviewed_at = ?
             WHERE id = ?
            """,
            (next_due, new_interval, new_ease, new_reps, new_lapses, now, int(row["id"])),
        )

        return SrsResult(
            next_due=next_due,
            interval_days=new_interval,
            reps=new_reps,
            ease=new_ease,
            lapses=new_lapses,
        )
