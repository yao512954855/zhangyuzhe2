"""分页参数解析 —— 契约《01-共享契约冻结》§5.1 / 10a F16。

- `page` 从 1 起，`page_size` 默认取 settings 表里的 `page_size_default`（20），上限 100。
- 非法值抛 `10001`（参数不合法），由全局 handler 转成带字段提示的响应。

注：`SettingsService` 在此处**延迟导入**，避免 core 层与 services 层形成循环依赖。
"""

from __future__ import annotations

from fastapi import Request

from app.core.errors import BizError, ErrorCode

PAGE_SIZE_MAX = 100


def _parse_int(raw: str | None, field: str, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise BizError(ErrorCode.PARAM_INVALID, f"{field} 必须是整数") from exc


def parse_pagination(request: Request) -> tuple[int, int]:
    """返回 `(page, page_size)`。"""
    from app.services.settings_service import SettingsService  # 延迟导入，避免循环

    default_size = SettingsService().get_int("page_size_default", 20)

    page = _parse_int(request.query_params.get("page"), "page", 1)
    page_size = _parse_int(request.query_params.get("page_size"), "page_size", default_size)

    if page < 1:
        raise BizError(ErrorCode.PARAM_INVALID, "page 必须大于等于 1")
    if page_size < 1 or page_size > PAGE_SIZE_MAX:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            f"page_size 必须在 1 到 {PAGE_SIZE_MAX} 之间",
        )

    return page, page_size
