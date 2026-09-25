"""SRS 间隔重复接口 —— WP-2F（二期，契约《01-共享契约冻结》§15.2 / §15.5）。

| 接口 | 说明 |
|---|---|
| `GET  /api/me/srs/due` | 我的到期复习列表（无到期 → 空列表，不报错） |
| `POST /api/me/srs/review` | 复习一条计划，按 SM-2 推进；找不到计划 → 70004 |

### 安全线（沿用 §15.4 的 A3 口径）

- **只读写自己的 `student_id`**：`due` / `review` 的查询都带 `student_id = ?`
  （来自 JWT），不允许看 / 改别人的计划。
- **越权与「不存在」同码**：`review` 找不到 `(student_id, word_id)` 的计划时
  一律 `70004`，不区分「这条计划归别人」还是「压根没有」。

### 响应

一律 `ok()` / `fail()`（契约 §3）：业务失败 = HTTP 200 + 非零 code。
未带 token → `10002`（由 `get_current_user` 统一抛出）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.db import read_conn, write_tx
from app.core.deps import CurrentUser, get_current_user
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.services.srs_service import (
    SRS_MAX_GRADE,
    SRS_MIN_GRADE,
    SrsResult,
    SrsService,
)

router = APIRouter(tags=["me-srs"])

#: `due` 的 `limit` 上限（防止一次拉全表）。
DUE_LIMIT_MAX = 200


def _parse_limit(raw: str | None) -> int:
    """解析 `due` 的 `limit` 查询参数：默认 20；非法或越界 → `10001`。"""
    if raw is None or raw == "":
        return 20
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise BizError(ErrorCode.PARAM_INVALID, "limit 必须是整数") from exc
    if value < 1:
        raise BizError(ErrorCode.PARAM_INVALID, "limit 必须大于等于 1")
    if value > DUE_LIMIT_MAX:
        raise BizError(ErrorCode.PARAM_INVALID, f"limit 不能超过 {DUE_LIMIT_MAX}")
    return value


def _parse_int_body(value: Any | None, field: str) -> int:
    """从请求体里取一个必填整数；缺失 / 非整数 → `10001`。"""
    if value is None:
        raise BizError(ErrorCode.PARAM_INVALID, f"{field} 不能为空")
    if isinstance(value, bool) or not isinstance(value, int):
        raise BizError(ErrorCode.PARAM_INVALID, f"{field} 必须是整数")
    return int(value)


# ---------------------------------------------------------------------------
# §15.2 我的到期复习列表
# ---------------------------------------------------------------------------


@router.get("/api/me/srs/due")
def srs_due(request: Request, user: CurrentUser = Depends(get_current_user)):
    """`GET /api/me/srs/due?limit=20`。

    返回当前学生**已到期**（`due_at <= 现在`）的复习计划，按 `due_at` 升序。
    没有任何到期计划 → `{items: []}`（code=0，不报错，契约 §15.2）。
    """
    limit = _parse_limit(request.query_params.get("limit"))

    with read_conn() as conn:
        items = SrsService.due_items(conn, int(user.id), limit)

    payload = [
        {
            "word_id": it.word_id,
            "unit_id": it.unit_id,
            "due_at": it.due_at,
            "interval_days": it.interval_days,
            "ease": it.ease,
            "reps": it.reps,
            "lapses": it.lapses,
            "en": it.en,
            "cn": it.cn,
            "type": it.word_type,
            "unit_name": it.unit_name,
        }
        for it in items
    ]
    return ok({"items": payload})


# ---------------------------------------------------------------------------
# §15.2 复习一条计划（SM-2 推进）
# ---------------------------------------------------------------------------


@router.post("/api/me/srs/review")
def srs_review(
    body: dict[str, Any],
    user: CurrentUser = Depends(get_current_user),
):
    """`POST /api/me/srs/review` body `{word_id, grade}`。

    - `word_id`：必填整数；
    - `grade`：SM-2 质量分，整数 0–5；越界 → `10001`；
    - 找不到该学生的这条计划 → `70004`（越权与「不存在」同码）；
    - 成功 → `{next_due, interval_days, reps}`（另附 `ease` / `lapses` 供客户端展示）。
    """
    word_id = _parse_int_body(body.get("word_id"), "word_id")
    grade = _parse_int_body(body.get("grade"), "grade")

    if grade < SRS_MIN_GRADE or grade > SRS_MAX_GRADE:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            f"grade 必须在 {SRS_MIN_GRADE}–{SRS_MAX_GRADE} 之间",
        )

    with write_tx() as conn:
        result: SrsResult = SrsService.review(
            conn,
            student_id=int(user.id),
            word_id=word_id,
            grade=grade,
        )

    return ok(
        {
            "next_due": result.next_due,
            "interval_days": result.interval_days,
            "reps": result.reps,
            "ease": result.ease,
            "lapses": result.lapses,
        }
    )
