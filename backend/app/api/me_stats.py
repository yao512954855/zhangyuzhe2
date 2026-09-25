"""学生端个人统计接口 —— WP-2A（个人正确率趋势）。

| 接口 | 说明 |
|---|---|
| `GET /api/me/accuracy-trend` | 个人逐日正确率趋势（学生本人，含今天最近 N 天，缺天补零） |

本文件只定义 `router`，由 `app/core/router_loader.load_routers` 自动发现挂载，
**不修改 `main.py`**。安全线：

- 只回**当前登录学生自己**的趋势（`accuracy_trend` 内 `WHERE student_id = ?`），
  不接收任何可指定他人的入参 —— 否则越权看别人正确率。
- 正确率口径统一转发 `UserService._accuracy()`（经 `StatsService.calc_accuracy`）。
- `days` 非法 → `10001`，业务失败一律 HTTP 200 + 非零 code。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.db import read_conn
from app.core.deps import CurrentUser, get_current_user
from app.core.errors import BizError, ErrorCode
from app.core.response import ok
from app.services.stats.trend_service import (
    DEFAULT_TREND_DAYS,
    MAX_TREND_DAYS,
    accuracy_trend,
)

router = APIRouter(tags=["me-stats"])


def _parse_days(raw: str | None) -> int:
    """解析 `days` 查询参数：默认 `DEFAULT_TREND_DAYS`，必须是 1..MAX_TREND_DAYS 的整数。"""
    if raw is None or raw.strip() == "":
        return DEFAULT_TREND_DAYS
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise BizError(ErrorCode.PARAM_INVALID, "days 必须是正整数") from exc
    if value <= 0:
        raise BizError(ErrorCode.PARAM_INVALID, "days 必须是正整数")
    if value > MAX_TREND_DAYS:
        raise BizError(
            ErrorCode.PARAM_INVALID, f"days 最多 {MAX_TREND_DAYS} 天"
        )
    return value


@router.get("/api/me/accuracy-trend")
def my_accuracy_trend(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """`GET /api/me/accuracy-trend?days=30`（WP-2A）。

    学生本人视角的逐日正确率趋势，按北京时间切天、缺天补零。

    响应 `{"points":[{"date","accuracy","answered","correct"}]}` —— `accuracy` 经
    `UserService._accuracy()` 计算，与项目内其他正确率口径一致；`answered` 是当天答题条数，
    `correct` 是当天正确条数。
    """
    days = _parse_days(request.query_params.get("days"))
    with read_conn() as conn:
        points = accuracy_trend(conn, user.id, days)
    return ok({"points": [p.to_dict() for p in points]})
