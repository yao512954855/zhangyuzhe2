"""听写播报云端 TTS 路由 —— 契约 v1.2（设备 TTS 不可用时的兜底）。

POST   /api/tts    {text, lang?="zh-CN"} → audio/wav 二进制
                   失败 → 200 + JSON envelope（code=TTS_SYNTH_FAILED 等）

为什么是 POST + 二进制：文本含任意单词/短句，放 query 不干净；
客户端拿到字节后落临时文件再播（audioplayers），不暴露云端 URL。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.services import tts_service

router = APIRouter(tags=["tts"])


class TtsIn(BaseModel):
    text: str
    lang: str = "zh-CN"


@router.post("/api/tts")
async def synthesize(payload: TtsIn) -> Response:
    audio = tts_service.synthesize(payload.text, payload.lang)
    return Response(content=audio, media_type="audio/wav")
