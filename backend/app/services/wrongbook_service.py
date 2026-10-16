"""错题本服务 —— WP-A，契约《01-共享契约冻结》§3.4 / §4.1（v1.3，todo11 A7）。

UPSERT 红线（契约 §3.4）：同 `(item_id, mode)` 只允许一条
`resolved_at IS NULL` 的未移出记录 —— 由本服务在写事务里保证
（DB 不加唯一索引，SQLite 部分索引仅 WAL 生效，为可移植性放服务层）。

**三态模型（契约 §3.4 v1.3）**，`wrong_book` 一行（未移出）的状态由两列表达：
- 未掌握：`mastered_at` / `resolved_at` 皆 NULL
- 已掌握：`mastered_at` 非空且 `resolved_at` 空 —— 判对时服务端自动写
  （`practice/answer correct=true`），**保留显示，不再自动移出**（废除 v1.1 #4）
- 已移出：`resolved_at` 非空 —— 仅手动删除（DELETE / batch-resolve）可到达
"""

from __future__ import annotations

from typing import Any

from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode
from app.schemas.simplify import PRACTICE_MODES


def _check_mode(mode: str) -> None:
    if mode not in PRACTICE_MODES:
        raise BizError(ErrorCode.PARAM_INVALID, "mode 必须是 listen / spell_zh2en / spell_en2zh")


def auto_add(*, item_id: int, mode: str) -> dict[str, Any]:
    """`POST /api/wrongbook/auto-add` {item_id,mode} —— UPSERT 未移出记录（C/D 判错时调用）。

    Returns:
        `{item_id, mode, wrong_count}`（方便调用方提示「已错 N 次」）。
    """
    _check_mode(mode)

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
            # UPSERT 已存在：只累加次数与时间，仍保持一条未移出记录
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


def mark_mastered(*, item_id: int, mode: str) -> dict[str, Any]:
    """判对留痕（F12-2）：UPSERT 未移出记录并写 `mastered_at`。

    - 无未移出记录 → 新建一行（wrong_count=1，mastered_at=now，resolved_at 空）；
    - 有未移出记录且未掌握 → 写 mastered_at；
    - **幂等：已有 mastered_at 不覆盖**（契约 §3.4 / 验收 U12-3）。

    由 `practice_service.answer(correct=true)` 调用（item 必存在于组卷，不重复校验）。
    """
    _check_mode(mode)

    with write_tx() as conn:
        now = utc_now_iso()
        open_row = conn.execute(
            """
            SELECT id, mastered_at FROM wrong_book
             WHERE item_id = ? AND mode = ? AND resolved_at IS NULL
            """,
            (item_id, mode),
        ).fetchone()

        if open_row is None:
            conn.execute(
                """
                INSERT INTO wrong_book (item_id, mode, wrong_count, last_wrong_at, mastered_at)
                VALUES (?, ?, 1, ?, ?)
                """,
                (item_id, mode, now, now),
            )
        elif open_row["mastered_at"] is None:
            conn.execute(
                "UPDATE wrong_book SET mastered_at = ? WHERE id = ?",
                (now, int(open_row["id"])),
            )
        # 已掌握（mastered_at 非空）→ 幂等，不覆盖时间戳

    return {"item_id": item_id, "mode": mode}


# status 三态（契约 §4.1 v1.3）：SQL 过滤片段（均已限定未移出）
_STATUS_FILTERS = {
    "unmastered": "wb.resolved_at IS NULL AND wb.mastered_at IS NULL",
    "mastered": "wb.resolved_at IS NULL AND wb.mastered_at IS NOT NULL",
    "all": "wb.resolved_at IS NULL",  # 已移出（resolved_at 非空）不属于错题本列表
}


def list_open(
    *,
    group_by: str,
    mode: str | None = None,
    status: str = "unmastered",
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """`GET /api/wrongbook?mode&status&group_by` —— 列未移出记录（F12-4）。

    - `mode`：listen | spell_zh2en | spell_en2zh 三类 tab 过滤，缺省不过滤；
    - `status`：unmastered（默认）| mastered | all 三态过滤（契约 §4.1 v1.3）；
    - `group_by` 决定排序（响应是平铺列表，分组语义 = 排序）：
      - `unit`：按单元（unit.sort）再按条目 sort 排
      - `type`：按 type 再按单元 sort 排
    响应每行含 `mastered_at`（未掌握为 null）。
    """
    if group_by not in ("unit", "type"):
        raise BizError(ErrorCode.PARAM_INVALID, "group_by 必须是 unit 或 type")
    if mode is not None and mode != "":
        _check_mode(mode)
        mode = str(mode)
    else:
        mode = None
    if status not in _STATUS_FILTERS:
        raise BizError(ErrorCode.PARAM_INVALID, "status 必须是 unmastered / mastered / all")

    order = (
        "ORDER BY u.sort, u.id, i.sort, i.id"
        if group_by == "unit"
        else "ORDER BY i.type, u.sort, u.id, i.sort, i.id"
    )
    where = _STATUS_FILTERS[status]
    params: list[Any] = []
    if mode is not None:
        where += " AND wb.mode = ?"
        params.append(mode)

    with read_conn() as conn:
        total = int(
            conn.execute(
                f"""
                SELECT COUNT(*) AS n
                  FROM wrong_book wb
                 WHERE {where}
                """,
                tuple(params),
            ).fetchone()["n"]
        )
        rows = conn.execute(
            f"""
            SELECT wb.item_id, i.en, i.pos, i.zh, wb.mode, wb.wrong_count,
                   wb.last_wrong_at, wb.mastered_at, i.unit_id,
                   u.name AS unit_name, i.type
              FROM wrong_book wb
              JOIN items i ON i.id = wb.item_id
              JOIN book_units u ON u.id = i.unit_id
             WHERE {where}
             {order}
             LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows], total


def resolve(*, item_id: int, mode: str) -> dict[str, Any]:
    """`DELETE /api/wrongbook/{item_id}/{mode}` —— 手动移出（= 置 resolved_at）。

    幂等：没有未移出记录时也返回成功（移出语义，不是资源删除）。
    """
    _check_mode(mode)

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


def batch_resolve(*, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """`POST /api/wrongbook/batch-resolve`（F12-5）—— 批量移出（逐条置 resolved_at）。

    - `{entries:[{item_id,mode},...]}`，不存在/已移出的条目**跳过不报错**；
    - 返回 `{resolved:n}`，n 只计成功置 resolved_at 的条数（验收 U12-6）。
    """
    if not isinstance(entries, list) or not entries:
        raise BizError(ErrorCode.PARAM_INVALID, "entries 必须是非空数组")

    parsed: list[tuple[int, str]] = []
    for e in entries:
        if not isinstance(e, dict) or "item_id" not in e or "mode" not in e:
            raise BizError(ErrorCode.PARAM_INVALID, "entries 每项必须含 item_id 与 mode")
        _check_mode(e["mode"])
        parsed.append((int(e["item_id"]), str(e["mode"])))

    now = utc_now_iso()
    with write_tx() as conn:
        resolved = 0
        for item_id, mode in parsed:
            cur = conn.execute(
                """
                UPDATE wrong_book
                   SET resolved_at = ?
                 WHERE item_id = ? AND mode = ? AND resolved_at IS NULL
                """,
                (now, item_id, mode),
            )
            resolved += cur.rowcount
    return {"resolved": resolved}
