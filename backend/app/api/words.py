"""词条接口 —— WP-BE-03 F6 ~ F8（接口设计 §5.6 ~ §5.8）。

| 接口 | 说明 |
|---|---|
| `GET    /api/words` | 跨单元词条列表（英语内容管理页，2026-09-30 新增） |
| `POST   /api/words` | 单条新增词条（同上） |
| `PATCH  /api/words/{id}` | 改词条；已答题则附 `history_affected: true` |
| `POST   /api/words/batch-status` | 批量启用 / 停用 |
| `DELETE /api/words/{id}` | 有记录 → `30003` + `record_count` |

### 「改」与「删」的分界线（需求 §4.6）

内容只有三种操作，选错哪种会出事：

| 操作 | 什么时候用 | 历史记录 |
|---|---|---|
| 改 | 发现中文释义写错了 | **不变**（`answers.expected` 是快照） |
| 停用 | 这个词本学期不教了 | 完整保留，学生端立即看不到 |
| 删除 | 导错了、要重来 | **只允许在零记录时**，否则 `30003` |

**停用不是「软删除」的礼貌说法，它就是正确的做法** ——
删掉一个练过 37 次的词条，那 37 条记录的分子分母就对不上了。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.schemas.content import WordBatchStatusIn, WordCreateIn, WordOut, WordPatchIn
from app.services.content_service import ContentService
from app.services.version_service import VersionService

router = APIRouter(tags=["words"])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _opt(request: Request, key: str) -> str | None:
    """取一个查询参数，空串归 None（前端常传 `?status=`）。"""
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


@router.get("/api/words")
async def list_words(request: Request, admin: CurrentUser = Depends(require_admin)) -> dict:
    """跨单元词条列表（英语内容管理页，2026-09-30 新增）。

    与 `GET /api/units/{id}/words`（admin 分支）的区别：不锚定单个单元，
    可按 `subject / grade / semester / unit_id / keyword / status` 组合筛选，
    每行 JOIN 带出 `unit_name / grade / semester`（跨单元浏览时看归属）。
    分页口径同契约 §5.1（`page_size ≤ 100`）。
    """
    page_no, page_size = parse_pagination(request)
    items, total = ContentService.list_words_admin_global(
        subject=_opt(request, "subject"),
        grade=_opt_grade(request),
        semester=_opt(request, "semester"),
        unit_id=int(_opt(request, "unit_id")) if _opt(request, "unit_id") else None,
        keyword=_opt(request, "keyword"),
        status=_opt(request, "status"),
        page=page_no,
        page_size=page_size,
    )
    data = [
        {
            **WordOut.model_validate(i).model_dump(),
            "unit_name": i.get("unit_name"),
            "grade": i.get("grade"),
            "semester": i.get("semester"),
        }
        for i in items
    ]
    return page(data, total, page_no, page_size)


@router.post("/api/words")
async def create_word(
    payload: WordCreateIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """单条新增词条（英语内容管理页「＋添加单词」，2026-09-30 新增）。

    同单元同 `en` 重复 → `10001`；单元不是英语单元 → `10001`；
    `order_no` 缺省排到单元末尾。创建成功返回全字段词条。
    """
    word = ContentService.create_word(
        unit_id=payload.unit_id,
        en=payload.en,
        cn=payload.cn,
        type_=payload.type,
        accepted=payload.accepted,
        phonetic=payload.phonetic,
        pos=payload.pos,
        example_en=payload.example_en,
        example_cn=payload.example_cn,
        require_spell=payload.require_spell,
        order_no=payload.order_no,
        operator_id=admin.id,
        ip=_ip(request),
    )
    return ok(WordOut.model_validate(word).model_dump())


@router.patch("/api/words/{word_id}")
async def patch_word(
    word_id: int,
    payload: WordPatchIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """修改词条（10a F6）。

    响应在词条字段外**额外带 `history_affected`**：
    该词条已有答题记录时为 `true`，前端据此弹确认（接口设计 §5.6 的
    「若该词条已有答题记录，响应中附带提示字段」）。

    ⚠️ 历史记录**不会**被回写（U15）：`answers.expected` 存的是答题当时的快照，
    改词条只影响以后的题。若回写，等于事后篡改学生的成绩。
    """
    changes = payload.model_dump(exclude_unset=True)
    word, history_affected = ContentService.patch_word(
        word_id, changes, operator_id=admin.id, ip=_ip(request)
    )
    # WP-2I：停用记 disable、其余改记 update
    op = "disable" if changes.get("status") == "inactive" else "update"
    VersionService.record_now(entity_type="word", entity_id=word_id, op=op, operator_id=admin.id)
    return ok({**WordOut.model_validate(word).model_dump(), "history_affected": history_affected})


@router.post("/api/words/batch-status")
async def batch_word_status(
    payload: WordBatchStatusIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """批量启用 / 停用（10a F7）。返回实际改动的条数。

    停用的词条**立即从学生端消失**（`list_words_student` 固定带 `status='active'`，U12），
    但历史记录与掌握度都保留 —— 这是「本学期不教这个词」的标准处理方式。
    """
    # WP-2I：批量改动前逐个捕获「改动前」快照（停用记 disable、启用记 update）
    op = "disable" if payload.status == "inactive" else "update"
    before = {
        wid: VersionService.capture_raw("word", wid) for wid in set(payload.word_ids)
    }
    affected = ContentService.batch_status(
        payload.word_ids, payload.status, operator_id=admin.id, ip=_ip(request)
    )
    for wid in set(payload.word_ids):
        VersionService.record_now(
            entity_type="word", entity_id=wid, op=op, operator_id=admin.id, snapshot=before.get(wid)
        )
    return ok(
        {
            "updated": affected,
            "status": payload.status,
            "requested": len(set(payload.word_ids)),
        }
    )


@router.delete("/api/words/{word_id}")
async def delete_word(
    word_id: int,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """删除词条（10a F8）。

    有答题记录 → `30003` + `data.record_count`（U9），提示改为停用；
    零记录才物理删除（U10）。
    """
    # WP-2I：删除前捕获整行快照（删除成功才落一条 delete 版本）
    snapshot = VersionService.capture_raw("word", word_id)
    data = ContentService.delete_word(word_id, operator_id=admin.id, ip=_ip(request))
    VersionService.record_now(
        entity_type="word", entity_id=word_id, op="delete", operator_id=admin.id, snapshot=snapshot
    )
    return ok(data)
