"""`preview_token` 存储 —— 契约《01-共享契约冻结》§14.1 第 4 条 / 10a F15。

**BE-02（学生名单导入）与 BE-03（内容导入）共用，不得各写一套。**

两步式导入的第一步（preview）把解析结果暂存起来，返回一个 token；
第二步（commit）凭 token 取回。默认有效期 **30 分钟**。

实现：内存字典 + 落盘 JSON（进程重启不丢）+ 过期自动清理。
并发安全：全部操作走同一把 `threading.RLock`。
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from app.core.config import BASE_DIR
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TTL_SECONDS = 1800  # 30 分钟

_STORE_PATH: Path = BASE_DIR / "data" / "preview_store.json"
_lock = threading.RLock()
_cache: dict[str, dict[str, Any]] | None = None


def _load() -> dict[str, dict[str, Any]]:
    """读取存储（带缓存）。文件损坏时降级为空存储，不影响服务启动。"""
    global _cache
    if _cache is not None:
        return _cache

    data: dict[str, dict[str, Any]] = {}
    if _STORE_PATH.exists():
        try:
            raw = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception as exc:  # noqa: BLE001 - 损坏则重建
            logger.warning("preview_store 文件损坏，已重置：%s", exc)

    _cache = data
    return _cache


def _save() -> None:
    """落盘。写失败不抛异常（内存态仍可用），只记日志。"""
    data = _cache or {}
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STORE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_STORE_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("preview_store 落盘失败：%s", exc)


def _purge_expired(data: dict[str, dict[str, Any]]) -> int:
    now = time.time()
    expired = [k for k, v in data.items() if float(v.get("expire_at", 0)) <= now]
    for k in expired:
        data.pop(k, None)
    return len(expired)


def put(payload: dict[str, Any], ttl: int = DEFAULT_TTL_SECONDS) -> str:
    """暂存 payload，返回 `preview_token`。"""
    with _lock:
        data = _load()
        _purge_expired(data)
        token = secrets.token_urlsafe(24)
        data[token] = {
            "payload": payload,
            "created_at": time.time(),
            "expire_at": time.time() + ttl,
        }
        _save()
        return token


def get(token: str) -> dict[str, Any] | None:
    """读取 payload；不存在或已过期返回 None。"""
    if not token:
        return None
    with _lock:
        data = _load()
        entry = data.get(token)
        if entry is None:
            return None
        if float(entry.get("expire_at", 0)) <= time.time():
            data.pop(token, None)
            _save()
            return None
        return entry.get("payload")


def pop(token: str) -> dict[str, Any] | None:
    """取出并删除（commit 成功后调用，避免重复提交）。"""
    if not token:
        return None
    with _lock:
        data = _load()
        entry = data.pop(token, None)
        _save()
        if entry is None:
            return None
        if float(entry.get("expire_at", 0)) <= time.time():
            return None
        return entry.get("payload")


def cleanup() -> int:
    """清理全部过期条目，返回清理数量。"""
    with _lock:
        data = _load()
        n = _purge_expired(data)
        if n:
            _save()
        return n


def clear_all() -> None:
    """清空存储（测试用）。"""
    with _lock:
        global _cache
        _cache = {}
        _save()
