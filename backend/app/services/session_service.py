"""SessionService —— 练习流程与组卷（WP-BE-04 / 接口设计 §9.1–§9.3）。

三段式：**组卷 → 逐 step 答题 → 结算**。

安全设计的核心（契约禁止清单 4）：**答案绝不下发客户端**。

- 组卷响应只给 `prompt`（题目侧）与 `target_len`（占位框数量），
  `expected` 在答题响应里**判定之后**才出现；
- 判定 100% 在服务端做（禁止清单 7：客户端不得自行实现判分/对齐/差异计算）。

口径要点：

- **一个词条 = 一项**（F14）。`cn2en` 且词条要求拼读时要**两步都对**才算该项正确。
- **`empty` 不计入分母**：某词条名下**所有**答案都是 `empty` 时，该项不进正确率分母。
- 每次答题都写 `answers`（`step=1` 说词 / `step=2` 拼读各一条），
  并在**同一写事务**内更新 `word_mastery`（契约 §7.2）。
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Any

from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode
from app.core.logging import get_logger
from app.services.judge import JudgeService, judge_word_or_spell
from app.services.judge.normalize import normalize_letters
from app.services.mastery_service import MasteryService
from app.services.settings_service import SettingsService

logger = get_logger(__name__)

# 计为「答对」的匹配类型（契约 §8.1）
CORRECT_MATCH_TYPES = ("exact", "loose", "fuzzy")

# 占位——未指定数量时的兜底上限，避免一次生成几千题（settings 未定义此项，属实现防御）
_MAX_ITEMS = 500


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_scope_json(raw: Any) -> list[int]:
    """`texts.recite_scope`（TEXT 列里存 JSON 数组）→ `list[int]`。

    损坏时返回空表而不是抛异常：范围坏了顶多是「全文背诵」，
    不该让整个组卷请求 500。
    """
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[int] = []
    for item in data:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _elapsed_ms(started_at: str | None, ended_at: str) -> int:
    start = _parse_iso(started_at)
    end = _parse_iso(ended_at)
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def _hint_penalty(hint_level: int) -> int:
    """提示扣分：一/二/三档 → settings 的 `hint_score_l1/l2/l3`（默认 20/40/60）。"""
    key = {1: "hint_score_l1", 2: "hint_score_l2", 3: "hint_score_l3"}.get(int(hint_level or 0))
    if key is None:
        return 0
    return SettingsService.get_int(key, {1: 20, 2: 40, 3: 60}[int(hint_level)])


def score_answer(
    *,
    is_correct: bool,
    retry_count: int,
    used_keyboard: bool,
    hint_level: int,
) -> int:
    """单题评分（需求 §6.4）。**最终答错一律 0 分**，下限 0。

    > 重试 −10/次、键盘 −15、提示一/二/三档 −20/−40/−60（走 settings，管理者可调）。
    """
    if not is_correct:
        return 0

    score = 100
    score -= max(0, int(retry_count or 0)) * SettingsService.get_int("retry_score_penalty", 10)
    if used_keyboard:
        score -= SettingsService.get_int("keyboard_score_penalty", 15)
    score -= _hint_penalty(hint_level)
    return max(0, score)


# ---------------------------------------------------------------------------
# 组卷
# ---------------------------------------------------------------------------


class SessionService:
    """组卷 / 答题 / 结算。"""

    # ================================================================== 组卷

    @staticmethod
    def _candidate_words(
        *,
        unit_id: int,
        scope: str,
        scope_detail: dict[str, Any] | None,
        student_id: int,
    ) -> list[dict[str, Any]]:
        """按范围取候选词条。**固定 `status='active'`**（停用内容不参与出题）。"""
        where = ["w.unit_id = ?", "w.status = 'active'"]
        params: list[Any] = [int(unit_id)]
        join_mastery = False

        if scope == "wrong":
            # 只练错题（§8.8：复用本接口，不新增）
            join_mastery = True
            where.append("m.wrong_count > 0")
        elif scope == "unmastered":
            # 排除已掌握（F：`mastered_at IS NOT NULL` 的词条不进候选）
            join_mastery = True
            where.append("(m.mastered_at IS NULL)")

        sql_where = " AND ".join(where)
        join_sql = (
            "LEFT JOIN word_mastery m ON m.word_id = w.id AND m.student_id = ?"
            if join_mastery
            else ""
        )
        mastery_params: list[Any] = [int(student_id)] if join_mastery else []
        # 没有 join 时不能引用 `m.`，否则 SQLite 报 no such column
        wrong_count_sql = (
            "COALESCE(m.wrong_count, 0) AS wrong_count" if join_mastery else "0 AS wrong_count"
        )

        with read_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT w.id, w.unit_id, w.type, w.en, w.cn, w.accepted, w.phonetic,
                       w.pos, w.example_en, w.example_cn, w.require_spell, w.order_no,
                       {wrong_count_sql}
                FROM words w
                {join_sql}
                WHERE {sql_where}
                ORDER BY w.order_no, w.id
                """,
                (*mastery_params, *params),
            ).fetchall()

        words = [dict(r) for r in rows]

        if scope == "range" and scope_detail:
            words = SessionService._slice_range(words, scope_detail)

        return words

    @staticmethod
    def _slice_range(words: list[dict[str, Any]], detail: dict[str, Any]) -> list[dict[str, Any]]:
        """自定义区间：`{"start": 5, "end": 20}` —— 按**序号**（1 起）闭区间切片。

        需求 §5.3 写的是「词条序号区间，如第 5–20 个」，所以按位置切而不是
        按 `order_no` 过滤（`order_no` 允许为空 / 重复，不可靠）。
        """
        try:
            start = int(detail.get("start", 1))
            end = int(detail.get("end", len(words)))
        except (TypeError, ValueError) as exc:
            raise BizError(ErrorCode.PARAM_INVALID, "scope_detail 的 start/end 必须是整数") from exc

        if start < 1 or end < start:
            raise BizError(ErrorCode.PARAM_INVALID, "scope_detail 区间不合法（start 从 1 起且 end ≥ start）")
        return words[start - 1 : end]

    # ------------------------------------------------------------------
    # 语文场次（BE-05 补齐，契约未定义）
    # ------------------------------------------------------------------

    @staticmethod
    def _candidate_texts(unit_id: int) -> list[dict[str, Any]]:
        """取该单元下**可背诵的课文**（固定 `status='active'`）。

        只取元信息，**不含正文** —— 正文由 §8.7 单独拉，
        否则一个单元十几篇课文会把组卷响应撑爆。
        """
        with read_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, title, author, total_chars, recite_scope, order_no
                FROM texts
                WHERE unit_id = ? AND status = 'active'
                ORDER BY order_no, id
                """,
                (int(unit_id),),
            ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def _create_chinese_session(
        cls,
        *,
        student_id: int,
        unit_id: int,
        unit: dict[str, Any],
        strategy: dict[str, Any],
    ) -> dict[str, Any]:
        """语文场次组卷：`items` 是课文清单（每项带背诵范围与总字数）。"""
        scope = str(strategy.get("scope") or "unit")
        detail = strategy.get("scope_detail")
        texts = cls._candidate_texts(unit_id)

        if scope == "range" and isinstance(detail, dict):
            texts = cls._slice_range(texts, detail)
        elif isinstance(detail, dict) and detail.get("text_ids"):
            wanted = {int(i) for i in detail["text_ids"]}
            texts = [t for t in texts if int(t["id"]) in wanted]

        if not texts:
            raise BizError(ErrorCode.PARAM_INVALID, "该范围内没有可背诵的课文")

        items: list[dict[str, Any]] = []
        for index, text in enumerate(texts, start=1):
            items.append(
                {
                    "seq": index,
                    "text_id": int(text["id"]),
                    "title": text["title"],
                    "author": text.get("author"),
                    "total_chars": int(text.get("total_chars") or 0),
                    # 背诵范围随题下发，App 不用再算一遍（与 §8.7 同口径）
                    "recite_scope": _parse_scope_json(text.get("recite_scope")),
                }
            )

        snapshot = {
            "scope": scope,
            "scope_detail": detail,
            "count": int(strategy.get("count") or 0),
            "count_mode": str(strategy.get("count_mode") or "all"),
            "order": str(strategy.get("order") or "sequential"),
            "mode": "recite",
            "time_limit_ms": int(strategy.get("time_limit_ms") or 0),
        }
        time_limit_ms = int(snapshot["time_limit_ms"])
        started_at = utc_now_iso()

        with write_tx() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions
                    (student_id, unit_id, content_type, strategy,
                     total_items, correct_items, hint_count, time_limit_ms, status, started_at)
                VALUES (?, ?, 'chinese', ?, ?, 0, 0, ?, 'ongoing', ?)
                """,
                (
                    int(student_id),
                    int(unit_id),
                    json.dumps(snapshot, ensure_ascii=False),
                    len(items),
                    time_limit_ms,
                    started_at,
                ),
            )
            session_id = int(cursor.lastrowid)

        return {
            "session_id": session_id,
            "unit_id": int(unit_id),
            "unit_name": unit.get("name"),
            "content_type": "chinese",
            "items": items,
            "time_limit_ms": time_limit_ms,
            "started_at": started_at,
        }

    @staticmethod
    def _apply_count(
        words: list[dict[str, Any]], *, count: int, count_mode: str
    ) -> list[dict[str, Any]]:
        """数量维度：全部 / 前 N 个 / 随机 N 个。"""
        if count_mode == "all" or count <= 0:
            return words
        if count_mode == "first_n":
            return words[:count]
        if count_mode == "random_n":
            return random.sample(words, min(count, len(words)))
        raise BizError(ErrorCode.PARAM_INVALID, f"count_mode 不支持：{count_mode}")

    @staticmethod
    def _apply_order(words: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
        """顺序维度：顺序 / 随机 / 错题优先 / 倒序。"""
        if order == "sequential":
            return list(words)
        if order == "reverse":
            return list(reversed(words))
        if order == "random":
            shuffled = list(words)
            random.shuffle(shuffled)
            return shuffled
        if order == "wrong_first":
            # 错题排前面，其余保持顺序（稳定排序）
            return sorted(words, key=lambda w: -int(w.get("wrong_count") or 0))
        raise BizError(ErrorCode.PARAM_INVALID, f"order 不支持：{order}")

    @staticmethod
    def _build_items(words: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
        """生成题序（F8 / F9）。

        ⚠️ 每项**只含题目侧字段**：`prompt` 与 `target_len`。
        绝不能出现 `en` / `cn` 的答案侧内容（U17 专门验这条）。
        """
        items: list[dict[str, Any]] = []
        for index, word in enumerate(words, start=1):
            item_mode = mode if mode in ("cn2en", "en2cn") else random.choice(["cn2en", "en2cn"])

            if item_mode == "cn2en":
                # 中→英：题干给中文；例句（中文）可展示，不泄题
                prompt = word["cn"]
                example_cn = word.get("example_cn")
                require_spell = bool(word.get("require_spell"))
            else:
                # 英→中：题干给英文；例句可能含答案侧信息，不下发
                prompt = word["en"]
                example_cn = None
                # 需求 §5.5：该模式**不要求拼读**
                require_spell = False

            items.append(
                {
                    "seq": index,
                    "word_id": int(word["id"]),
                    "type": word["type"],
                    "mode": item_mode,
                    "prompt": prompt,
                    "example_cn": example_cn,
                    "require_spell": require_spell,
                    # 英文去空格后的长度，供 App 画占位框（不泄露内容）
                    "target_len": len(normalize_letters(word["en"])),
                }
            )
        return items

    @classmethod
    def create_session(
        cls,
        *,
        student_id: int,
        unit_id: int,
        content_type: str,
        strategy: dict[str, Any],
    ) -> dict[str, Any]:
        """`POST /api/sessions`（§9.1）：按策略**一次性生成完整题序**并落快照。"""
        from app.services.content_service import ContentService

        unit = ContentService.get_unit(unit_id)

        # 语文场次：题序 = 课文列表（**由 BE-05 补齐，契约未定义**）
        # 接口设计 §9.1 只描述了英语（`items[].word_id`、`mode` 标注「英语专有」），
        # 但需求 §5.6/§5.8 明确语文也有场次（整篇限时 3/5/10 分钟），
        # 而 §9.4 的背诵接口又要求传 `session_id` —— 不补这条链路语文根本走不通。
        if str(content_type) == "chinese":
            return cls._create_chinese_session(
                student_id=student_id,
                unit_id=unit_id,
                unit=unit,
                strategy=strategy,
            )

        scope = str(strategy.get("scope") or "unit")
        if scope == "paragraph":
            # 段落级只对语文课文有意义；英语词条没有段落层级 → 退化为整单元
            logger.info("英语练习收到 scope=paragraph（unit=%s），退化为整单元", unit_id)
            scope = "unit"
        if scope == "lesson":
            # 词条没有「课」的层级（units 即最小组织单位）→ 等同整单元
            scope = "unit"

        words = cls._candidate_words(
            unit_id=unit_id,
            scope=scope,
            scope_detail=strategy.get("scope_detail"),
            student_id=student_id,
        )

        if not words:
            raise BizError(ErrorCode.PARAM_INVALID, "该范围内没有可练习的词条")

        count = int(strategy.get("count") or 0)
        count_mode = str(strategy.get("count_mode") or "all")
        order = str(strategy.get("order") or "sequential")
        mode = str(strategy.get("mode") or "cn2en")
        time_limit_ms = int(strategy.get("time_limit_ms") or 0)

        words = cls._apply_count(words, count=min(count, _MAX_ITEMS), count_mode=count_mode)
        words = cls._apply_order(words, order)
        items = cls._build_items(words, mode)

        snapshot = {
            "scope": scope,
            "scope_detail": strategy.get("scope_detail"),
            "count": count,
            "count_mode": count_mode,
            "order": order,
            "mode": mode,
            "time_limit_ms": time_limit_ms,
        }

        started_at = utc_now_iso()
        with write_tx() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions
                    (student_id, unit_id, content_type, strategy,
                     total_items, correct_items, hint_count, time_limit_ms, status, started_at)
                VALUES (?, ?, ?, ?, ?, 0, 0, ?, 'ongoing', ?)
                """,
                (
                    int(student_id),
                    int(unit_id),
                    content_type,
                    json.dumps(snapshot, ensure_ascii=False),
                    len(items),
                    time_limit_ms,
                    started_at,
                ),
            )
            session_id = int(cursor.lastrowid)

        return {
            "session_id": session_id,
            "unit_id": int(unit_id),
            "unit_name": unit.get("name"),
            "content_type": content_type,
            "items": items,
            "time_limit_ms": time_limit_ms,
            "started_at": started_at,
        }

    # ================================================================== 答题

    @staticmethod
    def _load_session(conn: Any, session_id: int, student_id: int) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (int(session_id),)).fetchone()
        if row is None:
            raise BizError(ErrorCode.NOT_FOUND, "练习场次不存在")
        session = dict(row)
        if int(session["student_id"]) != int(student_id):
            # 越权：不暴露「存在但不属于你」
            raise BizError(ErrorCode.NOT_FOUND, "练习场次不存在")
        if session["status"] != "ongoing":
            raise BizError(ErrorCode.PARAM_INVALID, "该场次已结束，不能再提交答案")
        return session

    @staticmethod
    def _timeout_verdict(expected: str, asr_text: str | None, confidence: float | None):
        """构造超时判定（F15 行为 A：自动判错）。"""
        from app.services.judge.types import Verdict

        return Verdict(
            is_correct=False,
            match_type="timeout",
            expected=expected,
            normalized=None,
            asr_text=asr_text,
            can_appeal=False,
            asr_confidence=confidence,
        )

    @classmethod
    def submit_answer(
        cls,
        *,
        student_id: int,
        session_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """`POST /api/sessions/{id}/answers`（§9.2）。

        判定 → 写 `answers` → **同事务**更新 `word_mastery` → 回传掌握状态。

        Returns:
            `{"answer_id", "is_correct", "match_type", "score", "expected",
               "can_appeal", "mastery", ...}`；行为 B 跳过时额外带 `skipped=True`。
        """
        word_id = int(payload["word_id"])
        mode = str(payload["mode"])
        step = int(payload["step"])
        asr_text = payload.get("asr_text")
        answer_text = payload.get("answer_text")
        # 键盘是明确输入，优先级高于语音识别结果
        answer = answer_text or asr_text

        with write_tx() as conn:
            session = cls._load_session(conn, session_id, student_id)

            row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
            if row is None:
                raise BizError(ErrorCode.CONTENT_NOT_FOUND, "词条不存在或已被删除")
            word = dict(row)

            accepted = _parse_accepted(word.get("accepted"))
            expected = word["en"] if (step == 2 or mode == "cn2en") else word["cn"]

            timed_out = bool(payload.get("timed_out")) or cls._is_time_over(session, payload)
            behavior = SettingsService.get_str("time_limit_behavior", "A").strip().upper()

            # ---- F15 行为 B：跳过，不计对错（连记录都不落，才真的"不计"） ----
            if timed_out and behavior == "B":
                logger.info(
                    "限时行为 B：跳过 student=%s session=%s word=%s",
                    student_id, session_id, word_id,
                )
                return {
                    "answer_id": None,
                    "skipped": True,
                    "is_correct": False,
                    "match_type": "timeout",
                    "score": 0,
                    "expected": expected,
                    "can_appeal": False,
                    "timed_out": True,
                    "mastery": MasteryService.get_snapshot(student_id, word_id).model_dump(),
                }

            # ---- 判定：100% 在服务端 ----
            # 英语模式一（cn2en）走「双口径」：念整词 / 逐字母拼都收（二期口径，
            # 见 `judge/word_spell.py`）。`used_spell=True` 表示最终采纳的是拼读口径，
            # 决定下面是否回填 `letter_seq` / `target_seq`。
            used_spell = False
            if timed_out and behavior == "A":
                verdict = cls._timeout_verdict(expected, asr_text, payload.get("asr_confidence"))
            elif step == 2:
                # 兼容旧客户端：合并单轮后 App 不再发 step=2，历史版本仍可能发。
                verdict = JudgeService.judge_spell(
                    asr_text=answer,
                    target=word["en"],
                    asr_confidence=payload.get("asr_confidence"),
                )
            elif mode == "cn2en":
                verdict, used_spell = judge_word_or_spell(
                    answer=answer,
                    expected=expected,
                    accepted=accepted,
                    asr_confidence=payload.get("asr_confidence"),
                )
            else:
                verdict = JudgeService.judge_text(
                    answer=answer,
                    expected=expected,
                    accepted=accepted,
                    asr_confidence=payload.get("asr_confidence"),
                )

            score = score_answer(
                is_correct=verdict.is_correct,
                retry_count=payload.get("retry_count") or 0,
                used_keyboard=bool(payload.get("used_keyboard")),
                hint_level=payload.get("hint_level") or 0,
            )

            # 逐字母序列只在「真的是拼读口径」时才落：显式 step=2，或模式一走字母
            # 序列回退。念整词判对时不该有 letter_seq（否则统计会说谎）。
            as_spell = step == 2 or used_spell
            letter_seq = verdict.normalized if as_spell else None
            target_seq = normalize_letters(word["en"]) if as_spell else None

            cursor = conn.execute(
                """
                INSERT INTO answers
                    (session_id, student_id, word_id, mode, step, expected,
                     asr_text, letter_seq, target_seq, answer_text,
                     is_correct, match_type, asr_confidence,
                     duration_ms, first_voice_ms, retry_count, used_keyboard,
                     hint_level, used_hint_count, provider)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?)
                """,
                (
                    int(session_id),
                    int(student_id),
                    word_id,
                    mode,
                    step,
                    verdict.expected,
                    asr_text,
                    letter_seq,
                    target_seq,
                    answer_text,
                    1 if verdict.is_correct else 0,
                    verdict.match_type,
                    payload.get("asr_confidence"),
                    int(payload.get("duration_ms") or 0),
                    payload.get("first_voice_ms"),
                    int(payload.get("retry_count") or 0),
                    1 if payload.get("used_keyboard") else 0,
                    int(payload.get("hint_level") or 0),
                    int(payload.get("used_hint_count") or 0),
                    payload.get("provider"),
                ),
            )
            answer_id = int(cursor.lastrowid)

            # ---- 同事务更新掌握度（empty 不调用，契约 §7.2） ----
            if verdict.match_type == "empty":
                snapshot = MasteryService.get_snapshot(student_id, word_id)
            else:
                snapshot = MasteryService.update_after_answer(
                    conn,
                    student_id=int(student_id),
                    word_id=word_id,
                    is_correct=verdict.is_correct,
                    match_type=verdict.match_type,
                    duration_ms=int(payload.get("duration_ms") or 0),
                    hint_level=int(payload.get("hint_level") or 0),
                    mastered_streak=MasteryService.mastered_streak(),
                )

        result: dict[str, Any] = {
            "answer_id": answer_id,
            "is_correct": verdict.is_correct,
            "match_type": verdict.match_type,
            "score": score,
            "expected": verdict.expected,
            "normalized": verdict.normalized,
            "can_appeal": verdict.can_appeal,
            "timed_out": bool(timed_out),
            "mastery": {
                "status": snapshot.status,
                "streak": snapshot.streak,
                "mastered": snapshot.mastered,
            },
        }
        # 拼读必须逐字母回显（需求 §5.4 硬需求）：diff 无论对错都给
        if verdict.diff is not None:
            result["diff"] = [item.model_dump() for item in verdict.diff]
        return result

    @classmethod
    def record_ws_answer(
        cls,
        *,
        student_id: int,
        session_id: int,
        word_id: int,
        mode: str,
        step: int,
        verdict: Any,
        asr_text: str,
        used_spell: bool = False,
        duration_ms: int = 0,
        first_voice_ms: int | None = None,
        retry_count: int = 0,
        hint_level: int = 0,
        used_hint_count: int = 0,
        provider: str | None = None,
    ) -> int:
        """语音（WS）判分落库：写 `answers` + **同事务**更新 `word_mastery`。

        判定已由 WS 侧 `JudgeService` 完成（含仲裁改判），这里**只负责持久化**，
        score 口径与 REST `submit_answer` 完全一致（同一 `score_answer`）。
        场次不存在/已结束/越权/词条缺失 → 抛 `BizError`（调用方降级为不落库，
        不影响判分回传）。

        Returns:
            新写入的 `answer_id`（verdict 回传与断线补发 replay 都靠它）。
        """
        with write_tx() as conn:
            cls._load_session(conn, session_id, student_id)

            row = conn.execute("SELECT * FROM words WHERE id = ?", (int(word_id),)).fetchone()
            if row is None:
                raise BizError(ErrorCode.CONTENT_NOT_FOUND, "词条不存在或已被删除")
            word = dict(row)

            score = score_answer(
                is_correct=verdict.is_correct,
                retry_count=retry_count,
                used_keyboard=False,
                hint_level=hint_level,
            )

            # 同 REST：显式 step=2 或模式一走字母序列回退，才回填逐字母序列
            as_spell = step == 2 or used_spell
            letter_seq = verdict.normalized if as_spell else None
            target_seq = normalize_letters(word["en"]) if as_spell else None

            cursor = conn.execute(
                """
                INSERT INTO answers
                    (session_id, student_id, word_id, mode, step, expected,
                     asr_text, letter_seq, target_seq, answer_text,
                     is_correct, match_type, asr_confidence,
                     duration_ms, first_voice_ms, retry_count, used_keyboard,
                     hint_level, used_hint_count, provider)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?)
                """,
                (
                    int(session_id),
                    int(student_id),
                    int(word_id),
                    mode,
                    int(step),
                    verdict.expected,
                    asr_text,
                    letter_seq,
                    target_seq,
                    None,
                    1 if verdict.is_correct else 0,
                    str(verdict.match_type),
                    verdict.asr_confidence,
                    int(duration_ms or 0),
                    first_voice_ms,
                    int(retry_count or 0),
                    0,
                    int(hint_level or 0),
                    int(used_hint_count or 0),
                    provider,
                ),
            )
            answer_id = int(cursor.lastrowid)

            if str(verdict.match_type) == "empty":
                # 与 REST 口径一致：empty 不更新掌握度（契约 §7.2）
                MasteryService.get_snapshot(student_id, word_id)
            else:
                MasteryService.update_after_answer(
                    conn,
                    student_id=int(student_id),
                    word_id=int(word_id),
                    is_correct=verdict.is_correct,
                    match_type=str(verdict.match_type),
                    duration_ms=int(duration_ms or 0),
                    hint_level=int(hint_level or 0),
                    mastered_streak=MasteryService.mastered_streak(),
                )
        return answer_id

    @staticmethod
    def _is_time_over(session: dict[str, Any], payload: dict[str, Any]) -> bool:
        """服务端兜底判超时：`time_limit_ms > 0` 且本次用时已达上限。

        客户端也会置 `timed_out`，但**不能只信客户端** —— 判定相关的输入
        一律服务端可复核（与「判定在服务端」同一原则）。
        """
        limit = int(session.get("time_limit_ms") or 0)
        if limit <= 0:
            return False
        return int(payload.get("duration_ms") or 0) >= limit

    # ================================================================== 结算

    @classmethod
    def _item_statistics(
        cls, conn: Any, session_id: int
    ) -> tuple[dict[int, dict[str, Any]], int]:
        """按**词条**聚合答案（F14：一个词条算一项）。

        Returns:
            `(items, empty_item_count)`；`items[word_id]` 含
            `records` / `valid` / `steps` / `all_correct` / `requirement_met`。
        """
        rows = conn.execute(
            """
            SELECT a.*, w.require_spell AS w_require_spell, w.en AS w_en, w.cn AS w_cn
            FROM answers a
            LEFT JOIN words w ON w.id = a.word_id
            WHERE a.session_id = ?
            ORDER BY a.id
            """,
            (int(session_id),),
        ).fetchall()

        items: dict[int, dict[str, Any]] = {}
        for row in rows:
            record = dict(row)
            # word 被删时 word_id 会变 NULL（ON DELETE SET NULL）→ 用 -1 兜住
            key = int(record["word_id"]) if record["word_id"] is not None else -1
            bucket = items.setdefault(
                key,
                {
                    "word_id": key,
                    "records": [],
                    "en": record.get("w_en"),
                    "cn": record.get("w_cn"),
                    "require_spell": record.get("w_require_spell"),
                },
            )
            bucket["records"].append(record)

        empty_items = 0
        for bucket in items.values():
            records = bucket["records"]
            valid = [r for r in records if r["match_type"] != "empty"]
            steps = {int(r["step"]) for r in valid}
            bucket["valid"] = valid
            bucket["steps"] = steps

            if not valid:
                # 该项名下的记录全是 empty → 不进分母（U25）
                empty_items += 1
                bucket["state"] = "empty"
                bucket["all_correct"] = False
                continue

            bucket["all_correct"] = all(r["is_correct"] for r in valid)

            # §八 #96 / #97（2026-09-29）**英语模式一合并单轮**：客户端只发 `word`
            # （`step=1`），「念整词 / 逐字母拼」两个口径都在 `judge_word_or_spell`
            # 内部收口，一个词条只落一条 `answers`。因此**不再要求「两步都做」** ——
            # 旧口径（`cn2en` + `require_spell` 强制 `step1` **且** `step2`）在合并单轮后
            # 会把**念对的词**判成「没做完」→ `correct_items` 恒为 0
            # （真机实测：apple / banana 都判对，结算页却显示「题量 0/2 · 正确率 0% ·
            # 卡住 2 处」，而 `score` 又是 100 —— 自相矛盾的根因就在这）。
            # 口径：`valid` 非空即证明学生已实际作答，作答要求即为满足；只要有效记录
            # 全对（`all_correct`，含「错了又重试」仍算错，不改）即该项正确。
            # `step=2` 仅在旧客户端 / 显式拼读任务出现，此时「任一命中即对」同样成立。
            bucket["requirement_met"] = bool(valid)

            if bucket["all_correct"] and bucket["requirement_met"]:
                bucket["state"] = "correct"
            else:
                bucket["state"] = "wrong"

        return items, empty_items

    @classmethod
    def finish_session(
        cls,
        *,
        student_id: int,
        session_id: int,
        reason: str = "completed",
    ) -> dict[str, Any]:
        """`POST /api/sessions/{id}/finish`（§9.3）：结算并落 `sessions`。"""
        ended_at = utc_now_iso()

        with write_tx() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (int(session_id),)).fetchone()
            if row is None:
                raise BizError(ErrorCode.NOT_FOUND, "练习场次不存在")
            session = dict(row)
            if int(session["student_id"]) != int(student_id):
                raise BizError(ErrorCode.NOT_FOUND, "练习场次不存在")

            # 语文场次没有 `answers`，统计口径来自 `recitations`（BE-05 补齐）
            if str(session["content_type"]) == "chinese":
                return cls._finish_chinese_session(conn, session, reason=reason)

            items, empty_items = cls._item_statistics(conn, session_id)

            total_items = int(session["total_items"]) or len(items)
            correct_items = sum(1 for b in items.values() if b.get("state") == "correct")
            # F14 / U25：**一个词条算一项**，分母 = 项数 − 全 empty 的项数
            counted = max(0, total_items - empty_items)

            accuracy = round(correct_items / counted, 4) if counted > 0 else 0.0

            all_records = [r for b in items.values() for r in b["records"]]

            # 单题分数：同一项的多个 step 取平均；场次分数 = 非 empty 项的平均。
            # ⚠️ `answers` 表**没有 score 列**（DDL 未定义，且契约禁止改 `001_init.sql`），
            #    所以这里按 F12 规则用落库的 retry/keyboard/hint 重算，与答题响应的分一致。
            scored_items = [b for b in items.values() if b.get("state") != "empty"]
            item_scores = [
                round(
                    sum(
                        score_answer(
                            is_correct=bool(r["is_correct"]),
                            retry_count=int(r["retry_count"] or 0),
                            used_keyboard=bool(r["used_keyboard"]),
                            hint_level=int(r["hint_level"] or 0),
                        )
                        for r in b["valid"]
                    )
                    / len(b["valid"]),
                    2,
                )
                for b in scored_items
                if b["valid"]
            ]
            score = round(sum(item_scores) / len(item_scores), 2) if item_scores else 0.0

            hint_count = sum(int(r["used_hint_count"] or 0) for r in all_records)
            durations = [int(r["duration_ms"] or 0) for r in all_records]
            avg_duration_ms = round(sum(durations) / len(durations)) if durations else 0

            wrong_items: list[dict[str, Any]] = []
            for bucket in items.values():
                if bucket.get("state") != "wrong":
                    continue
                # ⚠️ 不能用「最后一条记录」：一项可能先答错、后补答对（例如说词对了但拼读没做），
                #    取最后一条会把 loose/exact 当成错答原因，前端回显会自相矛盾。
                #    优先取**最后一条判错的记录**；一条都没有（纯粹是步数没做完）时，
                #    退回最后一条有效记录，让前端至少能显示学生说了什么。
                wrong_records = [r for r in bucket["valid"] if not r["is_correct"]]
                chosen = (wrong_records or bucket["valid"])[-1]
                wrong_items.append(
                    {
                        "word_id": bucket["word_id"] if bucket["word_id"] > 0 else None,
                        "en": bucket.get("en") or chosen.get("expected"),
                        "cn": bucket.get("cn"),
                        "your_answer": chosen.get("answer_text") or chosen.get("asr_text"),
                        "match_type": chosen.get("match_type"),
                    }
                )

            improved = cls._improved(
                conn, session, correct_items=correct_items, counted=counted
            )

            # U15（APP-03）：「中途退出」要把场次标记为 `abandoned`，而不是 `finished`。
            # `sessions.status` 的取值域是契约冻结的（`ongoing`/`finished`/`abandoned`），
            # 所以这里只是**多选一个已有的合法取值**，不加新状态。
            # 统计口径不受污染：`stats_service` 的 `finished_session_count` 按
            # `status = 'finished'` 过滤，被放弃的场次本就不该计入。
            final_status = "abandoned" if reason == "abandoned" else "finished"
            conn.execute(
                """
                UPDATE sessions
                   SET status = ?, ended_at = ?, score = ?,
                       correct_items = ?, hint_count = ?, total_items = ?
                 WHERE id = ?
                """,
                (
                    final_status,
                    ended_at,
                    score,
                    correct_items,
                    hint_count,
                    total_items,
                    int(session_id),
                ),
            )

        duration_ms = _elapsed_ms(session.get("started_at"), ended_at)

        return {
            "session_id": int(session_id),
            "status": final_status,
            "total_items": total_items,
            "correct_items": correct_items,
            "counted_items": counted,
            "empty_items": empty_items,
            "accuracy": accuracy,
            "score": score,
            "hint_count": hint_count,
            "avg_duration_ms": avg_duration_ms,
            "duration_ms": duration_ms,
            "wrong_items": wrong_items,
            "improved": improved,
            "reason": reason,
            "ended_at": ended_at,
        }

    # ------------------------------------------------------------------
    # 语文场次统计（BE-05 补齐，契约未定义）
    # ------------------------------------------------------------------

    @staticmethod
    def sync_chinese_session(conn: Any, session_id: int) -> dict[str, Any]:
        """按 `recitations` **全量重算**语文场次的统计（BE-05 与 BE-04 共用同一口径）。

        英语场次的口径是「一个词条算一项，`answers` 里数」（F14）；
        语文没有 `answers`，对应口径是「**一个字算一项**」：

        | `sessions` 列 | 语文取值 |
        |---|---|
        | `total_items` | 所有已结算背诵的目标总字数 |
        | `correct_items` | 所有已结算背诵的点亮总字数 |
        | `hint_count` | 三档提示次数合计 |
        | `score` | 各次背诵得分的平均分 |

        用重算而非累加：重复结算、重复背诵都不会把数算重（天然幂等）。
        """
        rows = conn.execute(
            """
            SELECT total_chars, matched_chars, hint_count, score
            FROM recitations
            WHERE session_id = ? AND finish_reason IS NOT NULL
            """,
            (int(session_id),),
        ).fetchall()

        total = sum(int(r["total_chars"] or 0) for r in rows)
        matched = sum(int(r["matched_chars"] or 0) for r in rows)

        hints = 0
        for row in rows:
            try:
                data = json.loads(row["hint_count"] or "{}")
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict):
                for value in data.values():
                    try:
                        hints += int(value)
                    except (TypeError, ValueError):
                        continue

        scores = [float(r["score"]) for r in rows if r["score"] is not None]
        score = round(sum(scores) / len(scores), 2) if scores else None

        conn.execute(
            """
            UPDATE sessions
               SET total_items = ?, correct_items = ?, hint_count = ?, score = ?
             WHERE id = ?
            """,
            (total, matched, hints, score, int(session_id)),
        )

        return {
            "recitations": len(rows),
            "total_chars": total,
            "matched_chars": matched,
            "hint_count": hints,
            "score": score,
            "covered_ratio": round(matched / total, 4) if total > 0 else 0.0,
        }

    @classmethod
    def _finish_chinese_session(
        cls,
        conn: Any,
        session: dict[str, Any],
        *,
        reason: str = "completed",
    ) -> dict[str, Any]:
        """收口语文场次：汇总统计 + 置 `finished`。

        与英语分支的区别：这里**没有逐题明细**，只有按篇的汇总 ——
        学生端的结算页是「本次背诵」那一页（§9.4 settle），
        场次汇总只喂管理端看板与 `GET /api/me/units` 的进度。
        """
        session_id = int(session["id"])
        stats = cls.sync_chinese_session(conn, session_id)
        ended_at = utc_now_iso()
        # 同英语分支：`abandoned` 是契约允许的 `sessions.status` 取值（U15）。
        final_status = "abandoned" if reason == "abandoned" else "finished"
        conn.execute(
            """
            UPDATE sessions
               SET status = ?, ended_at = ?
             WHERE id = ? AND status = 'ongoing'
            """,
            (final_status, ended_at, session_id),
        )

        return {
            "session_id": session_id,
            "unit_id": session.get("unit_id"),
            "content_type": "chinese",
            "status": final_status,
            "recitations": stats["recitations"],
            "total_items": stats["total_chars"],
            "correct_items": stats["matched_chars"],
            "covered_ratio": stats["covered_ratio"],
            "hint_count": stats["hint_count"],
            "score": stats["score"],
            "accuracy": stats["covered_ratio"],
            "wrong_items": [],
            "improved": None,
            "reason": reason,
            "ended_at": ended_at,
            "duration_ms": _elapsed_ms(session.get("started_at"), ended_at),
        }

    @staticmethod
    def _improved(
        conn: Any,
        session: dict[str, Any],
        *,
        correct_items: int,
        counted: int,
    ) -> bool:
        """是否比上一场进步。

        口径（契约未定义，此处实现并上报）：比较同 **学生 + 单元 + 英语** 的
        **上一场已完成**场次的**项级正确率**（`correct_items / total_items`）。
        没有历史场次时以 0.8 为「表现不错」的门槛。

        ⚠️ 两边都用 `sessions` 表里已有的两个计数（`total_items` /
        `correct_items`），不引入当前场次才有的 `empty` 修正 ——
        分母口径不一致的比较没有意义。
        """
        current_ratio = (correct_items / counted) if counted > 0 else 0.0
        current_id = int(session["id"])

        unit_id = session.get("unit_id")
        if unit_id is None:
            return current_ratio >= 0.8

        row = conn.execute(
            """
            SELECT total_items, correct_items,
                   CASE WHEN total_items > 0
                        THEN CAST(correct_items AS REAL) / total_items
                        ELSE 0 END AS ratio
            FROM sessions
            WHERE student_id = ? AND unit_id = ? AND content_type = 'english'
              AND status = 'finished' AND id <> ?
            ORDER BY ended_at DESC, id DESC
            LIMIT 1
            """,
            (int(session["student_id"]), int(unit_id), current_id),
        ).fetchone()
        if row is None:
            return current_ratio >= 0.8
        return current_ratio > float(row["ratio"] or 0)

    # ==================================================================
    # 读取侧：「我的记录」场次列表 / 单次详情
    # 契约 §14.1 缺口 7（2026-09-25 补），接口设计 §9.5
    # ==================================================================
    #
    # 需求 §5.9 一期就要求「按日期列出练习场次与成绩，可查看单次详情」，
    # 但原契约只有 3 个 POST、读取侧是空的（§八 #75）。以下三个方法补齐；
    # 正确率口径**复用 `finish_session` 落库时写进 `sessions` 的那两个计数**，
    # 不另立一套（否则列表、详情、结算页三处数字会互相打架）。

    @staticmethod
    def list_sessions(
        *,
        student_id: int,
        page: int,
        page_size: int,
        content_type: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """`GET /api/sessions`（§9.5.1）：我的练习场次列表。

        只查当前学生；按 `started_at DESC, id DESC`（最新在前）。
        `ongoing` 场次**照样列出**（不隐藏数据），客户端据 `status` 显示「进行中」；
        能不能进详情由 §9.5.2 在服务端把关。
        """
        # where 里全是**字面量片段**，用户输入一律走 params 绑定，不拼字符串。
        where = ["s.student_id = ?"]
        params: list[Any] = [int(student_id)]
        if content_type:
            where.append("s.content_type = ?")
            params.append(content_type)
        if status:
            where.append("s.status = ?")
            params.append(status)
        cond = " AND ".join(where)

        with read_conn() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS c FROM sessions s WHERE {cond}", params
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT s.id, s.unit_id, u.name AS unit_name, s.content_type,
                       s.status, s.total_items, s.correct_items, s.hint_count,
                       s.score, s.started_at, s.ended_at
                  FROM sessions s
                  LEFT JOIN units u ON u.id = s.unit_id
                 WHERE {cond}
                 ORDER BY s.started_at DESC, s.id DESC
                 LIMIT ? OFFSET ?
                """,
                [*params, int(page_size), int((page - 1) * page_size)],
            ).fetchall()

        return [_session_brief(dict(r)) for r in rows], int(total_row["c"] or 0)

    @classmethod
    def session_detail(cls, *, student_id: int, session_id: int) -> dict[str, Any]:
        """`GET /api/sessions/{id}`（§9.5.2）：单次场次详情。

        两条安全线（契约明文，改动前先想清楚）：

        1. **越权 === 不存在**：不属于当前学生的场次返回与「不存在」相同的
           `10004`，否则可以拿它逐个 id 探测别人有没有这个场次。
        2. **`ongoing` 场次绝不下发逐题明细**：`expected` 就是答案，练习中途
           返回等于**泄题**。直接以 `10003` 拒绝，返回体里**没有** `items`。
        """
        with read_conn() as conn:
            row = conn.execute(
                """
                SELECT s.id, s.student_id, s.unit_id, u.name AS unit_name,
                       s.content_type, s.status, s.total_items, s.correct_items,
                       s.hint_count, s.score, s.started_at, s.ended_at
                  FROM sessions s
                  LEFT JOIN units u ON u.id = s.unit_id
                 WHERE s.id = ?
                """,
                (int(session_id),),
            ).fetchone()

            if row is None or int(row["student_id"]) != int(student_id):
                raise BizError(ErrorCode.NOT_FOUND, "练习场次不存在")

            session = dict(row)
            if session["status"] == "ongoing":
                raise BizError(
                    ErrorCode.FORBIDDEN,
                    "该场次还没结束，暂时看不到逐题明细",
                )

            payload = _session_brief(session)
            if session["content_type"] == "chinese":
                payload["items"] = None
                payload["recitations"] = cls._chinese_recitations(
                    conn, int(session_id)
                )
            else:
                payload["items"] = cls._english_items(conn, int(session_id))
                payload["recitations"] = None

        return payload

    @classmethod
    def _english_items(cls, conn: Any, session_id: int) -> list[dict[str, Any]]:
        """英语场次的逐项明细（**一个词条一行**，F14 的「项」口径）。

        复用 `finish_session` 用的同一份 `_item_statistics`，两处口径不会漂。

        `your_answer` / `expected` 取**最后一条判错**的记录：一项可能先答错、
        后补答对（说词对了但拼读没做），取「最后一条」会把 `exact` 当成错答原因，
        前端回显会自相矛盾；没有判错记录（纯粹步数没做完）时退回最后一条有效记录。
        """
        items, _empty = cls._item_statistics(conn, int(session_id))
        out: list[dict[str, Any]] = []
        for bucket in items.values():
            valid = bucket.get("valid") or []
            wrong_records = [r for r in valid if not r["is_correct"]]
            chosen = (wrong_records or valid)[-1] if valid else None
            out.append(
                {
                    "word_id": (
                        int(bucket["word_id"]) if int(bucket["word_id"]) > 0 else None
                    ),
                    "en": bucket.get("en"),
                    "cn": bucket.get("cn"),
                    "state": bucket.get("state"),
                    "your_answer": (
                        (chosen.get("answer_text") or chosen.get("asr_text"))
                        if chosen
                        else None
                    ),
                    "expected": chosen.get("expected") if chosen else None,
                    "match_type": chosen.get("match_type") if chosen else None,
                    "steps": sorted(int(s) for s in (bucket.get("steps") or [])),
                    "hint_level": int(chosen.get("hint_level") or 0) if chosen else 0,
                    "used_keyboard": (
                        bool(chosen.get("used_keyboard")) if chosen else False
                    ),
                }
            )
        return out

    @staticmethod
    def _chinese_recitations(conn: Any, session_id: int) -> list[dict[str, Any]]:
        """语文场次的按篇明细（**纯只读**）。

        ⚠️ **绝不调用 `sync_chinese_session()`** —— 那个方法会 UPDATE sessions，
        在 GET 里调用等于「读接口偷偷写库」。

        `missed_detail[].char` 按 `texts.content_plain[start:end]` 重建
        （start/end 取自 `recitations.scope_snapshot`）：不采信客户端上报的
        `missed_chars`，且服务端重启、内存里的背诵引擎丢了以后依然能还原（§9.5.2）。

        只取**已结算**（`finish_reason IS NOT NULL`）的背诵 —— 与
        `sync_chinese_session` 的统计口径一致，避免「汇总说 1 篇、明细列 2 篇」。
        """
        rows = conn.execute(
            """
            SELECT r.id, r.text_id, r.scope_snapshot, r.total_chars, r.matched_chars,
                   r.missed_chars, r.covered_ratio, r.score, r.grade,
                   r.duration_ms, r.restart_count, r.finish_reason, r.created_at,
                   t.title AS title, t.content_plain AS content_plain
              FROM recitations r
              LEFT JOIN texts t ON t.id = r.text_id
             WHERE r.session_id = ? AND r.finish_reason IS NOT NULL
             ORDER BY r.id
            """,
            (int(session_id),),
        ).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            snapshot = _loads_json(item.get("scope_snapshot"), {})
            if not isinstance(snapshot, dict):
                snapshot = {}
            plain = str(item.get("content_plain") or "")
            start = _as_int(snapshot.get("start"), 0)
            end = _as_int(snapshot.get("end"), len(plain))
            target = plain[start:end]

            missed: list[dict[str, Any]] = []
            for raw in _loads_json(item.get("missed_chars"), []):
                pos = _as_int(raw, -1)
                if pos < 0:
                    continue
                missed.append(
                    {"pos": pos, "char": target[pos] if pos < len(target) else None}
                )

            out.append(
                {
                    "recitation_id": int(item["id"]),
                    "text_id": (
                        int(item["text_id"]) if item["text_id"] is not None else None
                    ),
                    "title": item.get("title"),
                    "total_chars": int(item["total_chars"] or 0),
                    "matched_chars": int(item["matched_chars"] or 0),
                    "covered_ratio": round(float(item["covered_ratio"] or 0.0), 4),
                    "grade": item.get("grade"),
                    "score": (
                        round(float(item["score"]), 2)
                        if item["score"] is not None
                        else None
                    ),
                    "duration_ms": int(item["duration_ms"] or 0),
                    "restart_count": int(item["restart_count"] or 0),
                    "finish_reason": item.get("finish_reason"),
                    "created_at": item.get("created_at"),
                    "missed_detail": missed,
                }
            )
        return out


