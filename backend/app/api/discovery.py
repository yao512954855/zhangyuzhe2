"""服务发现与运维接口 —— WP-BE-08 F1 / F3 / F7 / F12。

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `GET  /api/ping` | 免 | 极轻量探活（**不查库**，10b F1 要求 < 50 ms） |
| `GET  /api/server/info` | admin | 网卡地址 + 推荐地址 + `qr_content` + 降级告警 + 备份概览 |
| `POST /api/maintenance/backup` | admin | 手动备份（立即执行 + 返回校验结果，F7） |

### ⚠️ 本模块**接替** `main.py` 里的 `/api/ping` 桩
BE-01 在 `main.py` 留了一个同名桩，并在 docstring 里写明「BE-08 实施时必须删掉这个桩」。
原因是自动路由在 `main.py` **之后**注册，而 **同名路由先注册者生效** ——
不删桩的话，这里写的 `/api/ping` 永远不生效（前序包已把它记为 #41 缺陷）。
本包已删除该桩（见交付说明的「跨包改动」）。

### 为什么 `/api/ping` 不能查库
它是 App **每次启动**都要打的第一个请求（服务发现失败后还会重试 8 次），
而 U1 要求响应 < 50 ms。任何一次 `SELECT`（哪怕是 `PRAGMA`）在机械硬盘 + WAL 冷启动下
都可能摸到几十毫秒。这里只读**进程内常量与内存状态**。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app import __version__
from app.core.deps import CurrentUser, require_admin
from app.core.db import utc_now_iso
from app.core.errors import ErrorCode
from app.core.response import ok
from app.services import backup_service
from app.services.discovery import DISCOVERY_PORT, http_port, server_name, state
from app.services.discovery.udp_responder import responder

router = APIRouter(tags=["system"])


# --------------------------------------------------------------------------- 探活


@router.get("/api/ping")
async def ping() -> dict[str, Any]:
    """极简探活（免鉴权）。

    返回体按 **接口设计 §3.1** 定型：`service` / `version` / `server_name` / `time`。
    学生端 `ServerInfo.fromJson`（`student-app/lib/core/net/api_client.dart`）
    就是照这 4 个字段解析的 —— **改字段名会让 App 认不出服务器**。
    """
    return ok(
        {
            "service": "bjsys",
            "version": __version__,
            "server_name": server_name(),
            "time": utc_now_iso(),
        }
    )


# --------------------------------------------------------------------------- 服务信息


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "y", "on"}


@router.get("/api/server/info")
async def server_info(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """服务地址与运行状态（F3 / F12）。

    `?refresh=1` 强制重新探测网卡（会起 PowerShell，约 0.3–1 s）。
    默认走 30 s 缓存 —— 工作台首页会定期拉这个接口，不想每次刷页面都起进程。
    """
    import time

    force = _truthy(request.query_params.get("refresh"))
    snapshot = state.snapshot(now_ts=time.monotonic(), force_probe=force)

    return ok(
        {
            **snapshot,
            "service": "bjsys",
            "version": __version__,
            "app_port": http_port(),
            "discovery_port": DISCOVERY_PORT,
            "broadcast_enabled": responder.running,
            "broadcast_counters": responder.counters,
            "degraded": state.degraded,
            "backup": backup_service.summary(),
        }
    )


# --------------------------------------------------------------------------- 维护


@router.post("/api/maintenance/backup")
async def manual_backup(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """手动备份（F7）。**失败时不是静默 200** —— 用非零 code 明确报错。

    `VACUUM INTO` 在库较大时会花几秒，因此把阻塞调用放线程池，
    避免卡住事件循环（同一时间其他学生的答题请求不该被备份拖慢）。
    """
    import asyncio

    ip = request.client.host if request.client else None
    outcome = await asyncio.to_thread(
        backup_service.create_backup,
        daily=False,
        operator_id=admin.id,
        ip=ip,
    )
    if not outcome.ok:
        # 备份失败是**真实故障**，必须让管理端看见，不能用 code=0 糊过去
        from app.core.errors import BizError

        raise BizError(
            ErrorCode.INTERNAL_ERROR,
            f"备份失败：{outcome.error}",
            data=outcome.to_dict(),
        )
    return ok(outcome.to_dict())


# --------------------------------------------------------------------------- 供 main.py 复用


def degraded_reasons() -> list[dict[str, Any]]:
    """降级告警列表（10b F12 / U9 要求 `/api/health` 也能查到）。

    放在这里而不是 `main.py`，是为了让告警**登记方**（本包）与**读取方**同源。
    """
    return state.degraded_reasons()
