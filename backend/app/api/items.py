"""内容条目路由 —— WP-A，契约《01-共享契约冻结》§4.1 单元与内容段。

GET    /api/items?unit_id&type&page&page_size&keyword   分页列表
POST   /api/items                                       {unit_id,type,en,pos?,zh,alt_zh?,sort?}
PUT    /api/items/{id}                                  同上字段可选
DELETE /api/items/{id}
POST   /api/items/import          multipart: file(.xlsx) + form unit_id/type
GET    /api/items/export?unit_id&type                   → xlsx 二进制
GET    /api/items/import-template                       → xlsx 二进制
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import Response as FastAPIResponse

from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.schemas.simplify import ItemCreate, ItemUpdate
from app.services import item_service

router = APIRouter(tags=["items"])


@router.get("/api/items")
async def list_items(request: Request) -> dict:
    p, size = parse_pagination(request)
    unit_id_raw = request.query_params.get("unit_id")
    keyword = request.query_params.get("keyword")
    item_type = request.query_params.get("type") or None
    rows, total = item_service.list_items(
        unit_id=int(unit_id_raw) if unit_id_raw else None,
        item_type=item_type,
        keyword=keyword,
        limit=size,
        offset=(p - 1) * size,
    )
    return page(rows, total, p, size)


@router.post("/api/items")
async def create_item(body: ItemCreate) -> dict:
    return ok(
        item_service.create(
            unit_id=body.unit_id,
            item_type=body.type,
            en=body.en,
            pos=body.pos,
            zh=body.zh,
            alt_zh=body.alt_zh,
            sort=body.sort,
        )
    )


@router.put("/api/items/{item_id}")
async def update_item(item_id: int, body: ItemUpdate) -> dict:
    return ok(item_service.update(item_id, fields=body.model_dump()))


@router.delete("/api/items/{item_id}")
async def delete_item(item_id: int) -> dict:
    item_service.delete(item_id)
    return ok()


@router.post("/api/items/import")
async def import_items(
    file: UploadFile = File(...),
    unit_id: int = Form(...),
    type: str = Form(...),  # noqa: A002 - 契约字段名冻结为 type
) -> dict:
    raw = await file.read()
    return ok(item_service.import_items(raw, unit_id=unit_id, item_type=type))


@router.get("/api/items/export")
async def export_items(request: Request) -> Response:
    unit_id_raw = request.query_params.get("unit_id")
    item_type = request.query_params.get("type") or None
    data, headers = item_service.export(
        unit_id=int(unit_id_raw) if unit_id_raw else None,
        item_type=item_type,
    )
    return FastAPIResponse(content=data, headers=headers)


@router.get("/api/items/import-template")
async def import_template() -> Response:
    data, headers = item_service.build_items_template()
    return FastAPIResponse(content=data, headers=headers)
