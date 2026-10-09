"""单元路由 —— WP-A，契约《01-共享契约冻结》§4.1 单元与内容段。

GET    /api/units        全量单元树（不分页，契约 §4：units 返回全量树）
POST   /api/units        {name,sort}
PUT    /api/units/{id}   {name?,sort?}
DELETE /api/units/{id}   级联删 items（DB ON DELETE CASCADE）
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.response import ok
from app.schemas.simplify import UnitCreate, UnitUpdate
from app.services import unit_service

router = APIRouter(tags=["units"])


@router.get("/api/units")
async def list_units() -> dict:
    """全量单元树 [{id,name,sort,counts:{word,phrase,sentence}}]。"""
    return ok(unit_service.list_tree())


@router.post("/api/units")
async def create_unit(body: UnitCreate) -> dict:
    return ok(unit_service.create(name=body.name, sort=body.sort))


@router.put("/api/units/{unit_id}")
async def update_unit(unit_id: int, body: UnitUpdate) -> dict:
    return ok(
        unit_service.update(
            unit_id,
            name=body.name,
            sort=body.sort,
        )
    )


@router.delete("/api/units/{unit_id}")
async def delete_unit(unit_id: int) -> dict:
    unit_service.delete(unit_id)
    return ok()
