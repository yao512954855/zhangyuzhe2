"""作业布置接口 —— WP-2G（作业布置与截止提醒，二期 G2）。

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `GET    /api/homework` | admin | 分页列表（可 `class_id` 过滤） |
| `POST   /api/homework` | admin | 布置作业（自动给该班在读学生建 pending 提交记录） |
| `PATCH  /api/homework/{id}` | admin | 改标题 / 学科 / 单元 / 截止时间 / 状态 |
| `DELETE /api/homework/{id}` | admin | 删除（联级清理 submissions） |
| `GET    /api/me/homework` | student | 只看自己班级且 active 的作业 |

错误码（契约 §15.3）：`20004` 班级不存在 / `70002` 作业不存在 /
`70003` 作业已截止（due_at 早于当前时间）/ 非 admin → `10003` / 未登录 → `10002`。

⚠️ 一律 `ok()` / `page()`，业务失败 = HTTP 200 + 非零 code；**不修改 `main.py`**，本文件
由 `app/core/router_loader.load_routers` 自动发现挂载。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from pydantic import BaseModel, Field

from app.core.deps import CurrentUser, get_current_user, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.services.homework_service import (
    create_homework,
    delete_homework,
    list_homework,
    student_homework,
    update_homework,
)

router = APIRouter(prefix="/api", tags=["homework"])


class HomeworkCreateIn(BaseModel):
    """`POST /api/homework` 入参（契约 §15.2）。"""

    class_id: int = Field(ge=1, description="目标班级 id")
    title: str = Field(min_length=1, max_length=100, description="作业标题")
    subject: str = Field(description="chinese / english")
    unit_ids: list[int] = Field(default_factory=list, description="关联单元 id 列表")
    due_at: str | None = Field(default=None, description="截止时间，ISO 8601（可空）")


class HomeworkPatchIn(BaseModel):
    """`PATCH /api/homework/{id}` —— 只更新显式传入的字段。"""

    title: str | None = Field(default=None, min_length=1, max_length=100)
    subject: str | None = None
    unit_ids: list[int] | None = None
    due_at: str | None = None  # 空串表示清空截止时间
    status: str | None = None  # active / closed


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------
# 管理端：作业 CRUD
# ---------------------------------------------------------------------------


@router.get("/homework")
def list_homework_route(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """作业列表（管理端）。`class_id`（可选）按班级过滤，`page`/`page_size` 分页。"""
    page_no, page_size = parse_pagination(request)
    raw_class = request.query_params.get("class_id")
    class_id: int | None = None
    if raw_class is not None and raw_class.strip() != "":
        try:
            class_id = int(raw_class)
        except (TypeError, ValueError) as exc:
            raise BizError(ErrorCode.PARAM_INVALID, "class_id 必须是整数") from exc

    items, total = list_homework(page=page_no, page_size=page_size, class_id=class_id)
    return page(items, total, page_no, page_size)


@router.post("/homework")
def create_homework_route(
    payload: HomeworkCreateIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """布置作业（管理端）。"""
    result = create_homework(
        class_id=payload.class_id,
        title=payload.title,
        subject=payload.subject,
        unit_ids=payload.unit_ids,
        due_at=payload.due_at,
        created_by=admin.id,
        ip=_client_ip(request),
    )
    return ok(result)


@router.patch("/homework/{homework_id}")
def update_homework_route(
    homework_id: int,
    payload: HomeworkPatchIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """修改作业（管理端）。只更新显式传入的字段。"""
    data: dict[str, Any] = payload.model_dump(exclude_unset=True)
    result = update_homework(
        homework_id=homework_id,
        title=data.get("title"),
        subject=data.get("subject"),
        unit_ids=data.get("unit_ids"),
        due_at=data.get("due_at"),
        status=data.get("status"),
        operator_id=admin.id,
        ip=_client_ip(request),
    )
    return ok(result)


@router.delete("/homework/{homework_id}")
def delete_homework_route(
    homework_id: int,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """删除作业（管理端）。"""
    delete_homework(
        homework_id=homework_id,
        operator_id=admin.id,
        ip=_client_ip(request),
    )
    return ok()


# ---------------------------------------------------------------------------
# 学生端：只看自己班级
# ---------------------------------------------------------------------------


@router.get("/me/homework")
def my_homework_route(
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """`GET /api/me/homework`：学生只看自己班级且 active 的作业。

    响应 `{items:[{id,title,subject,due_at,status}]}`，`status` 为该生提交状态
    （pending / done）。未分班学生返回空列表。
    """
    items = student_homework(student_id=user.id, class_id=user.class_id)
    return ok({"items": items})
