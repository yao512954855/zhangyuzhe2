"""限流 —— 契约《01-共享契约冻结》§5.4 / 10a F11。

契约规定的四档：

| 维度 | 限制 |
|---|---|
| 单账号登录 | 5 次/分钟 |
| 单账号写接口 | 60 次/分钟 |
| 单账号 WebSocket | 1 路并发（新连接踢旧） |
| 全局 WebSocket | 60 路并发，队列 120，超出即拒 |

本模块提供两块能力：
1. `RateLimitMiddleware`：HTTP 层限流（登录 + 写请求），超限返回 `10005`。
2. `hit()` 计数原语：供业务层复用 —— 例如 BE-02 的「连续 6 次错误密码锁定」
   （10a 明确要求该计数放内存、不落库）与 BE-06 的 WebSocket 并发控制。

计数为**内存滑动窗口**：进程重启后计数清零，对登录锁定这类短周期场景可接受。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Awaitable, Callable

from app.core.errors import DEFAULT_MESSAGES, ErrorCode
from app.core.response import fail

# ---- 契约 §5.4 的档位 ----
LOGIN_LIMIT = 5
LOGIN_PATHS = ("/api/auth/login",)
WRITE_LIMIT = 60
WINDOW_SECONDS = 60

_lock = threading.RLock()
_hits: dict[str, list[float]] = {}

# 内存保护：键数量超过该值时做一次全量清理
_MAX_KEYS = 20000


def hit(key: str, limit: int, window_seconds: int = WINDOW_SECONDS) -> tuple[bool, int]:
    """记一次访问并判断是否超限。

    Returns:
        `(allowed, remaining)` —— `remaining` 为剩余额度，被拒时为 0。
    """
    now = time.time()
    cutoff = now - window_seconds

    with _lock:
        if len(_hits) > _MAX_KEYS:
            for k in [k for k, v in _hits.items() if not v or v[-1] <= cutoff]:
                _hits.pop(k, None)

        arr = _hits.setdefault(key, [])
        while arr and arr[0] <= cutoff:
            arr.pop(0)

        if len(arr) >= limit:
            return False, 0

        arr.append(now)
        return True, limit - len(arr)


def remaining(key: str, limit: int, window_seconds: int = WINDOW_SECONDS) -> int:
    """查询剩余额度（不计数）。"""
    cutoff = time.time() - window_seconds
    with _lock:
        arr = _hits.get(key) or []
        used = sum(1 for t in arr if t > cutoff)
    return max(0, limit - used)


def reset(key: str) -> None:
    """清零某个 key 的计数 —— **仅供测试基建使用**，生产代码不调用。

    契约 §5.4 规定登录**每次尝试都计数**（不看成功与否），因此**刻意不在
    "登录成功后"清零** —— 那会让限流形同虚设（偶尔成功一次即可无限重试）。
    见 `docs/dev/02` §八 #77。
    """
    with _lock:
        _hits.pop(key, None)


def clear_all() -> None:
    with _lock:
        _hits.clear()


def _find_header(scope: dict, name: bytes) -> str:
    for k, v in scope.get("headers") or []:
        if k == name:
            return v.decode("latin-1").strip()
    return ""


def _identity(scope: dict) -> str:
    """限流主体标识：优先账号（Authorization 头哈希），退化到 IP。"""
    auth = _find_header(scope, b"authorization")
    if auth:
        digest = hashlib.md5(auth.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
        return f"u:{digest}"
    client = scope.get("client") or ("unknown", 0)
    return f"ip:{client[0]}"


class RateLimitMiddleware:
    """HTTP 限流中间件（纯 ASGI）。"""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "")

        # 只拦写方法与登录接口，GET 不限流
        is_login = method == "POST" and path in LOGIN_PATHS
        is_write = method in ("POST", "PUT", "PATCH", "DELETE")

        if is_login or is_write:
            ident = _identity(scope)
            if is_login:
                key, limit = f"login:{ident}", LOGIN_LIMIT
            else:
                key, limit = f"write:{ident}", WRITE_LIMIT

            allowed, _ = hit(key, limit)
            if not allowed:
                body = json.dumps(
                    fail(ErrorCode.TOO_MANY_REQUESTS, DEFAULT_MESSAGES[ErrorCode.TOO_MANY_REQUESTS]),
                    ensure_ascii=False,
                ).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,  # 契约 §5.1：业务失败一律 HTTP 200
                        "headers": [
                            (b"content-type", b"application/json; charset=utf-8"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return

        await self.app(scope, receive, send)
