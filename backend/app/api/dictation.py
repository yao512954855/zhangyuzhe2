"""听写批改路由 —— 契约《01-共享契约冻结》§4.1（WP-D 批次3 实现）。

    POST /api/dictation/grade   multipart: file(手写照片) + form words=["apple",…]
        → {results:[{word,recognized,correct,confidence,uncertain}], ocr_content}

路由路径为批次2 冻结项，**不得改动**。图片约束（契约 §4）：
multipart/form-data，字段名 `file`，JPEG/PNG/WebP，≤10MB。

图片无效 / 云端 OCR 失败由 `dictation_service` 抛 `BizError(20006/20005)`，
经 `main.py` 全局 handler 转 HTTP 200 + 非零 code 信封（契约 §4）。

注意：本路由用**同步 `def`**（非 async）——OCR SDK 调用是阻塞 IO，
FastAPI 会把同步路由丢线程池执行，不卡事件循环。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form

from app.core.response import ok
from app.services import dictation_service

router = APIRouter(tags=["dictation"])


@router.post("/api/dictation/grade")
def grade(
    file: bytes = File(...),
    words: list[str] = Form(...),
) -> dict[str, Any]:
    """拍照批改：手写照片 + 词表 → 逐词对错与置信度。"""
    result = dictation_service.grade_handwriting(file, list(words))
    return ok(result)
