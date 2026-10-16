"""练习服务 —— WP-A，契约《01-共享契约冻结》§4.1 练习段（v1.3，todo11 二轮）。

会话管理（**内存会话，不落库**）：
- `start` 组卷（v1.3 F12-1）：`unit_ids`/`types` 数组多选（单数 `unit_id`/`type`
  保留兼容；同请求两者都传**以数组为准**）。组卷顺序 =
  types 按 word→phrase→sentence 分组 → 组内按请求中单元顺序 → sort → id，
  全量下发。session 快照 `unit_ids`/`types` 供 settle 回退。
- `answer`（v1.3 F12-2）：`spell_zh2en` 恢复**必走 HTTP**，`correct` 必填
  （语音判定在服务端 WS verdict §5，键盘判定在 App 本地 §6.4，本接口只留痕）；
  `spell_en2zh` 不传 `correct`，服务端用 `compare_service` 判定；
  `listen` 不走本接口（v1.1 #1 不变）。
  **判对（correct=true）→ 服务端自动写 wrong_book.mastered_at**（判对保留显示，
  不再自动移出，废除 v1.1 #4）；判错留痕由 App 调 `/api/wrongbook/auto-add`。
- `settle`（v1.3 F12-3）写 `practice_logs`：`unit_ids`/`types` 为 JSON 数组；
  请求缺省时回退 start 快照。`correct` 为逐题判对的个数，`wrong = total - correct`
  （未作答视为错，保证 correct + wrong = total 恒成立）。
- 进程重启丢会话可接受（App 重新 start，契约 §4.1 注）。
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode
from app.services import compare_service, wrongbook_service

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


def _resolve_selection(
    singular: int | str | None,
    plural: list[int] | list[str] | None,
    *,
    field_singular: str,
    field_plural: str,
) -> list[Any]:
    """单数/数组二选一：数组优先（契约 §4.1 v1.3：两者都传以数组为准）。

    单元数组去重保序；数组与单数都不传 → 10001。
    """
    if plural is not None:
        if not isinstance(plural, list) or not plural:
            raise BizError(ErrorCode.PARAM_INVALID, f"{field_plural} 必须是非空数组")
        seen: list[Any] = []
        for v in plural:
            if v not in seen:
                seen.append(v)
        return seen
    if singular is not None:
        return [singular]
    raise BizError(
        ErrorCode.PARAM_INVALID, f"{field_singular} 与 {field_plural} 必须传其一"
    )


def start(
    *,
    mode: str,
    unit_id: int | None = None,
    item_type: str | None = None,
    unit_ids: list[int] | None = None,
    item_types: list[str] | None = None,
) -> dict[str, Any]:
    """`POST /api/practice/start` → {session_id, items:[{id,en,pos,zh,alt_zh}]}。"""
    from app.schemas.simplify import PRACTICE_MODES, ITEM_TYPES

    if mode not in PRACTICE_MODES:
        raise BizError(ErrorCode.PARAM_INVALID, "mode 必须是 listen / spell_zh2en / spell_en2zh")

    sel_units: list[int] = _resolve_selection(
        unit_id, unit_ids, field_singular="unit_id", field_plural="unit_ids"
    )
    sel_types: list[str] = _resolve_selection(
        item_type, item_types, field_singular="type", field_plural="types"
    )
    for t in sel_types:
        if t not in ITEM_TYPES:
            raise BizError(ErrorCode.PARAM_INVALID, "type 必须是 word / phrase / sentence")

    # 组卷顺序（契约 §4.1 v1.3）：types 按 word→phrase→sentence 分组
    # → 组内按请求中的单元顺序 → sort → id。ITEM_TYPES 本身即该固定顺序。
    items: list[dict[str, Any]] = []
    with read_conn() as conn:
        placeholders_units = ",".join("?" for _ in sel_units)
        found_units = {
            int(r["id"])
            for r in conn.execute(
                f"SELECT id FROM book_units WHERE id IN ({placeholders_units})",
                tuple(sel_units),
            ).fetchall()
        }
        missing = [u for u in sel_units if int(u) not in found_units]
        if missing:
            raise BizError(ErrorCode.UNIT_NOT_FOUND, "单元不存在")

        for t in ITEM_TYPES:
            if t not in sel_types:
                continue
            for u in sel_units:
                rows = conn.execute(
                    """
                    SELECT id, en, pos, zh, alt_zh FROM items
                     WHERE unit_id = ? AND type = ?
                     ORDER BY sort, id
                    """,
                    (u, t),
                ).fetchall()
                items.extend(dict(r) for r in rows)

    if not items:
        raise BizError(ErrorCode.NO_PRACTICE_CONTENT)

    session_id = uuid.uuid4().hex
    with _lock:
        # 简单防泄漏：超上限时丢掉最旧的会话
        while len(_sessions) >= MAX_SESSIONS:
            _sessions.pop(next(iter(_sessions)))
        _sessions[session_id] = {
            # start 快照（settle 缺省回退用，F12-3）
            "unit_ids": [int(u) for u in sel_units],
            "types": list(sel_types),
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
    answer_text: str = "",
    elapsed_ms: int = 0,
    correct: bool | None = None,
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
        # 服务端判定（契约 §4.1：en2zh 不传 correct）
        correct = compare_service.zh_matches(answer_text, item["zh"], item.get("alt_zh", ""))
        if correct:
            matched_zh = item["zh"]
    else:
        # spell_zh2en（契约 v1.3 §4.1/§6.4）：判定已在服务端 verdict / 键盘本地完成，
        # 本接口恢复必走 HTTP 上报留痕，correct 必填。
        if correct is None:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                "spell_zh2en 必须携带 correct（判定结果上报）",
            )
        correct = bool(correct)

    session["results"][item_id] = bool(correct)

    # 判对 → 服务端自动写 mastered_at（判对保留显示，不再移出，契约 §3.4 / F12-2）
    if correct:
        wrongbook_service.mark_mastered(item_id=item_id, mode=mode)

    payload: dict[str, Any] = {"correct": bool(correct)}
    if matched_zh is not None:
        payload["matched_zh"] = matched_zh
    return payload


def settle(
    *,
    session_id: str,
    duration_ms: int = 0,
    unit_ids: list[int] | None = None,
    types: list[str] | None = None,
) -> dict[str, Any]:
    """`POST /api/practice/settle` → {}（服务端写 practice_logs）。

    `unit_ids`/`types` 缺省时回退 start 快照（契约 §4.1 v1.3）。
    """
    from app.schemas.simplify import ITEM_TYPES

    session = _get_session(session_id)

    log_unit_ids = unit_ids if unit_ids else session["unit_ids"]
    log_types = types if types else session["types"]
    if not isinstance(log_unit_ids, list) or not log_unit_ids:
        raise BizError(ErrorCode.PARAM_INVALID, "unit_ids 必须是非空数组")
    if not isinstance(log_types, list) or not log_types:
        raise BizError(ErrorCode.PARAM_INVALID, "types 必须是非空数组")
    for t in log_types:
        if t not in ITEM_TYPES:
            raise BizError(ErrorCode.PARAM_INVALID, "types 必须是 word / phrase / sentence")

    total = len(session["items"])
    correct = sum(1 for ok in session["results"].values() if ok)
    wrong = total - correct  # 未作答视为错：保证 correct + wrong == total

    with write_tx() as conn:
        conn.execute(
            """
            INSERT INTO practice_logs (unit_ids, types, mode, total, correct, wrong,
                                       duration_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                json.dumps([int(u) for u in log_unit_ids], separators=(",", ":")),
                json.dumps(list(log_types), separators=(",", ":")),
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
