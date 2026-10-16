"""错题本路由 —— WP-A，契约《01-共享契约冻结》§4.1 错题本段（v1.3，todo11 A7）。

POST   /api/wrongbook/auto-add        {item_id,mode} UPSERT 未移出记录
GET    /api/wrongbook?mode&status&group_by&page&page_size
       → [{item_id,en,pos,zh,mode,wrong_count,last_wrong_at,mastered_at,
           unit_id,unit_name,type}]
       # mode 过滤三类 tab：listen|spell_zh2en|spell_en2zh；
       # status: unmastered(默认)|mastered|all
DELETE /api/wrongbook/{item_id}/{mode}  手动移出（=置 resolved_at）
POST   /api/wrongbook/batch-resolve   {entries:[{item_id,mode},...]} 批量移出
       → {resolved:n}（不存在的条目跳过，不报错）
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.schemas.simplify import WrongbookAutoAdd
from app.services import wrongbook_service

router = APIRouter(tags=["wrongbook"])


class WrongbookBatchResolve(BaseModel):
    """v1.3 批量移出请求体。"""

    entries: list[dict] = Field(min_length=1)


@router.post("/api/wrongbook/auto-add")
async def auto_add(body: WrongbookAutoAdd) -> dict:
    return ok(wrongbook_service.auto_add(item_id=body.item_id, mode=body.mode))


@router.get("/api/wrongbook")
async def list_wrongbook(request: Request) -> dict:
    p, size = parse_pagination(request)
    group_by = request.query_params.get("group_by") or "unit"
    mode = request.query_params.get("mode") or None
    status = request.query_params.get("status") or "unmastered"
    rows, total = wrongbook_service.list_open(
        group_by=group_by,
        mode=mode,
        status=status,
        limit=size,
        offset=(p - 1) * size,
    )
    return page(rows, total, p, size)


@router.delete("/api/wrongbook/{item_id}/{mode}")
async def resolve_wrongbook(item_id: int, mode: str) -> dict:
    return ok(wrongbook_service.resolve(item_id=item_id, mode=mode))


@router.post("/api/wrongbook/batch-resolve")
async def batch_resolve_wrongbook(body: WrongbookBatchResolve) -> dict:
    return ok(wrongbook_service.batch_resolve(entries=body.entries))
