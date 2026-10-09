"""FastAPI 依赖注入 —— 简化版（WP-F 拆鉴权后）。

简化版无登录态：所有「当前用户」相关依赖一律返回本地占位用户，
不做任何 JWT 校验（契约《01-共享契约冻结》§4：无鉴权）。

保留函数名（get_current_user / require_admin / resolve_user_from_token）是为了
让 discovery.py、asr_ws.py 等保留模块继续导入可用；语义已从「鉴权」变为「占位」。
"""

from __future__ import annotations

from typing import Any, Iterator

from fastapi import Depends
from pydantic import BaseModel

from app.core.db import read_conn


class CurrentUser(BaseModel):
    """当前用户的最小字段（简化版恒为本地占位用户）。"""

    id: int = 0
    role: str = "admin"
    username: str = "local"
    real_name: str = "本地用户"
    class_id: int | None = None
    status: str = "active"

    @property
    def is_admin(self) -> bool:
        return True


def get_db() -> Iterator[Any]:
    """只读数据库连接依赖。"""
    with read_conn() as conn:
        yield conn


def get_current_user() -> CurrentUser:
    """简化版：直接返回本地占位用户，不做任何校验。"""
    return CurrentUser()


def resolve_user_from_token(token: str | None) -> CurrentUser | None:
    """简化版：忽略 token，恒返回本地占位用户（WebSocket 握手用）。"""
    return CurrentUser()


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """简化版：占位用户恒为 admin，直接放行。"""
    return user
