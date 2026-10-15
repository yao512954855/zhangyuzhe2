"""AI 能力模式查询/切换 —— 契约《docs/localize-dev/01-契约冻结》§5。

GET /api/capabilities            → 各能力当前生效模式 + 本地引擎就绪状态
PUT  /api/capabilities/mode      → 写 settings.capability_mode（管理端按钮）

只读 + 改 settings，无业务副作用；鉴权：无（S2 全局免登录，与现有接口一致）。
GET 不触发模型加载（local_ready 只查文件与 import，契约 §5/§6）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.capability import CAPS, engine_ready, is_locked, resolve_mode
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.services.settings_service import SettingsService

router = APIRouter(tags=["capabilities"])

_VALID_MODES = ("local", "hybrid", "cloud")


def _capabilities_payload() -> dict[str, Any]:
    """按契约 §5 结构组装（mode=该能力实际生效模式；locked=.env 锁定）。"""
    engines: dict[str, Any] = {}
    for cap in CAPS:
        ready, detail = engine_ready(cap)
        engines[cap] = {
            "mode": resolve_mode(cap),
            "locked": is_locked(cap),
            "local_ready": ready,
            "local_detail": detail,
        }
    return {
        "mode": SettingsService().get_str("capability_mode", "hybrid"),
        "engines": engines,
    }


@router.get("/api/capabilities")
async def get_capabilities() -> dict[str, Any]:
    return ok(_capabilities_payload())


class ModeIn(BaseModel):
    mode: str


@router.put("/api/capabilities/mode")
async def set_mode(payload: ModeIn) -> dict[str, Any]:
    mode = str(payload.mode or "").strip().lower()
    if mode not in _VALID_MODES:
        raise BizError(ErrorCode.PARAM_INVALID, message="模式取值须为 local/hybrid/cloud")
    SettingsService().set("capability_mode", mode)
    return ok(_capabilities_payload())
