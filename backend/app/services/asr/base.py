"""ASR 适配器基类与公共类型 —— WP-BE-06（契约《01-共享契约冻结》§7.4）。

**本文件是 WebSocket 网关与各云厂商实现之间的唯一契约面。**

| 名字 | 作用 |
|---|---|
| `ASRTaskConfig` | 一次识别任务的配置（契约 §7.4 冻结字段） |
| `ASRProvider` | 抽象基类：`start_session` / `push_audio` / `on_partial` / `on_final` / `close` |
| `ASRError` | 识别期错误，带 `ErrorCode`（50001~50004）与 `asr_logs.status` |
| `validate_pcm_chunk` | 音频规格校验（16kHz / 单声道 / 16bit / 裸 PCM），不符抛 `50004` |

## 两次「语义约定」（契约未写明，本包补齐并上报）

1. **`close()` = 结束识别并产出 `final`**，不是「仅仅释放连接」。
   契约 §7.4 的五个方法里没有 `end`/`finish`，而 WebSocket 的 `end` 消息必须触发最终判定，
   所以约定：`close()` 若尚未产出 `final` 就先 flush 再回调 `on_final`，然后释放云端连接。**幂等**。
2. **放弃本次识别用 `abort()`**（基类具体方法，非契约方法）：
   置 `_aborted` 后调 `close()`，此时**不触发 `on_final`**（`cancel` 消息语义：不计错）。
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from app.core.errors import ErrorCode

# ---------------------------------------------------------------------------
# 音频规格（F6 / 契约 §9.2 limits.chunk_ms）
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000          # 16kHz
CHANNELS = 1                 # 单声道
SAMPLE_WIDTH_BYTES = 2       # 16bit
BLOCK_ALIGN = CHANNELS * SAMPLE_WIDTH_BYTES   # 2 字节/采样点
BYTES_PER_MS = SAMPLE_RATE * BLOCK_ALIGN // 1000   # 32 字节/ms
DEFAULT_CHUNK_MS = 150

#: 单片最大允许时长（200ms 是契约上限的 100~200ms；留 1s 余量容忍客户端抖动）
MAX_CHUNK_MS = 1000
MAX_CHUNK_BYTES = MAX_CHUNK_MS * BYTES_PER_MS      # 32000

#: 明显不是裸 PCM 的容器/编码特征（F6：格式不符必须明确报 50004，不能静默返回空）
_CONTAINER_MARKERS: tuple[bytes, ...] = (
    b"RIFF",        # WAV 容器 —— 应传裸 PCM，不应带容器头
    b"OggS",        # Ogg
    b"ID3",         # MP3 标签
    b"fLaC",        # FLAC
    b"\xff\xfb",    # MP3 帧同步
    b"\xff\xf3",
    b"\xff\xf2",
)


def pcm_duration_ms(n_bytes: int) -> int:
    """裸 PCM 字节数 → 毫秒。`bytes / (16000*2)` 秒。"""
    if n_bytes <= 0:
        return 0
    return int(n_bytes // BYTES_PER_MS)


def validate_pcm_chunk(chunk: bytes, *, max_bytes: int = MAX_CHUNK_BYTES) -> None:
    """校验一片音频是否符合「16kHz / 单声道 / 16bit / 裸 PCM」。

    裸流无法真正反推采样率，因此校验的是**能确定性判定的部分**：

    - 长度为 0：跳过（不算错误，客户端偶尔会发空帧）
    - **奇数长度**：16bit 采样不可能出现半字节 → 格式错
    - **带容器/编码头**（RIFF / OggS / ID3 / FLAC / MP3 帧同步）→ 格式错
    - **单片过大**（> `MAX_CHUNK_MS`）→ 格式错（契约要求 100~200ms/包）

    Raises:
        ASRError: `ErrorCode.ASR_BAD_AUDIO`（50004）。
    """
    if not chunk:
        return
    if len(chunk) % BLOCK_ALIGN != 0:
        raise ASRError(
            ErrorCode.ASR_BAD_AUDIO,
            f"音频分片长度 {len(chunk)} 不是 {BLOCK_ALIGN} 的整数倍，"
            f"应为 16kHz/单声道/16bit 裸 PCM",
            status="error",
        )
    if any(chunk.startswith(m) for m in _CONTAINER_MARKERS):
        raise ASRError(
            ErrorCode.ASR_BAD_AUDIO,
            "检测到音频容器/编码头，客户端应发送裸 PCM（不带 WAV/MP3 头）",
            status="error",
        )
    if len(chunk) > max_bytes:
        raise ASRError(
            ErrorCode.ASR_BAD_AUDIO,
            f"音频分片 {len(chunk)} 字节（≈{pcm_duration_ms(len(chunk))}ms）"
            f"超过单片上限 {max_bytes} 字节（{MAX_CHUNK_MS}ms）",
            status="error",
        )


def pcm_to_wav(
    pcm: bytes, sample_rate: int = 16000, channels: int = 1, bits: int = 16
) -> bytes:
    """裸 PCM → 完整 WAV(RIFF) 容器。

    ⚠️ 非流式 HTTP 调用前**必须包壳**：`http_format: wav` 声称的是容器格式，
    百炼按 RIFF 解码 —— 直接把裸 PCM base64 过去会报 `DECODE_ERROR`（400）。
    本项目客户端（App）只发 16k/单声道/16bit 裸 PCM（`validate_pcm_chunk` 保证），
    所以这里按同规格补 44 字节 RIFF 头。
    """
    import struct

    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    data_len = len(pcm)
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + data_len)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
        + b"data"
        + struct.pack("<I", data_len)
    )
    return header + bytes(pcm)


def audio_to_data_uri(
    pcm: bytes, fmt: str = "wav", sample_rate: int = 16000
) -> str:
    """裸 PCM → data URI（非流式 HTTP 调用用）。

    `fmt == "wav"` 时先补 WAV(RIFF) 头再 base64 —— 2026-09-28 真机联调实测：
    裸 PCM 直接标 wav，百炼返回 400 DECODE_ERROR（选型报告 §3.1 的坑 +1，
    当年预研用的是完整 wav 文件，没暴露这层差异）。
    """
    if fmt == "wav":
        pcm = pcm_to_wav(pcm, sample_rate)
    return f"data:audio/{fmt};base64,{base64.b64encode(pcm).decode('ascii')}"


# ---------------------------------------------------------------------------
# 任务配置（契约 §7.4，字段冻结）
# ---------------------------------------------------------------------------

TaskType = Literal["word", "spell", "zh_answer", "recite"]


class ASRTaskConfig(BaseModel):
    """一次识别任务的配置 —— **字段与契约 §7.4 逐字一致**。"""

    session_id: int
    task_type: TaskType
    lang_hint: Literal["en", "zh"]
    hotwords: list[str] = Field(default_factory=list)   # 一期不启用，字段保留
    sample_rate: int = 16000
    enable_intermediate: bool = True


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------

class ASRError(Exception):
    """识别期错误。

    Attributes:
        code: `ErrorCode` 数值（50001 不可用 / 50002 超时 / 50003 空结果 / 50004 格式错）。
        status: 写入 `asr_logs.status` 的值（`ok`/`empty`/`timeout`/`error`）。
        action: 给客户端的处置建议（`retry`/`keyboard`/`abort`）。
    """

    def __init__(
        self,
        code: int,
        message: str = "",
        *,
        status: str = "error",
        action: str = "retry",
    ) -> None:
        self.code = int(code)
        self.message = message or "语音识别失败"
        self.status = status
        self.action = action
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

PartialCallback = Callable[[str, int], None]              # (text, ts_ms)
FinalCallback = Callable[[str, float | None, int], None]  # (text, confidence, duration_ms)
#: 语句级「已确定」结果回调（非最终 final，但不再是抖动中的 partial）——
#: v1.3 §5：utterance 只透传回显（决策 #11）并累积为松开（end）后整词
#: 判定 verdict 的转写来源（见 session_manager._spell_verdict）。
UtteranceCallback = Callable[[str, int], None]            # (text, ts_ms)


class ASRProvider(ABC):
    """ASR 厂商适配器（契约 §7.4）。

    子类实现三个抽象方法即可；回调注册、幂等、时长统计、放弃语义都在基类里统一处理，
    避免每个 Provider 各写一份再漂移。
    """

    #: 供 `asr_logs.provider` 记录
    name: str = "base"
    #: 供 `asr_logs.model` 记录
    model: str = ""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.cfg: ASRTaskConfig | None = None
        self._partial_cb: PartialCallback | None = None
        self._final_cb: FinalCallback | None = None
        self._utterance_cb: UtteranceCallback | None = None
        self._aborted = False
        self._closed = False
        self._final_emitted = False
        self._audio_bytes = 0

    # ---- 回调注册（契约签名） ----

    def on_partial(self, cb: PartialCallback) -> None:
        self._partial_cb = cb

    def on_final(self, cb: FinalCallback) -> None:
        self._final_cb = cb

    def on_utterance(self, cb: UtteranceCallback) -> None:
        """注册语句级结果回调（可选；不注册的 Provider 完全不发，零影响）。"""
        self._utterance_cb = cb

    # ---- 子类实现（契约签名） ----

    @abstractmethod
    async def start_session(self, cfg: ASRTaskConfig) -> None:
        """建立云端会话。失败必须抛 `ASRError`（不返回 None 让调用方猜）。"""

    @abstractmethod
    async def push_audio(self, chunk: bytes) -> None:
        """推送一片音频（裸 PCM）。**实现内必须先 `validate_pcm_chunk`。**"""

    @abstractmethod
    async def close(self) -> None:
        """结束识别：flush 未完成的结果 → 触发 `on_final` → 释放云端连接。幂等。"""

    # ---- 基类具体方法 ----

    async def abort(self) -> None:
        """**放弃**本次识别（WS `cancel`）：清理资源，**不触发 `on_final`**。"""
        self._aborted = True
        await self.close()

    # ---- 供子类调用的内部工具 ----

    async def _push_checked(self, chunk: bytes) -> int:
        """校验并累计音频，返回本次增量字节数。"""
        validate_pcm_chunk(chunk)
        self._audio_bytes += len(chunk)
        return len(chunk)

    def _emit_partial(self, text: str, ts_ms: int | None = None) -> None:
        """回调中间结果（已 abort / 无回调 / 关闭中间结果时静默丢弃）。"""
        if self._aborted or self._partial_cb is None:
            return
        if self.cfg is not None and not self.cfg.enable_intermediate:
            return
        self._partial_cb(text, ts_ms if ts_ms is not None else self.audio_ms)

    def _emit_utterance(self, text: str, ts_ms: int | None = None) -> None:
        """回调一条「已确定」的语句级结果（Vosk 端点边界 / close 残余）。

        与 `_emit_partial` 的区别：不是抖动中的中间态，是识别器自己判定
        「这句话说完了」的确定输出；与 `_emit_final` 的区别：**不幂等**——
        一次任务内可以有多个 utterance（拼读时一个字母一句）。
        空文本与 abort 后不触发；`enable_intermediate=False` **不拦**它
        （它不是中间结果，关中间结果不该把判格链路一起关掉）。
        """
        if self._aborted or self._utterance_cb is None:
            return
        text = (text or "").strip()
        if not text:
            return
        self._utterance_cb(text, ts_ms if ts_ms is not None else self.audio_ms)

    def _emit_final(self, text: str, confidence: float | None = None,
                    duration_ms: int | None = None) -> None:
        """回调最终结果（**幂等**；abort 后不触发）。"""
        if self._aborted or self._final_emitted:
            return
        self._final_emitted = True
        if self._final_cb is not None:
            self._final_cb(
                text,
                confidence,
                duration_ms if duration_ms is not None else self.audio_ms,
            )

    @property
    def audio_ms(self) -> int:
        """已推送音频的时长（毫秒）—— 供 `asr_logs.audio_ms`。"""
        return pcm_duration_ms(self._audio_bytes)

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def final_emitted(self) -> bool:
        return self._final_emitted

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} provider={self.name!r} model={self.model!r}>"
