"""设置路由 —— 契约《01-共享契约冻结》§4.1 设置段（v1.3 新增，v1.4 加 cloud_api_key）。

GET  /api/settings  → 全量设置（按 value_type 解析）。
                      ⚠️ `cloud_api_key` 返回**掩码**（前4后4，如 `sk-1****abcd`），
                      明文不出服务（契约 §9.2 安全红线）。
PUT  /api/settings  {dictation_countdown_s?, spell_verdict_countdown_s?,
                      dictation_hint?, cloud_api_key?}
                      → 全量返回（同 GET，同样掩码）；白名单键局部更新，
                        值域校验失败 10001。
                      cloud_api_key 空串 = 清除覆盖（回落 asr.yaml 种子）；
                      非空 = 立即生效（key_store 每次请求动态解析，无需重启）。

本文件为新增路由模块，`main.py` 自动发现（router_loader），无需注册。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.response import ok
from app.services.asr.key_store import SETTING_KEY, mask
from app.services.settings_service import SettingsService

router = APIRouter(tags=["settings"])


def _masked_all() -> dict[str, Any]:
    """全量设置 + cloud_api_key 掩码（GET / PUT 响应共用，明文不出服务）。"""
    data = SettingsService.get_all()
    if SETTING_KEY in data:
        data[SETTING_KEY] = mask(str(data.get(SETTING_KEY) or ""))
    return data


@router.get("/api/settings")
async def read_settings() -> dict:
    """全量设置（App 只读；管理端设置页读写；cloud_api_key 掩码）。"""
    return ok(_masked_all())


@router.put("/api/settings")
async def update_settings(body: dict[str, Any]) -> dict:
    """白名单键局部更新；值域校验（倒计时 1~10、hint ∈ zh|en、key ≤200 无换行）。"""
    SettingsService.update(body)
    return ok(_masked_all())
