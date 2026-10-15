"""AI 能力三模式解析 —— 契约《docs/localize-dev/01-契约冻结》§2。

三模式：local（纯本地）/ hybrid（本地优先失败切云端）/ cloud（纯云端）。

配置源与优先级（写死，不准其他模块自己拼逻辑）：
  1. `.env` 锁定键 CAP_TTS / CAP_OCR / CAP_ASR（非空且合法 → 直接生效 = 部署级锁定）
  2. settings 表键 `capability_mode`（管理端按钮写的，热生效）
  3. 都没有 → 默认 hybrid

⚠️ resolve_mode 必须**每次现读**（进程内不准缓存模式值）——管理端改完立即生效。
   engine_ready 的探活结果允许缓存 60s（§6 防雪崩），但那是「模型文件状态」不是「模式」。

⚠️ 本模块 import 时不做任何重活（不 import 引擎包、不查文件系统）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from app.core.config import BASE_DIR
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 能力清单（与契约 §2 的 env 键一一对应）
CAPS: tuple[str, ...] = ("tts", "ocr", "asr")
_CAP_ENV_KEYS: dict[str, str] = {"tts": "CAP_TTS", "ocr": "CAP_OCR", "asr": "CAP_ASR"}

_VALID_MODES = {"local", "hybrid", "cloud"}
_DEFAULT_MODE = "hybrid"

_SETTINGS_KEY = "capability_mode"

#: 探活结果缓存秒数（契约 §6：60s 内不反复重试加载判定）
_READY_CACHE_TTL_S = 60.0

# ---------------------------------------------------------------------------
# 模型清单 —— 各能力批次（11/12/13）往里登记；路径相对 BASE_DIR（backend/）。
# local_ready = 清单里所有必需文件存在 + required_imports 可 import。
# 批次 A 先给占位：detail 明确写「模型未登记」，local_ready=False。
# ---------------------------------------------------------------------------
_MODEL_SPECS: dict[str, dict[str, Any]] = {
    "tts": {
        "required_imports": [],          # 批次 B 填 ["sherpa_onnx"]
        "required_files": [],            # 批次 B 填模型文件相对路径
        "detail_pending": "本地 TTS 模型未登记",
    },
    "ocr": {
        "required_imports": [],          # 批次 C 填 ["rapidocr_onnxruntime"]
        "required_files": [],            # 包内置模型，通常为空
        "detail_pending": "本地 OCR 引擎未登记",
    },
    "asr": {
        "required_imports": [],          # 批次 E 填 ["vosk", "sherpa_onnx"]
        "required_files": [],
        "detail_pending": "本地 ASR 模型未登记",
    },
}

# 探活缓存：{cap: (expires_at, ready, detail)}
_ready_cache: dict[str, tuple[float, bool, str]] = {}


# ---------------------------------------------------------------------------
# .env 读取（照 dictation_service._read_ocr_env 的套路：进程环境优先，回落 .env）
# ---------------------------------------------------------------------------

def _read_env_value(key: str) -> str:
    """单个键：os.environ 优先，回落解析 backend/.env（不缓存，.env 很小）。"""
    val = os.environ.get(key)
    if val is not None and val.strip():
        return val.strip()
    env_file = BASE_DIR / ".env"
    if env_file.is_file():
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == key and v.strip():
                        return v.strip()
        except OSError:
            pass
    return ""


def is_locked(cap: str) -> bool:
    """对应 CAP_<CAP> 环境键非空即部署级锁定（契约 §2）。"""
    if cap not in CAPS:
        return False
    return bool(_read_env_value(_CAP_ENV_KEYS[cap]))


# ---------------------------------------------------------------------------
# 模式解析
# ---------------------------------------------------------------------------

def resolve_mode(cap: str) -> str:
    """tts/ocr/asr → 实际生效模式（local/hybrid/cloud）。

    优先级：env 锁定键 > settings.capability_mode > hybrid。
    非法值按 hybrid 处理并 warning（不准抛异常 —— 配置写错不能打死服务）。
    """
    if cap not in CAPS:
        raise ValueError(f"未知能力：{cap!r}（可选：{', '.join(CAPS)}）")

    locked = _read_env_value(_CAP_ENV_KEYS[cap]).lower()
    if locked:
        if locked in _VALID_MODES:
            return locked
        logger.warning(
            "CAP_%s=%s 非法（可选 local/hybrid/cloud），按 %s 处理",
            cap.upper(), locked, _DEFAULT_MODE,
        )
        return _DEFAULT_MODE

    # settings 现读（SettingsService 自带行级缓存，set() 会刷新 —— 管理端热生效）
    from app.services.settings_service import SettingsService  # 延迟导入防循环

    try:
        val = str(SettingsService().get_str(_SETTINGS_KEY, _DEFAULT_MODE)).strip().lower()
    except Exception as exc:  # noqa: BLE001 - DB 异常不打死请求
        logger.warning("读 settings.%s 失败（%s），按 %s 处理", _SETTINGS_KEY, exc, _DEFAULT_MODE)
        return _DEFAULT_MODE
    if val in _VALID_MODES:
        return val
    if val:
        logger.warning(
            "settings.%s=%r 非法（可选 local/hybrid/cloud），按 %s 处理",
            _SETTINGS_KEY, val, _DEFAULT_MODE,
        )
    return _DEFAULT_MODE


# ---------------------------------------------------------------------------
# 本地引擎探活（轻量：文件存在 + 包可 import；不触发模型加载 —— 契约 §5）
# ---------------------------------------------------------------------------

def register_model_spec(cap: str, *, imports: list[str], files: list[str], detail: str) -> None:
    """供 11/12/13 批次登记本地引擎清单（幂等覆盖）。"""
    _MODEL_SPECS[cap] = {
        "required_imports": list(imports),
        "required_files": list(files),
        "detail_pending": detail,
        "detail_ready": detail,
    }
    _ready_cache.pop(cap, None)


def engine_ready(cap: str) -> tuple[bool, str]:
    """本地引擎就绪判定：(True, 描述) / (False, 原因)。结果缓存 60s。

    只查「必需文件存在 + 包可 import」，**不加载模型**（懒加载语义，契约 §6）。
    """
    if cap not in CAPS:
        raise ValueError(f"未知能力：{cap!r}（可选：{', '.join(CAPS)}）")

    now = time.monotonic()
    cached = _ready_cache.get(cap)
    if cached and cached[0] > now:
        return cached[1], cached[2]

    ready, detail = _probe(cap)
    _ready_cache[cap] = (now + _READY_CACHE_TTL_S, ready, detail)
    return ready, detail


def _probe(cap: str) -> tuple[bool, str]:
    spec = _MODEL_SPECS.get(cap) or {}
    imports = list(spec.get("required_imports") or [])
    files = list(spec.get("required_files") or [])
    if not imports and not files:
        return False, str(spec.get("detail_pending") or "模型未登记")

    for mod in imports:
        try:
            __import__(mod)
        except Exception as exc:  # noqa: BLE001 - 探活吞一切 import 错
            return False, f"缺少依赖包 {mod}（{type(exc).__name__}）"

    missing: list[str] = []
    for rel in files:
        p = Path(rel)
        if not p.is_absolute():
            p = BASE_DIR / rel
        if not p.is_file() or p.stat().st_size <= 0:
            missing.append(rel)
    if missing:
        return False, "模型文件缺失：" + "、".join(missing[:3]) + ("等" if len(missing) > 3 else "")

    return True, str(spec.get("detail_ready") or "本地引擎就绪")
