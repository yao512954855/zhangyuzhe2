"""账号与鉴权 Schema —— WP-BE-02（10a F1 ~ F3、F13 ~ F14）。

约定（契约 §2 / §4.6）：
- JSON 字段名与数据库字段名保持一致，**不做驼峰转换**。
- API 层布尔一律 `true` / `false`，DB 侧 `0` / `1` 的转换在这层完成。
- 参数校验失败由 BE-01 的全局 handler 统一转成 `10001`（**message 带字段名**），
  本文件只负责声明「合法取值范围」。

密码策略（10a「关键实现要点」）：
- 注册用户名 **4–20 位字母数字**（全校唯一，靠 `uk_users_username` + `COLLATE NOCASE`）
- `real_name` **1–20 字**
- 密码 **6–20 位**，**允许纯数字弱口令**（小学生场景，需求已明确）
- 登录接口**不设** 6 位下限 —— 存量账号可能用过更短的旧密码，
  「密码对不对」是 `UserService.authenticate` 的职责，不该被表单校验提前拦掉。

本文件同时定义账号域共用的枚举 Literal（`Role` / `Semester`），
`schemas/class.py`、`schemas/student.py` 直接引用，避免枚举出现两份定义。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.excel import to_halfwidth

# ---------------------------------------------------------------------------
# 共用枚举（取值来自契约 §8，**不得新增别名**）
# ---------------------------------------------------------------------------

Role = Literal["admin", "student"]
Semester = Literal["first", "second"]
UserStatus = Literal["active", "disabled"]
ClassStatus = Literal["active", "archived"]
RegisterSource = Literal["import", "self"]

_LOGIN_USERNAME_MAX = 32
_USERNAME_RE = re.compile(r"^[0-9A-Za-z]{4,20}$")


def clean_username(raw: str) -> str:
    """用户名归一化：全角转半角 + 去首尾空格。

    学号常从 Excel 复制粘贴，全角数字是高频输入错误（10a「关键实现要点」）。
    比较与写入统一走本函数，保证 `Ａ２０２６０００１` 与 `A20260001` 视为同一账号。
    """
    return to_halfwidth(str(raw or "")).strip()


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------


class LoginIn(BaseModel):
    """`POST /api/auth/login`（接口设计 §2.1）。"""

    username: str = Field(min_length=1, max_length=_LOGIN_USERNAME_MAX, description="学号或用户名")
    password: str = Field(min_length=1, max_length=64, description="密码")
    role: Role = Field(description="admin / student，用于隔离两端入口")

    @field_validator("username")
    @classmethod
    def _v_username(cls, v: str) -> str:
        v = clean_username(v)
        if not v:
            raise ValueError("学号不能为空")
        return v


class RegisterIn(BaseModel):
    """`POST /api/auth/register` —— 学生自主注册（接口设计 §2.2）。"""

    username: str = Field(min_length=4, max_length=20, description="4–20 位字母数字，全校唯一")
    real_name: str = Field(min_length=1, max_length=20, description="姓名，1–20 字")
    password: str = Field(min_length=6, max_length=20, description="6–20 位，允许纯数字")
    class_id: int | None = Field(default=None, ge=1, description="不填进入「未分班」")

    @field_validator("username")
    @classmethod
    def _v_username(cls, v: str) -> str:
        v = clean_username(v)
        if not _USERNAME_RE.match(v):
            raise ValueError("学号必须是 4–20 位字母或数字")
        return v

    @field_validator("real_name")
    @classmethod
    def _v_real_name(cls, v: str) -> str:
        v = to_halfwidth(str(v or "")).strip()
        if not v:
            raise ValueError("姓名不能为空")
        if len(v) > 20:
            raise ValueError("姓名最多 20 个字")
        return v


class ProfileUpdateIn(BaseModel):
    """`PUT /api/me/profile` —— 年级学期偏好（接口设计 §8.2）。

    两个字段都可选，只更新传了的那个（双向持久化：本地为主 + 服务端同步）。
    """

    preferred_grade: int | None = Field(default=None, ge=1, le=9)
    preferred_semester: Semester | None = None


# ---------------------------------------------------------------------------
# 响应体（用 `model_validate(...).model_dump()` 统一出参，防止字段漂移）
# ---------------------------------------------------------------------------


class UserOut(BaseModel):
    """登录响应里的 `user` 字段 / `GET /api/auth/me` / `GET /api/me/profile`。

    `eye_*` 两项来自 `settings` 表（护眼设置是全站配置，不是每人的偏好），
    仅 `/api/me/profile` 填充；其余场景为 `None`（不参与展示）。
    """

    id: int
    role: Role
    username: str
    real_name: str
    class_id: int | None = None
    class_name: str | None = None
    preferred_grade: int | None = None
    preferred_semester: Semester | None = None
    status: UserStatus = "active"
    register_source: RegisterSource | None = None
    last_login_at: str | None = None
    created_at: str | None = None
    eye_rest_enabled: bool | None = None
    eye_warm_mode_auto: bool | None = None


class LoginOut(BaseModel):
    """登录 / 注册响应（契约 §5.2：`access_token` + `token_type` + `expires_in` + `user`）。

    ⚠️ 字段名以**契约 §5.2** 为准。《接口设计》§2.1 的示例写作 `"token"`，
    按契约优先级规则（契约 > 接口设计）统一为 `access_token`；
    管理端 `admin-web/src/api/types.ts` 亦按此实现。
    """

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserOut
