"""听写批改服务 —— 契约《01-共享契约冻结》§6.3（WP-D 批次3 实现）。

链路：学生纸上听写 → 拍照上传 → 阿里云读光 `RecognizeHandwriting` 手写识别
→ 文字块按阅读顺序（y 行带 60px + x）排序 → 与词表逐个比对（忽略大小写 /
空格 / 连字符）→ 返回逐词对错与置信度。

要点（与实测脚本 `scripts/_ocr_probe.py` 一致的经验）：
- SDK 请求用**二进制 body**（`RecognizeHandwritingRequest(body=...)`），
  `need_rotate=True` 自动摆正拍摄方向；
- 响应体的 `Data` 字段是 **JSON 字符串**，必须二次 `json.loads`；
- `prob` 为整块置信度（0~100），**<85 标 `uncertain`**（实测错词块 prob=92
  已是全场最低，低于 85 基本可判可疑位）；
- 单例懒加载 `_OcrClient`：AK 从 `backend/.env`（`OCR_ACCESS_KEY_ID/SECRET`）
  或进程环境读取，超时读 `OCR_TIMEOUT_S`（默认 15，契约 §8）。

异常语义（冻结，不得改）：
- 云端 OCR 报错 / 超时 → `BizError(20005)`；
- 图片无效（空 / 超 10MB / 非 JPEG/PNG/WebP）→ `BizError(20006)`。

红线（契约 §10.8）：**单测一律 Mock 本模块的 OCR 客户端**，真实网络调用
只允许出现在集成验收与 `scripts/` 下的实测脚本里。
"""

from __future__ import annotations

import difflib
import json
import os
from typing import Any

from app.core.errors import BizError, ErrorCode

# ---- 常量（契约 §4 / §6.3 冻结语义）----
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB
_CONFIDENCE_UNCERTAIN_BELOW = 85  # 置信度低于此值 → uncertain（拿不准）
_ROW_BAND_PX = 60  # y 方向行带宽度（px），同带内按 x 排序
_MISREAD_SIMILARITY_MIN = 0.5  # 误识别配对的最小相似度（低于则视为无对齐）
_OCR_ENDPOINT = "ocr-api.cn-hangzhou.aliyuncs.com"
_DEFAULT_TIMEOUT_S = 15  # OCR_TIMEOUT_S 缺省值（契约 §8）

# 图片魔数（契约 §4：JPEG/PNG/WebP）
_MAGIC_SIGNATURES: tuple[tuple[bytes, ...], ...] = (
    (b"\xff\xd8\xff",),  # JPEG
    (b"\x89PNG\r\n\x1a\n",),  # PNG
    (b"RIFF", b"WEBP"),  # WebP：前 4 字节 RIFF，8~12 字节 WEBP
)


def _is_supported_image(image_bytes: bytes) -> bool:
    """按魔数判断是否为契约允许的三种格式（不引第三方图像库）。"""
    if len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return True
    for sigs in _MAGIC_SIGNATURES:
        if any(image_bytes.startswith(sig) for sig in sigs):
            return True
    return False


def _read_ocr_env() -> dict[str, str]:
    """读 OCR 相关配置：进程环境优先，否则回落解析 `backend/.env`。

    不走 `core/config.py` 的 Settings（本包不改他包文件），这里只读
    契约 §8 冻结的三个键：OCR_ACCESS_KEY_ID / OCR_ACCESS_KEY_SECRET /
    OCR_TIMEOUT_S。
    """
    values: dict[str, str] = {}
    keys = ("OCR_ACCESS_KEY_ID", "OCR_ACCESS_KEY_SECRET", "OCR_TIMEOUT_S")
    for key in keys:
        env_val = os.environ.get(key)
        if env_val:
            values[key] = env_val.strip()
    if len(values) == len(keys):
        return values

    # 解析 backend/.env（与 scripts/_ocr_probe.py 同一套路）
    from app.core.config import BASE_DIR

    env_file = BASE_DIR / ".env"
    if env_file.is_file():
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    if k in keys and k not in values:
                        values[k] = v.strip()
        except OSError:
            pass  # 读不了 .env 就靠进程环境，缺 AK 会在懒加载时显式报 20005
    return values


