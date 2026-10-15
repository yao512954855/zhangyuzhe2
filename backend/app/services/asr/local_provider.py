"""本地 ASR Provider —— 批次 E（契约《01-契约冻结》§1.3 / §6 / 工作包 13 §4）。

**引擎路由**（按 `cfg.task_type`，与网关建 `ASRTaskConfig` 的口径一致，见
`session_manager.py:538`：spell_zh2en→task_type="spell"、spell_en2zh→"recite"）：

| task_type | 引擎 | 说明 |
|---|---|---|
| `"spell"`（逐字母拼读 / spell_zh2en） | Vosk + 26 字母 grammar | **语句边界实时判格**：每检测到一句话结束（念完一个字母停一下）即经 utterance 回调把确定结果交给网关 `_spell_feed` 逐格判定（契约 §5 修订 v1.0.3）；partial 仅供调试展示 |
| `"recite"` / `"word"` / `"zh_answer"`（转写 / spell_en2zh / 背诵 / 说词） | sherpa SenseVoice 离线 | 累积整段，close 时解码产 final |

要点（写死）：
- 模型进程内单例 + `threading.Lock` 双检锁（契约 §6）；Vosk Recognizer / sherpa stream **每会话新建**。
- `start_session` 模型缺失 / 加载异常 → 抛 `ASRError(50006)`，由 `ASRRouter` 当建连失败落下一顺位
  （hybrid 自动回云端，local 模式明确报错）。
- `close()` 同步产出 final（本地无批量 HTTP 提交语义，保持 base.py 对外时序不变）。
- 模块级 `register_model_spec("asr", ...)` 登记两模型，供 `/api/capabilities` 轻量探活。

⚠️ 网关上行音频按契约是 **16kHz / 单声道 / 16bit 裸 PCM**（`base.validate_pcm_chunk`）。
Vosk 模型原生 16kHz 直接用；SenseVoice 原生 16kHz 也直接用（整段离线解码，
不走自写重采样——预研实测自写线性插值降采样会引入混叠毁掉识别）。

📌 契约修订 v1.0.2（2026-10-13，姚工裁决）：sherpa streaming zipformer bilingual
（2023-02-20，8k）预研双门 FAIL（孤立英文词 0.04、中文短词 1/3），换 **SenseVoice
small int8**（zh-en-ja-ko-yue，16k，离线非流式）。provider 本就「累积到 close 整段
解码」，离线模型与该设计天然匹配。
"""

from __future__ import annotations

import json
import threading
from typing import Any

import numpy as np

from app.core.capability import register_model_spec
from app.core.config import BASE_DIR
from app.core.errors import ErrorCode
from app.core.logging import get_logger
from app.services.asr.base import ASRError, ASRProvider, ASRTaskConfig

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 模型清单（目录名与 download_models.py / 契约 §3 严格一致）
# ---------------------------------------------------------------------------

VOSK_MODEL_PREFIX = "vosk-model-small-en-us"
SHERPA_MODEL_PREFIX = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue"

LETTERS = [chr(ord("a") + i) for i in range(26)]
GRAMMAR = json.dumps(LETTERS)                       # Vosk 锁死 26 字母
VOSK_SR = 16000
SENSEVOICE_SR = 16000                               # SenseVoice 原生率

#: 服务端静音收口阈值（ms）：partial 停止变化超过这么久 → 主动取 Result 判格。
#: Vosk 内置 VAD 对短停顿不敏感（2026-10-13 实测 22 条 qa spell 音频
#: **0 次** AcceptWaveform 边界触发），真人「念一个字母停一下」必须靠
#: 这条路径保证实时判格（契约 §5 修订 v1.0.3）。
#: 700ms 取值：小于常见字母间停顿（0.3~1s）的下沿风险区，又不至于把
#: 念 "double u" 这类词内小停顿切断（切断也无妨——按字母逐格判，
#: 每个 flush 出的片段独立判格，顺序不错位）。
SILENCE_FLUSH_MS = 700

_MODEL_SPECS_FILES = [
    "models/asr/vosk-model-small-en-us-0.15/am/final.mdl",
    "models/asr/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/"
    "model.int8.onnx",
]


def _asr_model_dirs() -> tuple[Any, Any]:
    asr = BASE_DIR / "models" / "asr"
    vosk = next((p for p in asr.iterdir()
                 if p.is_dir() and p.name.startswith(VOSK_MODEL_PREFIX)), None)
    sherpa = next((p for p in asr.iterdir()
                   if p.is_dir() and p.name.startswith(SHERPA_MODEL_PREFIX)), None)
    return vosk, sherpa


# ---------------------------------------------------------------------------
# 模型单例 + 双检锁
# ---------------------------------------------------------------------------

_vosk_model: Any = None
_sherpa_rec: Any = None
_model_lock = threading.Lock()


