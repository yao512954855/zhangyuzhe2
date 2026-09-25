"""统一响应包装 —— 契约《01-共享契约冻结》§3 与 §5.3。

**所有接口必须使用 `ok()` / `page()`，禁止直接 `return dict`**（10a F6）。

响应结构：
```json
{"code": 0, "message": "ok", "data": ..., "trace_id": "8f3a2b1c9d0e"}
```
"""

from __future__ import annotations

from typing import Any

from app.core.trace import get_trace_id


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """成功响应（含业务成功与"业务上不允许"的非异常返回）。"""
    return {
        "code": 0,
        "message": message,
        "data": data,
        "trace_id": get_trace_id(),
    }


def page(
    items: list[Any],
    total: int,
    page: int,  # noqa: A002 - 与契约字段名保持一致
    page_size: int,
) -> dict[str, Any]:
    """分页响应，`data` 固定为 `{items, total, page, page_size}`（契约 §5.3）。"""
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        },
        "trace_id": get_trace_id(),
    }


def fail(code: int, message: str, data: Any = None) -> dict[str, Any]:
    """构造失败响应。

    ⚠️ **仅供异常处理器内部使用**。业务代码请 `raise BizError(...)`，
    由全局 handler 统一转成此结构交出（契约 §3：`fail` 由异常驱动）。
    """
    return {
        "code": code,
        "message": message,
        "data": data,
        "trace_id": get_trace_id(),
    }
