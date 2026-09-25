"""统计看板服务 —— WP-BE-07 F1 / F2 / F3 / F6 / F7 / F10 / F11。

需求追溯：《需求说明书》§4.7 数据看板。

---

## 一、口径定义（**改这里之前先看这一节**）

### 1.1 正确率（F6）

```
正确率 = (exact + loose + fuzzy) / (答题总数 − empty)
分母为 0 → 0.0
```

`empty`（没识别到声音）**不计入分母** —— 学生对着麦克风没说话不是「答错」。
这是全项目唯一口径，`calc_accuracy()` 只是 `UserService._accuracy()` 的**转发**，
**本模块不含第二份公式实现**（见下节说明）。

### 1.2 完成率

`完成率 = 至少练过 1 次的词条数 / 该范围词条总数`（**词条维度，不按学生维度**）。
- 单元级：分母 = 该单元 `active` 词条数。
- 班级级：分母 = 该班 `年级 + 学期` 下英语 `active` 词条数（班级没有「自己的单元」，
  只能按年级学期圈定范围）；分子 = 该班学生练过的**不同**词条数（跨学生去重）。

**「练过」的定义：至少有一条 `match_type <> 'empty'` 的答题记录。**
必须排除 `empty` —— 因为 BE-04 的 `MasteryService.update_after_answer` 对
`empty` **明文拒绝处理、不建 `word_mastery` 行**（见其模块 docstring 第 16 行）。
若这里把只有 empty 的题也算作「练过」，就会与「已掌握/练过」的另一口径
（`v_student_unit_progress`）对不上，同一个页面里出现两个不同的「练过词数」。

### 1.3 已掌握比例

`已掌握比例 = 被至少一名学生掌握的词条数 / 词条总数`（同样词条维度）。
掌握与否看 `word_mastery.mastered_at IS NOT NULL`（BE-04 物化，本模块只读）。

### 1.4 失败率（ASR）

`失败率 = (error + timeout) / 调用总数`。**`empty` 不算失败** —— 它是「学生没出声」，
不是服务故障；把它算进失败率会让一次课堂嘈杂直接触发运维告警。

### 1.5 天（day）一律按**北京时间**切

服务器时间存 UTC（契约 §4.6），但「今天」对管理者意味着北京时间的那一天。
SQL 里统一用 `date(datetime(created_at, '+8 hours'))` 切天。

---

## 二、F7「视图优先」的执行情况（含**两处刻意的例外**）

| 视图 | 用了吗 | 说明 |
|---|---|---|
| `v_student_unit_progress` | ✅ **用了** | F2 的 `unfinished_students` 与单元内学生进度。 |
| `v_class_today_activity` | ❌ **不能用** | 它按 `DATE(s.started_at)` 即 **UTC 日期**分桶。北京时间 00:00–08:00 的练习会被算进前一天，U11 专门验这件事。用了它就必然错。 |
| `v_word_error_ranking` | ❌ **不能用** | 它没有 `class_id` 维度、没有日期维度、也没有正确率，而 F1 要「按班级 + 日期范围的错题 Top N」、F2 要「按正确率升序的难词榜」—— 三个条件它一个都不满足。 |

F7 是**偏好**（少写重复聚合 SQL），不是绝对禁令：当视图的维度与需求不匹配时，
硬用视图会产出错的数字，比多写一段 SQL 糟得多。此处两处例外都写明了原因，
后续若视图补齐维度，应当改回用视图。

---

## 三、只读边界

本模块**只读** `answers` / `sessions` / `recitations` / `word_mastery` / `asr_logs`。
不写任何业务表（F5 申诉复核的唯一写入在 `app/api/appeals.py`，写的是
`answers.appeal_result` + `audit_logs`，且**不改 `is_correct`**）。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from typing import Any, Sequence

from app.core.db import read_conn
from app.core.errors import BizError, ErrorCode
from app.services.mastery_service import MasteryService
from app.services.user_service import UserService

#: `YYYY-MM-DD` 的严格形式（`date.fromisoformat` 从 3.11 起也接受 `YYYYMMDD`，见 `parse_day`）
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: 北京时间相对 UTC 的偏移小时数。
#: 中国自 1991 年起不再实行夏令时，**固定 +8 与 Asia/Shanghai 完全等价**，
#: 不是近似值（与 `app/tasks/scheduler.local_tz()` 同一结论）。
BEIJING_OFFSET_HOURS = 8

#: SQL 里切天用的小时位移表达式（写成常量避免各处手滑写成 `+8hour`）
_BJ = f"+{BEIJING_OFFSET_HOURS} hours"

#: 班级概览趋势默认天数
DEFAULT_TREND_DAYS = 7

#: 单个班级概览趋势最多返回多少天（防止管理端拉了 3 年数据把图表画爆）
MAX_TREND_DAYS = 180


# ===========================================================================
# 时间工具
# ===========================================================================


def beijing_today() -> date:
    """北京时间「今天」。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=BEIJING_OFFSET_HOURS))
    ).date()