def _load_vosk_model() -> Any:
    """Vosk Model 单例。缺失 → 抛 50006。"""
    global _vosk_model
    if _vosk_model is not None:
        return _vosk_model
    with _model_lock:
        if _vosk_model is not None:
            return _vosk_model
        vosk_dir, _ = _asr_model_dirs()
        if vosk_dir is None:
            raise ASRError(
                ErrorCode.LOCAL_ENGINE_UNAVAILABLE,
                "本地 Vosk 模型未下载（models/asr/vosk-model-small-en-us-*）",
                status="error",
            )
        from vosk import Model
        _vosk_model = Model(str(vosk_dir))
        logger.info("Vosk 模型已加载：%s", vosk_dir.name)
    return _vosk_model


def _load_sherpa_rec() -> Any:
    """SenseVoice OfflineRecognizer 单例（线程安全，按流隔离状态）。缺失 → 抛 50006。"""
    global _sherpa_rec
    if _sherpa_rec is not None:
        return _sherpa_rec
    with _model_lock:
        if _sherpa_rec is not None:
            return _sherpa_rec
        _, sherpa_dir = _asr_model_dirs()
        if sherpa_dir is None:
            raise ASRError(
                ErrorCode.LOCAL_ENGINE_UNAVAILABLE,
                "本地 SenseVoice 模型未下载（models/asr/sherpa-onnx-sense-voice-*）",
                status="error",
            )
        import sherpa_onnx

        model = sherpa_dir / "model.int8.onnx"
        tokens = sherpa_dir / "tokens.txt"
        if not model.is_file() or not tokens.is_file():
            raise ASRError(
                ErrorCode.LOCAL_ENGINE_UNAVAILABLE,
                f"SenseVoice 模型文件缺失（需 model.int8.onnx + tokens.txt）：{sherpa_dir}",
                status="error",
            )
        _sherpa_rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model), tokens=str(tokens),
            use_itn=True, num_threads=1, provider="cpu",
        )
        logger.info("SenseVoice 模型已加载：%s", sherpa_dir.name)
    return _sherpa_rec


# ---------------------------------------------------------------------------
# 重采样（numpy，不引入 scipy）
# ---------------------------------------------------------------------------

