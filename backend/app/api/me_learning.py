"""学生端学习接口 —— WP-BE-04 F16 ~ F19（接口设计 §8.3、§8.4、§8.8、§8.9）。

| 接口 | 说明 |
|---|---|
| `GET  /api/me/units` | 我的单元列表与进度（英语给词条维度，语文给课文维度） |
| `GET  /api/me/progress` | 进度总览：已练/已掌握、连续天数、正确率、今日统计 |
| `GET  /api/me/wrong-words` | **错题本**（契约 §14.1 缺口 6，一期只覆盖英语词条） |
| `POST /api/answers/{id}/appeal` | **学生申诉**（契约 §14.1 缺口 5） |

### 两条容易踩的安全线

- **错题本取 `word_mastery` 物化表**（§8.8 明文），不去聚合 `answers` ——
  后者随记录增长会越来越慢。
- **申诉必须校验归属**（§8.9）：`answer_id` 不属于当前登录学生 → `10004`，
  否则可以越权改他人记录。且**只置 `appealed`，绝不改 `is_correct`/`match_type`**。

### 关于 `_unit_progress` 的归属

§8.3 需要「单元 + 词条 + 课文 + 背诵」四表联查，横跨 BE-03 / BE-04 / BE-05 三个包。
它不属于掌握度（`MasteryService` 只管 `word_mastery`），也不属于练习流程，
此处先落在本文件；**BE-07（管理端统计）就位后如出现同口径需求，应下沉为共享服务**。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.db import read_conn
from app.core.deps import CurrentUser, get_current_user
from app.core.errors import BizError, ErrorCode
from app.core.pagination import parse_pagination
from app.core.response import ok, page
from app.schemas.me_learning import AppealIn
from app.services.mastery_service import MasteryService
from app.services.user_service import UserService

router = APIRouter(tags=["me-learning"])


def _opt(request: Request, key: str) -> str | None:
    raw = request.query_params.get(key)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _to_int(value: str | None, field: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BizError(ErrorCode.PARAM_INVALID, f"{field} 必须是整数") from exc


def _preference(user: CurrentUser) -> tuple[int | None, str | None]:
    """读取学生的年级 / 学期偏好（`users.preferred_*`）。"""
    with read_conn() as conn:
        row = conn.execute(
            "SELECT preferred_grade, preferred_semester FROM users WHERE id = ?",
            (user.id,),
        ).fetchone()
    if row is None:  # pragma: no cover - 依赖 get_current_user 已查过库
        return None, None
    return row["preferred_grade"], row["preferred_semester"]


# ---------------------------------------------------------------------------
# §8.3 我的单元列表与进度
# ---------------------------------------------------------------------------


def _chinese_unit_progress(student_id: int, unit_id: int) -> dict[str, Any]:
    """语文单元的课文维度进度（`text_total` / `recited` / `last_grade`）。"""
    with read_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(t.id) AS text_total
            FROM texts t
            WHERE t.unit_id = ? AND t.status = 'active'
            """,
            (unit_id,),
        ).fetchone()

        recited = conn.execute(
            """
            SELECT COUNT(DISTINCT r.text_id) AS recited
            FROM recitations r
            JOIN texts t ON t.id = r.text_id
            WHERE r.student_id = ? AND t.unit_id = ? AND t.status = 'active'
            """,
            (student_id, unit_id),
        ).fetchone()

        last = conn.execute(
            """
            SELECT r.grade AS last_grade, r.created_at AS last_recite_at
            FROM recitations r
            JOIN texts t ON t.id = r.text_id
            WHERE r.student_id = ? AND t.unit_id = ?
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT 1
            """,
            (student_id, unit_id),
        ).fetchone()

        with_error = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM recitations r
            JOIN texts t ON t.id = r.text_id
            WHERE r.student_id = ? AND t.unit_id = ? AND r.covered_ratio < 1.0
            """,
            (student_id, unit_id),
        ).fetchone()

    text_total = int(row["text_total"] or 0)
    recited_count = int(recited["recited"] or 0)
    error_count = int(with_error["c"] or 0)

    # 与词条级同一套枚举、同一套「按序命中即停」的派生规则（契约 §14 裁决 1）
    if last is None or recited_count <= 0:
        status = "not_started"
    elif text_total > 0 and recited_count >= text_total and error_count == 0:
        status = "mastered"
    elif error_count > 0:
        status = "practiced_with_error"
    else:
        status = "practiced_ok"

    return {
        "text_total": text_total,
        "recited": recited_count,
        "last_grade": last["last_grade"] if last else None,
        "last_practice_at": last["last_recite_at"] if last else None,
        "status": status,
    }


@router.get("/api/me/units")
def my_units(request: Request, user: CurrentUser = Depends(get_current_user)):
    """`GET /api/me/units`（§8.3）。

    `subject` / `grade` / `semester` 不传时取用户偏好；都没有则返回空列表
    （**不报错** —— 学生刚注册完还没选年级，首页要能正常渲染）。
    """
    subject = _opt(request, "subject")
    grade = _to_int(_opt(request, "grade"), "grade")
    semester = _opt(request, "semester")

    if grade is None or semester is None:
        pref_grade, pref_semester = _preference(user)
        grade = grade if grade is not None else pref_grade
        semester = semester if semester is not None else pref_semester

    if grade is None or semester is None:
        return ok({"grade": grade, "semester": semester, "items": []})

    where = ["u.grade = ?", "u.semester = ?", "u.status = 'active'"]
    params: list[Any] = [grade, semester]
    if subject:
        where.append("u.subject = ?")
        params.append(subject)

    with read_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT u.id, u.subject, u.name, u.grade, u.semester, u.order_no
            FROM units u
            WHERE {' AND '.join(where)}
            ORDER BY u.subject, u.order_no, u.id
            """,
            params,
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        unit = dict(row)
        item: dict[str, Any] = {
            "unit_id": int(unit["id"]),
            "subject": unit["subject"],
            "name": unit["name"],
        }
        if unit["subject"] == "english":
            stats = MasteryService.unit_counts(user.id, int(unit["id"]))
            item.update(
                {
                    "word_total": stats["word_total"],
                    "practiced": stats["practiced"],
                    "mastered": stats["mastered"],
                    "accuracy": stats["accuracy"],
                    "last_practice_at": stats["last_practice_at"],
                    "status": stats["status"],
                }
            )
        else:
            item.update(_chinese_unit_progress(user.id, int(unit["id"])))
        items.append(item)

    return ok({"grade": grade, "semester": semester, "items": items})


