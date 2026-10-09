"""错题本路由 —— WP-A，契约《01-共享契约冻结》§4.1 练习段。

POST   /api/wrongbook/auto-add        {item_id,mode} UPSERT 未解决记录
GET    /api/wrongbook?group_by=unit|type&page&page_size
       → [{item_id,en,pos,zh,mode,wrong_count,last_wrong_at,unit_id,unit_name,type}]
DELETE /api/wrongbook/{item_id}/{mode}  手动移出（=置 resolved_at）
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.schemas.simplify import WrongbookAutoAdd
from app.services import wrongbook_service

router = APIRouter(tags=["wrongbook"])


@router.post("/api/wrongbook/auto-add")
async def auto_add(body: WrongbookAutoAdd) -> dict:
    return ok(wrongbook_service.auto_add(item_id=body.item_id, mode=body.mode))


@router.get("/api/wrongbook")
async def list_wrongbook(request: Request) -> dict:
    p, size = parse_pagination(request)
    group_by = request.query_params.get("group_by") or "unit"
    rows, total = wrongbook_service.list_open(
        group_by=group_by, limit=size, offset=(p - 1) * size
    )
    return page(rows, total, p, size)


@router.delete("/api/wrongbook/{item_id}/{mode}")
async def resolve_wrongbook(item_id: int, mode: str) -> dict:
    return ok(wrongbook_service.resolve(item_id=item_id, mode=mode))
