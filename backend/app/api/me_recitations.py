"""二期 WP-2E：背诵事件回放 —— `GET /api/me/recitations/{id}/events`。

数据来源是 `recitation_events`（一期只写不读，本包首次开读）。
契约依据：

- `docs/dev/01-共享契约冻结.md` §15.2（二期 HTTP 端点）/ §3（错误码）：
  越权与「不存在」**同码 `10004`**（不暴露资源存在性，沿用 A3 安全口径，
  与 §8.9 申诉、`ReciteService._peek_engine` 同一做法）。
- `docs/dev/50-二期工作包拆解.md` §3（端点表）/ §6（独占文件）：
  响应 `{events:[{seq,pos,event_type,at_ms}]}`，统一 `ok()`。
- `recitation_events` 字段见 `migrations/001_init.sql` §3.9；
  `at_ms` 映射自 `elapsed_ms`（背诵进行到该事件时已过去的毫秒数，是回放时间轴
  的唯一毫秒级字段）。

实现要点：

- 先查 `recitations` 校验存在性 + 归属，**不通过则不往下走**；
- 再按 `seq` 升序读取 `recitation_events`；
- 只回自己的数据（归属校验即「student_id 过滤」）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.db import read_conn
from app.core.deps import CurrentUser, get_current_user
from app.core.errors import BizError, ErrorCode
from app.core.response import ok

router = APIRouter(tags=["me-recitations"])


@router.get("/api/me/recitations/{recitation_id}/events")
def recitation_events(
    recitation_id: int,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """背诵事件回放（§15.2）。

    只回自己的数据：背诵记录不存在或不属于当前学生 → 均按 `10004` 处理，
    不暴露资源存在性（与 §8.9 申诉同一口径）。
    """
    rid = int(recitation_id)

    with read_conn() as conn:
        row = conn.execute(
            "SELECT id, student_id FROM recitations WHERE id = ?",
            (rid,),
        ).fetchone()

        # 越权与「不存在」同码：不区分二者，避免探测他人背诵记录是否存在。
        if row is None or int(row["student_id"]) != int(user.id):
            raise BizError(ErrorCode.NOT_FOUND, "背诵记录不存在")

        rows = conn.execute(
            """
            SELECT seq, pos, event_type, elapsed_ms
            FROM recitation_events
            WHERE recitation_id = ?
            ORDER BY seq ASC
            """,
            (rid,),
        ).fetchall()

    events = [
        {
            "seq": int(r["seq"]),
            "pos": int(r["pos"]),
            "event_type": str(r["event_type"]),
            # `at_ms` 映射自 `elapsed_ms`（事件发生时已过去的毫秒数）；
            # 一期引擎落库时多为 NULL（一期只写不读，未填），如实回 `null`。
            "at_ms": (int(r["elapsed_ms"]) if r["elapsed_ms"] is not None else None),
        }
        for r in rows
    ]
    return ok({"events": events})
