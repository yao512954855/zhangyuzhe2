"""本地 OCR（RapidOCR）—— 输出与阿里云 RecognizeHandwriting 同构的 map。

详见 docs/localize-dev/01 §1.2（行为矩阵 + 同构 map 适配）：
dictation_service._parse_blocks 认的字段 = `Data.prism_wordsInfo`，每块
`{"word": str, "x": int, "y": int, "prob": int(0~100)}`；全文 = `Data.content`。
`Data` 本身是 **JSON 字符串**（上游 `grade_handwriting`/`_parse_blocks` 有二次
`json.loads` 兼容）。这样 dictation_service 的解析 / 对齐 / 判分层零改动。

引擎懒加载单例 + 双检锁（契约 §6：首次请求加载约 0.5~3s，一次性）。
"""

from __future__ import annotations

import json
import threading
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 进程内单例 + 双检锁（契约 §6：懒加载，首次请求加载，一次性）
_engine: Any = None
_engine_lock = threading.Lock()

# 在能力框架登记 OCR 本地引擎（批次 C）：包内置 PP-OCRv4 mobile，无需单独模型文件。
# ready 判定 = rapidocr_onnxruntime 可 import（import 成功即视为就绪）。
from app.core.capability import register_model_spec  # noqa: E402

register_model_spec(
    "ocr",
    imports=["rapidocr_onnxruntime"],
    files=[],
    detail="PP-OCRv4 mobile（包内置）",
)


def _get_engine() -> Any:
    """懒加载 RapidOCR 单例（双检锁）。加载/导入失败向上抛，由路由层兜底。"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR

                logger.info("加载本地 OCR 引擎 RapidOCR（PP-OCRv4 mobile）……")
                _engine = RapidOCR()
    return _engine


class LocalOcrClient:
    """接口与 dictation_service._OcrClient 完全一致：recognize(image_bytes) -> dict。

    返回结构与阿里云同构（契约 §1.2）：
        {"Data": json.dumps({"content": ..., "prism_wordsInfo": [...]}, ensure_ascii=False)}
    """

    def recognize(self, image_bytes: bytes) -> dict[str, Any]:
        engine = _get_engine()
        try:
            result = engine(image_bytes)
        except Exception as exc:  # noqa: BLE001 - 引擎/解码异常向上抛，路由层兜
            logger.warning("RapidOCR 识别异常：%s: %s", type(exc).__name__, exc)
            raise

        # RapidOCR 1.2.x 返回 2 元组 (results, scores)；
        #   results 每项 = [box, text, score_str]，box 为 4 角点 [[x,y], ...]。
        # 旧版有 3 元组 (boxes, txts, scores) 形态，这里兼容两种。
        items = _extract_items(result)

        blocks: list[dict[str, Any]] = []
        for box, text, score in items:
            # box 为 4 个角点 [[x,y], ...]，左上角 = box[0]（契约 §1.2）。
            try:
                x = int(box[0][0])
                y = int(box[0][1])
            except (TypeError, IndexError, ValueError):
                x = y = 0
            blocks.append({"word": str(text), "x": x, "y": y, "prob": _clamp_prob(score)})

        content = _build_content(blocks)
        return {
            "Data": json.dumps(
                {"content": content, "prism_wordsInfo": blocks}, ensure_ascii=False
            )
        }


def _extract_items(result: Any) -> list[tuple[Any, Any, Any]]:
    """把 RapidOCR 返回归一成 [(box, text, score), ...]。

    - 2 元组 (results, scores)：results 每项 = [box, text, score_str]
      （RapidOCR 1.2.x 实测形态，score 为字符串）
    - 3 元组 (boxes, txts, scores)：旧版形态
    - None / 空：无文本
    """
    if not result:
        return []
    if len(result) == 3:
        boxes, txts, scores = result
        if not boxes:
            return []
        return list(zip(boxes, txts, scores))
    # 2 元组
    items = result[0]
    if not items:
        return []
    return [(it[0], it[1], it[2]) for it in items]


def _clamp_prob(score: Any) -> int:
    """score ∈ [0,1] → 概率整数 [0,100]，钳位到 [0,100]。"""
    try:
        p = round(float(score) * 100)
    except (TypeError, ValueError):
        return 0
    if p < 0:
        return 0
    if p > 100:
        return 100
    return p


def _build_content(blocks: list[dict[str, Any]]) -> str:
    """按阅读顺序（y 行带 60px + x 升序）拼块，英文块间一个空格（契约 §1.2）。

    与 dictation_service._sort_reading_order 用同一行带宽度，保证 content 与
    解析排序一致。
    """
    ordered = sorted(blocks, key=lambda b: (round((b.get("y") or 0) / 60), b.get("x") or 0))
    return " ".join(str(b.get("word", "")) for b in ordered)