class _OcrClient:
    """阿里云读光 OCR 客户端（契约 §6.3：单例、懒加载）。

    只负责「图进 → 原始 dict 出」，解析与判分都在模块级函数里，
    方便单测 Mock（`_get_client` 返回 fake 即可，无需起网络）。
    """

    def __init__(self, access_key_id: str, access_key_secret: str, timeout_s: int) -> None:
        from alibabacloud_ocr_api20210707.client import Client as _SdkClient
        from alibabacloud_tea_openapi import models as open_api_models

        config = open_api_models.Config(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint=_OCR_ENDPOINT,
        )
        self._client = _SdkClient(config)
        self._timeout_s = timeout_s

    def recognize(self, image_bytes: bytes) -> dict[str, Any]:
        """调 RecognizeHandwriting，返回响应体 `to_map()` 原始结构。

        任何 SDK / 网络异常向上抛，由 `grade_handwriting` 统一映射 20005。
        """
        from alibabacloud_ocr_api20210707 import models as ocr_models
        from alibabacloud_tea_util import models as util_models

        request = ocr_models.RecognizeHandwritingRequest(
            body=image_bytes,  # 二进制 body（实测必须，走 form 字段会 400）
            need_rotate=True,
            output_char_info=True,
        )
        runtime = util_models.RuntimeOptions(
            connect_timeout=self._timeout_s * 1000,
            read_timeout=self._timeout_s * 1000,
        )
        response = self._client.recognize_handwriting_with_options(request, runtime)
        return response.body.to_map() if response.body else {}


_client: _OcrClient | None = None


def _get_client() -> _OcrClient:
    """懒加载单例（测试可直接改 `dictation_service._client` 注入 fake）。"""
    global _client
    if _client is None:
        env = _read_ocr_env()
        ak_id = env.get("OCR_ACCESS_KEY_ID", "")
        ak_secret = env.get("OCR_ACCESS_KEY_SECRET", "")
        if not ak_id or not ak_secret:
            raise BizError(ErrorCode.OCR_GRADE_FAILED, "批改服务未配置，请联系管理员")
        try:
            timeout_s = int(env.get("OCR_TIMEOUT_S") or _DEFAULT_TIMEOUT_S)
        except ValueError:
            timeout_s = _DEFAULT_TIMEOUT_S
        _client = _OcrClient(ak_id, ak_secret, timeout_s)
    return _client


# ------------------------------------------------------------------ 解析


