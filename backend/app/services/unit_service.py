"""单元服务 —— WP-A / BE-11，契约《01-共享契约冻结》§4.1 / §3.4（v1.3 两级树）。

裸 SQL（无 ORM），读走 `read_conn()`、写走 `write_tx()`（core/db.py）。

两级约束（契约 §3.4，v1.3）：`book_units.parent_id` 空 = 一级；二级只能挂一级下
（「parent 的 parent 必须为空」由本服务层校验，DB 不加 CHECK）。
`DELETE /api/units/{id}`：items 由 DB `ON DELETE CASCADE` 级联删；二级由本服务层
先删（005 的 parent_id 外键未带 ON DELETE，契约 §3.4 的「一级→二级→items 级联」
由「先删二级、级联删 items、再删一级」实现，单事务内完成）。
"""

from __future__ import annotations

from typing import Any

from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode

_COUNT_KEYS = ("word", "phrase", "sentence")


def _require_name(name: str | None) -> str:
    name = (name or "").strip()
    if not name:
        raise BizError(ErrorCode.PARAM_INVALID, "单元名称不能为空")
    return name


def _zero_counts() -> dict[str, int]:
    return {k: 0 for k in _COUNT_KEYS}


def list_tree() -> list[dict[str, Any]]:
    """`GET /api/units` —— 两级单元树（契约 §4.1 v1.3）。

    返回 [{id,name,sort,parent_id,counts:{word,phrase,sentence},
          children:[{...同构,children:[]}]}]；
    一级 counts = 自身 + 二级汇总；一级/二级均按 sort,id 升序。
    """
    with read_conn() as conn:
        units = conn.execute(
            "SELECT id, name, sort, parent_id FROM book_units ORDER BY sort, id"
        ).fetchall()
        counts = conn.execute(
            "SELECT unit_id, type, COUNT(*) AS n FROM items GROUP BY unit_id, type"
        ).fetchall()

    count_map: dict[int, dict[str, int]] = {}
    for row in counts:
        count_map.setdefault(int(row["unit_id"]), {})[row["type"]] = int(row["n"])

    nodes: dict[int, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for u in units:
        uid = int(u["id"])
        node = {
            "id": uid,
            "name": u["name"],
            "sort": int(u["sort"]),
            "parent_id": int(u["parent_id"]) if u["parent_id"] is not None else None,
            "counts": {
                **_zero_counts(),
                **{k: count_map.get(uid, {}).get(k, 0) for k in _COUNT_KEYS},
            },
            "children": [],
        }
        nodes[uid] = node

    for u in units:
        uid = int(u["id"])
        parent_id = int(u["parent_id"]) if u["parent_id"] is not None else None
        parent = nodes.get(parent_id) if parent_id is not None else None
        if parent is not None:
            parent["children"].append(nodes[uid])
        else:
            # parent_id 空（一级）；防御性兜底：孤儿行（父被删）也按一级展示
            roots.append(nodes[uid])

    # 一级 counts = 自身 + 二级汇总
    for root in roots:
        for child in root["children"]:
            for k in _COUNT_KEYS:
                root["counts"][k] += child["counts"][k]

    return roots


def _require_level1_parent(parent_id: int) -> dict[str, Any]:
    """parent_id 必须指向存在且为一级的单元（契约 §3.4 v1.3）。"""
    parent = get(parent_id)  # 不存在 → 30001
    if parent["parent_id"] is not None:
        raise BizError(ErrorCode.PARAM_INVALID, "单元最多两级，不能挂在二级单元下")
    return parent


def create(name: str, sort: int, parent_id: int | None = None) -> dict[str, Any]:
    """`POST /api/units` {name,sort,parent_id?} —— parent_id 空 = 一级。"""
    name = _require_name(name)
    if parent_id is not None:
        _require_level1_parent(int(parent_id))
    with write_tx() as conn:
        cur = conn.execute(
            "INSERT INTO book_units (name, sort, parent_id, created_at) VALUES (?, ?, ?, ?)",
            (name, int(sort), parent_id, utc_now_iso()),
        )
        unit_id = int(cur.lastrowid or 0)
    return {"id": unit_id, "name": name, "sort": int(sort), "parent_id": parent_id}


def get(unit_id: int) -> dict[str, Any]:
    with read_conn() as conn:
        row = conn.execute(
            "SELECT id, name, sort, parent_id FROM book_units WHERE id = ?", (unit_id,)
        ).fetchone()
    if row is None:
        raise BizError(ErrorCode.UNIT_NOT_FOUND, "单元不存在")
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "sort": int(row["sort"]),
        "parent_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
    }


def _has_children(unit_id: int, conn: Any) -> bool:
    row = conn.execute(
        "SELECT 1 FROM book_units WHERE parent_id = ? LIMIT 1", (unit_id,)
    ).fetchone()
    return row is not None


def update(
    unit_id: int,
    *,
    name: str | None = None,
    sort: int | None = None,
    parent_id: int | None = None,
    parent_id_set: bool = False,
) -> dict[str, Any]:
    """`PUT /api/units/{id}` {name?,sort?,parent_id?} —— 只改传入的字段。

    `parent_id_set=True` 时变更归属（契约 v1.3：parent_id 变更 = 移动归属，
    items 不动，只挪挂载点）：
    - `parent_id=None`（显式 null）= 升为一级；
    - `parent_id` 非空 = 挂到该一级下（目标必须是存在的一级单元）。
    """
    get(unit_id)  # 不存在 → 30001
    fields: list[str] = []
    values: list[Any] = []
    if name is not None:
        fields.append("name = ?")
        values.append(_require_name(name))
    if sort is not None:
        fields.append("sort = ?")
        values.append(int(sort))
    if parent_id_set:
        if parent_id is not None:
            parent_id = int(parent_id)
            if parent_id == unit_id:
                raise BizError(ErrorCode.PARAM_INVALID, "不能把单元挂到自己下面")
            _require_level1_parent(parent_id)
            # 带着二级挪到别人下面会变成三级，禁止
            with read_conn() as conn:
                if _has_children(unit_id, conn):
                    raise BizError(ErrorCode.PARAM_INVALID, "该单元下已有二级单元，不能再移动到其他单元下")
        fields.append("parent_id = ?")
        values.append(parent_id)
    if fields:
        with write_tx() as conn:
            conn.execute(
                f"UPDATE book_units SET {', '.join(fields)} WHERE id = ?",
                (*values, unit_id),
            )
    return get(unit_id)


def delete(unit_id: int) -> None:
    """`DELETE /api/units/{id}` —— 一级 → 二级 → items 级联删（契约 §3.4 v1.3）。

    005 的 parent_id 外键未带 ON DELETE，故同一事务内先删二级（items 随之
    被 FK CASCADE 删），再删一级；二级直删时 items 同样走 FK CASCADE。
    """
    get(unit_id)  # 不存在 → 30001
    with write_tx() as conn:
        conn.execute("DELETE FROM book_units WHERE parent_id = ?", (unit_id,))
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
