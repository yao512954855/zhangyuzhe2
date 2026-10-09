"""听写批改路由 —— WP-A 建壳，真实现归 WP-D（契约《01-共享契约冻结》§4.1）。

`POST /api/dictation/grade` 在批次2 返回 HTTP 501 + 业务码 `20005`（占位，
见 10-后端 §WP-A「OCR 壳的 501 占位」）。批次3 WP-D 把实现填进**同一文件**，
路由路径不得改动：

    POST /api/dictation/grade   multipart: file(手写照片) + form words=["apple",…]
        → {results:[{word,recognized,correct,confidence}], ocr_content}

图片约束（契约 §4）：multipart/form-data，字段名 `file`，JPEG/PNG/WebP，≤10MB。
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form
from fastapi.responses import JSONResponse

from app.core.errors import ErrorCode
from app.core.response import fail

router = APIRouter(tags=["dictation"])


@router.post("/api/dictation/grade")
async def grade(
    file: bytes = File(...),
    words: list[str] = Form(...),
) -> JSONResponse:
    """占位实现：批次2 一律返回 501 + `20005`（WP-D 批次3 替换本函数体）。"""
    return JSONResponse(
        status_code=501,
        content=fail(ErrorCode.OCR_GRADE_FAILED, "听写批改尚未上线，请稍后再试"),
    )
