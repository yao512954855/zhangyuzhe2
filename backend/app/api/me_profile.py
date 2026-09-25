"""个人中心接口 —— WP-BE-02 F13 / F14（接口设计 §8.1 / §8.2）。

| 接口 | 说明 |
|---|---|
| `GET /api/me/profile` | 个人信息 + 年级学期偏好 + 护眼设置 |
| `PUT /api/me/profile` | 更新年级学期偏好（**双向持久化**：本地为主 + 服务端同步） |

`eye_rest_enabled` / `eye_warm_mode_auto` 是**全站配置**（`settings` 表），
不是每人偏好，所以只读、且只经由 `SettingsService` 读出（契约禁止清单第 3 条）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.schemas.auth import ProfileUpdateIn, UserOut
from app.services.user_service import UserService

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("/profile")
async def get_profile(user: CurrentUser = Depends(get_current_user)) -> dict:
    """我的信息。学生登录后若 `preferred_grade` 为空，前端进年级学期选择引导。"""
    data = UserService.get_profile(user.id)
    return ok(UserOut.model_validate(data).model_dump())


@router.put("/profile")
async def update_profile(
    payload: ProfileUpdateIn,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """更新年级学期偏好。只更新请求里出现的字段。"""
    data = UserService.update_profile(
        user_id=user.id,
        preferred_grade=payload.preferred_grade,
        preferred_semester=payload.preferred_semester,
    )
    return ok(UserOut.model_validate(data).model_dump())
