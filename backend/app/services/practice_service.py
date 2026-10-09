"""练习服务 —— WP-A，契约《01-共享契约冻结》§4.1 练习段。

会话管理（**内存会话，不落库**）：
- `start` 组卷 = 全选：该 unit+type 全量条目按 sort 排序**全量下发**；
- `answer` 判定：`spell_en2zh` 走 `compare_service`（契约 §4.1 / §6.1）；
  `spell_zh2en` 兜底校验走 judge 的字母序列归一（逐格判定本体在 App 本地，§6.2/§6.4）；
- `settle` 写 `practice_logs`：`correct` 为逐题判对的个数，`wrong = total - correct`
  （未作答视为错，保证 correct + wrong = total 恒成立，见验收 U6）；
- 进程重启丢会话可接受（App 重新 start，契约 §4.1 注）。
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode
from app.services import compare_service
from app.services.judge.normalize import normalize_letters

#: 内存会话上限（防泄漏：单家庭场景远够用）
MAX_SESSIONS = 200

_sessions: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _get_session(session_id: str) -> dict[str, Any]:
    with _lock:
        session = _sessions.get(session_id)
    if session is None:
        raise BizError(ErrorCode.NOT_FOUND, "练习会话不存在或已失效，请重新开始")
    return session


def start(*, unit_id: int, item_type: str, mode: str) -> dict[str, Any]:
    """`POST /api/practice/start` → {session_id, items:[{id,en,pos,zh,alt_zh}]}。"""
    from app.schemas.simplify import PRACTICE_MODES, ITEM_TYPES

    if mode not in PRACTICE_MODES:
        raise BizError(ErrorCode.PARAM_INVALID, "mode 必须是 listen / spell_zh2en / spell_en2zh")
    if item_type not in ITEM_TYPES:
        raise BizError(ErrorCode.PARAM_INVALID, "type 必须是 word / phrase / sentence")

    with read_conn() as conn:
        unit = conn.execute(
            "SELECT id FROM book_units WHERE id = ?", (unit_id,)
        ).fetchone()
        if unit is None:
            raise BizError(ErrorCode.UNIT_NOT_FOUND, "单元不存在")
        rows = conn.execute(
            """
            SELECT id, en, pos, zh, alt_zh FROM items
             WHERE unit_id = ? AND type = ?
             ORDER BY sort, id
            """,
            (unit_id, item_type),
        ).fetchall()

    items = [dict(r) for r in rows]
    if not items:
        raise BizError(ErrorCode.NO_PRACTICE_CONTENT)

    session_id = uuid.uuid4().hex
    with _lock:
        # 简单防泄漏：超上限时丢掉最旧的会话
        while len(_sessions) >= MAX_SESSIONS:
            _sessions.pop(next(iter(_sessions)))
        _sessions[session_id] = {
            "unit_id": unit_id,
            "type": item_type,
            "mode": mode,
            "items": items,
            # 每题最后一答的对错（settle 统计口径）
            "results": {},
        }
    return {"session_id": session_id, "items": items}


def answer(
    *,
    session_id: str,
    item_id: int,
    mode: str,
    answer_text: str,
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    """`POST /api/practice/answer` → {correct, matched_zh?}。"""
    from app.schemas.simplify import PRACTICE_MODES

    if mode not in PRACTICE_MODES:
        raise BizError(ErrorCode.PARAM_INVALID, "mode 必须是 listen / spell_zh2en / spell_en2zh")

    session = _get_session(session_id)
    item = next((i for i in session["items"] if i["id"] == item_id), None)
    if item is None:
        raise BizError(ErrorCode.PARAM_INVALID, "item_id 不在本场练习里")

    if mode == "listen":
        # listen = 纸上写 + 拍照批改（走 /api/dictation/grade），没有在线判定口径
        raise BizError(ErrorCode.PARAM_INVALID, "listen 模式没有在线判定，请走拍照批改")

    matched_zh: str | None = None
    if mode == "spell_en2zh":
        correct = compare_service.zh_matches(answer_text, item["zh"], item.get("alt_zh", ""))
        if correct:
            matched_zh = item["zh"]
    else:  # spell_zh2en 兜底校验：字母序列归一后比较（App 本地已逐格判过）
        correct = (
            normalize_letters(answer_text) == normalize_letters(item["en"])
            and bool(normalize_letters(answer_text))
        )

    session["results"][item_id] = bool(correct)
    payload: dict[str, Any] = {"correct": bool(correct)}
    if matched_zh is not None:
        payload["matched_zh"] = matched_zh
    return payload


def settle(*, session_id: str, duration_ms: int = 0) -> dict[str, Any]:
    """`POST /api/practice/settle` → {}（服务端写 practice_logs）。"""
    session = _get_session(session_id)
    total = len(session["items"])
    correct = sum(1 for ok in session["results"].values() if ok)
    wrong = total - correct  # 未作答视为错：保证 correct + wrong == total

    with write_tx() as conn:
        conn.execute(
            """
            INSERT INTO practice_logs (unit_id, type, mode, total, correct, wrong,
                                       duration_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["unit_id"],
                session["type"],
                session["mode"],
                total,
                correct,
                wrong,
                max(0, int(duration_ms)),
                utc_now_iso(),
            ),
        )

    with _lock:
        _sessions.pop(session_id, None)
    return {}
