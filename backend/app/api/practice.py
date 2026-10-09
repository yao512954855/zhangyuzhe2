"""练习路由 —— WP-A，契约《01-共享契约冻结》§4.1 练习段。

POST /api/practice/start   {unit_id,type,mode} → {session_id, items:[…]}（组卷=全选）
POST /api/practice/answer  {session_id,item_id,mode,answer,elapsed_ms} → {correct, matched_zh?}
POST /api/practice/settle  {session_id,duration_ms} → {}（服务端写 practice_logs）
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.response import ok
from app.schemas.simplify import PracticeAnswer, PracticeSettle, PracticeStart
from app.services import practice_service

router = APIRouter(tags=["practice"])


@router.post("/api/practice/start")
async def start(body: PracticeStart) -> dict:
    return ok(
        practice_service.start(
            unit_id=body.unit_id,
            item_type=body.type,
            mode=body.mode,
        )
    )


@router.post("/api/practice/answer")
async def answer(body: PracticeAnswer) -> dict:
    return ok(
        practice_service.answer(
            session_id=body.session_id,
            item_id=body.item_id,
            mode=body.mode,
            answer_text=body.answer,
            elapsed_ms=body.elapsed_ms,
        )
    )


@router.post("/api/practice/settle")
async def settle(body: PracticeSettle) -> dict:
    return ok(practice_service.settle(session_id=body.session_id, duration_ms=body.duration_ms))
