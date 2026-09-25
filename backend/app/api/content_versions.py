"""内容版本快照接口 —— WP-2I（二期工作包 9）。

| 接口 | 说明 |
|---|---|
| `GET    /api/content/versions?entity_type=&entity_id=` | 查某实体的版本历史（admin） |
| `POST   /api/content/versions/{id}/rollback` | 回滚到该快照（admin） |

错误码：列表 `10001`（参数非法）；回滚 `70005`（版本不存在）。

⚠️ 不改动 `main.py` —— 路由靠 `app/core/router_loader.load_routers` 自动发现。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.db import write_tx
from app.core.deps import CurrentUser, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.services.version_service import VersionService

router = APIRouter(tags=["content-versions"])

_VALID_TYPES = ("unit", "word", "text")


@router.get("/api/content/versions")
async def list_content_versions(
    entity_type: str | None = None,
    entity_id: int | None = None,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """查某实体的版本历史（二期 §3 WP-2I）。

    `entity_type` 必须是 `unit` / `word` / `text`，`entity_id` 必填且为整数。
    """
    if entity_type not in _VALID_TYPES or entity_id is None:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            "entity_type 必须是 unit/word/text，且 entity_id 必填",
        )
    items = VersionService.list_versions(entity_type=entity_type, entity_id=entity_id)
    return ok({"items": items})


@router.post("/api/content/versions/{version_id}/rollback")
async def rollback_content_version(
    version_id: int,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """回滚到指定版本快照（二期 §3 WP-2I）。

    回滚**不会删除**任何内容（天然尊重「有记录只能停用不能删」的 30003 约束），
    而是把实体恢复为该快照捕获时的状态，并再写一条 `update` 版本留痕。
    """
    with write_tx() as conn:
        result = VersionService.rollback(
            conn, version_id=version_id, operator_id=admin.id
        )
    return ok(result)
