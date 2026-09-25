"""二期 WP-2H：保存「我的练习方案」 —— `/api/me/practice-plans*`。

契约依据：
- `docs/dev/01-共享契约冻结.md` §15.2（二期 HTTP 端点）/ §15.3（错误码 70001）/
  §3（一律 `ok()`，业务失败 = HTTP 200 + 非零 code）。
- `docs/dev/50-二期工作包拆解.md` §3（端点表：GET/POST 列表与 `{id}`；
  PUT/DELETE 越权同码 `70001`）/ §6（独占文件 `api/me_plans.py`）。
- `practice_plans` 表结构见 `migrations/002_wave2.sql`（已建好，本包唯一写入方）。

要点：
- **学生端接口**：鉴权走 `get_current_user`，只回自己的数据。
- **越权与「不存在」同码 `70001`**（不暴露资源存在性，沿用 A3 安全口径，
  与 §8.9 申诉、WP-2E 背诵回放同一做法）。
- `payload` 是 JSON 快照：服务端**存原样、取原样**，不解析内部结构，
  多语言客户端传什么维度就存什么维度（语文如实不发 `mode`、英语 `scope=lesson`
  静默退化等由客户端负责）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.deps import CurrentUser, get_current_user
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.services import plan_service

router = APIRouter(tags=["me-practice-plans"])

_VALID_SUBJECTS = ("chinese", "english")


# ---------------------------------------------------------------------------
# 请求体（Pydantic 直接给 10001 校验，契约 §3）
# ---------------------------------------------------------------------------


class PracticePlanCreate(BaseModel):
    """`POST /api/me/practice-plans` 的请求体。"""

    name: str = Field(..., min_length=1, max_length=60)
    subject: str
    # `payload` 是 JSON 快照（scope/count/order/mode/time_limit_ms/text_ids…），
    # 服务端原样存储，类型放 `Any`；具体结构是否为 dict 在 router 里再判。
    payload: Any


class PracticePlanUpdate(BaseModel):
    """`PUT /api/me/practice-plans/{id}` 的请求体（全字段可选，至少给一个）。"""

    name: str | None = Field(default=None, min_length=1, max_length=60)
    subject: str | None = None
    payload: Any | None = None


# ---------------------------------------------------------------------------
# 校验辅助
# ---------------------------------------------------------------------------


def _check_subject(subject: str) -> None:
    if subject not in _VALID_SUBJECTS:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            "学科只能是 chinese 或 english",
        )


def _check_name(name: str) -> str:
    """去首尾空白后必须非空（Pydantic 的 `strip_whitespace` 在 v2 已弃用，手动做）。"""
    stripped = name.strip()
    if not stripped:
        raise BizError(ErrorCode.PARAM_INVALID, "方案名称不能为空")
    return stripped


def _check_payload_is_object(payload: Any) -> None:
    """快照必须是 JSON 对象（dict）。

    契约 §15.1 把 payload 描述为 `scope/count/order/...` 这类键值结构，
    不接受裸标量或数组，避免把"配置快照"存成一个无法套用的怪值。
    """
    if not isinstance(payload, dict):
        raise BizError(ErrorCode.PARAM_INVALID, "payload 必须是一个 JSON 对象")


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/api/me/practice-plans")
def list_plans(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """列出当前学生的全部练习方案，**只回自己的**（契约 §15.2）。"""
    items = plan_service.list_plans(user.id)
    return ok({"items": items})


@router.post("/api/me/practice-plans")
def create_plan(
    body: PracticePlanCreate,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """保存一条方案，返回其 `id`。"""
    _check_subject(body.subject)
    _check_payload_is_object(body.payload)

    plan_id = plan_service.create_plan(
        student_id=user.id,
        name=_check_name(body.name),
        subject=body.subject,
        payload=body.payload,
    )
    return ok({"id": plan_id})


@router.put("/api/me/practice-plans/{plan_id}")
def update_plan(
    plan_id: int,
    body: PracticePlanUpdate,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """更新一条方案。

    越权（别人的）或不存在 → 均 `70001`（同码不暴露存在性）。
    一个字段都没给 → `10001`。
    """
    # 先判归属：不存在 / 不是自己的 → 70001（与"存在但无权限"同码）。
    if plan_service.get_own_plan(student_id=user.id, plan_id=int(plan_id)) is None:
        raise BizError(ErrorCode.PLAN_NOT_FOUND, "练习方案不存在")

    if body.name is None and body.subject is None and body.payload is None:
        raise BizError(ErrorCode.PARAM_INVALID, "没有要更新的内容")

    if body.subject is not None:
        _check_subject(body.subject)
    if body.payload is not None:
        _check_payload_is_object(body.payload)

    plan_service.update_plan(
        student_id=user.id,
        plan_id=int(plan_id),
        name=_check_name(body.name) if body.name is not None else None,
        subject=body.subject,
        payload=body.payload,
    )
    return ok()


@router.delete("/api/me/practice-plans/{plan_id}")
def delete_plan(
    plan_id: int,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """删除一条方案。

    越权（别人的）或不存在 → 均 `70001`（同码不暴露存在性）。
    """
    deleted = plan_service.delete_plan(student_id=user.id, plan_id=int(plan_id))
    if not deleted:
        raise BizError(ErrorCode.PLAN_NOT_FOUND, "练习方案不存在")
    return ok()
