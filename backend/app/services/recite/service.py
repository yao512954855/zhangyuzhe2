"""背诵业务服务 —— WP-BE-05 F1 ~ F5（接口设计 §9.4）。

职责分工：`ReciteEngine` 只管算法，本模块管**编排、鉴权、落库、防刷分**。

### 三条关键设计

**① 引擎在内存里按 `recitation_id` 缓存。**
一次背诵过程 `feed` 会被高频调用，全都不能落库（10b 规格「关于实时性」）。
进程重启丢进行中的背诵是可接受的（学生重背即可）；
`settle` 时若引擎已不在内存，则用 `transcript` **重建后重跑**，结算照样出得来。

**② `settle` 一律服务端重算，绝不采信客户端上报的 `matched_chars`（F5）。**
客户端只被允许补报 `transcript`（断线重连场景），
`matched_chars` / `missed_chars` 两个字段**只收不用** —— 收到即丢弃，防刷分。

**③ 事件在结算时一次性批量写库（F4），不在流式过程中逐条写。**
`recitation_events` 一期**只写不读**（二期做回放），
所以它不该成为背诵过程中的写放大源。
"""

from __future__ import annotations

import json
import threading
from typing import Any, Sequence

from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode
from app.core.logging import get_logger
from app.services.content_service import ContentService, _parse_scope
from app.services.recite.aligner import split_pinyin
from app.services.recite.engine import ReciteEngine
from app.services.settings_service import SettingsService

logger = get_logger(__name__)


