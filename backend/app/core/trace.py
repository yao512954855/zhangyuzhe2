"""请求追踪上下文 —— 契约《01-共享契约冻结》§3。

规则：
- 每次请求生成 **12 位十六进制** trace_id，写入 contextvars。
- 响应体与所有日志都携带它；**所有错误日志必须带 trace_id**。
- 客户端可通过请求头 `X-Trace-Id` 传入自定义值（便于前后端对账）。

实现说明（两条都踩过坑，别改回去）：
1. 使用**纯 ASGI 中间件**而非 `BaseHTTPMiddleware` —— 后者会把请求放进独立
   asyncio task，导致 contextvars 在子 task 中拿不到值（响应体里的 trace_id 会是空）。
2. trace_id 除了写 contextvars，**还要写进 `scope`**。原因：FastAPI 的中间件栈是
   `ServerErrorMiddleware(最外) → …用户中间件… → ExceptionMiddleware(最内)`，
   而注册在 `Exception` 上的全局处理器正是由 **ServerErrorMiddleware** 调用 ——
   它位于 `TraceIdMiddleware` **之外**。异常穿出 TraceIdMiddleware 时 `finally`
   已 reset 掉 contextvars，于是 10006 响应体里的 trace_id 会变成空串（验收用例 U6）。
   好在 `scope` 是同一个 dict 对象、被逐层原样透传，可用 `trace_context_from_scope()`
   在错误处理器里把上下文恢复回来。
"""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Iterator

# 客户端传入的 trace_id 必须符合此格式，否则忽略（防注入脏数据 / 超长串）
_TRACE_ID_RE = re.compile(r"^[0-9a-fA-F]{4,32}$")

TRACE_ID_HEADER = "X-Trace-Id"

# 自定义 scope key。加前缀避免与 ASGI / Starlette 的内置 key 撞名。
TRACE_ID_SCOPE_KEY = "bjsys.trace_id"

_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def new_trace_id() -> str:
    """生成 12 位十六进制 trace_id。"""
    return uuid.uuid4().hex[:12]


def set_trace_id(value: str) -> Any:
    """写入当前上下文，返回 token 供 reset。"""
    return _trace_id_var.set(value)


def reset_trace_id(token: Any) -> None:
    _trace_id_var.reset(token)


def get_trace_id() -> str:
    """读取当前 trace_id；无上下文时返回空串（由调用方决定兜底策略）。"""
    return _trace_id_var.get()


def get_trace_id_from_scope(scope: dict) -> str:
    """从 ASGI `scope` 里取 trace_id（不依赖 contextvars）。

    供运行在 `TraceIdMiddleware` **之外**的错误处理器使用（见模块顶部说明第 2 条）。
    """
    return scope.get(TRACE_ID_SCOPE_KEY) or ""


@contextmanager
def trace_context_from_scope(scope: dict) -> Iterator[None]:
    """在错误处理器中把 trace_id 上下文恢复回来。

    这样处理器内部的 `ok()` / `fail()` / `get_trace_id()` 无需任何改动，
    仍能拿到本次请求的 trace_id（`fail()` 从 contextvar 读取）。

    使用：
        with trace_context_from_scope(request.scope):
            return JSONResponse(content=fail(...))
    """
    tid = scope.get(TRACE_ID_SCOPE_KEY) or get_trace_id()
    if not tid:
        # 连 scope 里都没有（理论上不会发生），不强行造一个，
        # 否则响应体里的 trace_id 与日志里的对不上，反而误导排查。
        yield
        return
    token = set_trace_id(tid)
    try:
        yield
    finally:
        reset_trace_id(token)


class TraceIdMiddleware:
    """纯 ASGI 中间件：为每个 HTTP 请求建立 trace_id 上下文。"""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            # WebSocket / lifespan 直接放行
            await self.app(scope, receive, send)
            return

        # 从请求头取客户端传入的 trace_id（格式合法才采用）
        incoming = ""
        for key, value in scope.get("headers") or []:
            if key == b"x-trace-id":
                try:
                    candidate = value.decode("latin-1").strip()
                except Exception:  # pragma: no cover - 极少数非法字节
                    candidate = ""
                if _TRACE_ID_RE.match(candidate):
                    incoming = candidate
                break

        token = set_trace_id(incoming or new_trace_id())
        tid = get_trace_id()
        # 同步写进 scope：异常穿到最外层 ServerErrorMiddleware 后，
        # contextvars 已被 reset，错误处理器只能靠 scope 取回 trace_id（U6）。
        scope[TRACE_ID_SCOPE_KEY] = tid

        async def send_with_trace_id(message: dict) -> None:
            """给每个响应的头里塞 X-Trace-Id，方便前端与后端日志对账。"""
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                # 若业务侧（如 5xx 处理器）已显式设置则不重复添加
                if not any(k.lower() == b"x-trace-id" for k, _ in headers):
                    headers.append((b"x-trace-id", tid.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace_id)
        finally:
            reset_trace_id(token)
