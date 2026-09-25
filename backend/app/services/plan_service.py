"""WP-2H 练习方案服务层 —— `practice_plans` 表的唯一写入方（契约 §15.1）。

职责边界（与 `api/me_plans.py` 分层）：
- 本文件**只做 SQL 与行↔字典转换**，不碰 HTTP、不抛 `BizError`。
- 鉴权 / 归属 / 参数校验在 router 层做；归属即「`student_id` 过滤」。
- 所有写操作走 `write_tx()`，读走 `read_conn()`（契约 §4.3）。

**payload 是 JSON 快照**：用 `json.dumps(ensure_ascii=False)` 入库、
`json.loads` 出库，**原样往返**——服务端不解析它的内部结构，
多语言客户端传什么维度就存什么维度（语文如实不发 `mode`、英语 `scope=lesson`
静默退化等，都由客户端负责，服务端只存不判）。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.db import read_conn, write_tx

# 写入方唯一性：本包独占 `practice_plans`（契约 §15.6 / 50-文档 §2.1）。
_VALID_SUBJECTS = ("chinese", "english")


def _row_to_plan(row: Any) -> dict[str, Any]:
    """`sqlite3.Row` → 对外字典，payload 反序列化为 JSON 对象。"""
    payload_raw = row["payload"]
    try:
        payload = json.loads(payload_raw) if payload_raw is not None else {}
    except (json.JSONDecodeError, TypeError):
        # 理论上不该发生（入库前必是合法 JSON），兜底成空对象避免整个列表炸。
        payload = {}

    updated = row["updated_at"]
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "subject": str(row["subject"]),
        "payload": payload,
        "created_at": str(row["created_at"]) if row["created_at"] is not None else None,
        "updated_at": str(updated) if updated is not None else None,
    }


def list_plans(student_id: int) -> list[dict[str, Any]]:
    """列出某学生的全部练习方案，**只回自己的**（按 `student_id` 过滤）。

    排序：最近更新在前、同级按 id 倒序（新存的先看得到）。
    """
    with read_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, subject, payload, created_at, updated_at
            FROM practice_plans
            WHERE student_id = ?
            ORDER BY updated_at IS NULL, updated_at DESC, id DESC
            """,
            (student_id,),
        ).fetchall()
    return [_row_to_plan(r) for r in rows]


def create_plan(
    *, student_id: int, name: str, subject: str, payload: Any
) -> int:
    """插入一条方案，返回新 id。

    `subject` 必须是合法值（表上有 CHECK，非法会触发 IntegrityError，
    由 router 层先校验，这里不再重复判断）。
    """
    payload_text = json.dumps(payload, ensure_ascii=False)
    with write_tx() as conn:
        cursor = conn.execute(
            """
            INSERT INTO practice_plans
                (student_id, name, subject, payload, created_at)
            VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (student_id, name, subject, payload_text),
        )
        return int(cursor.lastrowid)


def get_own_plan(*, student_id: int, plan_id: int) -> dict[str, Any] | None:
    """取一条方案；**不存在或不属于该学生都返回 None**（越权与不存在同口径）。

    由 router 层据此统一抛 `70001`，不暴露资源存在性（契约 §15.2 / §15.3）。
    """
    with read_conn() as conn:
        row = conn.execute(
            """
            SELECT id, name, subject, payload, created_at, updated_at
            FROM practice_plans
            WHERE id = ? AND student_id = ?
            """,
            (plan_id, student_id),
        ).fetchone()
    return _row_to_plan(row) if row is not None else None


def update_plan(
    *,
    student_id: int,
    plan_id: int,
    name: str | None = None,
    subject: str | None = None,
    payload: Any | None = None,
) -> bool:
    """更新一条**属于自己**的方案。

    `WHERE id = ? AND student_id = ?` 同时完成归属校验：受影响行数 0
    表示「不存在或不是自己的」——router 据此判 `70001`。

    Returns:
        是否有行被更新。
    """
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if subject is not None:
        sets.append("subject = ?")
        params.append(subject)
    if payload is not None:
        sets.append("payload = ?")
        params.append(json.dumps(payload, ensure_ascii=False))
    # 任何写操作都刷新 updated_at，方便列表排序「最近改的在前」。
    sets.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")

    params.append(plan_id)
    params.append(student_id)

    with write_tx() as conn:
        cursor = conn.execute(
            "UPDATE practice_plans SET "
            + ", ".join(sets)
            + " WHERE id = ? AND student_id = ?",
            params,
        )
        return cursor.rowcount > 0


def delete_plan(*, student_id: int, plan_id: int) -> bool:
    """删除一条**属于自己**的方案（`WHERE` 带 `student_id` 做归属校验）。

    Returns:
        是否有行被删除。
    """
    with write_tx() as conn:
        cursor = conn.execute(
            "DELETE FROM practice_plans WHERE id = ? AND student_id = ?",
            (plan_id, student_id),
        )
        return cursor.rowcount > 0
