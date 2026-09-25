"""申诉复核接口 —— WP-BE-07 F4 / F5（需求 §6.1 申诉机制）。

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `GET  /api/appeals` | admin | 申诉列表，`status` 支持 `pending`/`upheld`/`rejected`/`all`（F4） |
| `POST /api/appeals/{id}/review` | admin | 复核：`upheld`/`rejected` + `remark`（F5） |

---

### ⛔ 硬约束：复核**绝不改** `is_correct`

学生的原始判定是有教学意义的记录（它驱动了掌握度、正确率、错题本）。
复核的作用是**给人一个结论**，不是「事后把分数改对」。
改 `is_correct` 会让 `word_mastery` 物化表与 `answers` 明细**对不上账** ——
而 `word_mastery` 的唯一写入方是 BE-04，本包改不了也不该改（契约 §4.2）。

所以本接口只写两个地方：

1. `answers.appeal_result` ← `upheld` / `rejected`（**唯一被改的业务列**）
2. `audit_logs` ← `action='appeal.review'`，`detail` 里带 `remark` 与复核前的结论

### ⚠️ `remark` 存在哪：`audit_logs.detail`，不是 `answers` 列

`answers` 表**没有** remark / reviewer / reviewed_at 任一列（见 `001_init.sql` §3.7）。
10b F5 要求「`upheld`/`rejected` + `remark`」，但没给 remark 指定落点 ——
DB 设计里也没有它的位置（DDL 是契约冻结件，本包无权加列）。
因此 remark 与复核人、复核时间一并写进审计 `detail`，列表接口再把最近的
一条 `appeal.review` 审计回读出来补进响应（`review_remark` / `reviewed_at` / `reviewer`）。
这样管理端界面完整，且不动 DDL。**此项已在交付说明里作为偏离上报。**

### `audio_available` 恒为 `false`

录音默认不落盘（需求 §9.5 / U8），服务端**没有音频可回放**。
字段保留是为了前端不必改版 —— 学生端一期若开录音留存，这里改成真实判断即可。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.audit import write_audit
from app.core.db import read_conn, write_tx
from app.core.deps import CurrentUser, require_admin
from app.core.errors import BizError, ErrorCode
from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.schemas.stats import AppealReviewIn
from app.services.stats_service import _BJ, normalise_range

router = APIRouter(tags=["appeals"])

#: `status` 查询参数 → SQL 条件。`pending` 是首屏默认（10b 关键实现要点）。
_STATUS_FILTERS: dict[str, str] = {
    "pending": "a.appealed = 1 AND a.appeal_result IS NULL",
    "upheld": "a.appealed = 1 AND a.appeal_result = 'upheld'",
    "rejected": "a.appealed = 1 AND a.appeal_result = 'rejected'",
    "all": "a.appealed = 1",
}

#: 复核动作的审计动作名（回读 remark 时也用它，改名字要一起改）
REVIEW_ACTION = "appeal.review"


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _review_trail(conn: Any, answer_ids: list[int]) -> dict[int, dict[str, Any]]:
    """回读每条申诉**最近一次**复核的审计记录（remark / 复核人 / 时间）。

    单独查一次而不是在主查询里 `json_extract` 子查询：一是避免依赖 SQLite 的
    JSON1 扩展行为（不同构建可能未启用），二是「最近一条」用 Python 归并比
    SQL 的相关子查询好读也好测。
    """
    if not answer_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT a.id, a.target_id, a.detail, a.created_at, a.user_id,
               u.real_name, u.username
        FROM audit_logs a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE a.action = ? AND a.target_type = 'answer'
          AND a.target_id IN ({",".join("?" * len(answer_ids))})
        ORDER BY a.created_at ASC, a.id ASC
        """,
        [REVIEW_ACTION, *answer_ids],
    ).fetchall()

    trail: dict[int, dict[str, Any]] = {}
    for r in rows:
        detail: dict[str, Any] = {}
        if isinstance(r["detail"], str) and r["detail"]:
            try:
                parsed = json.loads(r["detail"])
                if isinstance(parsed, dict):
                    detail = parsed
            except ValueError:  # pragma: no cover - 非法 JSON 不该出现
                detail = {}
        trail[int(r["target_id"])] = {
            "review_remark": detail.get("remark"),
            "review_result": detail.get("result"),
            "reviewed_at": r["created_at"],
            "reviewer_id": r["user_id"],
            "reviewer_name": r["real_name"] or r["username"],
        }
    return trail


# --------------------------------------------------------------------------- F4


