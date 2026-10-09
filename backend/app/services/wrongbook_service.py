"""错题本服务 —— WP-A，契约《01-共享契约冻结》§3.4 / §4.1。

UPSERT 红线（契约 §3.4）：同 `(item_id, mode)` 只允许一条
`resolved_at IS NULL` 的未解决记录 —— 由本服务在写事务里保证
（DB 不加唯一索引，SQLite 部分索引仅 WAL 生效，为可移植性放服务层）。

`resolved_at` 非空 = 已移出错题本（契约 §7：DB 无布尔列，API 不暴露该字段，
列表一律按 `resolved_at IS NULL` 过滤未解决）。
"""

from __future__ import annotations

from typing import Any

from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode
from app.schemas.simplify import PRACTICE_MODES


def auto_add(*, item_id: int, mode: str) -> dict[str, Any]:
    """`POST /api/wrongbook/auto-add` {item_id,mode} —— UPSERT 未解决记录（C/D 判错时调用）。

    Returns:
        `{item_id, mode, wrong_count}`（方便调用方提示「已错 N 次」）。
    """
    if mode not in PRACTICE_MODES:
        raise BizError(ErrorCode.PARAM_INVALID, "mode 必须是 listen / spell_zh2en / spell_en2zh")

    with write_tx() as conn:
        item = conn.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            raise BizError(ErrorCode.CONTENT_NOT_FOUND, "内容不存在")

        now = utc_now_iso()
        open_row = conn.execute(
            """
            SELECT id, wrong_count FROM wrong_book
             WHERE item_id = ? AND mode = ? AND resolved_at IS NULL
            """,
            (item_id, mode),
        ).fetchone()

        if open_row is not None:
            # UPSERT 已存在：只累加次数与时间，仍保持一条未解决记录
            conn.execute(
                """
                UPDATE wrong_book
                   SET wrong_count = wrong_count + 1, last_wrong_at = ?
                 WHERE id = ?
                """,
                (now, int(open_row["id"])),
            )
            wrong_count = int(open_row["wrong_count"]) + 1
        else:
            conn.execute(
                """
                INSERT INTO wrong_book (item_id, mode, wrong_count, last_wrong_at)
                VALUES (?, ?, 1, ?)
                """,
                (item_id, mode, now),
            )
            wrong_count = 1

    return {"item_id": item_id, "mode": mode, "wrong_count": wrong_count}


def list_open(
    *, group_by: str, limit: int = 20, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """`GET /api/wrongbook?group_by=unit|type` —— 只列未解决记录。

    `group_by` 决定排序（响应是平铺列表，分组语义 = 排序）：
    - `unit`：按单元（unit.sort）再按条目 sort 排
    - `type`：按 type 再按单元 sort 排
    """
    if group_by not in ("unit", "type"):
        raise BizError(ErrorCode.PARAM_INVALID, "group_by 必须是 unit 或 type")

    order = (
        "ORDER BY u.sort, u.id, i.sort, i.id"
        if group_by == "unit"
        else "ORDER BY i.type, u.sort, u.id, i.sort, i.id"
    )
    where = "wb.resolved_at IS NULL"

    with read_conn() as conn:
        total = int(
            conn.execute(
                f"""
                SELECT COUNT(*) AS n
                  FROM wrong_book wb
                 WHERE {where}
                """
            ).fetchone()["n"]
        )
        rows = conn.execute(
            f"""
            SELECT wb.item_id, i.en, i.pos, i.zh, wb.mode, wb.wrong_count,
                   wb.last_wrong_at, i.unit_id, u.name AS unit_name, i.type
              FROM wrong_book wb
              JOIN items i ON i.id = wb.item_id
              JOIN book_units u ON u.id = i.unit_id
             WHERE {where}
             {order}
             LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows], total


def resolve(*, item_id: int, mode: str) -> dict[str, Any]:
    """`DELETE /api/wrongbook/{item_id}/{mode}` —— 手动移出（= 置 resolved_at）。

    幂等：没有未解决记录时也返回成功（移出语义，不是资源删除）。
    """
    if mode not in PRACTICE_MODES:
        raise BizError(ErrorCode.PARAM_INVALID, "mode 必须是 listen / spell_zh2en / spell_en2zh")

    with write_tx() as conn:
        conn.execute(
            """
            UPDATE wrong_book
               SET resolved_at = ?
             WHERE item_id = ? AND mode = ? AND resolved_at IS NULL
            """,
            (utc_now_iso(), item_id, mode),
        )
    return {"item_id": item_id, "mode": mode}
