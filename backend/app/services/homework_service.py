"""作业布置服务 —— WP-2G（作业布置与截止提醒，二期 G2）。

**本模块是 `homework` 表与 `homework_submissions` 表的唯一写入方**（契约 §15.1 / §15.6）。

设计要点：
- 作业绑定到某个班级（`class_id`），学生在 `GET /api/me/homework` 里**只看到自己班级**的作业
  （归属过滤在服务层 `WHERE class_id = ?` 里完成，跨班窥探在源头就被挡掉）。
- 布置作业的同时，为**该班当前所有学生**生成 `homework_submissions`（status='pending'），
  这样学生端的「完成状态」才有据可查；之后转入该班的学生，通过 `LEFT JOIN` 自动视为 pending。
- 截止时间 `due_at` 若早于「现在」，视为「已截止」→ 抛 `70003`（契约 §15.3）。
- 全部写操作走 `db.write_tx()`（契约 §4.3），读走 `db.read_conn()`。
- 跨包写入纪律：只写 `homework` / `homework_submissions`，**绝不**碰别的表（尤其 `word_mastery` /
  `settings`，见契约 §15.6 禁止清单）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.audit import write_audit
from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode

# 学科取值（与 `homework.subject` 的 CHECK 约束一致）。
_SUBJECTS = ("chinese", "english")
# homework 状态取值。
_HOMEWORK_STATUS = ("active", "closed")
# submission 状态取值。
_SUBMISSION_STATUS = ("pending", "done")


def _parse_iso(value: str) -> datetime:
    """解析 ISO 8601 时间（`2026-09-23T10:11:29Z` 或带时区偏移），失败抛 `10001`。"""
    s = (value or "").strip()
    if not s:
        raise BizError(ErrorCode.PARAM_INVALID, "时间格式不能为空")
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError as exc:
        raise BizError(ErrorCode.PARAM_INVALID, "时间格式不正确，请使用 ISO 8601（如 2026-09-23T10:11:29Z）") from exc


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_unit_ids(raw: Any) -> list[int]:
    """把入参的 unit_ids 规整成 `list[int]`；非法（非列表 / 含非整数）抛 `10001`。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise BizError(ErrorCode.PARAM_INVALID, "unit_ids 必须是数组")
    out: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int):
            # bool 是 int 的子类，单独排除；其余非整数一律拒绝
            raise BizError(ErrorCode.PARAM_INVALID, "unit_ids 中的元素必须是整数")
        out.append(int(item))
    return out


def _load_unit_ids(column: Any) -> list[int]:
    """把库里存成 JSON 文本的 `unit_ids` 读回 `list[int]`。"""
    if column is None or column == "":
        return []
    try:
        data = json.loads(column)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [int(x) for x in data if isinstance(x, int)]


# =========================================================================== 管理端（admin）


