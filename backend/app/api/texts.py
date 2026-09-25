"""课文接口 —— WP-BE-03 F10（接口设计 §8.7）+ 契约未定义的管理接口。

| 接口 | 说明 |
|---|---|
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
from app.core.response import ok
from app.schemas.content import TextDetailOut, TextPatchIn
from app.services.content_service import ContentService

router = APIRouter(tags=["texts"])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


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
    return ok({**TextDetailOut.model_validate(data).model_dump(), "history_affected": history_affected})


@router.delete("/api/texts/{text_id}")
async def delete_text(
    text_id: int,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """删除课文。有背诵记录 → `30003` + `record_count`。"""
    data = ContentService.delete_text(text_id, operator_id=admin.id, ip=_ip(request))
    return ok(data)