@router.get("/api/appeals")
async def list_appeals(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """F4 申诉列表。**默认只返回待复核**（首屏最关心的就是「还有什么没处理」）。"""
    raw_status = (request.query_params.get("status") or "pending").strip().lower()
    condition = _STATUS_FILTERS.get(raw_status)
    if condition is None:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            "status 必须是 pending、upheld、rejected 或 all",
        )

    where = [condition]
    params: list[Any] = []

    student_id = request.query_params.get("student_id")
    if student_id:
        try:
            params.append(int(student_id))
        except ValueError as exc:
            raise BizError(ErrorCode.PARAM_INVALID, "student_id 必须是整数") from exc
        where.append("a.student_id = ?")

    class_id = request.query_params.get("class_id")
    if class_id:
        try:
            params.append(int(class_id))
        except ValueError as exc:
            raise BizError(ErrorCode.PARAM_INVALID, "class_id 必须是整数") from exc
        # 按学生**当前所在班级**筛，而不是按记录产生时的班级 ——
        # `answers` 表没有班级快照列，且学生转班后管理者想看的也是「他现在这个班」的记录。
        where.append("u.class_id = ?")

    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")
    if date_from or date_to:
        start, end = normalise_range(date_from, date_to)
        where.append("date(datetime(a.created_at, ?)) BETWEEN ? AND ?")
        params.extend([_BJ, start, end])

    clause = "WHERE " + " AND ".join(where)
    pg, ps = parse_pagination(request)

    with read_conn() as conn:
        total = int(
            conn.execute(
                f"""
                SELECT COUNT(*) AS n
                FROM answers a
                JOIN users u ON u.id = a.student_id
                {clause}
                """,
                params,
            ).fetchone()["n"]
            or 0
        )
        rows = conn.execute(
            f"""
            SELECT a.id, a.session_id, a.student_id, a.word_id, a.mode, a.step,
                   a.expected, a.asr_text, a.letter_seq, a.target_seq, a.answer_text,
                   a.is_correct, a.match_type, a.asr_confidence, a.duration_ms,
                   a.retry_count, a.used_keyboard, a.appealed, a.appeal_result,
                   a.provider, a.created_at,
                   u.username, u.real_name, u.class_id,
                   c.name AS class_name,
                   w.en, w.cn, w.unit_id,
                   un.name AS unit_name
            FROM answers a
            JOIN users u ON u.id = a.student_id
            LEFT JOIN classes c ON c.id = u.class_id
            LEFT JOIN words w ON w.id = a.word_id
            LEFT JOIN units un ON un.id = w.unit_id
            {clause}
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, ps, (pg - 1) * ps],
        ).fetchall()
        trail = _review_trail(conn, [int(r["id"]) for r in rows])

    items: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d.pop("appealed", None)
        # U8：一期不存录音 → 恒 false。保留字段是为了前端不必改版。
        d["audio_available"] = False
        d["appeal_id"] = int(r["id"])
        d.update(trail.get(int(r["id"]), {}))
        items.append(d)

    return page(items, total, pg, ps)


# --------------------------------------------------------------------------- F5


@router.post("/api/appeals/{appeal_id}/review")
async def review_appeal(
    appeal_id: int,
    payload: AppealReviewIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """F5 复核一条申诉。

    - `result=upheld`（申诉成立）/ `rejected`（维持原判）
    - **不改 `is_correct`**（见模块 docstring 的硬约束）
    - 写 `audit_logs`，`remark` 落在 `detail` 里
    """
    aid = int(appeal_id)
    ip = _ip(request)

    with write_tx() as conn:
        row = conn.execute(
            """
            SELECT id, student_id, word_id, expected, asr_text, is_correct,
                   match_type, appealed, appeal_result
            FROM answers WHERE id = ?
            """,
            (aid,),
        ).fetchone()
        if row is None:
            raise BizError(ErrorCode.NOT_FOUND, "申诉记录不存在")
        if not int(row["appealed"] or 0):
            raise BizError(ErrorCode.NOT_FOUND, "该记录没有申诉，无需复核")

        previous = row["appeal_result"]
        if previous == payload.result:
            # 幂等：重复点「同意」不该报错，也不该再写一条审计把历史冲乱
            return ok(
                {
                    "appeal_id": aid,
                    "result": payload.result,
                    "remark": payload.remark,
                    "previous_result": previous,
                    "changed": False,
                    "is_correct": int(row["is_correct"] or 0),
                    "message": "该申诉已是此结论，未重复处理。",
                }
            )

        # ⚠️ 只改 appeal_result 一列。is_correct 一个字都不动。
        conn.execute(
            "UPDATE answers SET appeal_result = ? WHERE id = ?",
            (payload.result, aid),
        )

        # 事务外还要用到的字段先拷出来：不能拿闭包外的 `row` 去读一个已关闭的连接。
        original = dict(row)

        write_audit(
            conn,
            user_id=admin.id,
            action=REVIEW_ACTION,
            target_type="answer",
            target_id=aid,
            detail={
                "result": payload.result,
                "remark": payload.remark,
                "previous_result": previous,
                # 留痕：证明复核没有动原始判定（对账时最常被质疑的一点）
                "is_correct_unchanged": int(original["is_correct"] or 0),
                "student_id": int(original["student_id"]) if original["student_id"] else None,
                "word_id": int(original["word_id"]) if original["word_id"] else None,
                "expected": original["expected"],
                "asr_text": original["asr_text"],
                "match_type": original["match_type"],
            },
            ip=ip,
        )

    return ok(
        {
            "appeal_id": aid,
            "result": payload.result,
            "remark": payload.remark,
            "previous_result": previous,
            "changed": True,
            "is_correct": int(original["is_correct"] or 0),
            "message": "复核完成",
        }
    )
