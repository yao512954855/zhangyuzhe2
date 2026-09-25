"""英语三档提示接口（WP-2J / 契约 §15.2 / §15.4）。

端点：`POST /api/sessions/{sid}/items/{idx}/hint`

**仅 `router` 定义，不改 `main.py`** —— 由 `core/router_loader.load_routers`
自动发现注册（契约 §5.4 / 10a F12）。

请求体：
    {"word_id": <int>, "mode": "cn2en"|"en2cn", "current_level": <int, 默认 0>}

响应（`ok()` 包裹）：
    {"level": <1|2|3>, "hint": "<提示内容>", "exhausted": <bool>}

业务失败一律 HTTP 200 + 非零 code（契约 §3 / §5.1）：
    - 越权 / 场次不存在 / 词条越界 → `10004`（同码）
    - `en2cn` 题型无提示 → `70007`
    - 越档（请求档位超过失败次数门控）→ `70006`
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.services.hint_service import HintService

router = APIRouter(tags=["hint"])


class EnglishHintIn(BaseModel):
    """英语提示请求体。

    `word_id` / `mode` 来自组卷响应 `items[]`，客户端持有；`current_level`
    是客户端当前已持有的最高档位（0 = 尚未取过），服务端据失败次数门控判定
    是否允许升到 `current_level + 1`。
    """

    word_id: int
    mode: str
    current_level: int = 0


@router.post("/api/sessions/{sid}/items/{idx}/hint")
def english_hint(
    sid: int,
    idx: int,
    payload: EnglishHintIn,
    user: CurrentUser = Depends(get_current_user),
):
    """取一次英语提示（§15.4 防作弊门控）。

    - `sid` / `idx` 来自路径；`idx` 仅用于契约路径，权威标识是 `word_id`。
    - 鉴权：只允许操作自己的 session；越权与不存在同码 `10004`（§15.2）。
    """
    return ok(
        HintService.english_hint(
            student_id=user.id,
            session_id=sid,
            item_idx=idx,
            word_id=payload.word_id,
            mode=payload.mode,
            current_level=payload.current_level,
        )
    )