def _parse_blocks(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """从 SDK 原始返回解析文字块：`Data` 二次 `json.loads` → prism_wordsInfo。"""
    data_raw = raw.get("Data") or raw.get("data") or "{}"
    try:
        data = json.loads(data_raw) if isinstance(data_raw, str) else (data_raw or {})
    except (TypeError, ValueError):
        raise BizError(ErrorCode.OCR_GRADE_FAILED) from None
    if not isinstance(data, dict):
        raise BizError(ErrorCode.OCR_GRADE_FAILED)
    blocks = data.get("prism_wordsInfo")
    if not isinstance(blocks, list):
        blocks = []
    return [b for b in blocks if isinstance(b, dict)]


def _sort_reading_order(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按阅读顺序排序：y 除以行带宽度取整（同带 = 同一行），带内按 x。"""
    return sorted(
        blocks,
        key=lambda b: (round((b.get("y") or 0) / _ROW_BAND_PX), b.get("x") or 0),
    )


def normalize_word(text: str) -> str:
    """判分用归一化：忽略大小写 / 空格 / 连字符（契约 §6.3）。

    另剥「1. / 1、 / 1)」这类题号前缀（实测样张里学生会抄题号，
    probe 同款处理）；剥完为空则视为无效块。
    """
    w = (text or "").strip().lower().replace("-", "").replace("‐", "").replace("–", "")
    w = "".join(w.split())
    # 剥题号前缀：数字 + (.|、|．|)|）) + 空格
    for sep in (".", "、", "．", ")", "）"):
        idx = w.find(sep)
        if 0 < idx and w[:idx].isdigit():
            w = w[idx + 1 :]
            break
    return w.strip()


def _block_confidence(block: dict[str, Any]) -> int:
    prob = block.get("prob")
    if isinstance(prob, (int, float)):
        return int(prob)
    try:
        return int(float(str(prob)))
    except (TypeError, ValueError):
        return 0


def _align(words: list[str], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """词表与识别块的对齐（两段式）。

    学生在纸上怎么排版不受控（可能两列、可能跳行），**阅读顺序未必等于
    词表顺序**，所以不能只做顺序对齐：

    1. **精确匹配**（与位置无关）：每个词在未消耗的块里找归一化后相等的
       一个 → 对上（correct=true）。这保证「写对了、只是位置排后了」
       不会误判；
    2. **剩余配对**：没精确对上的词（写错/未被识别）与剩下的块按归一化
       文本**相似度择优**配对 → correct=false，`recognized` = 块原文
       （误识别值）；相似度都不足或没块可配的词 `recognized=""`
       （漏写/完全没被识别）。

    返回与 `words` 等长的对齐结果列表：
    `{"block": dict | None, "correct": bool}`。
    """
    normalized_blocks = [normalize_word(b.get("word", "")) for b in blocks]
    normalized_words = [normalize_word(w) for w in words]
    used = [False] * len(blocks)
    matched: list[int | None] = [None] * len(words)
    correct = [False] * len(words)

    # 1) 精确匹配（顺序无关）
    for wi, word in enumerate(normalized_words):
        if not word:
            continue
        for bi, nb in enumerate(normalized_blocks):
            if not used[bi] and nb == word:
                matched[wi] = bi
                used[bi] = True
                correct[wi] = True
                break

    # 2) 剩余词 ↔ 剩余块：按归一化文本相似度择优配对（误识别，一律判错）。
    #    实测样张里有「Dictation - Unit 1」这类标题块，剩余块不只有错词，
    #    顺序配对会把错词错配到标题上 —— 用编辑相似度择优（阈值 0.5）。
    unmatched_words = [wi for wi, m in enumerate(matched) if m is None]
    free_blocks = [bi for bi, u in enumerate(used) if not u]
    candidates: list[tuple[float, int, int]] = []
    for wi in unmatched_words:
        for bi in free_blocks:
            if not normalized_blocks[bi]:  # 空块不算识别结果
                continue
            score = difflib.SequenceMatcher(
                None, normalized_words[wi], normalized_blocks[bi]
            ).ratio()
            if score >= _MISREAD_SIMILARITY_MIN:
                candidates.append((score, wi, bi))
    candidates.sort(key=lambda t: (-t[0], t[1]))
    taken_words: set[int] = set()
    taken_blocks: set[int] = set()
    for _score, wi, bi in candidates:
        if wi in taken_words or bi in taken_blocks:
            continue
        matched[wi] = bi
        taken_words.add(wi)
        taken_blocks.add(bi)

    return [
        {"block": blocks[m] if m is not None else None, "correct": correct[wi]}
        for wi, m in enumerate(matched)
    ]


# ------------------------------------------------------------------ 主入口


def grade_handwriting(image_bytes: bytes, words: list[str]) -> dict:
    """契约 §6.3 签名（冻结）：拍照批改入口。

    返回::

        {"results": [{"word", "recognized", "correct", "confidence",
                      "uncertain"}, ...],
         "ocr_content": 全文}

    - `recognized`：与该词对齐上的 OCR 原文；无对齐则 `""`；
    - `confidence`：对齐块的置信度（0~100），无对齐为 0；
    - `uncertain`：对齐块置信度 <85（拿不准，前端标「拿不准」）。

    失败抛 `BizError(20005)`；图片无效抛 `BizError(20006)`。
    """
    # ---- 图片有效性（契约 §4：JPEG/PNG/WebP，≤10MB）----
    if not image_bytes:
        raise BizError(ErrorCode.IMAGE_INVALID)
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise BizError(ErrorCode.IMAGE_INVALID)
    if not _is_supported_image(image_bytes):
        raise BizError(ErrorCode.IMAGE_INVALID)

    # ---- 调 OCR（一切异常 → 20005）----
    try:
        raw = _get_client().recognize(image_bytes)
    except BizError:
        raise
    except Exception as exc:  # noqa: BLE001 —— SDK/网络/超时统一映射
        from app.core.logging import get_logger

        get_logger(__name__).warning("OCR 批改失败：%s: %s", type(exc).__name__, exc)
        raise BizError(ErrorCode.OCR_GRADE_FAILED) from None

    blocks = _sort_reading_order(_parse_blocks(raw))

    # ---- 对齐与判分 ----
    alignments = _align(words, blocks)
    results: list[dict[str, Any]] = []
    for word, alignment in zip(words, alignments):
        block = alignment["block"]
        if block is None:
            results.append(
                {
                    "word": word,
                    "recognized": "",
                    "correct": False,
                    "confidence": 0,
                    "uncertain": False,
                }
            )
            continue
        confidence = _block_confidence(block)
        results.append(
            {
                "word": word,
                "recognized": str(block.get("word", "")),
                "correct": bool(alignment["correct"]),
                "confidence": confidence,
                "uncertain": confidence < _CONFIDENCE_UNCERTAIN_BELOW,
            }
        )

    # ---- 全文：Data 里的 content，缺失时按阅读顺序拼块 ----
    data_raw = raw.get("Data") or raw.get("data") or "{}"
    try:
        data = json.loads(data_raw) if isinstance(data_raw, str) else (data_raw or {})
    except (TypeError, ValueError):
        data = {}
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str):
        content = " ".join(str(b.get("word", "")) for b in blocks).strip()

    return {"results": results, "ocr_content": content}