def _as_float(raw: Any, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _as_int(raw: Any, default: int) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _loads(raw: Any, default: Any) -> Any:
    if not raw:
        return default
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return default
    return value if isinstance(value, type(default)) else default


class ReciteService:
    """语文背诵流程的业务服务（F1 ~ F5）。"""

    # recitation_id -> {"student_id": int, "engine": ReciteEngine}
    _engines: dict[int, dict[str, Any]] = {}
    # recitation_id -> 客户端补报的事件（App 断线期间产生的）
    _extra_events: dict[int, list[dict[str, Any]]] = {}
    _lock = threading.RLock()

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    @classmethod
    def _engine_kwargs(cls) -> dict[str, Any]:
        """从 `settings` 取引擎参数（禁止硬编码，10a 共同约定第 4 条）。"""
        return {
            "window": SettingsService.get_int("asr_align_window", 20),
            "noise_threshold": _as_float(
                SettingsService.get_str("asr_noise_threshold", "0.3"), 0.3
            ),
            "silence_hint_ms": SettingsService.get_int("silence_hint_ms", 8000),
            "miss_streak_hint": SettingsService.get_int("miss_streak_hint", 5),
        }

    @classmethod
    def _weights(cls) -> tuple[float, float, float]:
        return (
            _as_float(SettingsService.get_str("recite_weight_cover", "0.6"), 0.6),
            _as_float(SettingsService.get_str("recite_weight_accuracy", "0.25"), 0.25),
            _as_float(SettingsService.get_str("recite_weight_hint", "0.15"), 0.15),
        )

    # ------------------------------------------------------------------
    # 引擎缓存
    # ------------------------------------------------------------------

    @classmethod
    def _cache_engine(cls, recitation_id: int, student_id: int, engine: ReciteEngine) -> None:
        with cls._lock:
            cls._engines[int(recitation_id)] = {
                "student_id": int(student_id),
                "engine": engine,
            }

    @classmethod
    def _peek_engine(cls, recitation_id: int, student_id: int) -> ReciteEngine | None:
        with cls._lock:
            entry = cls._engines.get(int(recitation_id))
        if entry is None:
            return None
        # 越权访问按「不存在」处理，不泄露他人背诵的存在性（与 §8.9 申诉同口径）
        if int(entry["student_id"]) != int(student_id):
            raise BizError(ErrorCode.NOT_FOUND, "背诵记录不存在")
        return entry["engine"]

    @classmethod
    def _drop_engine(cls, recitation_id: int) -> None:
        with cls._lock:
            cls._engines.pop(int(recitation_id), None)

    @classmethod
    def clear_cache(cls) -> None:
        """清空内存缓存（测试与优雅退出用）。"""
        with cls._lock:
            cls._engines.clear()
            cls._extra_events.clear()

    # ------------------------------------------------------------------
    # F1：创建一次背诵
    # ------------------------------------------------------------------

    @classmethod
    def _read_recitation(cls, recitation_id: int) -> dict[str, Any]:
        with read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM recitations WHERE id = ?", (int(recitation_id),)
            ).fetchone()
        if row is None:
            raise BizError(ErrorCode.NOT_FOUND, "背诵记录不存在")
        return dict(row)

    @classmethod
    def create_recitation(
        cls,
        *,
        student_id: int,
        session_id: int,
        text_id: int,
        scope: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """`POST /api/recitations`（§9.4）。

        响应**不含拼音、不含正文**（F1 明文）：正文学生已在 §8.7 拿过，
        拼音是服务端内部实现，不外泄。

        `scope` 为空 = 全文背诵；`scope` 必须是课文里真实存在的段落下标。
        """
        with read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (int(session_id),)
            ).fetchone()
        if row is None or int(row["student_id"]) != int(student_id):
            raise BizError(ErrorCode.NOT_FOUND, "练习场次不存在")
        session = dict(row)
        if str(session["status"]) != "ongoing":
            raise BizError(ErrorCode.PARAM_INVALID, "该场次已结束，不能开始新的背诵")
        if str(session["content_type"]) != "chinese":
            raise BizError(ErrorCode.PARAM_INVALID, "该场次不是语文背诵场次")

        text = ContentService.get_text_row(text_id)
        if str(text.get("status") or "active") != "active":
            raise BizError(ErrorCode.CONTENT_INACTIVE, "该课文已停用")

        paragraphs = ContentService.parse_paragraphs(text.get("paragraphs"))
        if not paragraphs:
            raise BizError(ErrorCode.CONTENT_NOT_FOUND, "该课文没有可背诵的段落")

        # ⚠️ `texts.content_plain` **只含 `recite_scope` 范围内的字**（导入期由
        # `pinyin_service.build_scoped` 生成，见 BE-03）。所以段落下标到字符偏移的
        # 映射必须**只累计背诵范围内的段落**，不能按全部段落累加 ——
        # 否则「背诵范围不含第 0 段」时切片会整体错位
        # （例：3 段 chars=[5,7,3]、recite_scope=[1] 时 content_plain 只有 7 个字，
        # 按全段累加会切出 [5:12]，直接少掉前 5 个字）。
        recite_scope = _parse_scope(text.get("recite_scope"))
        if not recite_scope:
            recite_scope = [int(p["idx"]) for p in paragraphs]

        span: dict[int, tuple[int, int]] = {}
        offset = 0
        for para in paragraphs:
            index = int(para["idx"])
            chars = int(para.get("chars") or 0)
            if index in set(recite_scope):
                span[index] = (offset, offset + chars)
                offset += chars

        picked = sorted({int(i) for i in (scope or [])}) or sorted(recite_scope)
        outside = [i for i in picked if i not in span]
        if outside:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"背诵范围包含本篇未标记背诵的段落：{outside}",
            )

        start = min(span[i][0] for i in picked)
        end = max(span[i][1] for i in picked)
        if end <= start:
            raise BizError(ErrorCode.CONTENT_NOT_FOUND, "背诵范围内没有内容")

        plain = str(text.get("content_plain") or "")[start:end]
        target_tokens = split_pinyin(str(text.get("content_pinyin") or ""))
        pinyin = " ".join(target_tokens[start:end])
        char_count_per_paragraph = [span[i][1] - span[i][0] for i in picked]

        kwargs = cls._engine_kwargs()
        engine = ReciteEngine(
            content_plain=plain,
            content_pinyin=pinyin,
            scope=picked,
            **kwargs,
        )
        if engine.total_chars <= 0:
            raise BizError(ErrorCode.CONTENT_NOT_FOUND, "背诵范围内没有可对齐的内容")

        scope_payload = json.dumps(
            {"scope": picked, "start": start, "end": end}, ensure_ascii=False
        )
        with write_tx() as conn:
            cursor = conn.execute(
                """
                INSERT INTO recitations
                    (session_id, student_id, text_id, scope_snapshot, total_chars,
                     matched_chars, covered_ratio, restart_count, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, ?)
                """,
                (
                    int(session_id),
                    int(student_id),
                    int(text_id),
                    scope_payload,
                    engine.total_chars,
                    utc_now_iso(),
                ),
            )
            recitation_id = int(cursor.lastrowid)

        cls._cache_engine(recitation_id, student_id, engine)

        return {
            "recitation_id": recitation_id,
            "session_id": int(session_id),
            "text_id": int(text_id),
            "title": text.get("title"),
            "scope": picked,
            "total_chars": engine.total_chars,
            "char_count_per_paragraph": char_count_per_paragraph,
        }

    # ------------------------------------------------------------------
    # 实时：喂入识别结果 / 提示
    # ------------------------------------------------------------------

    @classmethod
    def feed(
        cls,
        *,
        student_id: int,
        recitation_id: int,
        partial_text: str,
        silence_ms: int = 0,
    ) -> dict[str, Any]:
        """喂入一段 ASR **累计文本**，返回推进后的进度。

        `silence_ms` 只是透传信息，用于在响应里附一条「建议给提示」的档位，
        **不消耗档位**（真正的提示要走 `hint()`，它才会记 `hint_count`）。
        """
        engine = cls._peek_engine(recitation_id, student_id)
        if engine is None:
            raise BizError(ErrorCode.NOT_FOUND, "背诵已过期，请重新开始")

        result = engine.feed(partial_text)
        # 用 `peek_hint_level`（无副作用）而不是 `check_auto_hint`：
        # 后者会把档位记成「已下发」，客户端随后真的调 `hint()` 就再也拿不到了。
        # ⚠️ 必须无条件调用（2026-10-01 修「卡壳死锁」）：原先 `silence_ms <= 0`
        # 直接给 None，而客户端 partial 到达即重置静音计时、恒传 0 ——
        # 「学生念叨但背不对」时 silence 分支与 miss/noise 分支全被这一行挡死。
        # silence_ms=0 时静音分支自然为 0 档，miss/noise 分支照常出建议。
        suggestion = engine.peek_hint_level(silence_ms)

        data = dict(engine.snapshot())
        data.update(
            {
                "recitation_id": int(recitation_id),
                "newly_matched": result.newly_matched,
                "is_noise": result.is_noise,
                "hint_suggestion": suggestion,
            }
        )
        return data

    @classmethod
    def hint(
        cls,
        *,
        student_id: int,
        recitation_id: int,
        level: int | None = None,
        trigger_by: str = "auto",
        silence_ms: int = 0,
    ) -> dict[str, Any]:
        """给一次提示。

        - `level` 显式给出（1..3）= 学生主动点「我卡住了」；
        - `level` 为 `None` = 服务端按静音/漏字自动判定，判定不出则返回 `level: null`。
        """
        engine = cls._peek_engine(recitation_id, student_id)
        if engine is None:
            raise BizError(ErrorCode.NOT_FOUND, "背诵已过期，请重新开始")

        if level is None:
            decided = engine.check_auto_hint(silence_ms)
            if decided is None:
                return {
                    "recitation_id": int(recitation_id),
                    "level": None,
                    "content": None,
                    "hint_count": dict(engine.hint_count),
                }
            level = decided

        content = engine.apply_hint(int(level), "manual" if trigger_by == "manual" else "auto")
        return {
            "recitation_id": int(recitation_id),
            "level": int(level),
            "content": content,
            "pos": engine.pos,
            "hint_count": dict(engine.hint_count),
        }

    @classmethod
    def restart(cls, *, student_id: int, recitation_id: int) -> dict[str, Any]:
        """「重新开始本段」（需求 §5.6）。"""
        engine = cls._peek_engine(recitation_id, student_id)
        if engine is None:
            raise BizError(ErrorCode.NOT_FOUND, "背诵已过期，请重新开始")
        engine.restart()
        return dict(engine.snapshot(), recitation_id=int(recitation_id))

    # ------------------------------------------------------------------
    # F2：事件补报
    # ------------------------------------------------------------------

    @classmethod
    def report_events(
        cls,
        *,
        student_id: int,
        recitation_id: int,
        events: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        """接收 App 断线期间攒下的事件流（F2）。

        **一期只写不读**，且服务端**不据此计算任何指标** —— 对齐与评分一律以服务端为准。
        这些事件在 `settle` 时接在服务端事件之后落库，只为「二期回放」保留素材。
        """
        row = cls._read_recitation(recitation_id)
        if int(row["student_id"]) != int(student_id):
            raise BizError(ErrorCode.NOT_FOUND, "背诵记录不存在")

        accepted = 0
        with cls._lock:
            bucket = cls._extra_events.setdefault(int(recitation_id), [])
            for event in events or []:
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("event_type") or "")
                if event_type not in ("matched", "missed", "hint", "restart", "noise"):
                    continue
                bucket.append(
                    {
                        "pos": _as_int(event.get("pos"), 0),
                        "char": event.get("char"),
                        "event_type": event_type,
                        "hint_level": (
                            _as_int(event.get("hint_level"), 0) or None
                            if event.get("hint_level") is not None
                            else None
                        ),
                        "trigger_by": event.get("trigger_by"),
                        "elapsed_ms": (
                            _as_int(event.get("elapsed_ms"), 0)
                            if event.get("elapsed_ms") is not None
                            else None
                        ),
                    }
                )
                accepted += 1

        return {
            "recitation_id": int(recitation_id),
            "accepted": accepted,
            "authoritative": "server",
        }

    # ------------------------------------------------------------------
    # F3/F5：结算
    # ------------------------------------------------------------------

    @classmethod
    def _rebuild_engine(
        cls, row: dict[str, Any], transcript: str
    ) -> ReciteEngine:
        """进程重启后按 `scope_snapshot` 重建引擎并重放 `transcript`。"""
        snapshot = _loads(row.get("scope_snapshot"), {"scope": [], "start": 0, "end": 0})
        text_id = row.get("text_id")
        if text_id is None:
            raise BizError(ErrorCode.CONTENT_NOT_FOUND, "课文已被删除，无法结算")

        text = ContentService.get_text_row(int(text_id))
        start = _as_int(snapshot.get("start"), 0)
        end = _as_int(snapshot.get("end"), 0)
        plain = str(text.get("content_plain") or "")[start:end]
        tokens = split_pinyin(str(text.get("content_pinyin") or ""))
        pinyin = " ".join(tokens[start:end])

        engine = ReciteEngine(
            content_plain=plain,
            content_pinyin=pinyin,
            scope=snapshot.get("scope") or [],
            **cls._engine_kwargs(),
        )
        if transcript:
            engine.feed(transcript)
        return engine

    @classmethod
    def settle(
        cls,
        *,
        student_id: int,
        recitation_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """`POST /api/recitations/{id}/settle`（§9.4）。

        **服务端重算**（F3/F5）：

        1. 引擎在内存 → 用实时状态；再补喂客户端传来的 `transcript`（断线补报）。
        2. 引擎不在内存（进程重启）→ 按 `scope_snapshot` 重建后重放 `transcript`。
        3. 客户端上报的 `matched_chars` / `missed_chars` **只收不用**。

        幂等：已结算过的背诵再次提交，直接返回原结果，不重复写库。
        """
        row = cls._read_recitation(recitation_id)
        if int(row["student_id"]) != int(student_id):
            raise BizError(ErrorCode.NOT_FOUND, "背诵记录不存在")

        reason = str(payload.get("finish_reason") or "manual")
        if reason not in ("completed", "manual", "timeout"):
            raise BizError(ErrorCode.PARAM_INVALID, f"finish_reason 不支持：{reason}")

        transcript = str(payload.get("transcript") or "")

        # ---- 已结算：幂等回放 ----
        if row.get("finish_reason") is not None:
            engine = cls._peek_engine(recitation_id, student_id)
            chars = _loads(row.get("missed_chars"), [])
            return cls._settle_payload(
                recitation_id=recitation_id,
                total_chars=int(row.get("total_chars") or 0),
                matched_chars=int(row.get("matched_chars") or 0),
                covered_ratio=float(row.get("covered_ratio") or 0.0),
                score=float(row.get("score") or 0.0),
                grade=str(row.get("grade") or ""),
                hint_count=_loads(row.get("hint_count"), {}),
                missed_chars=chars,
                target_chars=engine.target_chars if engine else None,
                replay=True,
                # 幂等重放：从库里读回当时的值，保证反复结算显示一致。
                duration_ms=_as_int(row.get("duration_ms"), 0),
                first_voice_ms=(
                    _as_int(row.get("first_voice_ms"), 0)
                    if row.get("first_voice_ms") is not None
                    else None
                ),
            )

        engine = cls._peek_engine(recitation_id, student_id)
        if engine is None:
            logger.info("背诵 #%s 引擎已不在内存（进程重启？），按 transcript 重建", recitation_id)
            engine = cls._rebuild_engine(row, transcript)
        elif transcript:
            # 补喂：`feed` 内部按累计语义只消费增量，短文本会被自动忽略
            engine.feed(transcript)

        if int(payload.get("restart_count") or 0) > engine.restart_count:
            # 客户端记的重开次数更多（例如服务端重启丢了实时状态）→ 取较大值
            engine.restart_count = int(payload["restart_count"])

        result = engine.finish(
            reason,
            duration_ms=_as_int(payload.get("duration_ms"), 0),
            first_voice_ms=(
                _as_int(payload.get("first_voice_ms"), 0)
                if payload.get("first_voice_ms") is not None
                else None
            ),
            weights=cls._weights(),
        )

        cls._persist(row, result, transcript)
        cls._drop_engine(recitation_id)

        return cls._settle_payload(
            recitation_id=recitation_id,
            total_chars=result.total_chars,
            matched_chars=result.matched_chars,
            covered_ratio=result.covered_ratio,
            score=float(result.score),
            grade=result.grade,
            hint_count=result.hint_count,
            missed_chars=result.missed_chars,
            target_chars=engine.target_chars,
            replay=False,
            duration_ms=int(result.duration_ms),
            first_voice_ms=result.first_voice_ms,
        )

    @classmethod
    def _settle_payload(
        cls,
        *,
        recitation_id: int,
        total_chars: int,
        matched_chars: int,
        covered_ratio: float,
        score: float,
        grade: str,
        hint_count: dict[str, Any],
        missed_chars: list[Any],
        target_chars: list[str] | None,
        replay: bool,
        duration_ms: int,
        first_voice_ms: int | None,
    ) -> dict[str, Any]:
        detail: list[dict[str, Any]] = []
        for raw in missed_chars or []:
            index = _as_int(raw, -1)
            if index < 0:
                continue
            char = target_chars[index] if target_chars and index < len(target_chars) else None
            detail.append({"pos": index, "char": char})

        return {
            "recitation_id": int(recitation_id),
            "total_chars": int(total_chars),
            "matched_chars": int(matched_chars),
            "missed_count": len(detail),
            "missed_detail": detail,
            "covered_ratio": round(float(covered_ratio), 4),
            "score": round(float(score), 2),
            "grade": grade,
            "hint_count": {str(k): _as_int(v, 0) for k, v in (hint_count or {}).items()},
            # 契约 §9.4：把**已落库**的用时 / 首声延迟回带，作为结算页的唯一口径。
            # 客户端本地计时会在切后台 / 杀进程时失真，且与学生报告、管理端统计不同源。
            "duration_ms": int(duration_ms),
            "first_voice_ms": None if first_voice_ms is None else int(first_voice_ms),
            "replay": bool(replay),
        }

    @classmethod
    def _persist(cls, row: dict[str, Any], result: Any, transcript: str) -> None:
        """写 `recitations` + 批量写 `recitation_events`（同一个写事务）。"""
        recitation_id = int(row["id"])
        engine = cls._peek_engine(recitation_id, int(row["student_id"]))
        server_events = engine.events if engine else []
        with cls._lock:
            extra = cls._extra_events.pop(recitation_id, [])

        with write_tx() as conn:
            conn.execute(
                """
                UPDATE recitations
                   SET transcript = ?, total_chars = ?, matched_chars = ?, missed_chars = ?,
                       hint_count = ?, restart_count = ?, duration_ms = ?, first_voice_ms = ?,
                       covered_ratio = ?, score = ?, grade = ?, finish_reason = ?
                 WHERE id = ?
                """,
                (
                    transcript,
                    int(result.total_chars),
                    int(result.matched_chars),
                    json.dumps(result.missed_chars, ensure_ascii=False),
                    json.dumps(result.hint_count, ensure_ascii=False),
                    int(getattr(engine, "restart_count", 0) if engine else 0),
                    int(result.duration_ms),
                    result.first_voice_ms,
                    float(result.covered_ratio),
                    float(result.score),
                    result.grade,
                    result.finish_reason,
                    recitation_id,
                ),
            )

            rows = [event.to_row() for event in server_events]
            for offset, event in enumerate(extra, start=len(rows) + 1):
                rows.append(
                    {
                        "seq": offset,
                        "pos": event.get("pos", 0),
                        "char": event.get("char"),
                        "event_type": event["event_type"],
                        "hint_level": event.get("hint_level"),
                        "trigger_by": event.get("trigger_by"),
                        "elapsed_ms": event.get("elapsed_ms"),
                    }
                )

            if rows:
                conn.executemany(
                    """
                    INSERT INTO recitation_events
                        (recitation_id, seq, pos, char, event_type,
                         hint_level, trigger_by, elapsed_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            recitation_id,
                            int(r["seq"]),
                            int(r["pos"]),
                            r["char"],
                            r["event_type"],
                            r["hint_level"],
                            r["trigger_by"],
                            r["elapsed_ms"],
                        )
                        for r in rows
                    ],
                )

            cls._sync_session(conn, row, result)

    @classmethod
    def _sync_session(cls, conn: Any, row: dict[str, Any], result: Any) -> None:
        """把背诵结果同步到 `sessions`（**契约未定义，实现补充**）。

        语文场次的统计靠 `answers` 是算不出来的（语文不写 `answers`），
        所以这里按 `recitations` 汇总，与 BE-04 的英语口径区分开。

        ⚠️ **不置 `status='finished'`**：一节课（一个场次）里学生往往要连着背好几篇，
        背完第一篇就关场次的话，第二篇直接开不出来（`create_recitation` 会拒「场次已结束」）。
        场次结束由学生显式调 `POST /api/sessions/{id}/finish`。

        汇总用**全量重算**而不是增量累加，所以重复结算/重复背诵都不会把数算重。
        """
        session_id = row.get("session_id")
        if session_id is None:
            return
        from app.services.session_service import SessionService

        SessionService.sync_chinese_session(conn, int(session_id))
