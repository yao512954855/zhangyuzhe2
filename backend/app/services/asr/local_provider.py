"""本地 ASR Provider —— 批次 E（契约《01-契约冻结》§1.3 / §6 / 工作包 13 §4）。

**引擎路由**（按 `cfg.task_type`，与网关建 `ASRTaskConfig` 的口径一致，见
`session_manager.py:538`：spell_zh2en→task_type="spell"、spell_en2zh→"recite"）：

| task_type | 引擎 | 说明 |
|---|---|---|
| `"spell"`（逐字母拼读 / spell_zh2en） | Vosk + 26 字母 grammar | **语句边界实时出句**：每检测到一句话结束（念完一个字母停一下）即经 utterance 回调透传网关回显（决策 #11），并累积为松开（end）后整词判定 verdict 的转写来源（契约 §5 v1.3）；partial 仅供调试展示 |
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
import re
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
WHISPER_DIR_NAME = "sherpa-onnx-whisper-small.en"

LETTERS = [chr(ord("a") + i) for i in range(26)]
GRAMMAR = json.dumps(LETTERS)                       # Vosk 锁死 26 字母
VOSK_SR = 16000
SENSEVOICE_SR = 16000                               # SenseVoice 原生率

#: 服务端静音收口阈值（ms）：partial 停止变化超过这么久 → 主动取 Result 出句。
#: Vosk 内置 VAD 对短停顿不敏感（2026-10-13 实测 22 条 qa spell 音频
#: **0 次** AcceptWaveform 边界触发），真人「念一个字母停一下」必须靠
#: 这条路径保证实时回显（契约 §5 v1.3：utterance 透传 + verdict 转写来源）。
#: 700ms 取值：小于常见字母间停顿（0.3~1s）的下沿风险区，又不至于把
#: 念 "double u" 这类词内小停顿切断（切断也无妨——每个 flush 出的片段
#: 独立成句、按序累积，整词判定时顺序不错位）。
SILENCE_FLUSH_MS = 700

_MODEL_SPECS_FILES = [
    "models/asr/vosk-model-small-en-us-0.15/am/final.mdl",
    "models/asr/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/"
    "model.int8.onnx",
    "models/asr/sherpa-onnx-whisper-small.en/small.en-encoder.int8.onnx",
    "models/asr/sherpa-onnx-whisper-small.en/small.en-decoder.int8.onnx",
]


def _asr_model_dirs() -> tuple[Any, Any]:
    asr = BASE_DIR / "models" / "asr"
    vosk = next((p for p in asr.iterdir()
                 if p.is_dir() and p.name.startswith(VOSK_MODEL_PREFIX)), None)
    sherpa = next((p for p in asr.iterdir()
                   if p.is_dir() and p.name.startswith(SHERPA_MODEL_PREFIX)), None)
    return vosk, sherpa


def _whisper_model_dir() -> Any:
    asr = BASE_DIR / "models" / "asr"
    return next((p for p in asr.iterdir()
                 if p.is_dir() and p.name == WHISPER_DIR_NAME), None)


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


#: Whisper 判分单例（效果优先升级 2026-10-19）：小模型缺失 → None（回退 Vosk），
#: **不**抛 50006——在线拼读链路仍由 Vosk 支撑，whisper 只参与重听判分。
_whisper_rec: Any = None


def _load_whisper_rec() -> Any:
    """whisper-small.en int8 OfflineRecognizer 单例（仅 offline_decode 用）。"""
    global _whisper_rec
    if _whisper_rec is not None:
        return _whisper_rec
    with _model_lock:
        if _whisper_rec is not None:
            return _whisper_rec
        wdir = _whisper_model_dir()
        if wdir is None:
            return None
        enc = wdir / "small.en-encoder.int8.onnx"
        dec = wdir / "small.en-decoder.int8.onnx"
        tokens = wdir / "small.en-tokens.txt"
        if not (enc.is_file() and dec.is_file() and tokens.is_file()):
            return None
        import sherpa_onnx

        _whisper_rec = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=str(enc), decoder=str(dec), tokens=str(tokens),
            num_threads=2, provider="cpu",
        )
        logger.info("Whisper 判分模型已加载：%s", wdir.name)
    return _whisper_rec


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

        念字母场景下一句就是一个字母：停一下边界即触发，服务端立刻透传
        回显（契约 §5 v1.3），不等 App 发 end。
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
            # 频域谱减预处理状态（真机低电平上行，见 push_audio 注释）
            self._sp_buf = np.zeros(0, dtype=np.float64)  # 不足一帧的残留样本
            self._sp_hist: list[np.ndarray] = []          # 噪声观测期的帧幅谱
            self._sp_noise: np.ndarray | None = None      # 冻结后的噪声幅谱
            self._sp_frozen = False                       # 噪声谱是否已冻结
            # 离线重解码素材（重听式判分，2026-10-16）：捕获**降噪后**的上行
            # PCM（≤30s），松开后整段重解码一次。与实时链路同一预处理——
            # 拿原始噪音重听反而会不如流式（坑已踩过）。
            self._offline_buf = bytearray()
        else:
            rec = _load_sherpa_rec()                      # 缺失 → 50006
            self.model = "sensevoice-small-int8"
            self._sherpa_stream = self._new_sherpa_stream(rec)
        logger.info("LocalProvider start task_type=%s engine=%s",
                    cfg.task_type, "vosk" if self._spell else "sensevoice")

    # ------------------------------------------------------------------
    # spell 通道维纳预处理（真机低电平上行专用）
    # ------------------------------------------------------------------

    #: 处理帧长：512 样本 = 32ms @16k（非重叠，rect 帧块效应对 Vosk 可忽略）
    _SP_FRAME = 512
    #: 强人声检测：帧能量 > 4× 历史安静帧中位能量 → 判定语音开始，冻结噪声谱
    _SP_VOICE_JUMP = 4.0
    #: 静音观测窗上限：攒满这么多帧仍无强人声也冻结（真机按住后 0.9~1.4s
    #: 才开读=28~44 帧；夹具开头静音短，靠强人声跳变提前冻结）
    _SP_MAX_QUIET = 40
    #: 噪声谱候选帧下限（少于这个数宁可继续等）
    _SP_MIN_QUIET = 3
    #: 幅度谱减系数与保底下限（⚠️ 必须用**幅度减法 + 低 floor**：
    #: 功率维纳对人声帧是「信号主导 bin 直通」，压制温和到 Vosk 无感
    #: （实锤能量比仅 0.988、输出逐样本不变）；离线验证有效的是
    #: mag - α·noise、floor=0.05 的幅度谱减，apple/orange 由错变全对）
    _SP_ALPHA = 1.5
    _SP_FLOOR = 0.05

    def _wiener_denoise(self, chunk: bytes) -> bytes:
        x = np.frombuffer(chunk, dtype=np.int16).astype(np.float64) / 32768.0
        buf = np.concatenate([self._sp_buf, x])
        n_frames = len(buf) // self._SP_FRAME
        if n_frames == 0:
            self._sp_buf = buf
            return chunk
        out = np.empty(n_frames * self._SP_FRAME, dtype=np.float64)
        for k in range(n_frames):
            frame = buf[k * self._SP_FRAME:(k + 1) * self._SP_FRAME]
            spec = np.fft.rfft(frame)
            mag = np.abs(spec)
            if not self._sp_frozen:
                # 噪声观测期：攒静音帧谱，强人声出现或攒满即冻结。
                # ⚠️ 不能用在线 EMA/min-tracking：门控挡不住静音期的呼吸/
                # 摩擦声（真机实测 noise_e 被抬到人声 e 的 81%，谱减后失真
                # banana→v a a a），min-tracking 又会统计到随机下尾巴
                # （低 5~10dB → gain≈1 形同直通）。业务事实「按住后 ~1s
                # 静音才开读」给了纯净噪声窗，取观测期帧的中位谱。
                e = float(np.sum(mag ** 2))
                hist_e = [float(np.sum(m ** 2)) for m in self._sp_hist]
                voice_started = (
                    len(hist_e) >= self._SP_MIN_QUIET
                    and e > self._SP_VOICE_JUMP * float(np.median(hist_e))
                )
                if voice_started or len(hist_e) >= self._SP_MAX_QUIET:
                    self._sp_noise = np.median(
                        self._sp_hist or [mag], axis=0)
                    self._sp_frozen = True
                else:
                    self._sp_hist.append(mag)
                out[k * self._SP_FRAME:(k + 1) * self._SP_FRAME] = frame
                continue
            # 幅度谱减（与离线验证实验同公式）：mag - α·noise，floor 保底
            mag_new = np.maximum(
                mag - self._SP_ALPHA * self._sp_noise,
                self._SP_FLOOR * mag,
            )
            out[k * self._SP_FRAME:(k + 1) * self._SP_FRAME] = np.fft.irfft(
                mag_new * np.exp(1j * np.angle(spec)), self._SP_FRAME)
        self._sp_buf = buf[n_frames * self._SP_FRAME:]
        return np.clip(out * 32767, -32768, 32767).astype(np.int16).tobytes()

    async def push_audio(self, chunk: bytes) -> None:
        await self._push_checked(chunk)                   # 校验 16k/单声道/16bit
        if self._spell:
            # 手机麦克风上行电平低（真机实测峰值仅 0.10~0.16、鼻音/弱尾音
            # 能量簇被底噪淹没），Vosk small 对此敏感：apple→eppl、banana 丢 nn、
            # orange 丢 n。温和维纳滤波（噪声谱取本会话最安静 20% 帧的中位）
            # 实测：apple/orange 由错变全对，干净夹具逐字零退化（2026-10-16）。
            # 仅 spell 通道加预处理；SenseVoice/云端对噪声鲁棒不动。
            chunk = self._wiener_denoise(chunk)
            # 重听素材捕获：降噪后 PCM（≤30s，spell 会话通常 2~8s）
            if len(self._offline_buf) < 960000:
                self._offline_buf.extend(chunk)
            # Vosk 拼读通道：两条出句触发路径（契约 §5 v1.3）——
            # ① Vosk 内置端点边界（念完整词不停/长停顿）；
            # ② 服务端静音收口：partial 停止变化超过 SILENCE_FLUSH_MS
            #    主动取 Result（真人「念一个字母停一下」的主路径）。
            # 每句经 utterance 透传回显（决策 #11）并累积为 end 后整词
            # 判定 verdict 的转写来源。
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

    def offline_decode(self) -> str:
        """重听式判分（2026-10-16 姚工拍板）：整段降噪音频离线重解码。

        流式识别边听边猜，字母听岔当场定版擦不掉（真机实测 apple 中段
        'app' 被猜成 'apx'、good morning 被切碎成三段 utterance）。松开后
        拿完整上下文重新解码一遍，显著更准。判分侧 session_manager 两边
        择优：重听为主、流式累积兜底，任一命中目标即判对；两遍皆错才判错。

        效果优先升级（2026-10-19）：判分引擎 Vosk → **whisper-small.en
        int8**（sherpa 离线，完整上下文强于 26 字母 grammar 的小模型）。
        whisper 缺失/异常时回退 Vosk 整段重解码，行为与旧版一致。

        输入是 push_audio 捕获的**降噪后** PCM（与实时链路同一预处理）；
        非 spell 通道 / 无素材 / 解码异常一律返回空串 → 判分侧自动回退
        流式累积，行为与旧版一致。
        """
        if not self._spell:
            return ""
        buf = bytes(getattr(self, "_offline_buf", b""))
        if not buf:
            return ""
        # ① whisper 判分（完整上下文，标点/大小写在本地归一化）
        text = self._whisper_decode(buf)
        if text:
            return text
        # ② 回退：Vosk 整段重解码（旧版逻辑，whisper 缺失/异常时到达）
        try:
            rec = self._new_vosk_recognizer(_load_vosk_model())
            texts: list[str] = []
            step = 3200                                    # 200ms @16k/16bit
            for i in range(0, len(buf), step):
                if self._vosk_accept(rec, buf[i:i + step]):
                    t = self._vosk_result(rec)
                    if t:
                        texts.append(t)
            t = self._vosk_final(rec)
            if t:
                texts.append(t)
            return " ".join(texts).strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("离线重解码失败，回退流式累积：%s", exc)
            return ""

    @staticmethod
    def _whisper_decode(pcm16: bytes) -> str:
        """whisper 离线解码降噪后整段 PCM；不可用/异常 → 空串（调用方回退）。"""
        rec = _load_whisper_rec()
        if rec is None:
            return ""
        try:
            f32 = (np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
                   / 32768.0)
            stream = rec.create_stream()
            stream.accept_waveform(16000, f32)
            rec.decode_stream(stream)
            text = stream.result.text
            # whisper 会输出标点/大小写（如 'B. O. O. K.'），judge.to_letters
            # 的 _key 只做小写与空白归一，标点必须在此剥掉，否则 token 匹配
            # 全部落空（'b.' ≠ 'b'）。
            return " ".join(re.sub(r"[^a-zA-Z ]", " ", text).split()).lower()
        except Exception as exc:  # noqa: BLE001
            logger.warning("whisper 离线解码异常，回退 Vosk 重解码：%s", exc)
            return ""

    async def close(self) -> None:
        if self._closed:
            return
        try:
            if self._spell:
                text = self._vosk_final(self._vosk_rec) if self._vosk_rec else ""
                # 残余音频（最后念的字母没触发边界，如一口气念完整词）也按
                # 语句级结果透传（verdict 转写来源）；随后仍走 final 保持
                # base.py「close 必产 final」的契约（final 由 _on_end 消费，
                # 整词判定只此一次）。
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
    detail="vosk-small-en-us-0.15（字母拼读实时回显） + sensevoice-small-int8（中英转写）"
           " + whisper-small.en-int8（重听判分，效果优先升级）",
)
