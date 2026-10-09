"""单元服务 —— WP-A，契约《01-共享契约冻结》§4.1 单元与内容。

裸 SQL（无 ORM），读走 `read_conn()`、写走 `write_tx()`（core/db.py）。
`DELETE /api/units/{id}` 的级联删 items 由 DB `ON DELETE CASCADE` 保证（契约 DDL）。
"""

from __future__ import annotations

from typing import Any

from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode


def _require_name(name: str | None) -> str:
    name = (name or "").strip()
    if not name:
        raise BizError(ErrorCode.PARAM_INVALID, "单元名称不能为空")
    return name


def list_tree() -> list[dict[str, Any]]:
    """`GET /api/units` —— 全量单元树 [{id,name,sort,counts:{word,phrase,sentence}}]。"""
    with read_conn() as conn:
        units = conn.execute(
            "SELECT id, name, sort FROM book_units ORDER BY sort, id"
        ).fetchall()
        counts = conn.execute(
            "SELECT unit_id, type, COUNT(*) AS n FROM items GROUP BY unit_id, type"
        ).fetchall()

    count_map: dict[int, dict[str, int]] = {}
    for row in counts:
        count_map.setdefault(int(row["unit_id"]), {})[row["type"]] = int(row["n"])

    return [
        {
            "id": int(u["id"]),
            "name": u["name"],
            "sort": int(u["sort"]),
            "counts": {
                "word": count_map.get(int(u["id"]), {}).get("word", 0),
                "phrase": count_map.get(int(u["id"]), {}).get("phrase", 0),
                "sentence": count_map.get(int(u["id"]), {}).get("sentence", 0),
            },
        }
        for u in units
    ]


def create(name: str, sort: int) -> dict[str, Any]:
    """`POST /api/units` {name,sort}。"""
    name = _require_name(name)
    with write_tx() as conn:
        cur = conn.execute(
            "INSERT INTO book_units (name, sort, created_at) VALUES (?, ?, ?)",
            (name, int(sort), utc_now_iso()),
        )
        unit_id = int(cur.lastrowid or 0)
    return {"id": unit_id, "name": name, "sort": int(sort)}


def get(unit_id: int) -> dict[str, Any]:
    with read_conn() as conn:
        row = conn.execute(
            "SELECT id, name, sort FROM book_units WHERE id = ?", (unit_id,)
        ).fetchone()
    if row is None:
        raise BizError(ErrorCode.UNIT_NOT_FOUND, "单元不存在")
    return dict(row)


def update(unit_id: int, *, name: str | None = None, sort: int | None = None) -> dict[str, Any]:
    """`PUT /api/units/{id}` {name?,sort?} —— 只改传入的字段。"""
    get(unit_id)  # 不存在 → 30001
    fields: list[str] = []
    values: list[Any] = []
    if name is not None:
        fields.append("name = ?")
        values.append(_require_name(name))
    if sort is not None:
        fields.append("sort = ?")
        values.append(int(sort))
    if fields:
        with write_tx() as conn:
            conn.execute(
                f"UPDATE book_units SET {', '.join(fields)} WHERE id = ?",
                (*values, unit_id),
            )
    return get(unit_id)


def delete(unit_id: int) -> None:
    """`DELETE /api/units/{id}` —— items 由 ON DELETE CASCADE 级联删。"""
    get(unit_id)  # 不存在 → 30001
    with write_tx() as conn:
        conn.execute("DELETE FROM book_units WHERE id = ?", (unit_id,))


def counts_by_ids(unit_ids: list[int]) -> dict[int, str]:
    """批量取单元名（错题本列表等只读场景用）。"""
    if not unit_ids:
        return {}
    placeholders = ",".join("?" * len(unit_ids))
    with read_conn() as conn:
        rows = conn.execute(
            f"SELECT id, name FROM book_units WHERE id IN ({placeholders})",
            unit_ids,
        ).fetchall()
    return {int(r["id"]): r["name"] for r in rows}
