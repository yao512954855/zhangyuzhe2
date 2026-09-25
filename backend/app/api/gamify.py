"""游戏化激励接口 —— WP-2K（二期契约 §15.2 / §3）。

| 接口 | 说明 |
|---|---|
| `GET /api/me/gamify` | 当前学生积分 / 连续天数 / 徽章墙 |

本文件只定义 `router`，由 `app/core/router_loader.load_routers` 自动发现挂载，
**不修改 `main.py`**。

安全线与口径：

- 只回**当前登录学生自己**的数据（`snapshot` / `apply_refresh` 内 `WHERE student_id = ?`），
  不接收任何可指定他人的入参 —— 否则越权看别人积分/徽章。
- **读时惰性刷新**：在 `GET` 内根据现有数据推导并 upsert `user_points` / `user_badges`
  （本包是两表的唯一写入方，契约 §15.1）。写操作走 `write_tx()` 全局写锁。
- 徽章必须由数据真实推导，禁止编造（已落到 `gamify_service.BADGE_CATALOG`）。
- 统一 `ok()`；业务失败一律 HTTP 200 + 非零 code（本项目契约 §3）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.db import write_tx
from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.services.gamify_service import apply_refresh, snapshot

router = APIRouter(tags=["me-gamify"])


@router.get("/api/me/gamify")
def my_gamify(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """`GET /api/me/gamify`（WP-2K）。

    学生本人的积分、连续练习天数与徽章墙。徽章墙含全部徽章定义，
    已达成项带 `earned=true` 与 `earned_at`，未达成项 `earned=false`（无 `earned_at`）。

    每次读取都做一次"读时惰性刷新"：按现有 `answers` / `sessions` /
    `word_mastery` / `recitations` 推导最新积分与徽章，并幂等写回
    `user_points` / `user_badges`（本包独占表）。
    """
    with write_tx() as conn:
        # 在同一个写事务内：先只读推导，再写回（同一连接，避免并发读到中间态）。
        snap = snapshot(conn, user.id)
        awarded = apply_refresh(conn, user.id, snap)

    for b in snap.badges:
        if b.earned:
            b.earned_at = awarded.get(b.code)
    badges_out = [b.to_dict() for b in snap.badges]
    return ok(
        {
            "points": snap.points,
            "streak_days": snap.streak_days,
            "badges": badges_out,
        }
    )
