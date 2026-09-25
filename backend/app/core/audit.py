"""审计日志写入 —— 契约《01-共享契约冻结》§7.5 / 10a F17。

签名已冻结，各包按此调用：
    write_audit(conn, *, user_id, action, target_type=None, target_id=None,
                detail=None, ip=None) -> None

⚠️ 必须在调用方**已有的写事务**内调用（`conn` 由调用方传入且已在 `write_tx()` 中），
本函数不自己开事务，也不提交。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def write_audit(
    conn: Any,
    *,
    user_id: int | None,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """写一条 `audit_logs`。

    Args:
        conn: 已在写事务中的连接。
        user_id: 操作人；系统操作可传 None。
        action: 动作标识，如 `student.import` / `student.reset_password`。
        target_type / target_id: 操作对象，如 `student` / 42。
        detail: 附加信息，序列化为 JSON 存储。
        ip: 客户端 IP。
    """
    detail_json = json.dumps(detail, ensure_ascii=False) if detail is not None else None

    conn.execute(
        """
        INSERT INTO audit_logs (user_id, action, target_type, target_id, detail, ip)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, action, target_type, target_id, detail_json, ip),
    )

    logger.info(
        "audit user=%s action=%s target=%s/%s",
        user_id,
        action,
        target_type,
        target_id,
    )
