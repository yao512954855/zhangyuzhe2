"""本地 TTS 引擎（sherpa-onnx 离线合成）—— localize-dev/01 §1.1/§6。

懒加载单例 + 双检锁；加载失败置 ready=False（60s 冷却，防雪崩）。
英文 lang=en-US → vits-piper-en_US-amy-medium；中文 zh-CN → vits-melo-tts-zh_en。
输出：float32 PCM → 16bit PCM → 包成 WAV 字节（RIFF），采样率取模型自身
（piper 22050 / melo 44100 均可，客户端 audioplayers 不挑采样率）。

⚠️ 本模块 import 时无副作用：不 import sherpa_onnx、不查盘。真正 import
   sherpa_onnx 推迟到首次 _ensure_engine，避免拖累 app 启动（capability.py 约定）。
⚠️ 模块底部调用 register_model_spec 把模型清单登记给 capability.engine_ready
   做轻量探活（只查文件 + import，不加载模型）。
"""

from __future__ import annotations

import threading
import time
import wave
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import BASE_DIR
from app.core.logging import get_logger

logger = get_logger(__name__)

_MODEL_ROOT = BASE_DIR / "models" / "tts"

# lang -> 模型目录候选（按序尝试，第一个加载成功者生效；效果优先升级 2026-10-19）
#   en-US：Kokoro-82M int8（音质代差提升）→ piper amy 兜底；zh-CN 仍 melo
_MODEL_CANDIDATES: dict[str, list[str]] = {
    "en-US": ["kokoro-int8-multi-lang-v1_1", "vits-piper-en_US-amy-medium"],
    "zh-CN": ["vits-melo-tts-zh_en"],
}

# lang -> 主模型目录（探活提示/报错用）
_MODEL_DIRS: dict[str, str] = {
    lang: cands[0] for lang, cands in _MODEL_CANDIDATES.items()
}

# 单例：模型目录名 -> 已加载的 sherpa OfflineTts 实例（进程内单例，契约 §6）
_engine: dict[str, Any] = {}
_engine_lock = threading.Lock()  # 双检锁 + 串行合成（sherpa offline 并发需串行）
# 加载失败冷却：模型名 -> 冷却到期 monotonic 时间（60s 内不反复重试加载）
_fail_until: dict[str, float] = {}
_fail_lock = threading.Lock()

_READY_TTL_S = 60.0


def _discover(model_dir: Path) -> tuple[str, str, str]:
    """在模型目录里定位 .onnx / tokens.txt / data_dir。

    data_dir：piper / melo 都不需要，故默认空；仅当目录内显式存在
    lexicon.txt / date.fst 等表外词典时才启用（当前两模型均不触发）。
    """
    onnx_files = [
        p for p in model_dir.glob("*.onnx") if not p.name.endswith(".onnx.json")
    ]
    if not onnx_files:
        raise FileNotFoundError(f"未找到 .onnx 模型文件：{model_dir}")
    # 优先取文件名最短的（主模型，过滤 .onnx.json 元数据）
    onnx_path = str(sorted(onnx_files, key=lambda p: len(p.name))[0])

    tokens = model_dir / "tokens.txt"
    if not tokens.is_file():
        raise FileNotFoundError(f"未找到 tokens.txt：{model_dir}")

    data_dir = ""
    # data_dir 只用于 espeak-ng-data（piper 系）；melo 等中文模型没有该目录，
    # ⚠️ 绝不能因存在 lexicon.txt 就把 data_dir 指到模型目录——sherpa 会拿假
    #    espeak 数据破坏前端，合成出近静音垃圾（2026-10-13 云端验音实测）。
    #    melo 需要的是 lexicon + dict_dir，在 _load 里单独传。
    espeak = model_dir / "espeak-ng-data"
    if not espeak.is_dir():
        for sub in model_dir.rglob("espeak-ng-data"):
            if sub.is_dir():
                espeak = sub
                break
    if espeak.is_dir():
        data_dir = str(espeak)

    return onnx_path, str(tokens), data_dir


