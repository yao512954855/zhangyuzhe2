"""FastAPI 依赖注入 —— 契约《01-共享契约冻结》§5.2 / 10a F10。

提供三个依赖：
- `get_db`：只读连接（写操作请在业务里用 `db.write_tx()`）
- `get_current_user`：当前登录用户
- `require_admin`：要求管理者角色

⚠️ 关键实现点（10a）：`get_current_user` **必须查库确认 `status='active'`**，
不能只信 JWT —— 否则停用账号手里的旧 token 还能继续用（验收用例 U13）。
"""

from __future__ import annotations

from typing import Any, Iterator

from fastapi import Depends, Header
from pydantic import BaseModel

from app.core.db import read_conn
from app.core.errors import BizError, ErrorCode
from app.core.security import decode_token


class CurrentUser(BaseModel):
    """当前登录用户的最小必要字段。"""

    id: int
    role: str
    username: str
    real_name: str
    class_id: int | None = None
    status: str = "active"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_db() -> Iterator[Any]:
    """只读数据库连接依赖。"""
    with read_conn() as conn:
        yield conn


def _extract_bearer(authorization: str | None) -> str:
    """从 `Authorization: Bearer xxx` 中取出 token。

    注意：这里**不做** HTTP 401 —— 契约 §5.1 规定业务失败一律 HTTP 200 + 非零 code，
    验收用例 U4 明确要求「不带 token 访问受保护路由 → code=10002, HTTP 200」。
    """
    if not authorization:
        raise BizError(ErrorCode.UNAUTHORIZED)
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise BizError(ErrorCode.UNAUTHORIZED)
    return parts[1].strip()


def get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    """解析 JWT 并**查库**返回当前用户。"""
    token = _extract_bearer(authorization)
    payload = decode_token(token)

    raw_sub = payload.get("sub")
    try:
        user_id = int(raw_sub)
    except (TypeError, ValueError) as exc:
        raise BizError(ErrorCode.UNAUTHORIZED) from exc

    with read_conn() as conn:
        row = conn.execute(
            """
            SELECT id, role, username, real_name, class_id, status
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    if row is None:
        raise BizError(ErrorCode.UNAUTHORIZED)

    # 停用账号的旧 token 立即失效
    if row["status"] != "active":
        raise BizError(ErrorCode.UNAUTHORIZED)

    return CurrentUser(
        id=row["id"],
        role=row["role"],
        username=row["username"],
        real_name=row["real_name"],
        class_id=row["class_id"],
        status=row["status"],
    )


def resolve_user_from_token(token: str | None) -> "CurrentUser | None":
    """从裸 token 解析用户，**失败返回 None 而不抛异常**（供 WebSocket 握手用）。

    为什么单独一个函数：WebSocket 握手拿不到 `Authorization` 头（契约 §6.1 用
    `?token=` query），且**必须自己决定关闭码 `4001`**，不能走 HTTP 的 `BizError` 流程。
    逻辑与 `get_current_user` 保持一致（含「停用账号旧 token 立即失效」）。
    """
    if not token or not str(token).strip():
        return None
    try:
        payload = decode_token(str(token).strip())
        user_id = int(payload.get("sub"))
    except Exception:  # noqa: BLE001 - token 过期/非法/缺 sub 一律视为未认证
        return None

    with read_conn() as conn:
        row = conn.execute(
            "SELECT id, role, username, real_name, class_id, status FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if row is None or row["status"] != "active":
        return None
    return CurrentUser(
        id=row["id"],
        role=row["role"],
        username=row["username"],
        real_name=row["real_name"],
        class_id=row["class_id"],
        status=row["status"],
    )


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """要求管理者角色，非 admin 抛 `10003`。"""
    if not user.is_admin:
        raise BizError(ErrorCode.FORBIDDEN)
    return user
