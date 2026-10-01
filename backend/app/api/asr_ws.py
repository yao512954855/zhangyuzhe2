"""ASR WebSocket 端点 `/ws/asr` —— WP-BE-06（契约 §6 / 接口设计 §10）。

**本文件只做「握手 + 收发帧」的搬运**；协议状态机全在
`app/services/asr/session_manager.py`（`ASRSession`），这样绝大多数验收用例
可以用「假 send + MockProvider」在纯单元测试里跑完。

三件这里必须做的事（放到 service 层反而绕）：

1. **握手校验 JWT**：失败 → `close(code=4001)`。
   注意必须先 `accept()` 再 `close(4001)`：不 accept 直接 close，Starlette 只能回 HTTP 403，
   客户端拿不到 4001 这个码（U1 断言的正是这个码）。
2. **一个后台 pump 任务**：Provider 的回调是同步的（契约 §7.4），
   只往 `session.outbox` 里 append；pump 负责顺序发出。
3. **用「后台 reader 任务 + 队列」而不是 `asyncio.wait_for(ws.receive())`**：
   后者在超时那一刻会**丢掉刚好到达的消息**，心跳/流控判断就会偶发丢帧。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket

from app.core.deps import resolve_user_from_token
from app.core.errors import ErrorCode
from app.core.logging import get_logger
from app.services.asr.session_manager import (
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_QUEUE_SIZE,
    DEFAULT_QUEUE_WAIT_MS,
    ASRSession,
    GlobalGate,
    SessionLimitError,
    SessionRegistry,
)
from app.services.asr.factory import load_asr_raw

logger = get_logger(__name__)

router = APIRouter(tags=["asr"])

#: 认证失败关闭码（契约 §6.1）
WS_CLOSE_UNAUTHORIZED = 4001
#: 「稍后再试」关闭码（IANA 1013 Try Again Later）—— 超出全局并发时用
WS_CLOSE_TRY_LATER = 1013
#: 同账号新连接踢掉旧连接时的关闭码
WS_CLOSE_KICKED = 4002

#: pump 周期（秒）：Provider 回调入队后最迟这么久发出
PUMP_INTERVAL_S = 0.02

_gate: GlobalGate | None = None


def get_gate() -> GlobalGate:
    """全局并发闸门（按 `asr.yaml` 的 limits 建一次；测试可 `reset_gate()`）。"""
    global _gate
    if _gate is None:
        raw = load_asr_raw()
        limits = raw.get("limits") or {}
        _gate = GlobalGate(
            max_connections=int(limits.get("max_connections") or DEFAULT_MAX_CONNECTIONS),
            queue_size=int(limits.get("queue_size") or DEFAULT_QUEUE_SIZE),
            wait_ms=int(limits.get("queue_wait_ms") or DEFAULT_QUEUE_WAIT_MS),
        )
    return _gate


def reset_gate() -> None:
    """测试用：重建闸门与注册表。"""
    global _gate
    _gate = None
    SessionRegistry.clear()


@router.websocket("/ws/asr")
async def asr_ws(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    last_answer_id: int | None = Query(default=None),
) -> None:
    """语音识别长连接。"""
    user = resolve_user_from_token(token)
    if user is None:
        await websocket.accept()
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="认证失败")
        return

    await websocket.accept()
    gate = get_gate()

    async def _send(msg: dict[str, Any]) -> None:
        # 发送留痕：2026-09-28 真机实测「verdict 已判分但 App 卡识别中」，
        # 发送环节零日志无从定位。info 级记 type + 关键字段，量小不刷屏。
        logger.info(
            "ASR → App user=%s type=%s%s",
            user.id,
            msg.get("type"),
            (
                f" answer_id={msg.get('answer_id')} is_correct={msg.get('is_correct')}"
                if msg.get("type") == "verdict"
                else ""
            ),
        )
        await websocket.send_text(json.dumps(msg, ensure_ascii=False))

    session = ASRSession(user=user, send=_send)

    # ---- 全局并发（F12 / 契约 §11）----
    try:
        await gate.acquire()
    except SessionLimitError as exc:
        await _send(
            ASRSession._error_payload(
                ErrorCode.ASR_UNAVAILABLE, exc.message, action="abort"
            )
        )
        await websocket.close(code=WS_CLOSE_TRY_LATER, reason="busy")
        return

    reader: asyncio.Task[None] | None = None
    pump: asyncio.Task[None] | None = None
    inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def _read_loop() -> None:
        while True:
            msg = await websocket.receive()
            await inbox.put(msg)

    async def _pump_loop() -> None:
        while True:
            try:
                await session.flush()
            except Exception as exc:  # noqa: BLE001 - 发送失败=连接已坏
                # ⚠️ 必须是 warning 且主动终止会话：之前是 debug 静默退出，
                #    verdict 永远发不出去、会话吊到客户端断开才收尾，无从排障。
                logger.warning("pump 发送失败，终止会话 user=%s：%s", user.id, exc)
                session.kicked = True
                return
            if session.kicked:
                return
            await asyncio.sleep(PUMP_INTERVAL_S)

    try:
        SessionRegistry.register(session)
        # ---- 断线重连补发 verdict（F13 / U12）----
        if last_answer_id is not None:
            await session.replay(last_answer_id)
            await session.flush()

        reader = asyncio.create_task(_read_loop())
        pump = asyncio.create_task(_pump_loop())

        while True:
            try:
                msg = await asyncio.wait_for(inbox.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if session.kicked:
                    break
                if session.heartbeat_expired:      # F14：60s 无消息
                    logger.info("WS 心跳超时，关闭连接 user=%s", user.id)
                    break
                if session.session_expired:        # 单次会话时长上限
                    logger.info("WS 会话超时，关闭连接 user=%s", user.id)
                    break
                continue

            mtype = msg.get("type")
            if mtype == "websocket.disconnect":
                break
            if mtype != "websocket.receive":
                continue
            raw_text = msg.get("text")
            raw_bytes = msg.get("bytes")
            if raw_text is not None:
                await session.handle_text(raw_text)
            elif raw_bytes is not None:
                await session.handle_bytes(raw_bytes)
    except Exception:  # noqa: BLE001 - 未预期异常不应留下泄漏的并发位
        logger.error("WS 会话异常终止 user=%s", user.id, exc_info=True)
    finally:
        for task in (reader, pump):
            if task is not None:
                task.cancel()
        for task in (reader, pump):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        await session.flush_quietly()
        await session.close()
        await gate.release()
        code = WS_CLOSE_KICKED if session.kicked else 1000
        try:
            await websocket.close(code=code)
        except Exception:  # noqa: BLE001
            pass
        logger.info("WS 会话结束 user=%s code=%s", user.id, code)
