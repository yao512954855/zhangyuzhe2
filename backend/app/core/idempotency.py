"""幂等键中间件 —— 契约《01-共享契约冻结》§5.1 / 10a F16。

写请求携带 `Idempotency-Key` 头时，**5 分钟内**重复提交直接返回首次结果。
典型场景：弱网下 App 重试答题提交，避免重复计分（BE-04 F20）。

实现为纯 ASGI 中间件，命中缓存直接回放响应体。

策略：
- 只处理 POST / PUT / PATCH。
- **只缓存 HTTP 200 的响应** —— 业务失败（非零 code）与 5xx 允许客户端重试。
- 内存存储 + TTL 5 分钟。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Awaitable, Callable

IDEMPOTENCY_HEADER = "idempotency-key"
DEFAULT_TTL_SECONDS = 300  # 5 分钟

_lock = threading.RLock()
# cache_key -> (expire_at, status, body, headers)
_store: dict[str, tuple[float, int, bytes, list]] = {}


def _purge_expired() -> None:
    now = time.time()
    for k in [k for k, v in _store.items() if v[0] <= now]:
        _store.pop(k, None)


def _find_header(scope: dict, name: bytes) -> str:
    for k, v in scope.get("headers") or []:
        if k == name:
            return v.decode("latin-1").strip()
    return ""


class IdempotencyMiddleware:
    """纯 ASGI 中间件。"""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.app = app
        self.ttl = ttl_seconds

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        key = _find_header(scope, b"idempotency-key")
        if not key:
            await self.app(scope, receive, send)
            return

        cache_key = f"{scope['method']}:{scope['path']}:{key}"

        with _lock:
            _purge_expired()
            cached = _store.get(cache_key)

        if cached is not None:
            _, status, body, headers = cached
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send({"type": "http.response.body", "body": body})
            return

        captured: dict[str, Any] = {"status": 200, "body": b"", "headers": []}

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                captured["status"] = message.get("status", 200)
                captured["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                captured["body"] += message.get("body", b"")
            await send(message)

        await self.app(scope, receive, send_wrapper)

        if captured["status"] == 200 and captured["body"]:
            with _lock:
                _store[cache_key] = (
                    time.time() + self.ttl,
                    captured["status"],
                    captured["body"],
                    captured["headers"],
                )


def clear_all() -> None:
    """清空缓存（测试用）。"""
    with _lock:
        _store.clear()
