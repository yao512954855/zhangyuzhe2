"""练习流程接口 —— WP-BE-04 F8 ~ F15、F20（接口设计 §9.1 ~ §9.3）。

| 接口 | 说明 |
|---|---|
| `POST /api/sessions` | 组卷：一次性生成完整题序，返回 `items`（**只有 `prompt`，绝不含答案**） |
| `POST /api/sessions/{id}/answers` | 服务端判定 + 落库 + 同事务更新掌握度 |
| `POST /api/sessions/{id}/finish` | 结算：项级正确率、分数、错题清单 |

**幂等**（F20）：答题提交带 `Idempotency-Key` 头时，5 分钟内重复提交直接回放首次响应
（防弱网重试重复计分）。该能力由 BE-01 的 `IdempotencyMiddleware` 提供，
**本包不需要额外代码**，但必须有测试钉住（U24）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.schemas.session import AnswerSubmitIn, SessionCreateIn, SessionFinishIn
from app.services.session_service import SessionService

router = APIRouter(tags=["sessions"])


@router.post("/api/sessions")
def create_session(
    payload: SessionCreateIn,
    user: CurrentUser = Depends(get_current_user),
):
    """开始一次练习（§9.1）。

    服务端按策略一次性生成完整题序；`prompt` 只含题目侧内容，
    `target_len` 只用于画占位框 —— **答案不在响应里**。
    """
    return ok(
        SessionService.create_session(
            student_id=user.id,
            unit_id=payload.unit_id,
            content_type=payload.content_type,
            strategy=payload.strategy.model_dump(),
        )
    )


@router.post("/api/sessions/{session_id}/answers")
def submit_answer(
    session_id: int,
    payload: AnswerSubmitIn,
    user: CurrentUser = Depends(get_current_user),
):
    """提交一次答题结果（§9.2）。

    判定 100% 在服务端完成，`expected` 判定后才返回；拼读额外返回 `diff[]`
    驱动逐字母回显（需求 §5.4 硬需求）。
    """
    return ok(
        SessionService.submit_answer(
            student_id=user.id,
            session_id=session_id,
            payload=payload.model_dump(),
        )
    )


@router.post("/api/sessions/{session_id}/finish")
def finish_session(
    session_id: int,
    payload: SessionFinishIn,
    user: CurrentUser = Depends(get_current_user),
):
    """结束练习并结算（§9.3）。

    正确率按**项**口径：`correct_items / (total_items − 全 empty 的项数)`（F14 / U25）。
    """
    return ok(
        SessionService.finish_session(
            student_id=user.id,
            session_id=session_id,
            reason=payload.reason,
        )
    )