def _load(model_dir: Path, name: str) -> Any:
    import sherpa_onnx  # 延迟 import：app 启动期不背这依赖

    if not model_dir.is_dir():
        raise FileNotFoundError(f"模型目录不存在：{model_dir}")

    if name.startswith("kokoro"):
        return _load_kokoro(model_dir)

    onnx_path, tokens_path, data_dir = _discover(model_dir)
    # vits 系中文模型（melo）需要 lexicon + jieba dict_dir；piper 等无此文件传空即可
    lexicon = model_dir / "lexicon.txt"
    dict_dir = model_dir / "dict"
    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=onnx_path,
        lexicon=str(lexicon) if lexicon.is_file() else "",
        tokens=tokens_path,
        data_dir=data_dir,
        dict_dir=str(dict_dir) if dict_dir.is_dir() else "",
    )
    model_config = sherpa_onnx.OfflineTtsModelConfig(
        vits=vits,
        num_threads=1,
        provider="cpu",
    )
    config = sherpa_onnx.OfflineTtsConfig(model=model_config)
    tts = sherpa_onnx.OfflineTts(config)
    if not tts:  # 极少数情况返回空句柄
        raise RuntimeError("sherpa OfflineTts 初始化返回空")
    return tts


def _load_kokoro(model_dir: Path) -> Any:
    """Kokoro-82M int8 多语种（zh+en）加载（效果优先升级 2026-10-19）。

    voices.bin 单文件打包全部音色，sid=0 为默认英文女声（af_heart）；
    中文数字/日期归一走 rule_fsts（date/number/phone-zh.fst）。
    """
    import sherpa_onnx

    model = model_dir / "model.int8.onnx"
    voices = model_dir / "voices.bin"
    tokens = model_dir / "tokens.txt"
    for p in (model, voices, tokens):
        if not p.is_file():
            raise FileNotFoundError(f"Kokoro 模型文件缺失：{p}")
    data_dir = model_dir / "espeak-ng-data"
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Kokoro espeak-ng-data 缺失：{data_dir}")

    lexicon = model_dir / "lexicon-us-en.txt"
    dict_dir = model_dir / "dict"
    rule_fsts = ",".join(
        str(model_dir / f)
        for f in ("date-zh.fst", "number-zh.fst", "phone-zh.fst")
        if (model_dir / f).is_file()
    )
    kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
        model=str(model),
        voices=str(voices),
        tokens=str(tokens),
        data_dir=str(data_dir),
        lexicon=str(lexicon) if lexicon.is_file() else "",
        dict_dir=str(dict_dir) if dict_dir.is_dir() else "",
    )
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            kokoro=kokoro, num_threads=2, provider="cpu",
        ),
        rule_fsts=rule_fsts,
    )
    tts = sherpa_onnx.OfflineTts(config)
    if not tts:
        raise RuntimeError("sherpa OfflineTts 初始化返回空")
    return tts


def _ensure_engine(lang: str) -> Any:
    """懒加载 + 双检锁 + 失败冷却 + 候选回退。返回已加载的 OfflineTts 实例。"""
    if lang not in _MODEL_DIRS:
        raise ValueError(
            f"本地 TTS 不支持语言 {lang!r}（可选：{', '.join(_MODEL_DIRS)}）"
        )

    # 失败冷却：60s 内不反复重试加载（防雪崩，契约 §6）
    with _fail_lock:
        if _fail_until.get(lang, 0.0) > time.monotonic():
            raise RuntimeError(f"本地 TTS 引擎 {lang} 冷却中（上次加载失败）")

    # 双检锁：命中单例直接返回
    inst = _engine.get(lang)
    if inst is not None:
        return inst

    with _engine_lock:
        inst = _engine.get(lang)
        if inst is not None:
            return inst
        # 候选按序尝试：Kokoro 缺失/加载失败 → piper 兜底
        last_exc: Exception | None = None
        for cand in _MODEL_CANDIDATES.get(lang, []):
            try:
                inst = _load(_MODEL_ROOT / cand, cand)
                break
            except Exception as exc:  # noqa: BLE001 - 记录后试下一候选
                last_exc = exc
                logger.warning("本地 TTS 候选引擎加载失败 %s：%s", cand, exc)
                inst = None
        if inst is None:
            exc = last_exc or FileNotFoundError(f"本地 TTS 无可用引擎 lang={lang}")
            logger.warning("本地 TTS 引擎加载失败 lang=%s：%s", lang, exc)
            with _fail_lock:
                _fail_until[lang] = time.monotonic() + _READY_TTL_S
            raise exc
        _engine[lang] = inst
        return inst


