"""认证接口 —— WP-BE-02 F1 ~ F3（接口设计 §2）。

| 接口 | 说明 |
|---|---|
| `POST /api/auth/login` | 学号或用户名登录；失败统一 `20002`（防枚举）；停用 `20003` |
| `POST /api/auth/register` | 学生自主注册，注册即可用（无需审核） |
| `GET  /api/auth/me` | 当前登录用户（App 冷启动校验 token 用） |

所有响应走 `ok()`（契约 §3），业务失败一律 **HTTP 200 + 非零 code**（契约 §5.1）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.core.security import create_access_token
from app.schemas.auth import LoginIn, LoginOut, RegisterIn, UserOut
from app.services.user_service import UserService

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/login")
async def login(payload: LoginIn, request: Request) -> dict:
    """登录并签发 JWT（契约 §5.2：`access_token` / `token_type` / `expires_in` / `user`）。"""
    user = UserService.authenticate(
        username=payload.username,
        password=payload.password,
        role=payload.role,
        ip=_client_ip(request),
    )
    token, expires_in = create_access_token(user_id=user["id"], role=user["role"])
    body = LoginOut(
        access_token=token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )
    return ok(body.model_dump())


@router.post("/register")
async def register(payload: RegisterIn, request: Request) -> dict:
    """学生自主注册 —— 注册成功**直接签发 token**（接口设计 §2.2）。"""
    user = UserService.register_student(
        username=payload.username,
        real_name=payload.real_name,
        password=payload.password,
        class_id=payload.class_id,
        ip=_client_ip(request),
    )
    token, expires_in = create_access_token(user_id=user["id"], role=user["role"])
    body = LoginOut(
        access_token=token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )
    return ok(body.model_dump())


@router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)) -> dict:
    """当前用户信息（每次**查库**，停用账号的旧 token 会立即失效）。"""
    data = UserService.get_user(user.id)
    return ok(UserOut.model_validate(data).model_dump())
