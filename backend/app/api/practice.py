"""练习路由 —— WP-A，契约《01-共享契约冻结》§4.1 练习段（v1.3，todo11 A1/A2/A5/A7）。

POST /api/practice/start   {mode, unit_id | unit_ids:[], type | types:[]}
                           → {session_id, items:[…]}（数组多选；两者都传以数组为准）
POST /api/practice/answer  {session_id,item_id,mode,answer,elapsed_ms,correct?}
                           → {correct, matched_zh?}
                           # v1.3：spell_zh2en 必走 HTTP，correct 必填（留痕）；
                           # spell_en2zh 不传 correct（服务端判定）；listen 不走本接口
POST /api/practice/settle  {session_id,duration_ms,unit_ids?,types?} → {}
                           # 服务端写 practice_logs（JSON 数组；缺省回退 start 快照）

请求模型在本文件内定义（v1.3 形状变更，不改公共 schemas/simplify.py 的旧模型，
保持单数模型供一轮入口/他包引用不动）。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.response import ok
from app.services import practice_service

router = APIRouter(tags=["practice"])


class PracticeStartBody(BaseModel):
    """v1.3：unit_ids/types 数组（单数保留兼容；同请求两者都传以数组为准）。"""

    mode: str = Field(min_length=1)
    unit_id: int | None = None
    unit_ids: list[int] | None = None
    type: str | None = None
    types: list[str] | None = None


class PracticeAnswerBody(BaseModel):
    """v1.3：`correct` 选填 —— spell_zh2en 必填（留痕上报），en2zh 不传（服务端判定）。"""

    session_id: str = Field(min_length=1)
    item_id: int
    mode: str
    answer: str = ""
    elapsed_ms: int = 0
    correct: bool | None = None


class PracticeSettleBody(BaseModel):
    """v1.3：unit_ids/types 选填 —— 缺省回退 start 快照。"""

    session_id: str = Field(min_length=1)
    duration_ms: int = 0
    unit_ids: list[int] | None = None
    types: list[str] | None = None


@router.post("/api/practice/start")
async def start(body: PracticeStartBody) -> dict:
    return ok(
        practice_service.start(
            mode=body.mode,
            unit_id=body.unit_id,
            item_type=body.type,
            unit_ids=body.unit_ids,
            item_types=body.types,
        )
    )


@router.post("/api/practice/answer")
async def answer(body: PracticeAnswerBody) -> dict:
    return ok(
        practice_service.answer(
            session_id=body.session_id,
            item_id=body.item_id,
            mode=body.mode,
            answer_text=body.answer,
            elapsed_ms=body.elapsed_ms,
            correct=body.correct,
        )
    )


@router.post("/api/practice/settle")
async def settle(body: PracticeSettleBody) -> dict:
    return ok(
        practice_service.settle(
            session_id=body.session_id,
            duration_ms=body.duration_ms,
            unit_ids=body.unit_ids,
            types=body.types,
        )
    )
