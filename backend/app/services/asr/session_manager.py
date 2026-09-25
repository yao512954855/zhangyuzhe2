"""WebSocket 会话与并发控制 —— WP-BE-06 F8/F9/F10/F12/F13/F14/F15/F17/F19。

## 为什么把会话逻辑放在 service 层而不是 `api/asr_ws.py`

`api/asr_ws.py` 只做「握手 + 收发帧」的搬运；**状态机全部在这里**，
并且出站消息走**注入的 `send` 协程**而不是直接持有 WebSocket 对象 ——
这样 U1~U16 里绝大多数用例可以用「假 send + MockProvider」在纯单元测试里跑完，
不需要起真实服务器（真机烟测再覆盖一次端到端）。

## 出站消息的收集方式

Provider 的回调是**同步**签名（契约 §7.4：`Callable[[str, int], None]`），
而发送是异步的。这里用 `outbox` 列表 + `flush()`：
回调只管 append（同步、不会阻塞），发送方按需 flush（顺序天然保持）。
端点侧由一个 pump 任务周期性 flush；测试里直接 `await session.flush()` 拿确定性。

## 单账号 1 路并发（F12）怎么实现

`SessionRegistry.register()` 会把**旧会话标记为 kicked 并调用它的 `closer`**，
新会话照常建立。旧连接由端点侧真正关闭（`api/asr_ws.py` 收到 `kicked` 标记后关 WS）。
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from typing import Any, Awaitable, Callable

from app.core.db import read_conn
from app.core.errors import ErrorCode
from app.core.logging import get_logger
from app.services.asr.arbitrator import ArbitratorProvider
from app.services.asr.base import (
    ASRError,
    ASRProvider,
    ASRTaskConfig,
    BYTES_PER_MS,
    DEFAULT_CHUNK_MS,
    SAMPLE_RATE,
)
from app.services.asr.factory import ASRRouter, load_asr_raw
from app.services.asr.hotwords import build_hotwords
from app.services.asr_log_service import ASRLogService

logger = get_logger(__name__)

SendFn = Callable[[dict[str, Any]], Awaitable[None]]

# ---- 默认限额（可被 asr.yaml 的 limits 覆盖）----
DEFAULT_MAX_CONNECTIONS = 60     # 契约 §11：全局 WebSocket 上限
DEFAULT_QUEUE_SIZE = 120         # 契约 §11：队列长度
DEFAULT_QUEUE_WAIT_MS = 5000     # 契约 §11：排队超 5 秒返回 50001
DEFAULT_HEARTBEAT_TIMEOUT_S = 60  # 契约 §6.2 / F14：60s 未收到消息主动关闭
DEFAULT_MAX_SESSION_S = 300      # 契约 §9.2 limits.max_session_seconds
#: 为仲裁在内存里保留的最近音频时长（**只为仲裁**，F18 仍然「不落盘」）
DEFAULT_ARBITRATION_BUFFER_MS = 15000

#: 连续失败多少次 → 整个会话切键盘模式（F15 / 契约 §6.5）
KEYBOARD_MODE_THRESHOLD = 3


class SessionLimitError(ASRError):
    """并发/排队超限（`50001`）。单独成类便于端点区分「连接被拒」与「识别失败」。"""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.ASR_UNAVAILABLE, message, status="error", action="abort")


class GlobalGate:
    """全局并发闸门：60 路并发 + 120 排队，排队超时即拒（F12 / 契约 §11）。

    用「计数 + 条件变量」而不是信号量：排队超时需要能**超时放弃**，
    而 `Semaphore.acquire()` 的超时语义在这里表达不清（放弃后计数要还原）。
    """

    def __init__(
        self,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        wait_ms: int = DEFAULT_QUEUE_WAIT_MS,
    ) -> None:
        self.max_connections = int(max_connections)
        self.queue_size = int(queue_size)
        self.wait_s = int(wait_ms) / 1000.0
        self._active = 0
        self._waiting = 0
        self._cv = asyncio.Condition()

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return self._waiting

    async def acquire(self) -> None:
        """占用一个并发位；排队超时或队列已满 → `50001`。"""
        async with self._cv:
            if self._active < self.max_connections:
                self._active += 1
                return
            if self._waiting >= self.queue_size:
                raise SessionLimitError("当前使用人数较多，请稍后再试")
            self._waiting += 1
            deadline = time.monotonic() + self.wait_s
            try:
                while self._active >= self.max_connections:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SessionLimitError("当前使用人数较多，请稍后再试")
                    try:
                        await asyncio.wait_for(self._cv.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        raise SessionLimitError("当前使用人数较多，请稍后再试") from None
                self._active += 1
            finally:
                self._waiting -= 1

    async def release(self) -> None:
        async with self._cv:
            if self._active > 0:
                self._active -= 1
            self._cv.notify_all()


class SessionRegistry:
    """会话注册表：单账号 1 路并发（F12/U11）+ 判定结果短期缓存（F13/U12）。"""

    #: 判定结果缓存条数（够断线重连补发即可，不必无限增长）
    VERDICT_CACHE_SIZE = 500

    _sessions: dict[int, "ASRSession"] = {}
    _verdicts: "OrderedDict[int, dict[str, Any]]" = OrderedDict()
    _lock = asyncio.Lock()

    # ---- 并发位 ----

    @classmethod
    def register(cls, session: "ASRSession") -> None:
        """登记会话；同账号已有会话则**标记旧会话被踢**（U11）。"""
        user_id = session.user_id
        old = cls._sessions.get(user_id)
        if old is not None and old is not session:
            old.kicked = True
            try:
                old.request_close("同账号新连接已建立")
            except Exception:  # noqa: BLE001
                pass
        cls._sessions[user_id] = session

    @classmethod
    def unregister(cls, session: "ASRSession") -> None:
        if cls._sessions.get(session.user_id) is session:
            cls._sessions.pop(session.user_id, None)

    @classmethod
    def active_count(cls) -> int:
        return len(cls._sessions)

    # ---- 判定结果缓存（断线重连补发 verdict） ----

    @classmethod
    def cache_verdict(cls, answer_id: int | None, payload: dict[str, Any]) -> None:
        if answer_id is None:
            return
        cls._verdicts[int(answer_id)] = payload
        while len(cls._verdicts) > cls.VERDICT_CACHE_SIZE:
            cls._verdicts.popitem(last=False)

    @classmethod
    def get_verdict(cls, answer_id: int | None) -> dict[str, Any] | None:
        if answer_id is None:
            return None
        return cls._verdicts.get(int(answer_id))

    @classmethod
    def clear(cls) -> None:
        """测试用：清空注册表与缓存。"""
        cls._sessions.clear()
        cls._verdicts.clear()


class ASRSession:
    """一次 WebSocket 连接的状态机。

    Args:
        user: `app.core.deps.CurrentUser`（需要 `id` / `role`）。
        send: 出站发送协程（真机是 `websocket.send_text`；测试里是收集器）。
        raw_config: `asr.yaml` 原始 dict；不传则自己读。
        router: 主备编排；不传则按 `raw_config` 建。
        arbitrator: 仲裁器；不传则按 `raw_config` 建。
    """

    def __init__(
        self,
        *,
        user: Any,
        send: SendFn,
        raw_config: dict[str, Any] | None = None,
        router: ASRRouter | None = None,
        arbitrator: ArbitratorProvider | None = None,
    ) -> None:
        self.user = user
        self.user_id: int = int(getattr(user, "id", 0))
        self._send = send
        self.raw = raw_config if raw_config is not None else load_asr_raw()

        limits = self.raw.get("limits") or {}
        self.chunk_ms = int(limits.get("chunk_ms") or DEFAULT_CHUNK_MS)
        self.max_session_s = int(
            limits.get("max_session_seconds") or DEFAULT_MAX_SESSION_S
        )
        self.heartbeat_timeout_s = int(
            limits.get("heartbeat_timeout_s") or DEFAULT_HEARTBEAT_TIMEOUT_S
        )
        self.lang_map: dict[str, str] = dict(
            self.raw.get("lang_map")
            or {"word": "en", "spell": "zh", "zh_answer": "zh", "recite": "zh"}
        )
        hw = self.raw.get("hotwords") or {}
        self._hotwords_enabled = bool(hw.get("enabled"))
        self._hotword_sources = list(hw.get("sources") or [])

        self.router = router if router is not None else ASRRouter(self.raw)
        self.arbitrator = (
            arbitrator if arbitrator is not None else ArbitratorProvider(self.raw)
        )

        # ---- 出站 ----
        self.outbox: list[dict[str, Any]] = []

        # ---- 运行态 ----
        self.cfg: ASRTaskConfig | None = None
        self.provider: ASRProvider | None = None
        self.last_activity = time.monotonic()
        self.started_at: float | None = None
        self.kicked = False
        self.closed = False

        self._task_started_at: float | None = None
        self._final: tuple[str, float | None, int] | None = None
        self._last_partial = ""
        self._consecutive_errors = 0
        self._keyboard_mode = False
        self._recitation_id: int | None = None
        self._answer_id: int | None = None
        #: 标准答案 —— 只进服务端判定，**绝不传给云厂商**（F10）
        self._expected: str | None = None
        self._close_reason: str | None = None
        # 仲裁所需音频：**只在内存**，且只留最近若干秒（F18：不落盘）
        self._pcm_window: deque[bytes] = deque()
        self._pcm_window_bytes = 0
        self._arb_buffer_ms = int(
            (self.raw.get("arbitrator") or {}).get("buffer_ms")
            or DEFAULT_ARBITRATION_BUFFER_MS
        )

    # ================================================================== 属性

    @property
    def keyboard_mode(self) -> bool:
        """整个会话是否已切键盘模式（F15：连续失败 3 次）。"""
        return self._keyboard_mode

    @property
    def running(self) -> bool:
        return self.provider is not None and not self.closed

    @property
    def session_expired(self) -> bool:
        """超过 `max_session_seconds`（契约 §9.2）。"""
        if self.started_at is None:
            return False
        return (time.monotonic() - self.started_at) > self.max_session_s

    @property
    def heartbeat_expired(self) -> bool:
        return (time.monotonic() - self.last_activity) > self.heartbeat_timeout_s

    def request_close(self, reason: str) -> None:
        self._close_reason = reason

    @property
    def close_reason(self) -> str | None:
        return self._close_reason

    # ================================================================== 入站

    async def handle_text(self, text: str) -> None:
        """处理一个 JSON 控制帧。"""
        self.last_activity = time.monotonic()
        try:
            import json

            msg = json.loads(text)
        except Exception:  # noqa: BLE001
            await self._error(ErrorCode.PARAM_INVALID, "控制帧不是合法 JSON", action="abort")
            return
        if not isinstance(msg, dict):
            await self._error(ErrorCode.PARAM_INVALID, "控制帧必须是对象", action="abort")
            return

        mtype = str(msg.get("type") or "")
        if mtype == "start":
            await self._on_start(msg)
        elif mtype == "end":
            await self._on_end(msg)
        elif mtype == "cancel":
            await self._on_cancel()
        elif mtype == "ping":
            self.outbox.append({"type": "pong", "ts_ms": self._now_ms()})
        else:
            await self._error(ErrorCode.PARAM_INVALID, f"未知消息类型：{mtype}", action="abort")

    async def handle_bytes(self, data: bytes) -> None:
        """处理一个二进制音频帧。"""
        self.last_activity = time.monotonic()
        if self.provider is None:
            await self._error(ErrorCode.PARAM_INVALID, "尚未 start 就发送音频", action="abort")
            return
        if self._keyboard_mode:
            return
        self._remember_audio(data)
        try:
            await self.provider.push_audio(data)
        except ASRError as exc:
            await self._fail(exc)

    async def replay(self, last_answer_id: int | None) -> None:
        """断线重连补发 `verdict`（F13 / U12）。"""
        cached = SessionRegistry.get_verdict(last_answer_id)
        if cached is not None:
            self.outbox.append(cached)

    async def flush(self) -> None:
        """把 `outbox` 里的消息顺序发出。"""
        while self.outbox:
            msg = self.outbox.pop(0)
            await self._send(msg)

    async def flush_quietly(self) -> None:
        """尽力发出剩余消息，**不抛异常**（连接已断时用，收尾阶段调）。"""
        try:
            await self.flush()
        except Exception as exc:  # noqa: BLE001
            logger.debug("收尾 flush 失败（已忽略）：%s", exc)
        self.outbox.clear()

    async def close(self) -> None:
        """释放资源（幂等）。"""
        if self.closed:
            return
        self.closed = True
        provider, self.provider = self.provider, None
        if provider is not None:
            try:
                await provider.abort()
            except Exception as exc:  # noqa: BLE001
                logger.debug("关闭 Provider 时忽略异常：%s", exc)
        self._pcm_window.clear()
        self._pcm_window_bytes = 0
        SessionRegistry.unregister(self)

    # ================================================================== 消息处理

    async def _on_start(self, msg: dict[str, Any]) -> None:
        if self._keyboard_mode:
            # F15：已切键盘模式 → 不再尝试云（U8）
            self.outbox.append(
                self._error_payload(
                    ErrorCode.ASR_UNAVAILABLE,
                    "本场已切换为键盘输入，不再尝试语音识别",
                    action="keyboard",
                    fallback="keyboard",
                )
            )
            return

        task_type = str(msg.get("task_type") or "")
        if task_type not in self.lang_map:
            await self._error(
                ErrorCode.PARAM_INVALID,
                f"task_type 取值非法：{task_type!r}",
                action="abort",
            )
            return

        session_id = msg.get("session_id")
        if session_id is None:
            await self._error(ErrorCode.PARAM_INVALID, "缺少 session_id", action="abort")
            return

        # 语种由**服务端**按 task_type 决定，不信任客户端 lang_hint（契约 §6.3）
        lang = self.lang_map[task_type]

        recitation_id = msg.get("recitation_id")
        if task_type == "recite":
            rid = self._resolve_recitation(session_id, msg)
            if rid is None:
                await self._error(
                    ErrorCode.NOT_FOUND
                    if msg.get("recitation_id") is not None
                    else ErrorCode.PARAM_INVALID,
                    "背诵任务缺少有效的 recitation_id",
                    action="abort",
                )
                return
            self._recitation_id = rid

        # `expected` / `answer_id` 只在服务端留存（F10：绝不下发云厂商）
        self.set_expected(msg.get("expected"))
        self.set_answer_id(msg.get("answer_id"))

        cfg = ASRTaskConfig(
            session_id=int(session_id),
            task_type=task_type,              # type: ignore[arg-type]
            lang_hint=lang,                   # type: ignore[arg-type]
            hotwords=build_hotwords(
                msg.get("hotwords"),
                enabled=self._hotwords_enabled,
                sources=self._hotword_sources,
            ),
            sample_rate=SAMPLE_RATE,
            enable_intermediate=bool(msg.get("enable_intermediate", True)),
        )
        self.cfg = cfg
        self._final = None
        self._last_partial = ""
        self._task_started_at = time.monotonic()
        self._pcm_window.clear()
        self._pcm_window_bytes = 0
        if self.started_at is None:
            self.started_at = time.monotonic()

        try:
            provider = await self.router.start(cfg)
        except ASRError as exc:
            await self._fail(exc, log=True)
            return
        except Exception as exc:  # noqa: BLE001
            await self._fail(
                ASRError(ErrorCode.ASR_UNAVAILABLE, f"建连失败：{exc}", status="error")
            )
            return

        self.provider = provider
        provider.on_partial(self._on_partial)     # 同步回调 → 仅入 outbox
        provider.on_final(self._on_final)

        self.outbox.append(
            {
                "type": "started",
                "provider": self.router.used_provider,
                "sample_rate": SAMPLE_RATE,
                "chunk_ms": self.chunk_ms,
            }
        )

    async def _on_end(self, msg: dict[str, Any]) -> None:
        provider, self.provider = self.provider, None
        if provider is None:
            await self._error(ErrorCode.PARAM_INVALID, "尚未 start 就 end", action="abort")
            return

        try:
            await provider.close()          # 契约约定：close 会产出 final
        except ASRError as exc:
            await self._fail(exc, log=True, provider=provider)
            return
        except Exception as exc:  # noqa: BLE001
            await self._fail(
                ASRError(ErrorCode.ASR_UNAVAILABLE, f"识别失败：{exc}", status="error"),
                provider=provider,
            )
            return

        final = self._final
        if final is None:
            # Provider 没产 final（异常实现）→ 当作空结果处理，绝不静默
            await self._fail(
                ASRError(ErrorCode.ASR_EMPTY, "云端没有返回识别结果", status="empty",
                         action="retry"),
                provider=provider,
            )
            return

        text, conf, duration_ms = final
        text = (text or "").strip()

        # 空结果 → 50003 + retry，**不计错**（F17 / U6）
        if not text:
            arbitrated = await self._try_arbitrate("empty")
            if arbitrated:
                text = arbitrated
            else:
                self._log(provider, status="empty",
                          error_code=str(ErrorCode.ASR_EMPTY))
                self.outbox.append(
                    self._error_payload(
                        ErrorCode.ASR_EMPTY,
                        "没有听到声音",
                        action="retry",
                        fallback="keyboard" if not self.arbitrator.enabled else None,
                    )
                )
                return

        self.outbox.append(
            {
                "type": "final",
                "text": text,
                "confidence": conf,
                "duration_ms": duration_ms,
            }
        )
        await self._judge_and_emit(text, conf, provider)

    async def _on_cancel(self) -> None:
        provider, self.provider = self.provider, None
        if provider is None:
            return
        try:
            await provider.abort()      # 放弃：不产 final、不判分、不计错
        except Exception as exc:  # noqa: BLE001
            logger.debug("cancel 时忽略异常：%s", exc)

    # ================================================================== 判定（F9 / F10）

    async def _judge_and_emit(
        self, text: str, conf: float | None, provider: ASRProvider
    ) -> None:
        assert self.cfg is not None
        task_type = self.cfg.task_type
        expected = str(self._expected_text() or "")

        if task_type == "recite":
            payload = await asyncio.to_thread(self._recite_feed, text)
            if payload is None:
                return
            self.outbox.append(payload)
            self._log(provider, status="ok")
            return

        from app.services.judge import JudgeService

        try:
            if task_type == "spell":
                verdict = JudgeService.judge_spell(asr_text=text, target=expected)
            else:
                verdict = JudgeService.judge_text(answer=text, expected=expected)
        except Exception as exc:  # noqa: BLE001
            await self._fail(
                ASRError(ErrorCode.ASR_UNAVAILABLE, f"判定失败：{exc}", status="error"),
                provider=provider,
            )
            return

        match_type = str(verdict.match_type)

        # fuzzy → 先仲裁（F19 三条触发场景之一），仲裁能救则改判（**不改产品链路**）
        if match_type == "fuzzy":
            arbitrated = await self._try_arbitrate("fuzzy")
            if arbitrated:
                try:
                    if task_type == "spell":
                        verdict = JudgeService.judge_spell(
                            asr_text=arbitrated, target=expected,
                            asr_confidence=verdict.asr_confidence,
                        )
                    else:
                        verdict = JudgeService.judge_text(
                            answer=arbitrated, expected=expected,
                            asr_confidence=verdict.asr_confidence,
                        )
                    match_type = str(verdict.match_type)
                except Exception:  # noqa: BLE001 - 仲裁结果判分失败则保留原判
                    pass

        payload: dict[str, Any] = {
            "type": "verdict",
            "task_type": task_type,
            "is_correct": bool(verdict.is_correct),
            "match_type": match_type,
            # `expected` 只用于服务端判定；这里下发是**契约 §6.2 verdict 的字段要求**
            # （判定已经完成，下发不构成提前泄题）
            "expected": verdict.expected,
            "asr_text": text,
            "normalized": verdict.normalized,
            "answer_id": self._answer_id,
            "can_appeal": bool(verdict.can_appeal),
            "diff": [d.model_dump() for d in verdict.diff] if verdict.diff else None,
        }
        self.outbox.append(payload)
        SessionRegistry.cache_verdict(self._answer_id, payload)
        self._log(provider, status="ok")
        self.router.note_ok()

    def _expected_text(self) -> str | None:
        """标准答案只可能在 `start` 里给过，这里从会话态取。"""
        return getattr(self, "_expected", None)

    def _recite_feed(self, text: str) -> dict[str, Any] | None:
        """`task_type=recite`：喂给 BE-05 的 `ReciteService`（同步、要连库）。

        返回给客户端的 `verdict` 形状（契约未定义语文形态，本包补齐并上报）：
        判定字段沿用 verdict，另加 `recite` 子对象承载进度/评分。
        """
        from app.services.recite import ReciteService

        try:
            progress = ReciteService.feed(
                student_id=self.user_id,
                recitation_id=int(self._recitation_id or 0),
                partial_text=text,
            )
        except Exception as exc:  # noqa: BLE001 - 背诵过期/越权等
            logger.warning("背诵 feed 失败：%s", exc)
            self.outbox.append(
                self._error_payload(
                    ErrorCode.NOT_FOUND, "背诵已过期或不属于你，请重新开始", action="abort"
                )
            )
            return None

        finished = int(progress.get("missed_chars") or 0) == 0 and int(
            progress.get("total_chars") or 0
        ) > 0 and int(progress.get("pos") or 0) >= int(progress.get("total_chars") or 0)
        payload: dict[str, Any] = {
            "type": "verdict",
            "task_type": "recite",
            "is_correct": bool(finished),
            "match_type": "exact" if finished else "wrong",
            "expected": "",
            "asr_text": text,
            "normalized": None,
            "answer_id": self._answer_id,
            "can_appeal": True,
            "diff": None,
            "recite": progress,
        }
        return payload

    # ================================================================== 仲裁（F19）

    def _remember_audio(self, chunk: bytes) -> None:
        """只为仲裁保留最近若干秒音频（内存，不落盘）。"""
        if not self.arbitrator.enabled:
            return
        self._pcm_window.append(bytes(chunk))
        self._pcm_window_bytes += len(chunk)
        cap = self._arb_buffer_ms * BYTES_PER_MS
        while self._pcm_window_bytes > cap and len(self._pcm_window) > 1:
            self._pcm_window_bytes -= len(self._pcm_window.popleft())

    async def _try_arbitrate(self, match_type: str) -> str | None:
        """按三条触发场景决定是否仲裁；返回仲裁文本或 `None`（未触发/仍失败）。"""
        if not self.arbitrator.should_trigger(match_type):
            return None
        pcm = b"".join(self._pcm_window)
        if not pcm:
            return None
        text = await self.arbitrator.arbitrate(pcm)
        return text or None

    # ================================================================== 回调

    def _on_partial(self, text: str, ts_ms: int) -> None:
        """Provider 的中间结果回调（**同步**，只入 outbox）。"""
        if self.cfg is None or self.kicked:
            return
        if self.cfg.task_type == "recite" and self._recitation_id is not None:
            # 语文：顺带推进服务端 pos，让「逐字点亮」有服务端口径
            progress = self._recite_feed(text)
            if progress is not None:
                self.outbox.append(
                    {
                        "type": "partial",
                        "text": text,
                        "is_final": False,
                        "ts_ms": ts_ms,
                        "recite": progress.get("recite"),
                    }
                )
                self._last_partial = text
                return
            # 背诵已失效 → 上面的 `_recite_feed` 已入 outbox 一条 error；这里不再发 partial
            return
        self.outbox.append(
            {
                "type": "partial",
                "text": text,
                "is_final": False,
                "ts_ms": ts_ms,
            }
        )
        self._last_partial = text

    def _on_final(self, text: str, confidence: float | None, duration_ms: int) -> None:
        self._final = (text, confidence, duration_ms)

    # ================================================================== 错误与日志

    async def _fail(
        self,
        exc: ASRError,
        *,
        log: bool = True,
        provider: ASRProvider | None = None,
    ) -> None:
        """统一处理识别失败：计数 → 可能切键盘 → 出站 error + 写日志。"""
        self._consecutive_errors += 1
        self.router.note_failure()
        if self._consecutive_errors >= KEYBOARD_MODE_THRESHOLD:
            self._keyboard_mode = True

        action = exc.action
        if self._keyboard_mode:
            action = "keyboard"
        elif exc.code == ErrorCode.ASR_TIMEOUT:
            action = "keyboard"      # 契约 §6.5：超时 >3s 直接给键盘

        if log:
            self._log(
                provider,
                status=exc.status,
                error_code=str(exc.code),
            )
        self.outbox.append(
            self._error_payload(
                exc.code,
                exc.message,
                action=action,
                fallback="keyboard" if self._keyboard_mode else None,
            )
        )

    async def _error(self, code: int, message: str, *, action: str = "retry") -> None:
        self.outbox.append(self._error_payload(code, message, action=action))

    @staticmethod
    def _error_payload(
        code: int,
        message: str,
        *,
        action: str,
        fallback: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "error",
            "code": int(code),
            "message": message,
            "ui_hint": _UI_HINTS.get(int(code), "请再试一次"),
            "action": action,
        }
        if fallback is not None:
            payload["fallback"] = fallback
        return payload

    def _log(
        self,
        provider: ASRProvider | None,
        *,
        status: str,
        error_code: str | None = None,
    ) -> None:
        """写 `asr_logs`（F16）—— `provider` 记**实际用上的**厂商（U9）。"""
        name = self.router.used_provider if provider is None else str(
            getattr(provider, "name", None) or self.router.used_provider
        )
        model = getattr(provider, "model", None) if provider is not None else None
        audio_ms = int(getattr(provider, "audio_ms", 0) or 0)
        latency = None
        if self._task_started_at is not None:
            latency = int((time.monotonic() - self._task_started_at) * 1000)
        ASRLogService.write(
            provider=name,
            model=str(model) if model else None,
            lang=self.cfg.lang_hint if self.cfg else None,
            task_type=self.cfg.task_type if self.cfg else None,
            audio_ms=audio_ms,
            latency_ms=latency,
            status=status,
            error_code=error_code,
            session_id=self.cfg.session_id if self.cfg else None,
            student_id=self.user_id,
        )

    def _resolve_recitation(self, session_id: Any, msg: dict[str, Any]) -> int | None:
        """解析语文背诵的 `recitation_id`（契约 §6.2 的 `start` 没有这个字段）。

        优先级：`start.recitation_id` → `start.text_id` / `word_id` 在**该场次**里
        找一篇「未结算」的背诵。**两者都没有** → 返回 None，由调用方报参数错。

        依据：BE-05 的 `POST /api/recitations` 会先建好背诵记录并返回 `recitation_id`，
        App 拿着它开 WS 是最自然的流程；但契约 `start` 消息里没有这个字段 → 已上报。
        """
        rid = msg.get("recitation_id")
        if rid is not None:
            try:
                rid_int = int(rid)
            except (TypeError, ValueError):
                return None
            with read_conn() as conn:
                row = conn.execute(
                    "SELECT id FROM recitations WHERE id = ? AND student_id = ?",
                    (rid_int, self.user_id),
                ).fetchone()
            return rid_int if row is not None else None

        text_id = msg.get("text_id", msg.get("word_id"))
        if text_id is None or session_id is None:
            return None
        try:
            try:
                with read_conn() as conn:
                    row = conn.execute(
                        """
                        SELECT id FROM recitations
                         WHERE student_id = ? AND text_id = ? AND finished_at IS NULL
                         ORDER BY id DESC LIMIT 1
                        """,
                        (self.user_id, int(text_id)),
                    ).fetchone()
            except Exception:  # noqa: BLE001 - 老库没有 recitations 表时不必炸
                return None
        except (TypeError, ValueError):
            return None
        return int(row["id"]) if row is not None else None

    def _now_ms(self) -> int:
        if self._task_started_at is None:
            return 0
        return int((time.monotonic() - self._task_started_at) * 1000)

    # ------------------------------------------------------------------ 供端点使用

    def set_expected(self, expected: str | None) -> None:
        """`expected` 只用于服务端判定（F10）—— 不传给云厂商，也不回显给客户端。"""
        self._expected = (expected or "").strip() or None

    def set_answer_id(self, answer_id: int | None) -> None:
        self._answer_id = answer_id


_UI_HINTS: dict[int, str] = {
    ErrorCode.ASR_UNAVAILABLE: "语音服务暂时不可用，可以先用键盘作答",
    ErrorCode.ASR_TIMEOUT: "网络有点慢，可以用键盘先答这一题",
    ErrorCode.ASR_EMPTY: "没听清，请靠近麦克风再说一次",
    ErrorCode.ASR_BAD_AUDIO: "录音有问题，请重新说一次",
}
