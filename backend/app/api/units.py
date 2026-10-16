"""单元路由 —— WP-A / BE-11，契约《01-共享契约冻结》§4.1 单元与内容段（v1.3 两级树）。

GET    /api/units        两级单元树（一级含二级 counts 汇总，不分页）
POST   /api/units        {name,sort,parent_id?}（parent_id 空 = 一级）
PUT    /api/units/{id}   {name?,sort?,parent_id?}（parent_id 变更 = 移动归属；服务层校验两级）
DELETE /api/units/{id}   级联删（一级→二级→items）

请求模型在本文件内定义（本文件为 BE-11 独占文件，不动共享 schemas/simplify.py）。
`parent_id` 用 `model_fields_set` 区分「未传」与「显式 null」：
- 未传 / 不在请求体里 → 不改归属；
- 显式 `null` → 升为一级；
- 非空整数 → 移动到该一级单元下。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.response import ok
from app.services import unit_service

router = APIRouter(tags=["units"])


class UnitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    sort: int = 0
    parent_id: int | None = None


class UnitUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    sort: int | None = None
    parent_id: int | None = None


@router.get("/api/units")
async def list_units() -> dict:
    """两级单元树 [{id,name,sort,parent_id,counts,children:[...]}]。"""
    return ok(unit_service.list_tree())


@router.post("/api/units")
async def create_unit(body: UnitCreate) -> dict:
    return ok(unit_service.create(name=body.name, sort=body.sort, parent_id=body.parent_id))


@router.put("/api/units/{unit_id}")
async def update_unit(unit_id: int, body: UnitUpdate) -> dict:
    return ok(
        unit_service.update(
            unit_id,
            name=body.name,
            sort=body.sort,
            parent_id=body.parent_id,
            parent_id_set="parent_id" in body.model_fields_set,
        )
    )


@router.delete("/api/units/{unit_id}")
async def delete_unit(unit_id: int) -> dict:
    unit_service.delete(unit_id)
    return ok()