def beijing_day(offset_days: int = 0) -> str:
    """北京时间某天（`offset_days=0` 是今天，`-1` 是昨天）的 `YYYY-MM-DD`。"""
    return (beijing_today() + timedelta(days=offset_days)).isoformat()


def parse_day(raw: str | None, field: str, default: str) -> str:
    """校验 `YYYY-MM-DD`。**非法值抛 10001 并带上字段名**（管理端要能提示到具体框）。

    ⚠️ **不能只靠 `date.fromisoformat`**：从 Python 3.11 起它还接受紧凑格式
    `YYYYMMDD`（`date.fromisoformat("20260924")` 是合法的）。
    那样 `date_from=20260924` 会被静默接受，管理端拼错参数却拿不到任何提示 ——
    所以这里先用正则把「带横线」这一格式本身钉死，再做合法性校验。
    """
    if raw is None or raw == "":
        return default
    text = raw.strip()
    if not _DAY_RE.fullmatch(text):
        raise BizError(ErrorCode.PARAM_INVALID, f"{field} 格式应为 YYYY-MM-DD")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise BizError(
            ErrorCode.PARAM_INVALID, f"{field} 不是有效日期（如 2 月 30 日）"
        ) from exc


def normalise_range(
    date_from: str | None,
    date_to: str | None,
    *,
    default_days: int = DEFAULT_TREND_DAYS,
    max_days: int = MAX_TREND_DAYS,
) -> tuple[str, str]:
    """把可选的 `date_from` / `date_to` 规范成闭区间 `(from, to)`。

    默认「近 7 天」是**含今天在内的 7 天**（今天 + 前 6 天），不是「今天往前推 7 天」——
    管理者说「近 7 天」时想看的是含今天的 7 个自然日。
    """
    end = parse_day(date_to, "date_to", beijing_day(0))
    start = parse_day(date_from, "date_from", _shift(end, -(int(default_days) - 1)))
    if start > end:
        raise BizError(ErrorCode.PARAM_INVALID, "date_from 不能晚于 date_to")
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if span > max_days:
        raise BizError(
            ErrorCode.PARAM_INVALID, f"查询范围最多 {max_days} 天，当前 {span} 天"
        )
    return start, end


def _shift(day: str, delta: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=delta)).isoformat()


def day_series(date_from: str, date_to: str) -> list[str]:
    """闭区间内的每一天（升序）。趋势图要**补零**，所以先生成完整天数序列。"""
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


# ===========================================================================
# 内部工具
# ===========================================================================


def _marks(count: int) -> str:
    """生成 `?,?,?` 占位符。`IN` 子句不能参数化「个数」，只能拼占位符。"""
    return ",".join("?" * max(int(count), 1))


def _avg_int(total: int | None, count: int | None) -> int | None:
    """平均值的整数化（毫秒字段都是整数，返回 None 表示没有样本）。"""
    if not count:
        return None
    return int(round((total or 0) / count))


