"""背诵流程接口 —— WP-BE-05 F1 ~ F5（接口设计 §9.4）。

| 接口 | 契约 | 说明 |
|---|---|---|
| `POST /api/recitations` | F1 | 开始一次背诵，返回 `recitation_id` / `total_chars` / 每段字数 |
| `POST /api/recitations/{id}/feed` | — | **实现补充**：补喂累计识别文本（WebSocket 之外的第二条通道） |
| `POST /api/recitations/{id}/hint` | — | **实现补充**：取一次提示（显式档位或服务端自动判定） |
| `POST /api/recitations/{id}/restart` | — | **实现补充**：「重新开始本段」（需求 §5.6） |
| `POST /api/recitations/{id}/events` | F2 | App 断线补报事件流 |
| `POST /api/recitations/{id}/settle` | F3/F5 | 结算（服务端重算，不采信客户端） |

### 三条安全线

1. **F1 响应不含拼音、不含正文** —— 正文学生在 §8.7 已拿过，拼音是服务端内部实现。
2. **F5 客户端上报的对齐数据只收不用** —— 学生谎报 `matched_chars=total` 也刷不了分。
3. **每次操作都校验 `recitations.student_id`** —— 越权访问按「不存在」处理（`10004`），
   与 §8.9 申诉同口径，不泄露他人记录的存在性。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.schemas.recite import (
    EventsReportIn,
    FeedIn,
    HintIn,
    RecitationCreateIn,
    SettleIn,
)
from app.services.recite.service import ReciteService

router = APIRouter(tags=["recite"])


@router.post("/api/recitations")
def create_recitation(
    payload: RecitationCreateIn,
    user: CurrentUser = Depends(get_current_user),
):
    """开始一次背诵（F1 / §9.4）。"""
    return ok(
        ReciteService.create_recitation(
            student_id=user.id,
            session_id=payload.session_id,
            text_id=payload.text_id,
            scope=payload.scope,
        )
    )


@router.post("/api/recitations/{recitation_id}/feed")
def feed(
    recitation_id: int,
    payload: FeedIn,
    user: CurrentUser = Depends(get_current_user),
):
    """补喂一段累计识别文本（实现补充）。

    正式链路走 WebSocket（BE-06）；这条 HTTP 通道用于断线补喂与接口级验收。
    响应里的 `hint_suggestion` 只是建议，**不消耗提示档位**。
    """
    return ok(
        ReciteService.feed(
            student_id=user.id,
            recitation_id=recitation_id,
            partial_text=payload.partial_text,
            silence_ms=payload.silence_ms,
        )
    )


@router.post("/api/recitations/{recitation_id}/hint")
def request_hint(
    recitation_id: int,
    payload: HintIn,
    user: CurrentUser = Depends(get_current_user),
):
    """取一次提示（实现补充）。

    `level` 为空时由服务端按静音 / 连续漏字判定；判定不出返回 `level: null`。
    """
    return ok(
        ReciteService.hint(
            student_id=user.id,
            recitation_id=recitation_id,
            level=payload.level,
            trigger_by=payload.trigger_by,
            silence_ms=payload.silence_ms,
        )
    )


@router.post("/api/recitations/{recitation_id}/restart")
def restart(
    recitation_id: int,
    user: CurrentUser = Depends(get_current_user),
):
    """重新开始本段（需求 §5.6，实现补充）。"""
    return ok(ReciteService.restart(student_id=user.id, recitation_id=recitation_id))


@router.post("/api/recitations/{recitation_id}/events")
def report_events(
    recitation_id: int,
    payload: EventsReportIn,
    user: CurrentUser = Depends(get_current_user),
):
    """App 断线补报事件流（F2）。服务端不据此计算任何指标。"""
    return ok(
        ReciteService.report_events(
            student_id=user.id,
            recitation_id=recitation_id,
            events=[e.model_dump() for e in payload.events],
        )
    )


@router.post("/api/recitations/{recitation_id}/settle")
def settle(
    recitation_id: int,
    payload: SettleIn,
    user: CurrentUser = Depends(get_current_user),
):
    """结算（F3/F5 / §9.4）。服务端用 `transcript` 重跑对齐，客户端数据仅参考。"""
    return ok(
        ReciteService.settle(
            student_id=user.id,
            recitation_id=recitation_id,
            payload=payload.model_dump(),
        )
    )