def _parse_accepted(raw: Any) -> list[str]:
    """`words.accepted` 是 JSON 数组字符串，解析失败一律当空列表。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("words.accepted 不是合法 JSON：%r", raw)
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    return []


# ---------------------------------------------------------------------------
# 读取侧（§9.5）用的小工具
# ---------------------------------------------------------------------------


def _as_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _loads_json(raw: Any, default: Any) -> Any:
    """TEXT 列里存的 JSON → Python 对象；坏了一律回 `default`。

    读侧接口不该因为一行脏数据就 500（`scope_snapshot` 坏了，顶多漏字标不出 `char`）。
    """
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _session_accuracy(correct_items: int, total_items: int) -> float:
    """场次级正确率 = `correct_items / total_items`（分母 0 → 0.0）。

    英语的 `total_items` 是**项数**（一个词条算一项，F14），语文是**字数**
    （一个字算一项）—— 同一个公式对两个学科都成立，故不按学科分叉。
    这两个计数就是 `finish_session` 落库时写进 `sessions` 的那两个，天然同源。
    """
    if total_items <= 0:
        return 0.0
    return round(correct_items / total_items, 4)


def _session_brief(row: dict[str, Any]) -> dict[str, Any]:
    """`sessions` 行 → 列表 / 详情共用的摘要字段（接口设计 §9.5.1）。"""
    total = int(row["total_items"] or 0)
    correct = int(row["correct_items"] or 0)
    ended_at = row.get("ended_at")
    return {
        "session_id": int(row["id"]),
        "content_type": row["content_type"],
        "unit_id": int(row["unit_id"]) if row.get("unit_id") is not None else None,
        "unit_name": row.get("unit_name"),
        "status": row["status"],
        "total_items": total,
        "correct_items": correct,
        "accuracy": _session_accuracy(correct, total),
        "score": round(float(row["score"]), 2) if row.get("score") is not None else None,
        "hint_count": int(row.get("hint_count") or 0),
        "started_at": row.get("started_at"),
        "ended_at": ended_at,
        # `ongoing` 场次没有 `ended_at` → 用时按 0（契约 §9.5.1）。
        "duration_ms": (
            _elapsed_ms(row.get("started_at"), ended_at) if ended_at else 0
        ),
    }
