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

from app.core.db import read_conn, write_tx
from app.core.errors import ErrorCode
from app.core.logging import get_logger
from app.services.asr.arbitrator import ArbitratorProvider
from app.services.asr.fallback import FallbackProvider
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
        fallback: 多模态兜底引擎（WP-2C）；不传则按 `raw_config` 建。
    """

    def __init__(
        self,
        *,
        user: Any,
        send: SendFn,
        raw_config: dict[str, Any] | None = None,
        router: ASRRouter | None = None,
        arbitrator: ArbitratorProvider | None = None,
        fallback: FallbackProvider | None = None,
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

        self.router = router if router is not None else ASRRouter(self._ordered_raw())
        self.arbitrator = (
            arbitrator if arbitrator is not None else ArbitratorProvider(self.raw)
        )
        self.fallback = (
            fallback if fallback is not None else FallbackProvider(self.raw)
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
        self._got_audio: bool = False   # 只为「首帧到达」日志，无业务语义
        self._orphan_frames: int = 0    # 未 start 期间丢弃的帧数（限流日志用）
        self._final: tuple[str, float | None, int] | None = None
        self._last_partial = ""
        self._consecutive_errors = 0
        self._keyboard_mode = False
        self._answer_id: int | None = None
        #: 标准答案 —— 只进服务端判定，**绝不传给云厂商**（F10）
        self._expected: str | None = None
        self._mode: str | None = None
        self._close_reason: str | None = None
        # ---- 简化版 spell_zh2en 流式拼读（契约 §5：letter 逐格判定）----
        #: 本次拼读的 item（items.id）；None = 不在拼读流程
        self._spell_item_id: int | None = None
        #: 目标字母序列（item.en 去空格与连字符，契约 §6.4 口径）
        self._spell_target: str = ""
        #: 已判过的格位游标（只前移，指向下一格）
        self._spell_index: int = 0
        #: letter 载荷里的 session_id（App 上行原样透传，可为练习会话字符串 id）
        self._spell_session_id: Any = 0
        # 仲裁所需音频：**只在内存**，且只留最近若干秒（F18：不落盘）
        self._pcm_window: deque[bytes] = deque()
        self._pcm_window_bytes = 0
        self._arb_buffer_ms = int(
            (self.raw.get("arbitrator") or {}).get("buffer_ms")
            or DEFAULT_ARBITRATION_BUFFER_MS
        )

    # ================================================================== 属性

    def _ordered_raw(self) -> dict[str, Any]:
        """按能力模式覆盖 ASR 主备顺序（localize-dev/01 §1.3）。

        - cloud  ：原样用 asr.yaml 的 provider/backup_provider（现状行为）
        - hybrid ：[local, <asr.yaml 原主厂商>]（本地优先，失败回落云端；
                   云端 = asr.yaml 配置的主厂商，生产即 aliyun，测试环境是 mock）
        - local  ：[local]（无备）

        复制 dict 覆盖，**不改 load_asr_raw() 的原始返回**；模式在会话建立时
        读取一次，进行中的会话不切换（契约 §1.3 规则 2）。
        local provider 尚未注册/模型缺失时，ASRFactory.create 会抛 —— 这正是
        预期：ASRRouter 的建连失败语义会落到下一顺位（hybrid→原主厂商）。
        """
        from app.core.capability import resolve_mode

        asr_mode = resolve_mode("asr")
        if asr_mode == "cloud":
            return dict(self.raw)
        ordered = dict(self.raw)
        original_primary = str(ordered.get("provider") or "aliyun").strip().lower()
        if asr_mode == "hybrid":
            ordered["provider"] = "local"
            # 回落顺位 = asr.yaml 原主厂商（local 不可能同时是原主厂商——尚未注册）
            ordered["backup_provider"] = original_primary
        else:  # local
            ordered["provider"], ordered["backup_provider"] = "local", ""
        return ordered

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
            # 未 start（或上一轮已 end）时 App 的常驻录音器仍会推帧 ——
            # **静默丢弃**，每轮只在前几帧记一次日志。
            # ⛔ 不能每帧回 error：App 端会连续收到上百条 abort，界面被刷成「没听清」。
            self._orphan_frames += 1
            if self._orphan_frames <= 2:
                logger.warning(
                    "ASR 音频帧在 start 之前丢弃 user=%s bytes=%s（累计 %s 帧）",
                    self.user.id, len(data), self._orphan_frames,
                )
            return
        if self._keyboard_mode:
            return
        if not self._got_audio:
            # 只记首帧：证明「App 的麦克风数据确实流到了服务端」
            self._got_audio = True
            self._dbg_bytes = 0
            self._dbg_frames = 0
            self._dbg_dump = bytearray()          # 临时诊断：dump 前 30s 上行音频
            logger.info("ASR 首帧音频到达 user=%s bytes=%s", self.user.id, len(data))
        # 临时诊断（真机排查音频上行）：每 20 帧打一次帧流统计
        self._dbg_frames = getattr(self, "_dbg_frames", 0) + 1
        self._dbg_bytes = getattr(self, "_dbg_bytes", 0) + len(data)
        dump = getattr(self, "_dbg_dump", None)
        if dump is not None and len(dump) < 960000:
            dump.extend(data)
            if len(dump) >= 960000:
                try:
                    import wave as _wave
                    buf = bytes(dump)
                    with _wave.open("D:/helloagent/zhanglaoshi_zhangyuze/qa/_app_uplink.wav", "wb") as f:
                        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
                        f.writeframes(buf)
                    logger.info("ASR 上行音频已 dump（30s）user=%s", self.user.id)
                except Exception:  # noqa: BLE001
                    pass
                self._dbg_dump = None
        if self._dbg_frames % 20 == 0:
            logger.info(
                "ASR 帧流 user=%s 帧=%s 累计=%sB ≈%sms",
                self.user.id, self._dbg_frames, self._dbg_bytes,
                self._dbg_bytes * 1000 // 32000,
            )
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

        # ---- 简化版契约 §5 上行：{type:'start', mode:'spell_zh2en', item_id} ----
        mode = msg.get("mode")
        if mode is not None:
            await self._on_spell_start(msg, str(mode))
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

        # `expected` / `answer_id` 只在服务端留存（F10：绝不下发云厂商）
        self.set_expected(msg.get("expected"))
        self.set_answer_id(msg.get("answer_id"))
        self._mode = str(msg["mode"]) if msg.get("mode") else None

        await self._start_provider(
            session_id=int(session_id),
            task_type=task_type,
            lang=lang,
            msg=msg,
        )

    async def _on_spell_start(self, msg: dict[str, Any], mode: str) -> None:
        """简化版背链路（spell_zh2en）的 start：按 item_id 查目标字母序列（契约 §5）。

        - 目标 = `items.en` 去空格与连字符（与 §6.4 App 本地判格同一口径）；
        - 同一 item 连续多轮 start（每个字母一轮 start→音频→end）**不清进度**，
          `_spell_index` 只在换 item 时重置 —— App 断线重连也不丢作答进度（契约 §5）。
        - `spell_en2zh`：网关只做**转写**（final 下发后收口），判定由 App 拿转写文本
          走 `POST /api/practice/answer`（compare_service，契约 §4.1 / §6.1）。
        """
        if mode not in ("spell_zh2en", "spell_en2zh"):
            await self._error(
                ErrorCode.PARAM_INVALID,
                f"mode 取值非法：{mode!r}（本通道只支持 spell_zh2en / spell_en2zh）",
                action="abort",
            )
            return
        item_id = msg.get("item_id")
        if item_id is None:
            await self._error(ErrorCode.PARAM_INVALID, "缺少 item_id", action="abort")
            return
        item_id = int(item_id)

        if mode == "spell_zh2en":
            if self._spell_item_id == item_id and self._spell_target:
                # 同一题继续拼：保留游标
                pass
            else:
                with read_conn() as conn:
                    row = conn.execute(
                        "SELECT en FROM items WHERE id = ?", (item_id,)
                    ).fetchone()
                if row is None or not row["en"]:
                    await self._error(
                        ErrorCode.CONTENT_NOT_FOUND, "内容不存在", action="abort"
                    )
                    return
                target = "".join(
                    ch for ch in str(row["en"]) if ch.isalpha()
                ).lower()
                if not target:
                    await self._error(
                        ErrorCode.PARAM_INVALID, "该条目没有可拼读的字母", action="abort"
                    )
                    return
                self._spell_item_id = item_id
                self._spell_target = target
                self._spell_index = 0
        else:
            # en2zh：不需要字母目标；换 item 时清掉 zh2en 残留的拼读游标，
            # 防止后续同一连接再进 zh2en 时复用错 target（防御性，正常不会发生）。
            if self._spell_item_id != item_id:
                self._spell_item_id = item_id
                self._spell_target = None
                self._spell_index = 0
        self._mode = mode

        # 契约 §5 的 start 未强制携带 session_id；letter 载荷需要它，
        # 按 App 传值原样透传（练习会话 id 是字符串，不强转），缺省记 0
        self._spell_session_id: Any = msg.get("session_id") or 0
        cfg_session = self._spell_session_id if str(self._spell_session_id).isdigit() else 0

        # 通道选择（2026-10-13 修订 v1.0.3）：
        # - zh2en（逐字母判格）：**Vosk 语句边界实时判格** —— 念完一个字母
        #   停一下即触发一次判定并下发 letter，不等 App 发 end（App 此模式
        #   没有 end 入口，2026-10-13 真机「字母格永远不亮」根因）；
        #   end / close 仅作兜底（残余音频在 close 时经 utterance 通道判格）。
        # - en2zh（看英文说中文意思）：从「开始说」到「我说完了」中间可能隔几十秒，
        #   走批量会把整段攒着最后撞超时（真机实测 103s 音频 3s 超时必炸）→
        #   **复用 recite 的流式 WS 通道**（lang 同为 zh），实时 partial + end 后收 final。
        task_type = "spell" if mode == "spell_zh2en" else "recite"
        await self._start_provider(
            session_id=cfg_session,
            task_type=task_type,
            lang=self.lang_map.get(task_type, "zh"),
            msg=msg,
        )

    async def _start_provider(
        self,
        *,
        session_id: int,
        task_type: str,
        lang: str,
        msg: dict[str, Any],
    ) -> None:
        """建立一轮 ASR 任务（start / 每个字母轮共用）。"""
        cfg = ASRTaskConfig(
            session_id=session_id,
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
        self._got_audio = False
        self._orphan_frames = 0     # 新一轮开始，孤儿帧计数归零
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
        provider.on_utterance(self._on_utterance)  # 语句级结果（Vosk 拼读实时判格）

        # 排障关键：start 成功要留痕（用户反馈「一直收音」时，看这行 +
        # 下面的错误日志就能判断 App 到底发了什么）
        logger.info(
            "ASR start user=%s practice_session=%s task=%s lang=%s provider=%s",
            self.user.id, cfg.session_id, cfg.task_type, cfg.lang_hint,
            self.router.used_provider,
        )
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
            logger.warning("ASR end 在 start 之前到达 user=%s", self.user.id)
            await self._error(ErrorCode.PARAM_INVALID, "尚未 start 就 end", action="abort")
            return

        logger.info(
            "ASR end user=%s audio_ms=%s（开始等待云端 final）",
            self.user.id, getattr(provider, "audio_ms", "?"),
        )
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

        # WP-2C：置信度低（疑似听错）→ 先用多模态兜底做一次**忠实**识别再判分（§9.7）。
        # 未启用兜底 / 置信度正常 → 完全不改变原有行为。
        if conf is not None and self.fallback.should_trigger(confidence=conf):
            rescued = await self._try_fallback(confidence=conf)
            if rescued:
                text, conf = rescued, None

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

        # 简化版 spell_zh2en：判格已由 utterance 通道实时完成（Vosk 每句
        # 结束即判，见 _on_utterance）；此处**不再重复判** —— 同一文本再跑
        # 一遍 _spell_feed 会对着已推进的游标错位重判（契约 §5 修订 v1.0.3）。
        if self._mode == "spell_zh2en" and self._spell_target:
            self._log(provider, status="ok")
            self.router.note_ok()
            return

        # 简化版 spell_en2zh：`final` 已下发（上方），网关不判分 ——
        # App 拿转写文本走 /api/practice/answer（compare_service，契约 §6.1）
        if self._mode == "spell_en2zh":
            self._log(provider, status="ok")
            self.router.note_ok()
            return

        await self._judge_and_emit(text, conf, provider)

    # ================================================================== 拼读流式判定（契约 §5）

    def _spell_feed(self, text: str) -> None:
        """把一句识别文本映射成字母，逐个判当前格并下发 `letter`（契约 §5）。

        判定引擎 = `services/judge` 拼读判定的谐音映射（原样复用，
        contract §5：「判定引擎 = 现有 services/judge.py 拼读判定原样复用」）。
        由 utterance 通道驱动（Vosk 每句结束一次；close 的残余也走此通道）：
        学生一次念出多个字母（或一口气念完整词）时按序逐格推进；
        判错的格**不填**（游标不动），App 侧按 §6.4 处理连错提示。
        """
        from app.services.judge.homophone import get_map

        letters = get_map().to_letters(text)
        target = self._spell_target
        while self._spell_index < len(target):
            if not letters:
                break
            letter, letters = letters[0], letters[1:]
            correct = letter == target[self._spell_index]
            self.outbox.append(
                {
                    "type": "letter",
                    "index": self._spell_index,
                    "letter": letter,
                    "correct": correct,
                    "session_id": self._spell_session_id,
                    "item_id": self._spell_item_id,
                }
            )
            if correct:
                self._spell_index += 1

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
        self, text: str, conf: float | None, provider: ASRProvider | None
    ) -> None:
        assert self.cfg is not None
        task_type = self.cfg.task_type
        # 标准答案只来自 start 显式携带（F10）——旧的「按 word_id 查 words 表」
        # 回退已随 WP-F 删表拆除（简化版答案在 items 表，走 §5 letter 判定链路）
        expected = self._expected or ""

        from app.services.judge import JudgeService, judge_word_or_spell

        try:
            # 英语模式一「双口径」：`word` 步念整词 / 逐字母拼都收（二期口径）。
            # `spell` 分支保留以兼容旧客户端（合并单轮后 App 不再发）。
            if task_type == "spell":
                verdict = JudgeService.judge_spell(asr_text=text, target=expected)
            elif task_type == "word":
                verdict = judge_word_or_spell(answer=text, expected=expected)[0]
            else:
                verdict = JudgeService.judge_text(answer=text, expected=expected)
        except Exception as exc:  # noqa: BLE001
            await self._fail(
                ASRError(ErrorCode.ASR_UNAVAILABLE, f"判定失败：{exc}", status="error"),
                provider=provider,
                allow_fallback=False,   # ASR 已出文本，判分崩了不该再识别一遍
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
                    elif task_type == "word":
                        verdict = judge_word_or_spell(
                            answer=arbitrated, expected=expected,
                            asr_confidence=verdict.asr_confidence,
                        )[0]
                    else:
                        verdict = JudgeService.judge_text(
                            answer=arbitrated, expected=expected,
                            asr_confidence=verdict.asr_confidence,
                        )
                    match_type = str(verdict.match_type)
                except Exception:  # noqa: BLE001 - 仲裁结果判分失败则保留原判
                    pass

        # 语音判分不再落库：旧的 answers/word_mastery 落库链路已随 WP-F 删表拆除
        # （简化版不落库逐题作答，练习留痕走 REST /api/practice/answer + settle）
        logger.info(
            "ASR 判分 user=%s session=%s task=%s text=%r match=%s correct=%s",
            self.user.id, self.cfg.session_id, task_type, text,
            match_type, bool(verdict.is_correct),
        )

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

    # ================================================================== 仲裁（F19）

    def _remember_audio(self, chunk: bytes) -> None:
        """为**仲裁 / 兜底**保留最近若干秒音频（内存，不落盘）。"""
        # WP-2C：兜底引擎同样需要这一窗音频；只在仲裁开启时才留会导致兜底永远拿不到声音。
        if not (self.arbitrator.enabled or self.fallback.enabled):
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

    async def _try_fallback(self, *, asr_failed: bool = False,
                            confidence: float | None = None) -> str | None:
        """WP-2C：ASR 失败 / 低置信时，用多模态兜底再识别一次。

        这是「**降级键盘之前**」的最后一次识别机会（需求 §9.4 / §9.7）。
        返回兜底文本或 `None`（未触发 / 拿不到音频 / 兜底也失败）。
        """
        if not self.fallback.should_trigger(
            asr_failed=asr_failed, confidence=confidence
        ):
            return None
        pcm = b"".join(self._pcm_window)
        if not pcm:
            return None
        text = await self.fallback.fallback(pcm)
        return text or None

    # ================================================================== 回调

    def _on_partial(self, text: str, ts_ms: int) -> None:
        """Provider 的中间结果回调（**同步**，只入 outbox）。"""
        if self.cfg is None or self.kicked:
            return
        # 临时诊断：partial 变化才打
        if text != self._last_partial:
            logger.info("ASR partial user=%s text=%r", self.user.id, text)
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

    def _on_utterance(self, text: str, ts_ms: int) -> None:
        """Provider 的语句级结果回调（同步，仅入 outbox）。

        Vosk 拼读通道专用：念完一个字母停一下即触发一次。zh2en 模式立刻
        逐格判定并下发 `letter`（契约 §5 修订 v1.0.3）—— 这是「看中文背
        英文」页面字母格亮起的**唯一实时路径**（App 此模式无 end 入口）。
        其他模式（recite/word/en2zh 等）忽略：它们的 final 仍在 end 时收口。

        同时把识别文本本身透传 App（修订 v1.0.4）：utterance 与随后的
        `letter` 判格**同源同序**，页面「实时显示听到的字母」展示的就是
        实际参与判格的文本，不会出现「显示的」和「判分的」不一致。
        """
        if self.cfg is None or self.kicked:
            return
        if not (self._mode == "spell_zh2en" and self._spell_target):
            return
        logger.info(
            "ASR utterance user=%s text=%r spell_index=%s/%s",
            self.user.id, text, self._spell_index, len(self._spell_target),
        )
        self.outbox.append(
            {
                "type": "utterance",
                "text": text,
                "item_id": self._spell_item_id,
                "ts_ms": ts_ms,
            }
        )
        self._spell_feed(text)

    # ================================================================== 错误与日志

    async def _fail(
        self,
        exc: ASRError,
        *,
        log: bool = True,
        provider: ASRProvider | None = None,
        allow_fallback: bool = True,
    ) -> None:
        """统一处理识别失败：**先试多模态兜底** → 计数 → 可能切键盘 → 出站 error + 写日志。"""
        # WP-2C：ASR 识别失败时，在降级键盘之前先用多模态兜底再识别一次
        # （需求 §9.4 降级策略 / §9.7「不要把孩子赶去键盘」）。
        # ⛔ `allow_fallback=False` 用于「判定失败」等 ASR 已成功出文本的场景 ——
        #    那类失败再识别一遍没有意义，且会与判定形成回环。
        if (
            allow_fallback
            and self.cfg is not None
            and await self._rescue_with_fallback(provider)
        ):
            return

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

    async def _rescue_with_fallback(self, provider: ASRProvider | None) -> bool:
        """兜底成功 → 直接下发 `final` 并判分，返回 True（本轮**不算失败**）。

        兜底失败 / 未触发 → 返回 False，调用方按原逻辑降级（计数、可能切键盘）。
        """
        rescued = await self._try_fallback(asr_failed=True)
        if not rescued:
            return False
        self.outbox.append(
            {
                "type": "final",
                "text": rescued,
                "confidence": None,
                "duration_ms": 0,
            }
        )
        await self._judge_and_emit(rescued, None, provider)
        return True

    async def _error(self, code: int, message: str, *, action: str = "retry") -> None:
        # 网关发给 App 的错误必须落日志：之前只在 asr_logs 记 code，
        # 「App 一直收音」这类问题完全无从排查（2026-09-28 真机联调教训）。
        logger.warning(
            "ASR 网关错误 user=%s code=%s action=%s msg=%s",
            self.user.id, code, action, message,
        )
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
        """写 `asr_logs`（F16）—— `provider` 记**实际用上的**厂商（U9）。

        WP-F 拆除了 `ASRLogService` 中间层（见 F-2 删除清单），这里改为直接落库；
        `asr_logs` 是保留表（保留的 asr/ 子系统与 asr_daily_rollup 任务仍读写）。
        """
        name = self.router.used_provider if provider is None else str(
            getattr(provider, "name", None) or self.router.used_provider
        )
        model = getattr(provider, "model", None) if provider is not None else None
        audio_ms = int(getattr(provider, "audio_ms", 0) or 0)
        latency = None
        if self._task_started_at is not None:
            latency = int((time.monotonic() - self._task_started_at) * 1000)
        try:
            with write_tx() as conn:
                conn.execute(
                    """
                    INSERT INTO asr_logs
                        (session_id, student_id, provider, model, lang, task_type,
                         audio_ms, latency_ms, status, error_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.cfg.session_id if self.cfg else None,
                        self.user_id,
                        name,
                        str(model) if model else None,
                        self.cfg.lang_hint if self.cfg else None,
                        self.cfg.task_type if self.cfg else None,
                        audio_ms,
                        latency,
                        status,
                        error_code,
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - 日志失败不影响识别主链路
            logger.warning("asr_logs 写失败（已忽略）：%s", exc)

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
