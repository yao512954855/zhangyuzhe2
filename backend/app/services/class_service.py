"""ClassService —— WP-BE-02 班级域（10a F4 / F5，契约 §14.1 第 1 条）。

**本模块是 `classes` 表的唯一写入方**（契约 §4.2：一张表只有一个包拥有写权限）。

设计要点：
- 班级列表默认**只返回 `status='active'`**（接口设计 §4.1：「`archived` 表示已归档
  班级，不再出现在默认列表」）；显式传 `status=archived` 或 `status=all` 才放开。
- `student_count` / `unassigned_count` 的口径：**在册学生数**（`role='student'`，
  不按 `status` 过滤）。理由：这两个数要跟「名单导入后看到的人数」对得上，
  若悄悄排除停用账号，管理者会发现数字对不上却查不出原因。
- 班级名唯一性由 `uk_classes_name`（**只对 `status='active'` 生效**的部分唯一索引）
  保证 —— 归档班级不占名字，所以「归档后重建同名班级」是合法的。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from app.core.audit import write_audit
from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)


class ClassService:
    """班级读写。全部静态/类方法，调用形如 `ClassService.list_classes(...)`。"""

    # ------------------------------------------------------------------ 查询

    @staticmethod
    def grade_semester(conn: Any, class_id: int | None) -> tuple[int | None, str | None]:
        """查班级的年级与学期，用于归班后回填学生偏好（10a「关键实现要点」）。

        Args:
            conn: 已打开的连接（读或写事务内均可）。
            class_id: 班级 ID；传 `None` 直接返回 `(None, None)`（未分班是合法状态）。

        Raises:
            BizError: `20004` —— 班级不存在（学生导入/归班时给出行级提示用）。
        """
        if class_id is None:
            return None, None
        row = conn.execute(
            "SELECT grade, semester FROM classes WHERE id = ?", (int(class_id),)
        ).fetchone()
        if row is None:
            raise BizError(ErrorCode.CLASS_NOT_FOUND, f"班级不存在（id={class_id}）")
        return row["grade"], row["semester"]

    @staticmethod
    def name_map(class_ids: Iterable[int]) -> dict[int, str]:
        """批量取班级名（**O(1) 查询**，禁止循环单查）。"""
        ids = sorted({int(i) for i in class_ids if i is not None})
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        with read_conn() as conn:
            rows = conn.execute(
                f"SELECT id, name FROM classes WHERE id IN ({placeholders})", ids
            ).fetchall()
        return {r["id"]: r["name"] for r in rows}

    @staticmethod
    def active_index() -> list[dict[str, Any]]:
        """全部活跃班级（导入时做「年级 + 学期 + 名称」匹配用）。"""
        with read_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, name, grade, semester, school_year
                FROM classes
                WHERE status = 'active'
                """
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def unassigned_count() -> int:
        """未分班学生数（`class_id IS NULL`）。"""
        with read_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'student' AND class_id IS NULL"
            ).fetchone()
        return int(row["n"])

    @staticmethod
    def list_classes(
        *,
        grade: int | None = None,
        semester: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """分页查班级，附带 `student_count`。

        Args:
            status: `None` → 只看活跃班级；`'archived'` → 只看已归档；
                    `'all'` → 不限状态。

        Returns:
            `(items, total)`。
        """
        where: list[str] = []
        params: list[Any] = []

        if status in (None, ""):
            where.append("status = 'active'")
        elif status != "all":
            where.append("status = ?")
            params.append(status)

        if grade is not None:
            where.append("grade = ?")
            params.append(int(grade))
        if semester:
            where.append("semester = ?")
            params.append(semester)
        if keyword:
            where.append("(name LIKE ? OR IFNULL(remark, '') LIKE ?)")
            like = f"%{keyword.strip()}%"
            params.extend([like, like])

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        with read_conn() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM classes {where_sql}", params
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"""
                SELECT
                    c.id, c.name, c.grade, c.semester, c.school_year, c.remark,
                    c.status, c.created_at, c.updated_at,
                    (SELECT COUNT(*) FROM users u
                      WHERE u.class_id = c.id AND u.role = 'student') AS student_count
                FROM classes c
                {where_sql}
                ORDER BY c.grade ASC, c.semester ASC, c.id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()

        return [dict(r) for r in rows], total

    @staticmethod
    def get_class(class_id: int) -> dict[str, Any]:
        """取单个班级（含 `student_count`），不存在抛 `20004`。"""
        with read_conn() as conn:
            row = conn.execute(
                """
                SELECT
                    c.id, c.name, c.grade, c.semester, c.school_year, c.remark,
                    c.status, c.created_at, c.updated_at,
                    (SELECT COUNT(*) FROM users u
                      WHERE u.class_id = c.id AND u.role = 'student') AS student_count
                FROM classes c
                WHERE c.id = ?
                """,
                (int(class_id),),
            ).fetchone()
        if row is None:
            raise BizError(ErrorCode.CLASS_NOT_FOUND, f"班级不存在（id={class_id}）")
        return dict(row)

    # ------------------------------------------------------------------ 写入

    @staticmethod
    def create(
        *,
        name: str,
        grade: int,
        semester: str,
        school_year: str | None = None,
        remark: str | None = None,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """创建班级。同名（活跃班级内）返回 `10001` + 明确提示（10a F5）。"""
        now = utc_now_iso()
        try:
            with write_tx() as conn:
                dup = conn.execute(
                    "SELECT id FROM classes WHERE name = ? AND status = 'active'", (name,)
                ).fetchone()
                if dup is not None:
                    raise BizError(
                        ErrorCode.PARAM_INVALID,
                        f"已存在同名班级「{name}」，请换一个名称（同名班级 {dup['id']}）",
                    )

                cur = conn.execute(
                    """
                    INSERT INTO classes (name, grade, semester, school_year, remark,
                                         status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (name, int(grade), semester, school_year, remark, now),
                )
                new_id = cur.lastrowid
                write_audit(
                    conn,
                    user_id=operator_id,
                    action="class.create",
                    target_type="class",
                    target_id=new_id,
                    detail={"name": name, "grade": grade, "semester": semester},
                    ip=ip,
                )
        except sqlite3.IntegrityError as exc:
            # 并发下的兜底：部分唯一索引 uk_classes_name 拦截
            raise BizError(ErrorCode.PARAM_INVALID, f"已存在同名班级「{name}」") from exc

        logger.info("创建班级 id=%s name=%s", new_id, name)
        return ClassService.get_class(int(new_id))

    @staticmethod
    def update(
        *,
        class_id: int,
        name: str | None = None,
        remark: str | None = None,
        status: str | None = None,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """改名称 / 备注 / 归档（契约 §14.1 第 1 条）。"""
        sets: list[str] = []
        params: list[Any] = []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if remark is not None:
            sets.append("remark = ?")
            params.append(remark)
        if status is not None:
            sets.append("status = ?")
            params.append(status)

        if not sets:
            raise BizError(ErrorCode.PARAM_INVALID, "没有需要修改的内容")

        now = utc_now_iso()
        try:
            with write_tx() as conn:
                row = conn.execute(
                    "SELECT id, name FROM classes WHERE id = ?", (int(class_id),)
                ).fetchone()
                if row is None:
                    raise BizError(ErrorCode.CLASS_NOT_FOUND, f"班级不存在（id={class_id}）")

                if name is not None:
                    dup = conn.execute(
                        """
                        SELECT id FROM classes
                        WHERE name = ? AND status = 'active' AND id <> ?
                        """,
                        (name, int(class_id)),
                    ).fetchone()
                    if dup is not None:
                        raise BizError(
                            ErrorCode.PARAM_INVALID,
                            f"已存在同名班级「{name}」，请换一个名称",
                        )

                sets.append("updated_at = ?")
                params.append(now)
                params.append(int(class_id))
                conn.execute(f"UPDATE classes SET {', '.join(sets)} WHERE id = ?", params)

                write_audit(
                    conn,
                    user_id=operator_id,
                    action="class.update",
                    target_type="class",
                    target_id=int(class_id),
                    detail={
                        k: v
                        for k, v in (("name", name), ("remark", remark), ("status", status))
                        if v is not None
                    },
                    ip=ip,
                )
        except sqlite3.IntegrityError as exc:
            raise BizError(ErrorCode.PARAM_INVALID, f"已存在同名班级「{name}」") from exc

        return ClassService.get_class(int(class_id))
