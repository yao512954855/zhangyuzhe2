"""内容版本快照接口 —— WP-2I（二期工作包 9）。

| 接口 | 说明 |
|---|---|
| `GET    /api/content/versions/recent?entity_type=&op=&page=` | 全局最近变更（版本页默认视图，2026-09-30 方案 B） |
| `GET    /api/content/versions?entity_type=&entity_id=&with_snapshot=` | 查某实体的版本历史（admin） |
| `POST   /api/content/versions/{id}/rollback` | 回滚到该快照（admin） |

错误码：列表 `10001`（参数非法）；回滚 `70005`（版本不存在）。

⚠️ 不改动 `main.py` —— 路由靠 `app/core/router_loader.load_routers` 自动发现。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.db import write_tx
from app.core.deps import CurrentUser, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.services.version_service import VersionService

router = APIRouter(tags=["content-versions"])

_VALID_TYPES = ("unit", "word", "text")
_VALID_OPS = ("create", "update", "disable", "delete")


@router.get("/api/content/versions/recent")
async def list_recent_content_versions(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """全局最近变更列表（版本页默认视图，2026-09-30 方案 B）。

    不再要求手填实体 ID；按记录倒序返回全部内容变更，JOIN 带出
    `entity_name / unit_name / grade / semester / operator_name`。
    `entity_type` / `op` 可选过滤；分页口径同契约 §5.1（`page_size ≤ 100`）。
    """
    entity_type = request.query_params.get("entity_type") or None
    op = request.query_params.get("op") or None
    if entity_type is not None and entity_type not in _VALID_TYPES:
        raise BizError(ErrorCode.PARAM_INVALID, "entity_type 必须是 unit/word/text")
    if op is not None and op not in _VALID_OPS:
        raise BizError(ErrorCode.PARAM_INVALID, "op 必须是 create/update/disable/delete")

    page_no, page_size = parse_pagination(request)
    data = VersionService.list_recent(
        entity_type=entity_type, op=op, page=page_no, page_size=page_size
    )
    return page(data["items"], data["total"], data["page"], data["page_size"])


@router.get("/api/content/versions")
async def list_content_versions(
    entity_type: str | None = None,
    entity_id: int | None = None,
    with_snapshot: bool = False,
    admin: CurrentUser = Depends(require_admin),
) -> dict:
    """查某实体的版本历史（二期 §3 WP-2I）。

    `entity_type` 必须是 `unit` / `word` / `text`，`entity_id` 必填且为整数。
    `with_snapshot=1` 时附带解析后的快照 JSON（版本页 diff 展示用）。
    """
    if entity_type not in _VALID_TYPES or entity_id is None:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            "entity_type 必须是 unit/word/text，且 entity_id 必填",
        )
    items = VersionService.list_versions(
        entity_type=entity_type, entity_id=entity_id, with_snapshot=with_snapshot
    )
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
