"""单元接口 —— WP-BE-03 F1 ~ F5、F9（接口设计 §5.1 ~ §5.5、§8.5、§8.6）。

| 接口 | 说明 |
|---|---|
| `GET    /api/units` | 分页；按 `subject`/`grade`/`semester`/`status` 筛；带 4 个派生计数 |
| `POST   /api/units` | 新建；`(subject,grade,semester,name)` 重复 → `10001` |
| `PATCH  /api/units/{id}` | 改 `name`/`order_no`/`status`/`remark` |
| `DELETE /api/units/{id}` | 单元下任一内容有答题记录 → `30003` + `record_count` |
| `GET    /api/units/{id}/words` | **双角色**（契约 §14 裁决 3） |
| `GET    /api/units/{id}/texts` | **双角色**，不含正文 |

### 双角色的响应形态（本文件的关键约定）

`/api/units/{id}/words` 是**同一路径、两种用途**（契约 §14 裁决 3）：

| 角色 | 用途 | 响应 `data` | 词条字段 |
|---|---|---|---|
| `admin` | 管理端表格（接口设计 §5.5） | `{items,total,page,page_size,unit_id,unit_name,subject}` | 全字段（含 `accepted`、`status`） |
| `student` | 练习取词（接口设计 §8.5） | `{unit_id,unit_name,subject,words}` **不分页** | 隐藏 `accepted`/`status`，按 `mode` 藏一侧，附 `mastery` |

**学生端刻意不分页**：§8.5 明确要求「一次性返回并在 App 本地缓存，
练习过程中不再请求网络」—— 一个单元 20–40 条，分页反而让离线练习变复杂。
管理端走 `page()` 是因为老师要翻一个年级的所有词，量级完全不同。

> ⚠️ **不下发 `accepted`**（契约禁止清单第 4 条）：它是判分依据，
> 提前给到客户端等于把答案给了学生。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, get_current_user, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.schemas.content import (
    MasteryBrief,
    TextBriefOut,
    UnitCreateIn,
    UnitOut,
    UnitPatchIn,
    WordOut,
    WordStudentOut,
)
from app.services.content_service import ContentService

router = APIRouter(tags=["units"])


def _opt(request: Request, key: str) -> str | None:
    """取一个查询参数，空串归 None（前端常传 `?subject=`）。"""
    raw = request.query_params.get(key)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


# ---------------------------------------------------------------------------
# 单元 CRUD
# ---------------------------------------------------------------------------


@router.get("/api/units")
async def list_units(request: Request, admin: CurrentUser = Depends(require_admin)) -> dict:
    """单元列表（10a F1）。

    `status` **缺省时不过滤** —— 管理端要能看到已停用的单元并恢复它（10a D）。
    """
    page_no, page_size = parse_pagination(request)

    grade_raw = _opt(request, "grade")
    grade: int | None = None
    if grade_raw is not None:
        try:
            grade = int(grade_raw)
        except ValueError as exc:
            raise BizError(ErrorCode.PARAM_INVALID, "grade 必须是整数") from exc
        if grade < 1 or grade > 9:
            raise BizError(ErrorCode.PARAM_INVALID, "grade 必须在 1 到 9 之间")

    items, total = ContentService.list_units(
        subject=_opt(request, "subject"),
        grade=grade,
        semester=_opt(request, "semester"),
        status=_opt(request, "status"),
        page=page_no,
        page_size=page_size,
    )
    return page([UnitOut.model_validate(i).model_dump() for i in items], total, page_no, page_size)


@router.post("/api/units")
async def create_unit(
    payload: UnitCreateIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """新建单元（10a F2）。同名 → `10001`（U19）。"""
    data = ContentService.create_unit(
        subject=payload.subject,
        grade=payload.grade,
        semester=payload.semester,
        name=payload.name,
        order_no=payload.order_no,
        remark=payload.remark,
        operator_id=admin.id,
        ip=request.client.host if request.client else None,
    )
    return ok(UnitOut.model_validate(data).model_dump())


@router.patch("/api/units/{unit_id}")
async def patch_unit(
    unit_id: int,
    payload: UnitPatchIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """改单元（10a F3）。

    用 `model_fields_set` 分辨「没传这个字段」与「传了 null」：
    前者不动，后者对 `remark` 就是「清空备注」。
    """
    changes = payload.model_dump(exclude_unset=True)
    data = ContentService.update_unit(
        unit_id,
        changes,
        operator_id=admin.id,
        ip=request.client.host if request.client else None,
    )
    return ok(UnitOut.model_validate(data).model_dump())


@router.delete("/api/units/{unit_id}")
async def delete_unit(
    unit_id: int,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """删除单元（10a F4）。有练习记录 → `30003`，请改用停用。"""
    data = ContentService.delete_unit(
        unit_id,
        operator_id=admin.id,
        ip=request.client.host if request.client else None,
    )
    return ok(data)


# ---------------------------------------------------------------------------
# 单元下的内容（双角色）
# ---------------------------------------------------------------------------


@router.get("/api/units/{unit_id}/words")
async def list_unit_words(
    unit_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """单元词条（10a F5 / 契约 §14 裁决 3）—— **按角色返回不同结构**，见模块 docstring。"""
    unit = ContentService.get_unit(unit_id)
    meta: dict[str, Any] = {
        "unit_id": unit["id"],
        "unit_name": unit["name"],
        "subject": unit["subject"],
    }

    only = _opt(request, "only")
    keyword = _opt(request, "keyword")

    # ---------------- 学生端：练习取词，一次给全
    if not user.is_admin:
        rows = ContentService.list_words_student(
            unit_id,
            student_id=user.id,
            mode=_opt(request, "mode"),
            only=only,
            keyword=keyword,
        )
        words = []
        for row in rows:
            brief = row.get("_mastery")
            words.append(
                WordStudentOut.from_row(
                    row,
                    mode=_opt(request, "mode"),
                    mastery=MasteryBrief(**brief) if brief else MasteryBrief(),
                ).model_dump()
            )
        return ok({**meta, "words": words})

    # ---------------- 管理端：表格分页，全字段
    page_no, page_size = parse_pagination(request)
    items, total = ContentService.list_words_admin(
        unit_id,
        keyword=keyword,
        status=_opt(request, "status"),
        page=page_no,
        page_size=page_size,
    )
    resp = page([WordOut.model_validate(i).model_dump() for i in items], total, page_no, page_size)
    resp["data"].update(meta)
    return resp


@router.get("/api/units/{unit_id}/texts")
async def list_unit_texts(
    unit_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """单元课文列表（10a F9 / 接口设计 §8.6）—— **不含正文**。

    学生端固定只出 `status='active'`（10a D）；管理端能看到停用的，并附带
    `recite_scope_chars`（背诵范围字数），方便核对导入结果。
    """
    unit = ContentService.get_unit(unit_id)
    for_student = not user.is_admin

    rows = ContentService.list_texts(
        unit_id,
        for_student=for_student,
        student_id=user.id,
        status=_opt(request, "status"),
    )

    texts = []
    for row in rows:
        model = TextBriefOut.model_validate(row)
        dumped = model.model_dump()
        if for_student:
            # 学生端不暴露管理字段（接口设计 §8.6 的字段集里没有它们）
            for key in ("status", "order_no", "created_at", "recite_scope"):
                dumped.pop(key, None)
        texts.append(dumped)

    return ok(
        {
            "unit_id": unit["id"],
            "unit_name": unit["name"],
            "subject": unit["subject"],
            "texts": texts,
        }
    )
