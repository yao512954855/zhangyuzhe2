"""TTS 合成服务 —— 听写播报通道（契约 v1.2，叠加 localize-dev/01 §1.1 三模式）。

为什么需要：部分国产 ROM（实测小米/MIUI）**不预装任何标准 TTS 引擎**，
`flutter_tts` 直接 `speak failed: not bound to TTS engine`。听写播报不能
依赖机型运气，故提供后端合成端点，App 端设备 TTS 失败时自动回退。

实现：云端走阿里百炼 `qwen3-tts-flash`（HTTP 非流式），复用 `asr.yaml` 的
api_key（不新增凭证文件）；本地走 sherpa-onnx 离线合成（tts_local）。

模式路由（契约 §1.1）：
- local  ：只走本地；失败 → BizError(50006)，**绝不**碰云端（契约红线）
- hybrid ：先本地；本地抛任何异常/产物非法 → 自动走云端；云端也失败 → 50005
- cloud  ：与现状一致，只走云端

缓存：键改为 md5(f"{engine}|{lang}|{text}")，engine∈{"local","cloud"}，
不同引擎产物分开放、互不命中（旧无 engine 前缀的键自然不命中，无需迁移）。
缓存目录不变 data/tts_cache/。

⚠️ httpx 必须 `trust_env=False`：WorkBuddy 会话注入的慢代理会坑死 dashscope 请求。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.core.capability import engine_ready, resolve_mode
from app.core.config import BASE_DIR
from app.core.errors import BizError, ErrorCode
from app.core.logging import get_logger
from app.services import tts_local

logger = get_logger(__name__)

_DASHSCOPE_TTS_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
)
_TTS_MODEL = "qwen3-tts-flash"
_TTS_VOICE = "Cherry"  # 该 key 当前唯一已授权音色；中英文均可朗读
_MAX_TEXT_LEN = 200
_TIMEOUT_S = 15.0

_CACHE_DIR = BASE_DIR / "data" / "tts_cache"


def _load_api_key() -> str:
    """取百炼 key（与 ASR 同源，不新增凭证文件）：settings.cloud_api_key 优先，
    回落 asr.yaml 种子 —— 管理端改 key 对云端 TTS 同样实时生效。"""
    from app.services.asr.key_store import resolve

    path = BASE_DIR / "asr.yaml"
    if not path.exists():
        return resolve("")
    try:
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # pragma: no cover - YAML 语法错误
        return resolve("")
    aliyun = (raw.get("aliyun") or {}) if isinstance(raw, dict) else {}
    return resolve(str(aliyun.get("api_key") or ""))


def _cache_path(engine: str, text: str, lang: str) -> Path:
    """缓存键 md5(f"{engine}|{lang}|{text}")（契约 §1.1）。"""
    digest = hashlib.md5(f"{engine}|{lang}|{text}".encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"{digest}.wav"


def _synthesize_remote(text: str, api_key: str) -> bytes:
    """调百炼 qwen3-tts-flash 合成，返回 wav 字节。失败抛 BizError。"""
    try:
        r = httpx.post(
            _DASHSCOPE_TTS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": _TTS_MODEL, "input": {"text": text, "voice": _TTS_VOICE}},
            timeout=_TIMEOUT_S,
            trust_env=False,
        )
    except httpx.HTTPError as e:
        logger.warning("云端 TTS 请求失败: %s", e)
        raise BizError(ErrorCode.TTS_SYNTH_FAILED) from e
    if r.status_code != 200:
        logger.warning("云端 TTS 返回 %s: %s", r.status_code, r.text[:200])
        raise BizError(ErrorCode.TTS_SYNTH_FAILED)
    try:
        audio_url = r.json()["output"]["audio"]["url"]
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("云端 TTS 响应缺 audio.url: %s", r.text[:200])
        raise BizError(ErrorCode.TTS_SYNTH_FAILED) from e
    try:
        audio = httpx.get(audio_url, timeout=_TIMEOUT_S, trust_env=False)
        audio.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("云端 TTS 音频下载失败: %s", e)
        raise BizError(ErrorCode.TTS_SYNTH_FAILED) from e
    if len(audio.content) < 128 or not audio.content.startswith(b"RIFF"):
        logger.warning("云端 TTS 音频内容异常 bytes=%s", len(audio.content))
        raise BizError(ErrorCode.TTS_SYNTH_FAILED)
    return audio.content


# ---------------------------------------------------------------------------
# 三模式路由实现
# ---------------------------------------------------------------------------

def _local_available() -> bool:
    """本地 TTS 能力是否就绪（与 capability.engine_ready 探活口径一致：两模型齐备）。

    契约：本地 TTS 能力以「英文(piper)+中文(melo)」为一个整体对外暴露，
    任一模型缺失即视为本地能力未就绪 —— 此时 local 模式应报 50006、
    hybrid 应直接回落云端（避免半残能力被误用）。
    """
    try:
        ready, _ = engine_ready("tts")
    except Exception:  # noqa: BLE001 - 探活异常按不可用处理
        ready = False
    return ready


def _synthesize_cloud(text: str, lang: str) -> bytes:
    """纯云端通道（与历史行为一致）。命中 cloud 缓存直接返回。"""
    cached = _cache_path("cloud", text, lang)
    if cached.exists() and cached.stat().st_size > 128:
        return cached.read_bytes()

    api_key = _load_api_key()
    if not api_key:
        logger.warning("asr.yaml 缺百炼 api_key，云端 TTS 不可用")
        raise BizError(ErrorCode.TTS_SYNTH_FAILED)

    t0 = time.monotonic()
    audio = _synthesize_remote(text, api_key)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(audio)
    logger.info(
        "TTS 合成 ok engine=cloud chars=%s bytes=%s cost=%.2fs text=%r",
        len(text), len(audio), time.monotonic() - t0, text[:20],
    )
    return audio


def _synthesize_local_only(text: str, lang: str) -> bytes:
    """纯本地通道：失败必须抛 50006 且**绝不**碰云端（契约 §1.1 红线）。"""
    if not _local_available():
        # 本地能力未就绪（模型缺失/包未装）→ 直接 50006，不尝试加载、不碰云端
        logger.warning("本地 TTS 能力未就绪（local 模式不回落云端）")
        raise BizError(ErrorCode.LOCAL_ENGINE_UNAVAILABLE)

    cached = _cache_path("local", text, lang)
    if cached.exists() and cached.stat().st_size > 128:
        return cached.read_bytes()

    try:
        audio = tts_local.synthesize_local(text, lang)
    except Exception as exc:  # noqa: BLE001 - 任何本地异常都转 50006，不回落
        logger.warning("本地 TTS 引擎失败（local 模式不回落云端）: %s", exc)
        raise BizError(ErrorCode.LOCAL_ENGINE_UNAVAILABLE) from exc

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(audio)
    logger.info(
        "TTS 合成 ok engine=local chars=%s bytes=%s text=%r",
        len(text), len(audio), text[:20],
    )
    return audio


def _synthesize_hybrid(text: str, lang: str) -> bytes:
    """混合通道：本地能力就绪时先本地；本地失败 log 后回落云端；云端也失败 → 50005。

    本地能力未就绪（如中文模型缺失）→ 跳过本地直接走云端兜底，不浪费加载尝试。
    """
    if _local_available():
        cached = _cache_path("local", text, lang)
        if cached.exists() and cached.stat().st_size > 128:
            return cached.read_bytes()

        try:
            audio = tts_local.synthesize_local(text, lang)
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(audio)
            logger.info(
                "TTS 合成 ok engine=local chars=%s bytes=%s text=%r",
                len(text), len(audio), text[:20],
            )
            return audio
        except Exception as exc:  # noqa: BLE001 - 本地失败，回落云端
            logger.warning("本地 TTS 失败，回落云端: %s", exc)

    return _synthesize_cloud(text, lang)


def synthesize(text: str, lang: str = "zh-CN") -> bytes:
    """合成播报音频（wav），带磁盘缓存。仅供 `api/tts.py` 调用。

    签名不变（api/tts.py 零改动）；按 resolve_mode("tts") 路由 local/hybrid/cloud。
    """
    text = text.strip()
    if not text or len(text) > _MAX_TEXT_LEN:
        raise BizError(ErrorCode.PARAM_INVALID, message="播报文本为空或超长")
    if lang not in ("en-US", "zh-CN"):
        lang = "zh-CN"

    mode = resolve_mode("tts")
    if mode == "cloud":
        return _synthesize_cloud(text, lang)
    if mode == "local":
        return _synthesize_local_only(text, lang)
    # hybrid（默认）
    return _synthesize_hybrid(text, lang)
