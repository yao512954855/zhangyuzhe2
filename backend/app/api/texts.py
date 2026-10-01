"""课文接口 —— WP-BE-03 F10（接口设计 §8.7）+ 契约未定义的管理接口。

| 接口 | 说明 |
|---|---|
| `GET    /api/texts` | 跨单元课文列表（语文内容管理页，2026-09-30 新增） |
| `POST   /api/texts` | 单条新增课文（同上，含拼音校验） |
| `GET    /api/texts/{id}` | 课文详情：`paragraphs` + `recite_scope` + `scope_snapshot`；**不下发拼音** |
| `PATCH  /api/texts/{id}` | 改标题 / 作者 / 状态 / 序号（⚠️ 契约未定义，详见下） |
| `DELETE /api/texts/{id}` | 有背诵记录 → `30003` |

> ⚠️ **`PATCH` / `DELETE` 在《接口设计》与 10a F 表中都没有**
> （F9 / F10 只列了两个 `GET`）。但需求 §4.6「内容操作：修改 / 停用 / 删除」
> 与「有答题记录的内容只能停用不能删除」对课文同样成立 —— 少这两个接口，
> 课文就是「只能灌进去，不能管」。故按 `words` 的同一形态补齐，
> **已列入待姚工确认的契约补充项**。

### 为什么 `GET` 要分角色

契约 §14 裁决 3 把 `/api/units/{id}/texts` 归为双角色路径，`/api/texts/{id}` 同理：
学生端只需要「段落 + 背诵范围」去渲染背诵页；
管理端还要 `status` / `order_no` 这些运维字段。
两者共用同一个 handler，靠角色裁剪 —— **不要另开一条学生专用路径**。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, get_current_user, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.schemas.content import TextCreateIn, TextDetailOut, TextPatchIn
from app.services.content_service import ContentService
from app.services.version_service import VersionService

router = APIRouter(tags=["texts"])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _opt(request: Request, key: str) -> str | None:
    raw = request.query_params.get(key)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _opt_grade(request: Request) -> int | None:
    raw = _opt(request, "grade")
    if raw is None:
        return None
    try:
        grade = int(raw)
    except ValueError as exc:
        raise BizError(ErrorCode.PARAM_INVALID, "grade 必须是整数") from exc
    if grade < 1 or grade > 9:
        raise BizError(ErrorCode.PARAM_INVALID, "grade 必须在 1 到 9 之间")
    return grade


@router.get("/api/texts")
async def list_texts(request: Request, admin: CurrentUser = Depends(require_admin)) -> dict:
    """跨单元课文列表（语文内容管理页，2026-09-30 新增）。

    列表项 = `GET /api/units/{id}/texts`（admin 分支）的字段
    + `unit_name / grade / semester`（跨单元浏览时看归属，页面定稿要求）。
    ⚠️ 不含正文（同 §8.6「列表不含正文」的约定）。
    """
    page_no, page_size = parse_pagination(request)
    items, total = ContentService.list_texts_admin_global(
        subject=_opt(request, "subject"),
        grade=_opt_grade(request),
        semester=_opt(request, "semester"),
        unit_id=int(_opt(request, "unit_id")) if _opt(request, "unit_id") else None,
        keyword=_opt(request, "keyword"),
        status=_opt(request, "status"),
        page=page_no,
        page_size=page_size,
    )
    return page(items, total, page_no, page_size)


@router.post("/api/texts")
async def create_text(
    payload: TextCreateIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """单条新增课文（语文内容管理页「＋添加课文」，2026-09-30 新增）。

    正文分段 / 背诵范围 / 拼音校验与导入链路**同一套服务**（口径必须一致）；
    含无法拼音化的字 → `40003`；同单元同标题 → `10001`。
    """
    data = ContentService.create_text(
        unit_id=payload.unit_id,
        title=payload.title,
        author=payload.author,
        content_raw=payload.content_raw,
        recite_scope=payload.recite_scope,
        operator_id=admin.id,
        ip=_ip(request),
    )
    return ok(TextDetailOut.model_validate(data).model_dump())


@router.get("/api/texts/{text_id}")
async def get_text(
    text_id: int,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """课文详情（10a F10 / 接口设计 §8.7）。

    返回 `paragraphs`（标了 `recite`）与 `recite_scope`，
    再给一个 `scope_snapshot` = 背诵范围在正文里的字符区间 `[start, end)`，
    对齐引擎按它裁剪 `content_plain`。

    ⚠️ **响应中没有 `content_plain` / `content_pinyin`**（§8.7 明文「不下发拼音序列」）。
    """
    data = ContentService.get_text_detail(text_id, for_student=not user.is_admin)
    return ok(TextDetailOut.model_validate(data).model_dump())


@router.patch("/api/texts/{text_id}")
async def patch_text(
    text_id: int,
    payload: TextPatchIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """改课文元信息（标题 / 作者 / 序号 / 状态）。正文改动请走重新导入。"""
    changes = payload.model_dump(exclude_unset=True)
    data, history_affected = ContentService.patch_text(
        text_id, changes, operator_id=admin.id, ip=_ip(request)
    )
    # WP-2I：停用记 disable、其余改记 update
    op = "disable" if changes.get("status") == "inactive" else "update"
    VersionService.record_now(entity_type="text", entity_id=text_id, op=op, operator_id=admin.id)
    return ok({**TextDetailOut.model_validate(data).model_dump(), "history_affected": history_affected})


@router.delete("/api/texts/{text_id}")
async def delete_text(
    text_id: int,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """删除课文。有背诵记录 → `30003` + `record_count`。"""
    # WP-2I：删除前捕获整行快照（删除成功才落一条 delete 版本）
    snapshot = VersionService.capture_raw("text", text_id)
    data = ContentService.delete_text(text_id, operator_id=admin.id, ip=_ip(request))
    VersionService.record_now(
        entity_type="text", entity_id=text_id, op="delete", operator_id=admin.id, snapshot=snapshot
    )
    return ok(data)