class StatsService:
    """统计看板（F1 / F2 / F3 / F10 / F11）。全部为只读方法。"""

    # ------------------------------------------------------------------ F6

    @staticmethod
    def calc_accuracy(correct: int, counted: int) -> float:
        """**正确率唯一口径**（F6）—— 转发到 `UserService._accuracy()`。

        ⚠️ 10b F6 要求「抽出 `calc_accuracy()` 单一函数，全项目唯一实现」。
        BE-02 已先落地了唯一实现 `UserService._accuracy()`（其 docstring 亦称
        「全项目唯一」）。若本包再写一份公式，项目里就有**两份**口径 ——
        与 F6 的目的（杜绝口径漂移）正好相反。

        因此这里**只做转发**：既满足「统计模块有一个具名的口径函数」，
        又不制造第二份实现。U5（同一份数据用不同接口查正确率结果一致）由
        `UserService._accuracy` 本体保证。
        """
        return UserService._accuracy(int(correct), int(counted))

    # ------------------------------------------------------------------ 班级

    @staticmethod
    def class_overview(
        class_id: int,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """F1 班级概览。

        返回：人数、今日活跃、完成率、平均正确率、平均用时、按天趋势、错题 Top N。
        """
        cid = int(class_id)
        start, end = normalise_range(date_from, date_to)
        top_n = max(1, min(int(top_n), 50))

        with read_conn() as conn:
            klass = conn.execute(
                "SELECT id, name, grade, semester, school_year, status FROM classes WHERE id = ?",
                (cid,),
            ).fetchone()
            if klass is None:
                raise BizError(ErrorCode.CLASS_NOT_FOUND)

            rows = conn.execute(
                """
                SELECT id, status, real_name, username
                FROM users
                WHERE class_id = ? AND role = 'student'
                """,
                (cid,),
            ).fetchall()
            student_ids = [int(r["id"]) for r in rows]
            active_students = sum(1 for r in rows if r["status"] == "active")

            data = StatsService._class_answer_stats(conn, student_ids, start, end)
            today = StatsService._class_today(conn, student_ids)
            trend = StatsService._class_trend(conn, student_ids, start, end)
            top_wrong = StatsService._class_top_wrong(conn, student_ids, start, end, top_n)
            completion = StatsService._class_completion(
                conn, student_ids, klass["grade"], klass["semester"]
            )
            duration = StatsService._class_duration(conn, student_ids, start, end)

        return {
            "class_id": cid,
            "class_name": klass["name"],
            "grade": int(klass["grade"]),
            "semester": klass["semester"],
            "school_year": klass["school_year"],
            "status": klass["status"],
            "student_count": len(student_ids),
            "active_student_count": active_students,
            "today_active_students": today["active_students"],
            "today_session_count": today["session_count"],
            "today_item_count": today["item_count"],
            "today_date": beijing_day(0),
            "date_from": start,
            "date_to": end,
            "answer_count": data["answer_count"],
            "counted_count": data["counted_count"],
            "correct_count": data["correct_count"],
            "accuracy": StatsService.calc_accuracy(data["correct_count"], data["counted_count"]),
            "avg_answer_ms": duration["avg_answer_ms"],
            "avg_session_ms": duration["avg_session_ms"],
            "completion_ratio": completion["ratio"],
            "practiced_word_count": completion["practiced_words"],
            "scope_word_count": completion["scope_words"],
            "trend": trend,
            "top_wrong_words": top_wrong,
        }

    @staticmethod
    def _class_answer_stats(
        conn: sqlite3.Connection, student_ids: list[int], start: str, end: str
    ) -> dict[str, int]:
        if not student_ids:
            return {"answer_count": 0, "counted_count": 0, "correct_count": 0}
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS answer_count,
                   SUM(CASE WHEN match_type <> 'empty' THEN 1 ELSE 0 END) AS counted_count,
                   SUM(CASE WHEN match_type IN ('exact','loose','fuzzy')
                            THEN 1 ELSE 0 END) AS correct_count
            FROM answers
            WHERE student_id IN ({_marks(len(student_ids))})
              AND date(datetime(created_at, ?)) BETWEEN ? AND ?
            """,
            # ⚠️ 修饰符 '+8 hours' 走参数绑定是**故意**的：SQLite 的
            #   `datetime(X, ?)` 第二参可以是绑定值（它不是 SQL 标识符位置）。
            #   这样偏移量只有 `_BJ` 一个出处，不会各写各的。
            [*student_ids, _BJ, start, end],
        ).fetchone()
        return {
            "answer_count": int(row["answer_count"] or 0),
            "counted_count": int(row["counted_count"] or 0),
            "correct_count": int(row["correct_count"] or 0),
        }

    @staticmethod
    def _class_today(conn: sqlite3.Connection, student_ids: list[int]) -> dict[str, int]:
        """今日活跃（**北京时间今天**）。

        刻意不用 `v_class_today_activity`：那个视图按 UTC 日期分桶
        （见模块 docstring §二）。这里按北京时间重算。
        """
        if not student_ids:
            return {"active_students": 0, "session_count": 0, "item_count": 0}
        today = beijing_day(0)
        row = conn.execute(
            f"""
            SELECT COUNT(DISTINCT student_id) AS active_students,
                   COUNT(DISTINCT session_id) AS session_count,
                   COUNT(*)                   AS item_count
            FROM answers
            WHERE student_id IN ({_marks(len(student_ids))})
              AND date(datetime(created_at, ?)) = ?
            """,
            [*student_ids, _BJ, today],
        ).fetchone()
        return {
            "active_students": int(row["active_students"] or 0),
            "session_count": int(row["session_count"] or 0),
            "item_count": int(row["item_count"] or 0),
        }

    @staticmethod
    def _class_trend(
        conn: sqlite3.Connection, student_ids: list[int], start: str, end: str
    ) -> list[dict[str, Any]]:
        """按天趋势。**缺的天要补零**，否则管理端图表会把两点直接连线，视觉上骗人。"""
        series = day_series(start, end)
        buckets: dict[str, dict[str, int]] = {}
        if student_ids:
            for row in conn.execute(
                f"""
                SELECT date(datetime(created_at, ?)) AS d,
                       COUNT(DISTINCT student_id) AS active_students,
                       COUNT(DISTINCT session_id) AS session_count,
                       COUNT(*)                   AS item_count,
                       SUM(CASE WHEN match_type <> 'empty' THEN 1 ELSE 0 END) AS counted_count,
                       SUM(CASE WHEN match_type IN ('exact','loose','fuzzy')
                                THEN 1 ELSE 0 END) AS correct_count
                FROM answers
                WHERE student_id IN ({_marks(len(student_ids))})
                  AND date(datetime(created_at, ?)) BETWEEN ? AND ?
                GROUP BY d
                """,
                [_BJ, *student_ids, _BJ, start, end],
            ).fetchall():
                buckets[str(row["d"])] = {
                    "active_students": int(row["active_students"] or 0),
                    "session_count": int(row["session_count"] or 0),
                    "item_count": int(row["item_count"] or 0),
                    "counted_count": int(row["counted_count"] or 0),
                    "correct_count": int(row["correct_count"] or 0),
                }

        trend: list[dict[str, Any]] = []
        for day in series:
            b = buckets.get(
                day,
                {
                    "active_students": 0,
                    "session_count": 0,
                    "item_count": 0,
                    "counted_count": 0,
                    "correct_count": 0,
                },
            )
            trend.append(
                {
                    "date": day,
                    **b,
                    "accuracy": StatsService.calc_accuracy(
                        b["correct_count"], b["counted_count"]
                    ),
                }
            )
        return trend

    @staticmethod
    def _class_top_wrong(
        conn: sqlite3.Connection, student_ids: list[int], start: str, end: str, top_n: int
    ) -> list[dict[str, Any]]:
        """错题 Top N（按错误次数降序）。

        刻意不用 `v_word_error_ranking`：该视图无班级维度、无日期维度，
        且不含正确率（见模块 docstring §二）。
        """
        if not student_ids:
            return []
        rows = conn.execute(
            f"""
            SELECT w.id AS word_id, w.en, w.cn, w.unit_id, u.name AS unit_name,
                   COUNT(*)                    AS error_count,
                   COUNT(DISTINCT a.student_id) AS student_count
            FROM answers a
            JOIN words w ON w.id = a.word_id
            LEFT JOIN units u ON u.id = w.unit_id
            WHERE a.student_id IN ({_marks(len(student_ids))})
              AND a.is_correct = 0
              AND a.match_type <> 'empty'
              AND date(datetime(a.created_at, ?)) BETWEEN ? AND ?
            GROUP BY w.id
            ORDER BY error_count DESC, w.id ASC
            LIMIT ?
            """,
            [*student_ids, _BJ, start, end, top_n],
        ).fetchall()
        return [
            {
                "word_id": int(r["word_id"]),
                "en": r["en"],
                "cn": r["cn"],
                "unit_id": r["unit_id"],
                "unit_name": r["unit_name"],
                "error_count": int(r["error_count"] or 0),
                "student_count": int(r["student_count"] or 0),
            }
            for r in rows
        ]

    @staticmethod
    def _class_completion(
        conn: sqlite3.Connection, student_ids: list[int], grade: int, semester: str
    ) -> dict[str, int | float]:
        """完成率（词条维度，口径见模块 docstring §1.2）。"""
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM words w
            JOIN units u ON u.id = w.unit_id
            WHERE u.subject = 'english' AND u.grade = ? AND u.semester = ?
              AND u.status = 'active' AND w.status = 'active'
            """,
            (int(grade), semester),
        ).fetchone()
        scope = int(row["n"] or 0)

        practiced = 0
        if student_ids:
            row = conn.execute(
                f"""
                SELECT COUNT(DISTINCT word_id) AS n
                FROM answers
                WHERE student_id IN ({_marks(len(student_ids))})
                  AND word_id IS NOT NULL
                  AND match_type <> 'empty'
                """,
                student_ids,
            ).fetchone()
            practiced = int(row["n"] or 0)

        return {
            "practiced_words": practiced,
            "scope_words": scope,
            "ratio": round(practiced / scope, 4) if scope > 0 else 0.0,
        }

    @staticmethod
    def _class_duration(
        conn: sqlite3.Connection, student_ids: list[int], start: str, end: str
    ) -> dict[str, int | None]:
        """平均用时。

        `avg_answer_ms` = 单题平均耗时；`avg_session_ms` = 单场平均总耗时
        （先把同一场的题耗时加起来，再对场次求平均 —— 而不是再除以场次题数，
        这样「一场 20 题」和「一场 5 题」对平均值的影响是同等的，符合「用时长不长」的直觉）。
        """
        if not student_ids:
            return {"avg_answer_ms": None, "avg_session_ms": None}
        marks = _marks(len(student_ids))
        row = conn.execute(
            f"""
            SELECT SUM(duration_ms) AS total, COUNT(*) AS n
            FROM answers
            WHERE student_id IN ({marks})
              AND duration_ms > 0
              AND date(datetime(created_at, ?)) BETWEEN ? AND ?
            """,
            [*student_ids, _BJ, start, end],
        ).fetchone()
        avg_answer = _avg_int(row["total"], row["n"])

        row = conn.execute(
            f"""
            SELECT AVG(session_total) AS avg_session_ms
            FROM (
                SELECT SUM(duration_ms) AS session_total
                FROM answers
                WHERE student_id IN ({marks})
                  AND date(datetime(created_at, ?)) BETWEEN ? AND ?
                GROUP BY session_id
                HAVING SUM(duration_ms) > 0
            )
            """,
            [*student_ids, _BJ, start, end],
        ).fetchone()
        raw = row["avg_session_ms"]
        return {
            "avg_answer_ms": avg_answer,
            "avg_session_ms": int(round(raw)) if raw is not None else None,
        }

    # ------------------------------------------------------------------ 单元

    @staticmethod
    def unit_overview(unit_id: int, *, hard_word_limit: int = 20) -> dict[str, Any]:
        """F2 单元掌握度。

        `unfinished_students` 走视图 `v_student_unit_progress`（F7 的正面例子）。

        ⚠️ 该视图里的 `word_total` **不是**单元词条总数，而是「该学生在该单元有
        `word_mastery` 记录（= 练过）的词条数」—— 名字有歧义。因此「未完成」的判定
        必须拿**外部算出的单元真实词条数**去比，不能拿视图自己的 `word_total` 比
        （那样两边恒等，未完成名单永远是空的）。此处已按前者实现。
        """
        uid = int(unit_id)
        hard_word_limit = max(1, min(int(hard_word_limit), 100))

        with read_conn() as conn:
            unit = conn.execute(
                "SELECT id, subject, grade, semester, name, status FROM units WHERE id = ?",
                (uid,),
            ).fetchone()
            if unit is None:
                raise BizError(ErrorCode.UNIT_NOT_FOUND)

            word_total = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM words WHERE unit_id = ? AND status = 'active'",
                    (uid,),
                ).fetchone()["n"]
                or 0
            )

            agg = conn.execute(
                """
                SELECT COUNT(*) AS answer_count,
                       COUNT(DISTINCT a.student_id) AS student_count,
                       SUM(CASE WHEN a.match_type <> 'empty' THEN 1 ELSE 0 END) AS counted_count,
                       SUM(CASE WHEN a.match_type IN ('exact','loose','fuzzy')
                                THEN 1 ELSE 0 END) AS correct_count,
                       COUNT(DISTINCT CASE WHEN a.match_type <> 'empty' THEN a.word_id END)
                           AS practiced_words
                FROM answers a
                JOIN words w ON w.id = a.word_id
                WHERE w.unit_id = ?
                """,
                (uid,),
            ).fetchone()

            mastered = conn.execute(
                """
                SELECT COUNT(*) AS mastered_pairs,
                       COUNT(DISTINCT m.word_id) AS mastered_words
                FROM word_mastery m
                JOIN words w ON w.id = m.word_id
                WHERE w.unit_id = ? AND m.mastered_at IS NOT NULL
                """,
                (uid,),
            ).fetchone()

            unfinished = conn.execute(
                """
                SELECT p.student_id, u.username, u.real_name, u.status,
                       p.word_practiced, p.word_mastered,
                       p.correct_total, p.answer_total
                FROM v_student_unit_progress p
                JOIN users u ON u.id = p.student_id
                WHERE p.unit_id = ? AND p.word_practiced < ?
                ORDER BY p.word_practiced ASC, p.student_id ASC
                LIMIT 200
                """,
                (uid, word_total),
            ).fetchall()

            hard_rows = conn.execute(
                """
                SELECT w.id AS word_id, w.en, w.cn, w.type,
                       COUNT(*) AS answer_count,
                       SUM(CASE WHEN a.match_type <> 'empty' THEN 1 ELSE 0 END) AS counted_count,
                       SUM(CASE WHEN a.match_type IN ('exact','loose','fuzzy')
                                THEN 1 ELSE 0 END) AS correct_count,
                       SUM(CASE WHEN a.is_correct = 0 AND a.match_type <> 'empty'
                                THEN 1 ELSE 0 END) AS error_count
                FROM answers a
                JOIN words w ON w.id = a.word_id
                WHERE w.unit_id = ?
                GROUP BY w.id
                HAVING SUM(CASE WHEN a.match_type <> 'empty' THEN 1 ELSE 0 END) > 0
                ORDER BY (SUM(CASE WHEN a.match_type IN ('exact','loose','fuzzy')
                                   THEN 1 ELSE 0 END) * 1.0
                          / SUM(CASE WHEN a.match_type <> 'empty' THEN 1 ELSE 0 END)) ASC,
                         answer_count DESC,
                         w.id ASC
                LIMIT ?
                """,
                (uid, hard_word_limit),
            ).fetchall()

        practiced_words = int(agg["practiced_words"] or 0)
        mastered_words = int(mastered["mastered_words"] or 0)
        return {
            "unit_id": uid,
            "unit_name": unit["name"],
            "subject": unit["subject"],
            "grade": int(unit["grade"]),
            "semester": unit["semester"],
            "status": unit["status"],
            "word_total": word_total,
            "student_count": int(agg["student_count"] or 0),
            "answer_count": int(agg["answer_count"] or 0),
            "counted_count": int(agg["counted_count"] or 0),
            "correct_count": int(agg["correct_count"] or 0),
            "accuracy": StatsService.calc_accuracy(
                int(agg["correct_count"] or 0), int(agg["counted_count"] or 0)
            ),
            "practiced_word_count": practiced_words,
            "completion_ratio": round(practiced_words / word_total, 4) if word_total else 0.0,
            "mastered_word_count": mastered_words,
            "mastered_pair_count": int(mastered["mastered_pairs"] or 0),
            "mastered_ratio": round(mastered_words / word_total, 4) if word_total else 0.0,
            "unfinished_students": [
                {
                    "student_id": int(r["student_id"]),
                    "username": r["username"],
                    "real_name": r["real_name"],
                    "status": r["status"],
                    "practiced_words": int(r["word_practiced"] or 0),
                    "mastered_words": int(r["word_mastered"] or 0),
                    "missing_words": max(word_total - int(r["word_practiced"] or 0), 0),
                    "accuracy": StatsService.calc_accuracy(
                        int(r["correct_total"] or 0), int(r["answer_total"] or 0)
                    ),
                }
                for r in unfinished
            ],
            "hard_words": [
                {
                    "word_id": int(r["word_id"]),
                    "en": r["en"],
                    "cn": r["cn"],
                    "type": r["type"],
                    "answer_count": int(r["answer_count"] or 0),
                    "counted_count": int(r["counted_count"] or 0),
                    "correct_count": int(r["correct_count"] or 0),
                    "error_count": int(r["error_count"] or 0),
                    "accuracy": StatsService.calc_accuracy(
                        int(r["correct_count"] or 0), int(r["counted_count"] or 0)
                    ),
                }
                for r in hard_rows
            ],
        }

    # ------------------------------------------------------------------ 学生

    @staticmethod
    def student_report(student_id: int, *, recent_limit: int = 10) -> dict[str, Any]:
        """F3 个人报告。

        `mastered_words` / `streak_days` / `wrong_words` 走 `MasteryService`
        （物化表，O(1)），**不实时扫 `answers`**（§8.8 明文要求）。
        """
        sid = int(student_id)
        recent_limit = max(1, min(int(recent_limit), 50))

        with read_conn() as conn:
            student = conn.execute(
                """
                SELECT u.id, u.username, u.real_name, u.status, u.class_id,
                       u.register_source, u.created_at, u.last_login_at,
                       c.name AS class_name
                FROM users u
                LEFT JOIN classes c ON c.id = u.class_id
                WHERE u.id = ? AND u.role = 'student'
                """,
                (sid,),
            ).fetchone()
            if student is None:
                raise BizError(ErrorCode.NOT_FOUND, "学生不存在")

            answer = conn.execute(
                """
                SELECT COUNT(*) AS answer_count,
                       SUM(CASE WHEN match_type <> 'empty' THEN 1 ELSE 0 END) AS counted_count,
                       SUM(CASE WHEN match_type IN ('exact','loose','fuzzy')
                                THEN 1 ELSE 0 END) AS correct_count
                FROM answers WHERE student_id = ?
                """,
                (sid,),
            ).fetchone()

            counts = conn.execute(
                """
                SELECT (SELECT COUNT(*) FROM sessions WHERE student_id = ?) AS session_count,
                       (SELECT COUNT(*) FROM sessions
                         WHERE student_id = ? AND status = 'finished') AS finished_session_count,
                       (SELECT COUNT(DISTINCT date(datetime(created_at, ?)))
                          FROM answers WHERE student_id = ?) AS practice_days,
                       (SELECT COUNT(*) FROM word_mastery
                         WHERE student_id = ? AND mastered_at IS NOT NULL) AS mastered_words,
                       (SELECT COUNT(*) FROM recitations WHERE student_id = ?) AS recitation_count
                """,
                (sid, sid, _BJ, sid, sid, sid),
            ).fetchone()

            days = [
                str(r["d"])
                for r in conn.execute(
                    """
                    SELECT DISTINCT date(datetime(created_at, ?)) AS d
                    FROM answers WHERE student_id = ?
                    ORDER BY d DESC LIMIT 400
                    """,
                    (_BJ, sid),
                ).fetchall()
            ]

            sessions = conn.execute(
                """
                SELECT s.id, s.content_type, s.unit_id, u.name AS unit_name,
                       s.strategy, s.total_items, s.correct_items, s.hint_count,
                       s.score, s.status, s.started_at, s.ended_at
                FROM sessions s
                LEFT JOIN units u ON u.id = s.unit_id
                WHERE s.student_id = ?
                ORDER BY s.started_at DESC, s.id DESC
                LIMIT ?
                """,
                (sid, recent_limit),
            ).fetchall()

            recitations = conn.execute(
                """
                SELECT r.id, r.text_id, t.title AS text_title, r.grade, r.score,
                       r.covered_ratio, r.total_chars, r.matched_chars,
                       r.duration_ms, r.first_voice_ms, r.finish_reason,
                       r.restart_count, r.created_at
                FROM recitations r
                LEFT JOIN texts t ON t.id = r.text_id
                WHERE r.student_id = ?
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT ?
                """,
                (sid, recent_limit),
            ).fetchall()

        wrong_items, wrong_total = MasteryService.wrong_words(sid, page=1, page_size=10)

        return {
            "student_id": sid,
            "username": student["username"],
            "real_name": student["real_name"],
            "status": student["status"],
            "class_id": student["class_id"],
            "class_name": student["class_name"],
            "register_source": student["register_source"],
            "created_at": student["created_at"],
            "last_login_at": student["last_login_at"],
            "practice_days": int(counts["practice_days"] or 0),
            "session_count": int(counts["session_count"] or 0),
            "finished_session_count": int(counts["finished_session_count"] or 0),
            "answer_count": int(answer["answer_count"] or 0),
            "counted_count": int(answer["counted_count"] or 0),
            "correct_count": int(answer["correct_count"] or 0),
            "accuracy": StatsService.calc_accuracy(
                int(answer["correct_count"] or 0), int(answer["counted_count"] or 0)
            ),
            "mastered_words": int(counts["mastered_words"] or 0),
            "streak_days": MasteryService.streak_days(days, beijing_day(0)),
            "recitation_count": int(counts["recitation_count"] or 0),
            "recent_sessions": [
                {
                    "session_id": int(r["id"]),
                    "content_type": r["content_type"],
                    "unit_id": r["unit_id"],
                    "unit_name": r["unit_name"],
                    "strategy": r["strategy"],
                    "total_items": int(r["total_items"] or 0),
                    "correct_items": int(r["correct_items"] or 0),
                    "hint_count": int(r["hint_count"] or 0),
                    "score": r["score"],
                    "status": r["status"],
                    "started_at": r["started_at"],
                    "ended_at": r["ended_at"],
                }
                for r in sessions
            ],
            "recitations": [
                {
                    "recitation_id": int(r["id"]),
                    "text_id": r["text_id"],
                    "text_title": r["text_title"],
                    "grade": r["grade"],
                    "score": r["score"],
                    "covered_ratio": r["covered_ratio"],
                    "total_chars": int(r["total_chars"] or 0),
                    "matched_chars": int(r["matched_chars"] or 0),
                    "duration_ms": int(r["duration_ms"] or 0),
                    "first_voice_ms": r["first_voice_ms"],
                    "finish_reason": r["finish_reason"],
                    "restart_count": int(r["restart_count"] or 0),
                    "created_at": r["created_at"],
                }
                for r in recitations
            ],
            "wrong_word_total": int(wrong_total),
            "wrong_words": wrong_items,
        }

    # ---------------------------------------------------------------- F10

    @staticmethod
    def audit_logs(
        *,
        page: int,
        page_size: int,
        user_id: int | None = None,
        action: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """F10 审计日志查询（分页 + 按用户/动作/时间筛）。

        `action` 支持**前缀匹配**（`student.` 能筛出全部学生相关动作）——
        审计的动作名是 `域.动词` 点分结构（契约 §7.5），按域筛是最常用的用法，
        强制全等匹配会逼着管理者去猜完整动作名。
        """
        where: list[str] = []
        params: list[Any] = []

        if user_id is not None:
            where.append("a.user_id = ?")
            params.append(int(user_id))
        if action:
            raw = action.strip()
            where.append("a.action LIKE ? ESCAPE '\\'")
            escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"{escaped}%")
        if date_from or date_to:
            start, end = normalise_range(date_from, date_to, default_days=DEFAULT_TREND_DAYS)
            where.append("date(datetime(a.created_at, ?)) BETWEEN ? AND ?")
            params.extend([_BJ, start, end])

        clause = f"WHERE {' AND '.join(where)}" if where else ""

        with read_conn() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM audit_logs a {clause}", params
                ).fetchone()["n"]
                or 0
            )
            rows = conn.execute(
                f"""
                SELECT a.id, a.user_id, a.action, a.target_type, a.target_id,
                       a.detail, a.ip, a.created_at,
                       u.username, u.real_name, u.role
                FROM audit_logs a
                LEFT JOIN users u ON u.id = a.user_id
                {clause}
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()

        items: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            raw_detail = d.get("detail")
            if isinstance(raw_detail, str) and raw_detail:
                import json

                try:
                    d["detail"] = json.loads(raw_detail)
                except ValueError:
                    # 老记录/手工写入的非法 JSON：原样返回，别让列表整页 500
                    pass
            items.append(d)
        return items, total

    # ---------------------------------------------------------------- F11

    #: 导出的学生成绩表列（班级导出与个人导出共用同一份列定义）
    CLASS_EXPORT_HEADERS: Sequence[str] = (
        "学号", "姓名", "班级", "练习场次", "答题数", "有效题数", "正确题数",
        "正确率", "已掌握词数", "最近练习时间",
    )

    STUDENT_EXPORT_HEADERS: Sequence[str] = (
        "场次ID", "类型", "单元", "策略", "题数", "正确数", "正确率",
        "得分", "状态", "开始时间", "结束时间",
    )

    @staticmethod
    def export_class_rows(class_id: int) -> tuple[str, list[Sequence[Any]]]:
        """F11 班级成绩导出的数据行。**正确率统一走 `calc_accuracy`**。"""
        cid = int(class_id)
        with read_conn() as conn:
            klass = conn.execute(
                "SELECT id, name, grade, semester FROM classes WHERE id = ?", (cid,)
            ).fetchone()
            if klass is None:
                raise BizError(ErrorCode.CLASS_NOT_FOUND)

            rows = conn.execute(
                """
                SELECT u.id, u.username, u.real_name,
                       (SELECT COUNT(*) FROM sessions s WHERE s.student_id = u.id)
                           AS session_count,
                       (SELECT COUNT(*) FROM answers a WHERE a.student_id = u.id)
                           AS answer_count,
                       (SELECT COUNT(*) FROM answers a
                         WHERE a.student_id = u.id AND a.match_type <> 'empty')
                           AS counted_count,
                       (SELECT COUNT(*) FROM answers a
                         WHERE a.student_id = u.id
                           AND a.match_type IN ('exact','loose','fuzzy'))
                           AS correct_count,
                       (SELECT COUNT(*) FROM word_mastery m
                         WHERE m.student_id = u.id AND m.mastered_at IS NOT NULL)
                           AS mastered_words,
                       (SELECT MAX(a.created_at) FROM answers a WHERE a.student_id = u.id)
                           AS last_answer_at
                FROM users u
                WHERE u.class_id = ? AND u.role = 'student'
                ORDER BY u.username
                """,
                (cid,),
            ).fetchall()

        data_rows: list[Sequence[Any]] = []
        for r in rows:
            data_rows.append(
                (
                    r["username"],
                    r["real_name"],
                    klass["name"],
                    int(r["session_count"] or 0),
                    int(r["answer_count"] or 0),
                    int(r["counted_count"] or 0),
                    int(r["correct_count"] or 0),
                    f"{StatsService.calc_accuracy(int(r['correct_count'] or 0), int(r['counted_count'] or 0)):.1%}",
                    int(r["mastered_words"] or 0),
                    r["last_answer_at"] or "",
                )
            )
        return f"{klass['name']}_成绩单.xlsx", data_rows

    @staticmethod
    def export_student_rows(student_id: int) -> tuple[str, list[Sequence[Any]]]:
        """F11 学生成绩导出（**按场次**，一行一场）。"""
        sid = int(student_id)
        with read_conn() as conn:
            student = conn.execute(
                "SELECT id, username, real_name FROM users WHERE id = ? AND role = 'student'",
                (sid,),
            ).fetchone()
            if student is None:
                raise BizError(ErrorCode.NOT_FOUND, "学生不存在")

            rows = conn.execute(
                """
                SELECT s.id, s.content_type, u.name AS unit_name, s.strategy,
                       s.total_items, s.correct_items, s.score, s.status,
                       s.started_at, s.ended_at,
                       (SELECT COUNT(*) FROM answers a
                         WHERE a.session_id = s.id AND a.match_type <> 'empty')
                           AS counted_count,
                       (SELECT COUNT(*) FROM answers a
                         WHERE a.session_id = s.id
                           AND a.match_type IN ('exact','loose','fuzzy'))
                           AS correct_count
                FROM sessions s
                LEFT JOIN units u ON u.id = s.unit_id
                WHERE s.student_id = ?
                ORDER BY s.started_at ASC, s.id ASC
                """,
                (sid,),
            ).fetchall()

        data_rows: list[Sequence[Any]] = []
        for r in rows:
            data_rows.append(
                (
                    int(r["id"]),
                    "语文" if r["content_type"] == "chinese" else "英语",
                    r["unit_name"] or "",
                    r["strategy"],
                    int(r["total_items"] or 0),
                    int(r["correct_items"] or 0),
                    f"{StatsService.calc_accuracy(int(r['correct_count'] or 0), int(r['counted_count'] or 0)):.1%}",
                    r["score"],
                    r["status"],
                    r["started_at"],
                    r["ended_at"] or "",
                )
            )
        return f"{student['real_name']}_成绩单.xlsx", data_rows