def _resample_int16(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x.astype(np.int16)
    n_out = max(1, int(round(len(x) * sr_out / sr_in)))
    old_idx = np.linspace(0, len(x) - 1, n_out)
    new = np.interp(old_idx, np.arange(len(x)), x.astype(np.float64))
    return np.clip(new, -32768, 32767).astype(np.int16)


def _pcm_to_int16(chunk: bytes) -> np.ndarray:
    return np.frombuffer(chunk, dtype=np.int16)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class LocalProvider(ASRProvider):
    """本地 ASR 适配器：Vosk（字母拼读）+ sherpa（中英转写）。

    不触网、不落盘（音频仅在内存转发，契约 F18）。
    """

    name = "local"

    # ---- 引擎 seam（便于单测 monkeypatch） ----

    def _new_vosk_recognizer(self, model: Any) -> Any:
        from vosk import KaldiRecognizer
        return KaldiRecognizer(model, VOSK_SR, GRAMMAR)

    def _vosk_accept(self, rec: Any, pcm16: bytes) -> bool:
        """喂一片音频；返回 True = Vosk 端点检测判定「一句话说完了」。

        念字母场景下一句就是一个字母：停一下边界即触发，服务端立刻判格，
        不等 App 发 end（契约 §5 修订 v1.0.3）。
        """
        return rec.AcceptWaveform(pcm16)

    def _vosk_partial(self, rec: Any) -> str:
        return json.loads(rec.PartialResult()).get("text", "")

    def _vosk_result(self, rec: Any) -> str:
        """取**本句**的确定识别结果（边界触发后调用；内部状态随之清空）。"""
        return json.loads(rec.Result()).get("text", "")

    def _vosk_final(self, rec: Any) -> str:
        return json.loads(rec.FinalResult()).get("text", "")

    def _new_sherpa_stream(self, rec: Any) -> Any:
        return rec.create_stream()

    def _sherpa_accept(self, stream: Any, sr: int, f32: np.ndarray) -> None:
        stream.accept_waveform(sr, f32)

    def _sherpa_decode(self, rec: Any, stream: Any) -> None:
        rec.decode_stream(stream)

    def _sherpa_result(self, stream: Any) -> str:
        return stream.result.text

    # ---- 生命周期 ----

    async def start_session(self, cfg: ASRTaskConfig) -> None:
        self.cfg = cfg
        self._spell = (cfg.task_type == "spell")          # Vosk 通道
        self._vosk_rec: Any = None
        self._sherpa_stream: Any = None
        self._sherpa_acc: list[np.ndarray] = []           # 累积 16k PCM（sherpa 通道）

        if self._spell:
            model = _load_vosk_model()                    # 缺失 → 50006
            self.model = "vosk-small-en-us-0.15"
            self._vosk_rec = self._new_vosk_recognizer(model)
            # 静音收口状态（每会话新建 recognizer 时一并重置）
            self._last_partial = ""
            self._last_voice_ms = 0
        else:
            rec = _load_sherpa_rec()                      # 缺失 → 50006
            self.model = "sensevoice-small-int8"
            self._sherpa_stream = self._new_sherpa_stream(rec)
        logger.info("LocalProvider start task_type=%s engine=%s",
                    cfg.task_type, "vosk" if self._spell else "sensevoice")

    async def push_audio(self, chunk: bytes) -> None:
        await self._push_checked(chunk)                   # 校验 16k/单声道/16bit
        if self._spell:
            # Vosk 拼读通道：两条判格触发路径（契约 §5 修订 v1.0.3）——
            # ① Vosk 内置端点边界（念完整词不停/长停顿）；
            # ② 服务端静音收口：partial 停止变化超过 SILENCE_FLUSH_MS
            #    主动取 Result（真人「念一个字母停一下」的主路径）。
            # 2026-10-13 真机根因：原实现只在 close 时出 final，而 App 此
            # 模式没有 end 入口 → 字母格永远不亮，页面表现为卡死。
            utterance_end = self._vosk_accept(self._vosk_rec, chunk)
            if utterance_end:
                text = self._vosk_result(self._vosk_rec)
                logger.info("[dbg-vosk] 边界 text=%r audio_ms=%s",
                            text, self.audio_ms)
                self._last_partial = ""
                if text:
                    self._emit_utterance(text)
                return
            part = self._vosk_partial(self._vosk_rec)
            now_ms = self.audio_ms
            if part != self._last_partial:
                self._last_partial = part
                self._last_voice_ms = now_ms
            # 临时诊断：钉死线上 Vosk partial 链路
            self._dbg_n = getattr(self, "_dbg_n", 0) + 1
            if part or self._dbg_n % 60 == 1:
                logger.info("[dbg-vosk] 帧=%s partial=%r", self._dbg_n, part)
            if part and now_ms - self._last_voice_ms >= SILENCE_FLUSH_MS:
                # 念完一个字母停了一下 → 主动收口本句
                text = self._vosk_result(self._vosk_rec)
                logger.info("[dbg-vosk] 静音收口 text=%r audio_ms=%s",
                            text, self.audio_ms)
                self._last_partial = ""
                if text:
                    self._emit_utterance(text)
                return
            if part:
                self._emit_partial(part)
        else:
            # sherpa 通道：累积整段，close 时整体重采样+解码（边界无插值伪影）
            self._sherpa_acc.append(_pcm_to_int16(chunk))

    async def close(self) -> None:
        if self._closed:
            return
        try:
            if self._spell:
                text = self._vosk_final(self._vosk_rec) if self._vosk_rec else ""
                # 残余音频（最后念的字母没触发边界，如一口气念完整词）也按
                # 语句级结果判格；随后仍走 final 保持 base.py「close 必产
                # final」的契约（final 由 _on_end 消费，不重复判格）。
                if text:
                    self._emit_utterance(text)
            else:
                text = self._sherpa_close_decode()
            self._emit_final(text, None, self.audio_ms)
        finally:
            self._closed = True
            self._vosk_rec = None
            self._sherpa_stream = None
            self._sherpa_acc = []

    def _sherpa_close_decode(self) -> str:
        if not self._sherpa_stream or not self._sherpa_acc:
            return ""
        rec = _sherpa_rec if _sherpa_rec is not None else None
        # 单例可能尚未加载（极端并发），这里按需取
        if rec is None:
            rec = _load_sherpa_rec()
        # ⚠️ SenseVoice 原生 16k，网关 PCM 就是 16k，直接整段喂入离线解码。
        #    禁止自写重采样——线性插值无抗混叠会毁掉声学特征（预研实测）。
        whole = np.concatenate(self._sherpa_acc)
        f32 = (whole.astype(np.float64) / 32768.0).astype(np.float32)
        self._sherpa_accept(self._sherpa_stream, SENSEVOICE_SR, f32)
        self._sherpa_decode(rec, self._sherpa_stream)
        return self._sherpa_result(self._sherpa_stream)


# ---------------------------------------------------------------------------
# 探活登记（供 /api/capabilities 的 engine_ready 轻量探活）
# ---------------------------------------------------------------------------

register_model_spec(
    "asr",
    imports=["vosk", "sherpa_onnx"],
    files=_MODEL_SPECS_FILES,
    detail="vosk-small-en-us-0.15（字母拼读） + sensevoice-small-int8（中英转写，契约修订 v1.0.2）",
)