def synthesize_local(text: str, lang: str = "zh-CN") -> bytes:
    """本地离线合成 → WAV 字节。失败抛 Exception（由路由层决定 50006 或回落云端）。

    约定：抛异常即代表「本引擎不可用/产物非法」，路由层据此走契约 §1.1 矩阵。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("本地 TTS 合成文本为空")

    tts = _ensure_engine(lang)
    # sherpa offline tts 实例并发合成需串行（听写播报单用户场景，写死此决策）
    with _engine_lock:
        try:
            result = tts.generate(text, sid=0, speed=1.0)
        except Exception as exc:  # noqa: BLE001 - 合成失败交给路由层
            logger.warning("sherpa 合成失败 lang=%s text=%r: %s", lang, text[:20], exc)
            raise

    samples = getattr(result, "samples", None)
    if samples is None or len(samples) == 0:
        raise RuntimeError("本地 TTS 产出空音频")
    sample_rate = int(getattr(result, "sample_rate", 22050))
    return _to_wav_bytes(samples, sample_rate)


def _to_wav_bytes(samples: Any, sample_rate: int) -> bytes:
    """float32 [-1,1] PCM → 16bit PCM WAV（RIFF）字节。"""
    arr = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16)
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def is_ready() -> tuple[bool, str]:
    """轻量探活：两模型文件齐全 + sherpa_onnx 可 import。

    只查「必需文件存在 + 包可 import」，**不加载模型**（懒加载语义，契约 §6）。
    结果由 capability.engine_ready 再缓存 60s。
    """
    try:
        import sherpa_onnx  # noqa: F401 - 仅验证可 import
    except Exception as exc:  # noqa: BLE001
        return False, f"缺少依赖包 sherpa_onnx（{type(exc).__name__}）"

    missing: list[str] = []
    for lang, dirname in _MODEL_DIRS.items():
        d = _MODEL_ROOT / dirname
        if not d.is_dir():
            missing.append(f"{dirname}(目录缺失)")
            continue
        try:
            _discover(d)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{dirname}({exc})")

    if missing:
        return False, "模型文件缺失：" + "、".join(missing[:3]) + (
            "等" if len(missing) > 3 else ""
        )
    return True, "kokoro-82M-int8（英文，兜底 piper）+ melo-zh_en"


def _register_specs() -> None:
    """把 TTS 模型清单登记给 capability.engine_ready（轻量探活用）。

    在 import 时调用。**中英文两模型都必须齐备**才算 tts 就绪
    （契约 §1.1：TTS 同时服务英文报词与中文报义，缺一不可用）。
    因此对两个模型目录各自登记「就绪所需关键文件」：tokens.txt 必存在，
    onnx 存在则一并登记；任一模型目录缺失/文件不全 → 探活判 False。
    """
    from app.core.capability import register_model_spec

    imports = ["sherpa_onnx"]
    files: list[str] = []
    ready_dirs = 0
    for lang, dirname in _MODEL_DIRS.items():
        d = _MODEL_ROOT / dirname
        # tokens.txt 是各 sherpa tts 模型通用必备文件；用它代表「该模型已下载」。
        files.append(str(d / "tokens.txt"))
        if d.is_dir():
            try:
                onnx_path, _tokens_path, _ = _discover(d)
                files.append(onnx_path)  # onnx 存在再登记，缺失则由 tokens.txt 判 False
                ready_dirs += 1
            except Exception:  # noqa: BLE001 - 文件不全则忽略，探活会判 False
                pass
        else:
            # 模型目录整体缺失：补一个必不存在的文件，确保探活判 False
            files.append(str(d / "model.onnx"))

    detail = (
        "kokoro-82M-int8（英文，兜底 piper）+ melo-zh_en"
        if ready_dirs == len(_MODEL_DIRS)
        else "本地 TTS 模型未就绪"
    )
    register_model_spec("tts", imports=imports, files=files, detail=detail)


# import 副作用：仅登记清单（轻量，无 import 重活、无磁盘加载）
_register_specs()