def list_homework(
    *,
    class_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """分页列出作业（管理端）。

    每项附带派生字段：`class_name` / `submitted_count`（已提交人数）/
    `student_count`（该班在读学生数）。

    Args:
        class_id: 可选，按班级过滤；`None` 表示全部班级。
    """
    where: list[str] = []
    params: list[Any] = []
    if class_id is not None:
        where.append("h.class_id = ?")
        params.append(int(class_id))

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with read_conn() as conn:
        total = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM homework h {where_sql}", params
            ).fetchone()["n"]
        )
        rows = conn.execute(
            f"""
            SELECT h.id, h.class_id, h.created_by, h.title, h.subject,
                   h.unit_ids, h.due_at, h.status, h.created_at,
                   c.name AS class_name,
                   (SELECT COUNT(*) FROM homework_submissions s
                      WHERE s.homework_id = h.id AND s.status = 'done') AS submitted_count,
                   (SELECT COUNT(*) FROM users u
                      WHERE u.class_id = h.class_id
                        AND u.role = 'student' AND u.status = 'active') AS student_count
            FROM homework h
            LEFT JOIN classes c ON c.id = h.class_id
            {where_sql}
            ORDER BY h.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()

    items: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["unit_ids"] = _load_unit_ids(d.get("unit_ids"))
        d["submitted_count"] = int(d.get("submitted_count") or 0)
        d["student_count"] = int(d.get("student_count") or 0)
        items.append(d)
    return items, total


def create_homework(
    *,
    class_id: int,
    title: str,
    subject: str,
    unit_ids: Any,
    due_at: str | None,
    created_by: int,
    ip: str | None = None,
) -> dict[str, Any]:
    """布置一条作业（管理端 `POST /api/homework`）。

    Raises:
        BizError: `20004` 班级不存在 / `10001` 学科非法或 unit_ids 非法 /
                  `70003` 截止时间早于当前时间。
    """
    # 1) 班级必须存在（越权与「不存在」口径：这里直接按「班级不存在」返回 20004）
    with read_conn() as conn:
        cls = conn.execute(
            "SELECT id, name FROM classes WHERE id = ?", (int(class_id),)
        ).fetchone()
    if cls is None:
        raise BizError(ErrorCode.CLASS_NOT_FOUND, f"班级不存在（id={class_id}）")

    # 2) 字段校验
    if subject not in _SUBJECTS:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            f"学科只能是 {'/'.join(_SUBJECTS)}",
        )
    parsed_units = _parse_unit_ids(unit_ids)

    due_iso: str | None = None
    if due_at is not None and str(due_at).strip() != "":
        dt = _parse_iso(str(due_at))
        if dt < _now_utc():
            raise BizError(ErrorCode.HOMEWORK_EXPIRED, "作业已截止，截止时间不能早于当前时间")
        due_iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    title = (title or "").strip()
    if not title:
        raise BizError(ErrorCode.PARAM_INVALID, "作业标题不能为空")

    now = utc_now_iso()
    with write_tx() as conn:
        cur = conn.execute(
            """
            INSERT INTO homework
                (class_id, created_by, title, subject, unit_ids, due_at, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (int(class_id), int(created_by), title, subject, json.dumps(parsed_units), due_iso, now),
        )
        homework_id = int(cur.lastrowid)

        # 为「该班在读学生」批量生成 pending 提交记录（归属写入方唯一性：仅本服务写）
        conn.execute(
            """
            INSERT INTO homework_submissions (homework_id, student_id, status, created_at)
            SELECT ?, id, 'pending', ?
            FROM users
            WHERE class_id = ? AND role = 'student' AND status = 'active'
            """,
            (homework_id, now, int(class_id)),
        )

        write_audit(
            conn,
            user_id=int(created_by),
            action="homework.create",
            target_type="homework",
            target_id=homework_id,
            detail={"class_id": int(class_id), "title": title, "subject": subject, "due_at": due_iso},
            ip=ip,
        )

    return {"id": homework_id}


def update_homework(
    *,
    homework_id: int,
    title: str | None = None,
    subject: str | None = None,
    unit_ids: Any = None,
    due_at: str | None = None,
    status: str | None = None,
    operator_id: int,
    ip: str | None = None,
) -> dict[str, Any]:
    """修改作业（管理端 `PATCH /api/homework/{id}`）。

    Raises:
        BizError: `70002` 作业不存在 / `10001` 学科或状态非法、due_at 格式错 /
                  `70003` 截止时间早于当前时间。
    """
    with read_conn() as conn:
        row = conn.execute(
            "SELECT id FROM homework WHERE id = ?", (int(homework_id),)
        ).fetchone()
    if row is None:
        raise BizError(ErrorCode.HOMEWORK_NOT_FOUND, f"作业不存在（id={homework_id}）")

    sets: list[str] = []
    params: list[Any] = []
    detail: dict[str, Any] = {}

    if title is not None:
        t = title.strip()
        if not t:
            raise BizError(ErrorCode.PARAM_INVALID, "作业标题不能为空")
        sets.append("title = ?")
        params.append(t)
        detail["title"] = t

    if subject is not None:
        if subject not in _SUBJECTS:
            raise BizError(ErrorCode.PARAM_INVALID, f"学科只能是 {'/'.join(_SUBJECTS)}")
        sets.append("subject = ?")
        params.append(subject)
        detail["subject"] = subject

    if unit_ids is not None:
        parsed = _parse_unit_ids(unit_ids)
        sets.append("unit_ids = ?")
        params.append(json.dumps(parsed))
        detail["unit_ids"] = parsed

    if due_at is not None:
        # 允许把截止时间清空（传空串）
        if str(due_at).strip() == "":
            sets.append("due_at = NULL")
            detail["due_at"] = None
        else:
            dt = _parse_iso(str(due_at))
            if dt < _now_utc():
                raise BizError(ErrorCode.HOMEWORK_EXPIRED, "作业已截止，截止时间不能早于当前时间")
            iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            sets.append("due_at = ?")
            params.append(iso)
            detail["due_at"] = iso

    if status is not None:
        if status not in _HOMEWORK_STATUS:
            raise BizError(ErrorCode.PARAM_INVALID, f"状态只能是 {'/'.join(_HOMEWORK_STATUS)}")
        sets.append("status = ?")
        params.append(status)
        detail["status"] = status

    if not sets:
        # 没有可改字段：按契约仍算成功（幂等），直接返回当前作业
        return get_homework(homework_id)

    with write_tx() as conn:
        conn.execute(
            f"UPDATE homework SET {', '.join(sets)} WHERE id = ?",
            [*params, int(homework_id)],
        )
        write_audit(
            conn,
            user_id=int(operator_id),
            action="homework.update",
            target_type="homework",
            target_id=int(homework_id),
            detail=detail,
            ip=ip,
        )

    return get_homework(homework_id)


def delete_homework(*, homework_id: int, operator_id: int, ip: str | None = None) -> None:
    """删除作业（管理端 `DELETE /api/homework/{id}`）。

    `homework_submissions` 随 `ON DELETE CASCADE` 一并清理（契约 §2.3）。

    Raises:
        BizError: `70002` 作业不存在。
    """
    with read_conn() as conn:
        row = conn.execute(
            "SELECT id FROM homework WHERE id = ?", (int(homework_id),)
        ).fetchone()
    if row is None:
        raise BizError(ErrorCode.HOMEWORK_NOT_FOUND, f"作业不存在（id={homework_id}）")

    with write_tx() as conn:
        conn.execute("DELETE FROM homework WHERE id = ?", (int(homework_id),))
        write_audit(
            conn,
            user_id=int(operator_id),
            action="homework.delete",
            target_type="homework",
            target_id=int(homework_id),
            detail={"homework_id": int(homework_id)},
            ip=ip,
        )


def get_homework(homework_id: int) -> dict[str, Any]:
    """取单条作业（含派数字段）。不存在抛 `70002`。"""
    with read_conn() as conn:
        row = conn.execute(
            """
            SELECT h.id, h.class_id, h.created_by, h.title, h.subject,
                   h.unit_ids, h.due_at, h.status, h.created_at,
                   c.name AS class_name,
                   (SELECT COUNT(*) FROM homework_submissions s
                      WHERE s.homework_id = h.id AND s.status = 'done') AS submitted_count,
                   (SELECT COUNT(*) FROM users u
                      WHERE u.class_id = h.class_id
                        AND u.role = 'student' AND u.status = 'active') AS student_count
            FROM homework h
            LEFT JOIN classes c ON c.id = h.class_id
            WHERE h.id = ?
            """,
            (int(homework_id),),
        ).fetchone()
    if row is None:
        raise BizError(ErrorCode.HOMEWORK_NOT_FOUND, f"作业不存在（id={homework_id}）")
    d = dict(row)
    d["unit_ids"] = _load_unit_ids(d.get("unit_ids"))
    d["submitted_count"] = int(d.get("submitted_count") or 0)
    d["student_count"] = int(d.get("student_count") or 0)
    return d


# =========================================================================== 学生端


def student_homework(*, student_id: int, class_id: int | None) -> list[dict[str, Any]]:
    """学生端 `GET /api/me/homework`：只看**自己班级**且处于 `active` 的作业。

    归属过滤是这条查询的安全底线——只回 `class_id = ?` 的班级作业，
    跨班作业在 SQL 层面就被排除，学生端无从窥探他人班级作业。

    返回字段（契约 §15.2）：`{items:[{id,title,subject,due_at,status}]}`。
    `status` 为该生对此作业的提交状态（pending/done；未生成提交记录时按 pending 计）。

    学生未分班（`class_id is None`）→ 返回空列表，不报错。
    """
    if class_id is None:
        return []

    with read_conn() as conn:
        rows = conn.execute(
            """
            SELECT h.id, h.title, h.subject, h.due_at,
                   COALESCE(s.status, 'pending') AS status
            FROM homework h
            LEFT JOIN homework_submissions s
                   ON s.homework_id = h.id AND s.student_id = ?
            WHERE h.class_id = ? AND h.status = 'active'
            ORDER BY (CASE WHEN h.due_at IS NULL THEN 1 ELSE 0 END), h.due_at ASC, h.id DESC
            """,
            (int(student_id), int(class_id)),
        ).fetchall()

    return [dict(r) for r in rows]
