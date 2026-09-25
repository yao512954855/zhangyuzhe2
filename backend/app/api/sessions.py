"""练习流程接口 —— WP-BE-04 F8 ~ F15、F20（接口设计 §9.1 ~ §9.3）。

| 接口 | 说明 |
|---|---|
| `POST /api/sessions` | 组卷：一次性生成完整题序，返回 `items`（**只有 `prompt`，绝不含答案**） |
| `POST /api/sessions/{id}/answers` | 服务端判定 + 落库 + 同事务更新掌握度 |
| `POST /api/sessions/{id}/finish` | 结算：项级正确率、分数、错题清单 |
| `GET  /api/sessions` | **我的记录：场次列表**（§9.5.1，契约 §14.1 缺口 7） |
| `GET  /api/sessions/{id}` | **我的记录：单次详情**（§9.5.2） |

**幂等**（F20）：答题提交带 `Idempotency-Key` 头时，5 分钟内重复提交直接回放首次响应
（防弱网重试重复计分）。该能力由 BE-01 的 `IdempotencyMiddleware` 提供，
**本包不需要额外代码**，但必须有测试钉住（U24）。

**读取侧（§9.5）的两条安全线**（详见各自 docstring）：

1. 详情的**越权与「不存在」返回同一个 `10004`**，不给探测他人场次的可乘之机；
2. `ongoing` 场次**不下发逐题明细**（`expected` 就是答案，中途返回等于泄题）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, get_current_user
from app.core.errors import BizError, ErrorCode
from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.schemas.session import AnswerSubmitIn, SessionCreateIn, SessionFinishIn
from app.services.session_service import SessionService

router = APIRouter(tags=["sessions"])

#: §9.5.1 列表接口的取值域（契约冻结，不接受别的值）。
_CONTENT_TYPES = ("english", "chinese")
_STATUSES = ("ongoing", "finished", "abandoned")


def _opt(request: Request, key: str) -> str | None:
    raw = request.query_params.get(key)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _enum_opt(request: Request, key: str, allowed: tuple[str, ...]) -> str | None:
    """取枚举型查询参数；给了取值域外的值 → `10001`。

    **不静默当空**：`?content_type=eng` 若被忽略、返回全班次，前端会以为筛选生效了，
    比直接报错更难查。
    """
    value = _opt(request, key)
    if value is None:
        return None
    if value not in allowed:
        raise BizError(ErrorCode.PARAM_INVALID, f"{key} 只能是 {'/'.join(allowed)}")
    return value


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


# ---------------------------------------------------------------------------
# §9.5 我的记录：场次列表 / 单次详情（契约 §14.1 缺口 7）
# ---------------------------------------------------------------------------


@router.get("/api/sessions")
def list_sessions(request: Request, user: CurrentUser = Depends(get_current_user)):
    """`GET /api/sessions`（§9.5.1）：我的练习场次列表。

    需求 §5.9「我的记录：按日期列出练习场次与成绩」——只回**当前学生自己的**场次，
    按 `started_at DESC, id DESC`。`ongoing` 场次照样列出（不隐藏数据），
    客户端据 `status` 显示「进行中」。
    """
    page_no, page_size = parse_pagination(request)
    content_type = _enum_opt(request, "content_type", _CONTENT_TYPES)
    status = _enum_opt(request, "status", _STATUSES)

    items, total = SessionService.list_sessions(
        student_id=user.id,
        page=page_no,
        page_size=page_size,
        content_type=content_type,
        status=status,
    )
    return page(items, total, page_no, page_size)


@router.get("/api/sessions/{session_id}")
def session_detail(session_id: int, user: CurrentUser = Depends(get_current_user)):
    """`GET /api/sessions/{id}`（§9.5.2）：单次场次详情（正确率 / 用时 / 逐题）。

    - 不属于当前学生的场次 → `10004`（**与「不存在」同码**，不暴露存在性）；
    - `ongoing` 场次 → `10003` 且**不带逐题明细**（`expected` 即答案，防泄题）；
    - 英语给 `items[]`（一个词条一行），语文给 `recitations[]`（每篇含 `missed_detail`）。
    """
    return ok(
        SessionService.session_detail(student_id=user.id, session_id=session_id)
    )
