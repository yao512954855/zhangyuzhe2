"""课堂实时大屏 —— WP-2D（二期 G1）。

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `GET /api/stats/live-board` | **admin** | 课堂实时大屏：班级实时概览 + 学生实时概览 |

### 数据来源与口径

- **纯只读**，不写任何业务表（与 `stats.py` 同一定位）。
- 实时数据来自 `answers`（今日北京时间的练习记录）+ `classes` / `users`（班级与学生名单）。
- **正确率唯一口径**：一律 `UserService._accuracy(correct, counted)`（契约 §8.1 / §15.5）。
  本模块**不重写公式**，只转发——保证与班级概览 / 学生报告完全一致，杜绝口径漂移。
- 今日口径按**北京时间**切天（与 `stats_service._class_today` 一致，刻意不用
  `v_class_today_activity` 那个按 UTC 分桶的视图，见契约 §14.1 第 14 项）。
- `class_id` 可选：传了则只返回该班级及其学生；不传返回全部班级与全部学生。
  `class_id` 不存在 → `20004`（CLASS_NOT_FOUND），与越权同口径不暴露存在性无关，属正常业务错误。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.db import read_conn, utc_now_iso
from app.core.deps import CurrentUser, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.services.stats_service import beijing_day
from app.services.user_service import UserService

router = APIRouter(tags=["live-board"])

# ⚠️ 时区偏移只在此处有出处（与 `stats_service._BJ` 同源）：北京时间 = UTC + 8h。
_BEIJING_OFFSET = "+8 hours"


def _today_class_stats(
    conn: Any, student_ids: list[int], today: str
) -> dict[int, dict[str, Any]]:
    """按 `student_id` 聚合今日的练习情况。

    返回 `{student_id: {sessions, items, counted, correct}}`；无记录的学生不出现在结果里。
    """
    out: dict[int, dict[str, Any]] = {}
    if not student_ids:
        return out
    rows = conn.execute(
        f"""
        SELECT student_id,
               COUNT(DISTINCT session_id) AS sessions,
               COUNT(*)                   AS items,
               SUM(CASE WHEN match_type <> 'empty' THEN 1 ELSE 0 END) AS counted,
               SUM(CASE WHEN match_type IN ('exact','loose','fuzzy')
                        THEN 1 ELSE 0 END) AS correct
        FROM answers
        WHERE date(datetime(created_at, ?)) = ?
          AND student_id IN ({','.join('?' * len(student_ids))})
        GROUP BY student_id
        """,
        [_BEIJING_OFFSET, today, *student_ids],
    ).fetchall()
    for r in rows:
        out[int(r["student_id"])] = {
            "sessions": int(r["sessions"] or 0),
            "items": int(r["items"] or 0),
            "counted": int(r["counted"] or 0),
            "correct": int(r["correct"] or 0),
        }
    return out


def _alltime_student_stats(
    conn: Any, student_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """按 `student_id` 聚合全部历史的练习情况。

    返回 `{student_id: {total, counted, correct, last_active}}`。
    """
    out: dict[int, dict[str, Any]] = {}
    if not student_ids:
        return out
    rows = conn.execute(
        f"""
        SELECT student_id,
               COUNT(*) AS total,
               SUM(CASE WHEN match_type <> 'empty' THEN 1 ELSE 0 END) AS counted,
               SUM(CASE WHEN match_type IN ('exact','loose','fuzzy')
                        THEN 1 ELSE 0 END) AS correct,
               MAX(created_at) AS last_active
        FROM answers
        WHERE student_id IN ({','.join('?' * len(student_ids))})
        GROUP BY student_id
        """,
        student_ids,
    ).fetchall()
    for r in rows:
        out[int(r["student_id"])] = {
            "total": int(r["total"] or 0),
            "counted": int(r["counted"] or 0),
            "correct": int(r["correct"] or 0),
            "last_active": r["last_active"],
        }
    return out


@router.get("/api/stats/live-board")
async def live_board(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """课堂实时大屏：班级 + 学生的实时练习概览。

    查询参数：
    - `class_id`（可选）：聚焦单个班级；不存在 → `20004`。

    返回：`{updated_at, classes:[…], students:[…]}`。
    """
    raw_class = request.query_params.get("class_id")
    class_filter: int | None = None
    if raw_class is not None and raw_class != "":
        try:
            class_filter = int(raw_class)
        except (TypeError, ValueError) as exc:
            raise BizError(ErrorCode.PARAM_INVALID, "class_id 必须是整数") from exc

    today = beijing_day(0)

    with read_conn() as conn:
        # ---- 班级名单（按过滤条件） ----
        if class_filter is not None:
            class_rows = conn.execute(
                """
                SELECT id, name, grade, semester, school_year, status
                FROM classes WHERE id = ?
                """,
                (class_filter,),
            ).fetchall()
            if not class_rows:
                raise BizError(ErrorCode.CLASS_NOT_FOUND)
        else:
            class_rows = conn.execute(
                """
                SELECT id, name, grade, semester, school_year, status
                FROM classes ORDER BY grade, name
                """
            ).fetchall()

        class_ids = [int(r["id"]) for r in class_rows]
        class_name_by_id = {int(r["id"]): r["name"] for r in class_rows}

        # ---- 学生名单（只取属于展示班级的、role=student） ----
        if class_ids:
            placeholders = ",".join("?" * len(class_ids))
            student_rows = conn.execute(
                f"""
                SELECT id, username, real_name, class_id, status
                FROM users
                WHERE role = 'student' AND class_id IN ({placeholders})
                ORDER BY class_id, real_name
                """,
                class_ids,
            ).fetchall()
        else:
            student_rows = []

        student_ids = [int(r["id"]) for r in student_rows]

        # ---- 今日 / 全量聚合 ----
        today_by_student = _today_class_stats(conn, student_ids, today)
        alltime_by_student = _alltime_student_stats(conn, student_ids)

    # ---- 班级聚合（把下属学生的今日数据 rollup） ----
    classes: list[dict[str, Any]] = []
    # student -> class 映射，便于 rollup
    student_class: dict[int, int] = {int(r["id"]): int(r["class_id"]) for r in student_rows}

    for c in class_rows:
        cid = int(c["id"])
        members = [sid for sid, ccls in student_class.items() if ccls == cid]
        student_count = len(members)
        active_student_count = sum(
            1 for r in student_rows if int(r["class_id"]) == cid and r["status"] == "active"
        )

        t_active = 0
        t_sessions = 0
        t_items = 0
        t_counted = 0
        t_correct = 0
        for sid in members:
            rec = today_by_student.get(sid)
            if rec is None:
                continue
            t_active += 1
            t_sessions += rec["sessions"]
            t_items += rec["items"]
            t_counted += rec["counted"]
            t_correct += rec["correct"]

        classes.append(
            {
                "class_id": cid,
                "class_name": c["name"],
                "grade": int(c["grade"]),
                "semester": c["semester"],
                "school_year": c["school_year"],
                "status": c["status"],
                "student_count": student_count,
                "active_student_count": active_student_count,
                "today_active_students": t_active,
                "today_session_count": t_sessions,
                "today_item_count": t_items,
                # ⚠️ 正确率**只**走 UserService._accuracy，不重写公式
                "today_accuracy": UserService._accuracy(t_correct, t_counted),
            }
        )

    # ---- 学生明细 ----
    students: list[dict[str, Any]] = []
    for r in student_rows:
        sid = int(r["id"])
        today_rec = today_by_student.get(sid, {"sessions": 0, "items": 0, "counted": 0, "correct": 0})
        all_rec = alltime_by_student.get(
            sid, {"total": 0, "counted": 0, "correct": 0, "last_active": None}
        )
        students.append(
            {
                "student_id": sid,
                "username": r["username"],
                "real_name": r["real_name"],
                "class_id": int(r["class_id"]),
                "class_name": class_name_by_id.get(int(r["class_id"]), ""),
                "status": r["status"],
                "today_session_count": today_rec["sessions"],
                "today_item_count": today_rec["items"],
                "today_accuracy": UserService._accuracy(today_rec["correct"], today_rec["counted"]),
                "practice_count": all_rec["total"],
                "accuracy": UserService._accuracy(all_rec["correct"], all_rec["counted"]),
                "last_active_at": all_rec["last_active"],
            }
        )

    return ok(
        {
            "updated_at": utc_now_iso(),
            "classes": classes,
            "students": students,
        }
    )
