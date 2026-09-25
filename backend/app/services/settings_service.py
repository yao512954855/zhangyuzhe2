"""SettingsService —— 契约《01-共享契约冻结》§7.5 / 10a F13。

**全项目唯一读取 `settings` 表的入口。**
禁止清单第 3 条明确：任何包不得直接 `SELECT value FROM settings`，一律走这里。

设计：
- 首次读取把整表载入**内存缓存**，之后读操作零 SQL 开销（首页/热路径友好）。
- `set()` 写库 + 在同一把锁内刷新缓存，避免缓存与库不一致。
- 类型严格按 `value_type` 字段解析（`string` / `int` / `bool` / `json`）。
- 新增配置项必须走迁移脚本插入（契约 §9.3），本服务只负责读与改值。
"""

from __future__ import annotations

import json
import threading
from typing import Any

from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.logging import get_logger

logger = get_logger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on", "y", "t"}


class SettingsService:
    """系统配置读写。全部方法为类方法，调用方式 `SettingsService().get_int(...)`。"""

    _cache: dict[str, tuple[str, str]] | None = None  # key -> (value, value_type)
    _lock = threading.RLock()

    # ------------------------------------------------------------------ 内部

    @classmethod
    def _ensure_loaded(cls, force: bool = False) -> dict[str, tuple[str, str]]:
        with cls._lock:
            if cls._cache is None or force:
                with read_conn() as conn:
                    rows = conn.execute(
                        "SELECT key, value, value_type FROM settings"
                    ).fetchall()
                cls._cache = {r["key"]: (r["value"], r["value_type"]) for r in rows}
                logger.debug("settings 缓存已加载，共 %d 项", len(cls._cache))
            return cls._cache

    @classmethod
    def _raw(cls, key: str) -> tuple[str, str] | None:
        return cls._ensure_loaded().get(key)

    @classmethod
    def refresh(cls) -> None:
        """强制重载缓存（多进程/手工改库后调用）。"""
        cls._ensure_loaded(force=True)

    @classmethod
    def _infer_type(cls, value: Any) -> str:
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, (dict, list)):
            return "json"
        return "string"

    @classmethod
    def _serialize(cls, value: Any, value_type: str) -> str:
        if value_type == "bool":
            return "1" if value else "0"
        if value_type == "json":
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    # ------------------------------------------------------------------ 读

    @classmethod
    def get_str(cls, key: str, default: str = "") -> str:
        raw = cls._raw(key)
        if raw is None:
            return default
        return raw[0]

    @classmethod
    def get_int(cls, key: str, default: int = 0) -> int:
        raw = cls._raw(key)
        if raw is None:
            return default
        try:
            return int(str(raw[0]).strip())
        except (TypeError, ValueError):
            logger.warning("settings[%s] 不是合法整数，回退默认值 %s", key, default)
            return default

    @classmethod
    def get_bool(cls, key: str, default: bool = False) -> bool:
        raw = cls._raw(key)
        if raw is None:
            return default
        return str(raw[0]).strip().lower() in _TRUE_VALUES

    @classmethod
    def get_json(cls, key: str, default: Any = None) -> Any:
        raw = cls._raw(key)
        if raw is None:
            return default
        try:
            return json.loads(raw[0])
        except (TypeError, ValueError):
            logger.warning("settings[%s] 不是合法 JSON，回退默认值", key)
            return default

    @classmethod
    def get_all(cls) -> dict[str, Any]:
        """返回全部配置（按 value_type 解析后的字典），供管理端展示。"""
        data = cls._ensure_loaded()
        out: dict[str, Any] = {}
        for key, (value, vtype) in data.items():
            if vtype == "int":
                try:
                    out[key] = int(value)
                except ValueError:
                    out[key] = value
            elif vtype == "bool":
                out[key] = str(value).strip().lower() in _TRUE_VALUES
            elif vtype == "json":
                try:
                    out[key] = json.loads(value)
                except ValueError:
                    out[key] = value
            else:
                out[key] = value
        return out

    @classmethod
    def describe(cls) -> list[dict[str, Any]]:
        """带 value_type 与 description 的完整列表（管理端配置页用）。"""
        with read_conn() as conn:
            rows = conn.execute(
                """
                SELECT key, value, value_type, description, updated_at
                FROM settings
                ORDER BY key
                """
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ 写

    @classmethod
    def set(cls, key: str, value: Any) -> None:
        """写库并刷新缓存。

        保持原有的 `value_type`（只改值不改类型）；键不存在时按 Python 类型推断后插入。
        """
        with cls._lock:
            with write_tx() as conn:
                row = conn.execute(
                    "SELECT value_type FROM settings WHERE key = ?", (key,)
                ).fetchone()
                value_type = row["value_type"] if row else cls._infer_type(value)
                text = cls._serialize(value, value_type)

                cur = conn.execute(
                    "UPDATE settings SET value = ?, updated_at = ? WHERE key = ?",
                    (text, utc_now_iso(), key),
                )
                if cur.rowcount == 0:
                    conn.execute(
                        """
                        INSERT INTO settings (key, value, value_type, description, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (key, text, value_type, None, utc_now_iso()),
                    )

            if cls._cache is not None:
                cls._cache[key] = (text, value_type)
            logger.info("settings 更新：%s = %s (%s)", key, text, value_type)