# ---------------------------------------------------------------------------
# §8.4 进度总览
# ---------------------------------------------------------------------------


@router.get("/api/me/progress")
def my_progress(user: CurrentUser = Depends(get_current_user)):
    """`GET /api/me/progress`（§8.4）。正确率口径与 `UserService._accuracy()` 一致。"""
    return ok(MasteryService.progress_overview(user.id))


# ---------------------------------------------------------------------------
# §8.8 错题本
# ---------------------------------------------------------------------------


@router.get("/api/me/wrong-words")
def my_wrong_words(request: Request, user: CurrentUser = Depends(get_current_user)):
    """`GET /api/me/wrong-words`（§8.8）。

    只返回 `word_mastery.wrong_count > 0` 的词条，按错误次数降序；
    不传 `unit_id` 时按用户偏好年级学期取全部。「只练错题」复用
    `POST /api/sessions` 的 `scope='wrong'`，**不新增接口**。
    """
    page_no, page_size = parse_pagination(request)
    unit_id = _to_int(_opt(request, "unit_id"), "unit_id")

    grade: int | None = None
    semester: str | None = None
    if unit_id is None:
        grade_raw = _opt(request, "grade")
        grade = _to_int(grade_raw, "grade")
        semester = _opt(request, "semester")
        if grade is None or semester is None:
            pref_grade, pref_semester = _preference(user)
            grade = grade if grade is not None else pref_grade
            semester = semester if semester is not None else pref_semester

    items, total = MasteryService.wrong_words(
        user.id,
        unit_id=unit_id,
        grade=grade,
        semester=semester,
        page=page_no,
        page_size=page_size,
    )

    payload: list[dict[str, Any]] = []
    for item in items:
        payload.append(
            {
                "word_id": int(item["word_id"]),
                "unit_id": int(item["unit_id"]),
                "unit_name": item["unit_name"],
                "type": item["type"],
                "en": item["en"],
                "cn": item["cn"],
                "phonetic": item["phonetic"],
                "wrong_count": int(item["wrong_count"] or 0),
                "total_count": int(item["total_count"] or 0),
                "last_wrong_at": item["last_wrong_at"],
                "last_your_answer": item["last_your_answer"],
                "mastery_status": item["mastery_status"],
            }
        )
    return page(payload, total, page_no, page_size)


# ---------------------------------------------------------------------------
# §8.9 学生申诉
# ---------------------------------------------------------------------------


@router.post("/api/answers/{answer_id}/appeal")
def appeal_answer(
    answer_id: int,
    payload: AppealIn,
    user: CurrentUser = Depends(get_current_user),
):
    """`POST /api/answers/{id}/appeal`（§8.9）。

    - 仅 `match_type ∈ {loose, fuzzy}`（即答题响应 `can_appeal=true`）允许申诉，否则 `10003`；
    - `answer_id` 不存在或**不属于当前学生** → `10004`；
    - **不改判分**，只置 `appealed=1`；重复调用幂等。
    """
    with read_conn() as conn:
        row = conn.execute(
            """
            SELECT id, student_id, match_type, appealed
            FROM answers WHERE id = ?
            """,
            (answer_id,),
        ).fetchone()

    if row is None or int(row["student_id"]) != int(user.id):
        # 不区分「不存在」与「不属于你」，避免探测他人记录
        raise BizError(ErrorCode.NOT_FOUND, "答题记录不存在")

    if row["match_type"] not in ("loose", "fuzzy"):
        raise BizError(
            ErrorCode.FORBIDDEN,
            "该题次不支持申诉（只有机器判定存疑的题次可以申诉）",
        )

    # 幂等：已申诉则直接返回成功，不重复计数
    if int(row["appealed"] or 0) == 1:
        return ok({"answer_id": int(answer_id), "appealed": True})

    from app.core.audit import write_audit
    from app.core.db import utc_now_iso, write_tx

    with write_tx() as conn:
        conn.execute(
            """
            UPDATE answers SET appealed = 1, appeal_result = NULL
             WHERE id = ? AND appealed = 0
            """,
            (answer_id,),
        )
        write_audit(
            conn,
            user_id=int(user.id),
            action="answer.appeal",
            target_type="answer",
            target_id=int(answer_id),
            detail={
                "reason": (payload.reason or "").strip() or None,
                "match_type": row["match_type"],
                "at": utc_now_iso(),
            },
        )

    return ok({"answer_id": int(answer_id), "appealed": True})
