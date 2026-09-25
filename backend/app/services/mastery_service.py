"""MasteryService —— 掌握度服务（契约《01-共享契约冻结》§7.2，**签名已冻结**）。

**`word_mastery` 表的唯一写入方**（契约 §4.2 / 禁止清单 3）。
其他包（BE-03 学生端列表、BE-07 统计）只能通过本服务**读**。

唯一口径（契约 §14 裁决 1）：`not_started` / `practiced_with_error` /
`practiced_ok` / `mastered`，词条级与单元级**共用同一枚举**，
判定必须**按优先级顺序、命中即停**，否则「既 mastered 又有错」这类组合
会产生实现分歧。

```
词条级： total_count == 0 → mastered_at 非空 → wrong_count > 0 → 其余
单元级： 单元无练习     → 全部词条 mastered → 有练习且有错 → 有练习且无错
```

⚠️ `match_type='empty'` 时**不得调用** `update_after_answer` ——
识别为空不计入掌握度（否则学生多说几次空结果就会掉状态）。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from app.core.db import read_conn, utc_now_iso
from app.core.logging import get_logger
from app.services.settings_service import SettingsService
from app.services.user_service import UserService

logger = get_logger(__name__)

# 契约 §14 裁决 1：四值之外一律非法。严禁引入 untrained / wrong / all_correct 等别名。
MasteryStatus = Literal["not_started", "practiced_with_error", "practiced_ok", "mastered"]

ALL_STATUSES: tuple[str, ...] = (
    "not_started",
    "practiced_with_error",
    "practiced_ok",
    "mastered",
)


class MasterySnapshot(BaseModel):
    """掌握度快照（契约 §7.2 冻结）。接口 §9.2 的 `mastery` 字段即此结构 + wrong_count。"""

    status: MasteryStatus
    streak: int
    mastered: bool


def derive_word_status(row: dict[str, Any] | None) -> MasteryStatus:
    """词条级派生（契约 §7.2，**严格按序，命中即停**）。

    顺序不可换：一个词条可能「曾经掌握过、后来又练错了」，
    换序会得出完全不同的结论。
    """
    if not row or not row.get("total_count"):
        return "not_started"
    if row.get("mastered_at"):
        return "mastered"
    if (row.get("wrong_count") or 0) > 0:
        return "practiced_with_error"
    return "practiced_ok"


class MasteryService:
    """无状态服务；所有方法为 `classmethod`。

    用 `classmethod` 而非实例方法，是为了让契约 §7.2 的 `self` 签名在
    **`MasteryService.get_status(...)` 与 `MasteryService().get_status(...)`
    两种调用方式下都成立** —— BE-03 已按类方式调用（`content_service.py`），
    这里必须兼容，否则会静默走进 `except` 分支退化成降级派生。
    """

    # ================================================================== 读

    @classmethod
    def _rows_for(cls, conn: Any, student_id: int, word_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not word_ids:
            return {}
        placeholders = ",".join("?" * len(word_ids))
        rows = conn.execute(
            f"""
            SELECT word_id, streak, total_count, correct_count, wrong_count,
                   hint_total, last_result, last_answer_at, mastered_at, avg_duration_ms
            FROM word_mastery
            WHERE student_id = ? AND word_id IN ({placeholders})
            """,
            (student_id, *word_ids),
        ).fetchall()
        return {int(r["word_id"]): dict(r) for r in rows}

    @classmethod
    def get_status(cls, student_id: int, word_ids: list[int]) -> dict[int, MasteryStatus]:
        """批量查掌握状态，返回 `word_id -> status`。

        **O(1) 批量查询，禁止循环单查**（契约 §7.2）。
        没有练习记录的词条返回 `not_started`，而不是缺席 —— 调用方不必判空。
        """
        if not student_id or not word_ids:
            return {}
        unique = list(dict.fromkeys(int(i) for i in word_ids))
        with read_conn() as conn:
            raw = cls._rows_for(conn, int(student_id), unique)
        return {wid: derive_word_status(raw.get(wid)) for wid in unique}

    @classmethod
    def get_snapshots(cls, student_id: int, word_ids: list[int]) -> dict[int, MasterySnapshot]:
        """批量取完整快照（`status` / `streak` / `mastered`）。"""
        if not student_id or not word_ids:
            return {}
        unique = list(dict.fromkeys(int(i) for i in word_ids))
        with read_conn() as conn:
            raw = cls._rows_for(conn, int(student_id), unique)

        out: dict[int, MasterySnapshot] = {}
        for wid in unique:
            row = raw.get(wid)
            status = derive_word_status(row)
            out[wid] = MasterySnapshot(
                status=status,
                streak=int(row["streak"]) if row else 0,
                mastered=status == "mastered",
            )
        return out

    @classmethod
    def get_snapshot(cls, student_id: int, word_id: int) -> MasterySnapshot:
        """单个词条的快照（答题响应回传用）。"""
        return cls.get_snapshots(student_id, [word_id]).get(
            int(word_id), MasterySnapshot(status="not_started", streak=0, mastered=False)
        )

    # ================================================================== 写

    @classmethod
    def update_after_answer(
        cls,
        conn: Any,
        *,
        student_id: int,
        word_id: int,
        is_correct: bool,
        match_type: str,
        duration_ms: int,
        hint_level: int,
        mastered_streak: int,
    ) -> MasterySnapshot:
        """在**答题写入的同一事务内**更新掌握度（契约 §7.2）。

        Args:
            conn: 调用方传入的、已在 `write_tx()` 中的连接。**本方法不自开事务。**
            student_id / word_id: 定位记录。
            is_correct: 本次是否判对。
            match_type: 判定类型；`empty` 时调用方**不应调用本方法**（此处只记 warning）。
            duration_ms: 本次用时（用于增量平均）。
            hint_level: 本次使用的最高提示档位（0–3），累计进 `hint_total`。
            mastered_streak: 连对多少次算掌握（settings `mastery_streak`，默认 3）。

        Returns:
            更新后的 `MasterySnapshot`，供接口直接回传前端即时刷新。

        Notes:
            答错时 `streak` **立即归零**、`mastered_at` 置 `NULL`（需求 §6.5）。
            这里用「读-改-写」而非 SQL 自增表达式，是因为 `mastered_at` 的置位/清零
            依赖 `streak` 的新值，一条 UPDATE 写不清楚。
        """
        if match_type == "empty":
            # 契约明文禁止，但调用方可能漏判 —— 记日志避免静默走错
            logger.warning(
                "update_after_answer 收到 match_type='empty'（student=%s word=%s），已忽略",
                student_id, word_id,
            )
            return cls.get_snapshot(student_id, word_id)

        now = utc_now_iso()
        row = conn.execute(
            """
            SELECT streak, total_count, correct_count, wrong_count, hint_total,
                   mastered_at, avg_duration_ms
            FROM word_mastery
            WHERE student_id = ? AND word_id = ?
            """,
            (student_id, word_id),
        ).fetchone()

        previous: dict[str, Any] = (
            dict(row)
            if row is not None
            else {
                "streak": 0, "total_count": 0, "correct_count": 0, "wrong_count": 0,
                "hint_total": 0, "mastered_at": None, "avg_duration_ms": 0,
            }
        )

        total_count = int(previous["total_count"]) + 1
        correct_count = int(previous["correct_count"]) + (1 if is_correct else 0)
        wrong_count = int(previous["wrong_count"]) + (0 if is_correct else 1)
        hint_total = int(previous["hint_total"]) + max(0, int(hint_level or 0))

        if is_correct:
            streak = int(previous["streak"]) + 1
        else:
            streak = 0

        if not is_correct:
            mastered_at: str | None = None
        elif previous["mastered_at"]:
            mastered_at = str(previous["mastered_at"])  # 已掌握则保持原时间
        elif streak >= max(1, int(mastered_streak)):
            mastered_at = now
        else:
            mastered_at = None

        # 增量平均（避免为了一个均值把历史全读出来）
        previous_total = total_count - 1
        avg_duration_ms = round(
            (int(previous["avg_duration_ms"]) * previous_total + max(0, int(duration_ms or 0)))
            / total_count
        )

        payload = (
            streak,
            total_count,
            correct_count,
            wrong_count,
            hint_total,
            1 if is_correct else 0,
            now,
            mastered_at,
            avg_duration_ms,
            student_id,
            word_id,
        )

        if row is None:
            conn.execute(
                """
                INSERT INTO word_mastery
                    (streak, total_count, correct_count, wrong_count, hint_total,
                     last_result, last_answer_at, mastered_at, avg_duration_ms,
                     student_id, word_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        else:
            conn.execute(
                """
                UPDATE word_mastery
                   SET streak = ?, total_count = ?, correct_count = ?, wrong_count = ?,
                       hint_total = ?, last_result = ?, last_answer_at = ?, mastered_at = ?,
                       avg_duration_ms = ?
                 WHERE student_id = ? AND word_id = ?
                """,
                payload,
            )

        return MasterySnapshot(
            status=derive_word_status(
                {
                    "total_count": total_count,
                    "mastered_at": mastered_at,
                    "wrong_count": wrong_count,
                }
            ),
            streak=streak,
            mastered=mastered_at is not None,
        )

    # ================================================================== 单元级

    @classmethod
    def unit_counts(cls, student_id: int, unit_id: int) -> dict[str, Any]:
        """单元内词条的掌握汇总（接口 §8.3 用的 `word_total/practiced/mastered/accuracy`）。

        只统计 `status='active'` 的词条 —— 停用内容不参与出题，也不该计入进度。
        """
        with read_conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(w.id)                                                    AS word_total,
                    SUM(CASE WHEN m.total_count > 0 THEN 1 ELSE 0 END)             AS practiced,
                    SUM(CASE WHEN m.mastered_at IS NOT NULL THEN 1 ELSE 0 END)     AS mastered,
                    SUM(CASE WHEN m.wrong_count > 0 THEN 1 ELSE 0 END)             AS with_error,
                    SUM(CASE WHEN m.total_count > 0 THEN m.total_count ELSE 0 END) AS answer_total
                FROM words w
                LEFT JOIN word_mastery m
                       ON m.word_id = w.id AND m.student_id = ?
                WHERE w.unit_id = ? AND w.status = 'active'
                """,
                (int(student_id), int(unit_id)),
            ).fetchone()

            accuracy_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN a.match_type <> 'empty' THEN 1 ELSE 0 END) AS counted,
                    SUM(CASE WHEN a.match_type IN ('exact', 'loose', 'fuzzy')
                             THEN 1 ELSE 0 END) AS correct
                FROM answers a
                JOIN words w ON w.id = a.word_id
                WHERE a.student_id = ? AND w.unit_id = ?
                """,
                (int(student_id), int(unit_id)),
            ).fetchone()

            last_row = conn.execute(
                """
                SELECT MAX(a.created_at) AS last_practice_at
                FROM answers a
                JOIN words w ON w.id = a.word_id
                WHERE a.student_id = ? AND w.unit_id = ?
                """,
                (int(student_id), int(unit_id)),
            ).fetchone()

        word_total = int(row["word_total"] or 0)
        practiced = int(row["practiced"] or 0)
        mastered = int(row["mastered"] or 0)
        with_error = int(row["with_error"] or 0)

        return {
            "word_total": word_total,
            "practiced": practiced,
            "mastered": mastered,
            "with_error": with_error,
            "answer_total": int(row["answer_total"] or 0),
            "accuracy": UserService._accuracy(
                int(accuracy_row["correct"] or 0), int(accuracy_row["counted"] or 0)
            ),
            "last_practice_at": last_row["last_practice_at"] if last_row else None,
            "status": cls.derive_unit_status(
                word_total=word_total,
                practiced=practiced,
                mastered=mastered,
                with_error=with_error,
            ),
        }

    @staticmethod
    def derive_unit_status(
        *,
        word_total: int,
        practiced: int,
        mastered: int,
        with_error: int,
    ) -> MasteryStatus:
        """单元级派生（契约 §7.2，**严格按序，命中即停**）。

        1. 单元下无任何练习 → `not_started`
        2. 单元内全部词条 mastered → `mastered`
        3. 有练习且有错 → `practiced_with_error`
        4. 有练习且无错 → `practiced_ok`

        ⚠️ 第 1 条必须在第 2 条之前：空单元的「全部 mastered」在集合论上为真，
        但语义上应是「未练」。
        """
        if word_total <= 0 or practiced <= 0:
            return "not_started"
        if mastered >= word_total:
            return "mastered"
        if with_error > 0:
            return "practiced_with_error"
        return "practiced_ok"

    # ================================================================== 错题本

    @classmethod
    def wrong_words(
        cls,
        student_id: int,
        *,
        unit_id: int | None = None,
        grade: int | None = None,
        semester: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """错题本数据（接口 §8.8）。

        取自 `word_mastery` 物化表（**O(1)**），不去聚合 `answers`
        （§8.8 明文要求，索引 `idx_mastery_wrong`）。

        Returns:
            `(items, total)`；`items` 含 `last_your_answer`（最近一次答错时学生说的内容）。
        """
        where = ["m.student_id = ?", "m.wrong_count > 0", "w.status = 'active'"]
        params: list[Any] = [int(student_id)]

        if unit_id is not None:
            where.append("w.unit_id = ?")
            params.append(int(unit_id))
        if grade is not None:
            where.append("u.grade = ?")
            params.append(int(grade))
        if semester:
            where.append("u.semester = ?")
            params.append(semester)

        sql_where = " AND ".join(where)

        with read_conn() as conn:
            total = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) AS c
                    FROM word_mastery m
                    JOIN words w ON w.id = m.word_id
                    JOIN units u ON u.id = w.unit_id
                    WHERE {sql_where}
                    """,
                    params,
                ).fetchone()["c"]
            )

            rows = conn.execute(
                f"""
                SELECT
                    w.id AS word_id, w.unit_id, u.name AS unit_name, w.type,
                    w.en, w.cn, w.phonetic, w.order_no,
                    m.wrong_count, m.total_count, m.streak,
                    m.mastered_at,
                    (SELECT a.created_at FROM answers a
                      WHERE a.student_id = m.student_id AND a.word_id = m.word_id
                        AND a.is_correct = 0 AND a.match_type <> 'empty'
                      ORDER BY a.created_at DESC, a.id DESC LIMIT 1) AS last_wrong_at,
                    (SELECT COALESCE(a.answer_text, a.asr_text) FROM answers a
                      WHERE a.student_id = m.student_id AND a.word_id = m.word_id
                        AND a.is_correct = 0 AND a.match_type <> 'empty'
                      ORDER BY a.created_at DESC, a.id DESC LIMIT 1) AS last_your_answer
                FROM word_mastery m
                JOIN words w ON w.id = m.word_id
                JOIN units u ON u.id = w.unit_id
                WHERE {sql_where}
                ORDER BY m.wrong_count DESC, last_wrong_at DESC, w.id
                LIMIT ? OFFSET ?
                """,
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()

        items: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            item["mastery_status"] = derive_word_status(item)
            items.append(item)
        return items, total

    # ================================================================== 进度总览

    @classmethod
    def progress_overview(cls, student_id: int) -> dict[str, Any]:
        """`GET /api/me/progress`（接口 §8.4）。"""
        student_id = int(student_id)
        today_prefix = utc_now_iso()[:10]

        with read_conn() as conn:
            words = conn.execute(
                """
                SELECT
                    COUNT(w.id) AS total_words,
                    SUM(CASE WHEN m.total_count > 0 THEN 1 ELSE 0 END) AS practiced_words,
                    SUM(CASE WHEN m.mastered_at IS NOT NULL THEN 1 ELSE 0 END) AS mastered_words
                FROM words w
                LEFT JOIN word_mastery m ON m.word_id = w.id AND m.student_id = ?
                WHERE w.status = 'active'
                """,
                (student_id,),
            ).fetchone()

            texts = conn.execute(
                """
                SELECT
                    COUNT(t.id) AS total_texts,
                    (SELECT COUNT(DISTINCT r.text_id) FROM recitations r
                      WHERE r.student_id = ? AND r.text_id IS NOT NULL) AS recited_texts
                FROM texts t
                WHERE t.status = 'active'
                """,
                (student_id,),
            ).fetchone()

            accuracy_row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN match_type <> 'empty' THEN 1 ELSE 0 END) AS counted,
                       SUM(CASE WHEN match_type IN ('exact', 'loose', 'fuzzy')
                                THEN 1 ELSE 0 END) AS correct
                FROM answers WHERE student_id = ?
                """,
                (student_id,),
            ).fetchone()

            today = conn.execute(
                """
                SELECT COUNT(DISTINCT session_id) AS sessions,
                       COUNT(*) AS items,
                       SUM(CASE WHEN match_type IN ('exact', 'loose', 'fuzzy')
                                THEN 1 ELSE 0 END) AS correct
                FROM answers
                WHERE student_id = ? AND substr(created_at, 1, 10) = ?
                """,
                (student_id, today_prefix),
            ).fetchone()

            days = [
                r["d"]
                for r in conn.execute(
                    """
                    SELECT DISTINCT substr(created_at, 1, 10) AS d
                    FROM answers WHERE student_id = ?
                    ORDER BY d DESC LIMIT 400
                    """,
                    (student_id,),
                ).fetchall()
            ]

        return {
            "total_words": int(words["total_words"] or 0),
            "practiced_words": int(words["practiced_words"] or 0),
            "mastered_words": int(words["mastered_words"] or 0),
            "total_texts": int(texts["total_texts"] or 0),
            "recited_texts": int(texts["recited_texts"] or 0),
            "streak_days": cls.streak_days(days, today_prefix),
            "accuracy": UserService._accuracy(
                int(accuracy_row["correct"] or 0), int(accuracy_row["counted"] or 0)
            ),
            "today": {
                "sessions": int(today["sessions"] or 0),
                "items": int(today["items"] or 0),
                "correct": int(today["correct"] or 0),
            },
        }

    @staticmethod
    def streak_days(days_desc: list[str], today: str) -> int:
        """连续练习天数：从今天（或昨天）起向前数连续日期。

        今天还没练但昨天练了，仍算连续 —— 否则学生每天打开 App 前
        都会先看到「连续 0 天」，反馈太差。
        """
        if not days_desc:
            return 0

        def parse(value: str) -> date:
            year, month, day = (int(x) for x in value.split("-"))
            return date(year, month, day)

        try:
            today_date = parse(today)
        except (ValueError, TypeError):  # pragma: no cover - 日期格式异常
            return 0

        unique = sorted({d for d in days_desc if d}, reverse=True)
        parsed = [parse(d) for d in unique]

        if parsed[0] not in (today_date, today_date - timedelta(days=1)):
            return 0

        streak = 1
        for previous, current in zip(parsed, parsed[1:]):
            if previous - current == timedelta(days=1):
                streak += 1
            else:
                break
        return streak

    # ================================================================== 工具

    @classmethod
    def mastered_streak(cls) -> int:
        """连对多少次算掌握（settings `mastery_streak`，默认 3）。"""
        return SettingsService.get_int("mastery_streak", 3)

    @staticmethod
    def dump_hint_count(value: Any) -> str:
        """`hint_count` JSON 序列化（语音/背诵共用）。"""
        return json.dumps(value, ensure_ascii=False)
