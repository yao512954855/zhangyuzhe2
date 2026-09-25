"""背诵引擎 —— WP-BE-05 / 契约 §7.3。

`ReciteEngine` 是**纯内存、纯算法**的对象：只管对齐、推进、提示、结算，
**不碰数据库**（落库由 `service.py` 的 `ReciteService` 负责）。

设计约束（10b 规格「关于实时性」）：

- 一次背诵过程中 `feed` 会被高频调用（每次 ASR partial 一次），
  所以所有状态放内存，**绝不在过程中写库**。
- 进程重启会丢进行中的背诵 —— 可接受（学生重背即可）。
  结算时若引擎已不在内存，用 `transcript` 重建后再算（见 `ReciteService.settle`）。

一段典型的调用序列：

    engine = ReciteEngine(content_plain=..., content_pinyin=..., scope=[0, 1])
    engine.feed("我与父亲")            # → FeedResult(pos=4, newly_matched=[0,1,2,3])
    engine.feed("我与父亲不相见")      # 累计文本，只消费增量
    engine.check_auto_hint(8000)       # → 1（卡壳，降一档）
    engine.apply_hint(1, "auto")       # → "____"（还剩几个字）
    engine.finish("completed")         # → ReciteResult(score=..., grade=...)
"""

from __future__ import annotations

from typing import Any, Sequence

from app.core.logging import get_logger
from app.services.recite import scoring
from app.services.recite.aligner import (
    best_alignment,
    build_candidates,
    compile_target,
    match_ratio,
    to_pinyin_seq,
)
from app.services.recite.types import (
    EventType,
    FeedResult,
    FinishReason,
    HintTrigger,
    ReciteEvent,
    ReciteResult,
)

logger = get_logger(__name__)

# 与 `aligner._MAX_ACTUAL_LEN` 保持一致：超长的识别结果只取前 N 个 token
_MAX_ACTUAL_LEN = 400

# 提示档位的上限（需求 §5.7 三档）
MAX_HINT_LEVEL = 3

# 三档提示给几个字（见 `apply_hint` 的说明）
_HINT3_CONTEXT = 5


class ReciteEngine:
    """单次背诵的引擎实例（按 `recitation_id` 缓存于 `ReciteService`）。

    Args:
        content_plain: **已按背诵范围裁剪**的纯汉字序列。
        content_pinyin: 对应的空格分隔拼音序列（与上面严格等长）。
        scope: 背诵范围（段落下标），仅作记录与调用方核对，引擎不据此再裁剪。
        window: 局部对齐的滑动窗口大小（settings `asr_align_window`，默认 20）。
        noise_threshold: 噪声判定阈值（settings `asr_noise_threshold`，默认 0.3）。
            语义是**匹配率低于该值即判为噪声**，只忽略、不记错。
        silence_hint_ms: 静音多久自动提示（settings `silence_hint_ms`，默认 8000）。
        miss_streak_hint: 连续漏多少字判定卡壳（settings `miss_streak_hint`，默认 5）。
    """

    def __init__(
        self,
        *,
        content_plain: str,
        content_pinyin: str,
        scope: Sequence[int] | None = None,
        window: int = 20,
        noise_threshold: float = 0.3,
        silence_hint_ms: int = 8000,
        miss_streak_hint: int = 5,
    ) -> None:
        self.target_chars, self.target_pinyin = compile_target(content_plain, content_pinyin)
        # 对齐**不用** `target_pinyin` 本身，而是用「可接受读音集合」——
        # 否则「快乐地跑」会被读成 `kuai le di pao`，学生念标准的 `de` 反而判错。
        # 详见 `aligner.readings()`。
        self.target_candidates = build_candidates(self.target_chars, self.target_pinyin)
        self.scope = [int(i) for i in (scope or [])]

        self.window = max(1, int(window or 20))
        self.noise_threshold = float(noise_threshold or 0.0)
        self.silence_hint_ms = max(0, int(silence_hint_ms or 0))
        self.miss_streak_hint = max(0, int(miss_streak_hint or 0))

        # ---- 进行态 ----
        self.pos: int = 0
        self.miss_streak: int = 0
        self.restart_count: int = 0
        self.hint_count: dict[str, int] = {"1": 0, "2": 0, "3": 0}

        self._consumed_len: int = 0
        self._matched: set[int] = set()
        self._missed: set[int] = set()
        self._events: list[ReciteEvent] = []

        # 同一位置不重复触发同一档及更低档提示（需求 §5.7）
        self._hint_pos: int | None = None
        self._hint_ceiling: int = 0

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def total_chars(self) -> int:
        return len(self.target_chars)

    @property
    def matched_count(self) -> int:
        return len(self._matched)

    @property
    def events(self) -> list[ReciteEvent]:
        """内存中暂存的事件（结算时批量提交，F4）。"""
        return list(self._events)

    @property
    def finished(self) -> bool:
        return self.pos >= self.total_chars > 0

    def remaining(self) -> int:
        return max(0, self.total_chars - self.pos)

    # ------------------------------------------------------------------
    # 事件暂存
    # ------------------------------------------------------------------

    def _push_event(
        self,
        event_type: EventType,
        *,
        pos: int,
        char: str | None = None,
        hint_level: int | None = None,
        trigger_by: HintTrigger | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        self._events.append(
            ReciteEvent(
                seq=len(self._events) + 1,
                pos=int(pos),
                event_type=event_type,
                char=char,
                hint_level=hint_level,
                trigger_by=trigger_by,
                elapsed_ms=elapsed_ms,
            )
        )

    # ------------------------------------------------------------------
    # 主流程：喂入识别结果
    # ------------------------------------------------------------------

    def feed(self, partial_text: str) -> FeedResult:
        """喂入 ASR 的中间结果，增量对齐并推进 `pos`。

        **注意入参语义**（10b 规格第 1 条）：`partial_text` 是「到目前为止识别出的
        **全文**」，不是增量。引擎内部记 `_consumed_len`，每次只取新增片段，
        否则同一段话会被反复计数（U2 就是钉这个的）。
        """
        text = str(partial_text or "")
        # 累计文本只增不减：调用方（或结算时的补喂）传入更短的文本时直接忽略，
        # 否则会把 `_consumed_len` 改小，下一轮重复消费同一段话。
        if len(text) <= self._consumed_len:
            return FeedResult(pos=self.pos, newly_matched=[], is_noise=False)
        new_text = text[self._consumed_len :]
        self._consumed_len = len(text)

        if not new_text.strip():
            return FeedResult(pos=self.pos, newly_matched=[], is_noise=False)

        in_seq = to_pinyin_seq(new_text)[:_MAX_ACTUAL_LEN]
        if not in_seq:
            # 有文本但抽不出汉字（纯英文/数字/符号）—— 识别结果压根不是中文，
            # 按噪声处理：不推进、不记错。学生的自我纠正照旧会被后续 feed 接住。
            self._push_event("noise", pos=self.pos)
            return FeedResult(pos=self.pos, newly_matched=[], is_noise=True)

        # 已经背到末尾：后面说的都是多余内容，按噪声忽略（U5 回读的第二种形态）
        if self.total_chars == 0 or self.pos >= self.total_chars:
            self._push_event("noise", pos=self.pos)
            return FeedResult(pos=self.pos, newly_matched=[], is_noise=True)

        alignment = best_alignment(self.target_candidates, in_seq, self.pos, self.window)
        ratio = match_ratio(alignment, len(in_seq))

        if alignment is None or ratio < self.noise_threshold:
            # 噪声：不推进 pos、不记错（需求 §6.3「完全背错一段 → 视为噪声，
            # 等待学生自我纠正」）。学生重说一遍时新内容会落在 consumed_len 之后，
            # 自然会被重新处理，不会永久丢失。
            self._push_event("noise", pos=self.pos)
            return FeedResult(pos=self.pos, newly_matched=[], is_noise=True)

        newly: list[int] = []
        for index in alignment.matched:
            if index not in self._matched:
                self._matched.add(index)
                self._missed.discard(index)
                newly.append(index)
                self._push_event("matched", pos=index, char=self.target_chars[index])

        for index in alignment.missed:
            if index not in self._missed and index not in self._matched:
                self._missed.add(index)
                self._push_event("missed", pos=index, char=self.target_chars[index])

        # 连续漏字计数：只认**尾部**连续未点亮（U7 的卡壳判定输入）
        trailing = alignment.trailing_miss
        if trailing > 0:
            self.miss_streak += trailing
        else:
            self.miss_streak = 0

        # 推进到**认领边界**而不是对齐片段末尾：
        # 片段尾部那一串没人认领的 missing 是「学生还没背到」，不该算跳过（U1）。
        self.pos = max(self.pos, alignment.claimed_end)
        if self.pos > self.total_chars:
            self.pos = self.total_chars

        return FeedResult(pos=self.pos, newly_matched=newly, is_noise=False)

    def feed_batch(self, texts: Sequence[str]) -> FeedResult:
        """连续喂多段**累计文本**（App 断线补报时用），返回最后一次的结果。"""
        result = FeedResult(pos=self.pos, newly_matched=[], is_noise=False)
        for text in texts:
            result = self.feed(text)
        return result

    # ------------------------------------------------------------------
    # 提示
    # ------------------------------------------------------------------

    def peek_hint_level(self, silence_ms: int) -> int | None:
        """**纯计算**：此刻应触发几档提示（`None` = 不该触发）。**不改变任何状态**。

        与 `check_auto_hint` 分开是为了避免一个隐蔽的坑：
        `feed` 的响应里要附一条「建议给提示」的档位，若那里调 `check_auto_hint`，
        它会顺手把档位记成「已下发」（`_hint_ceiling`），
        客户端随后真的调 `hint()` 时反而拿到 `None` —— 提示永远送不出去。
        """
        silence = max(0, int(silence_ms or 0))
        level = 0

        if self.silence_hint_ms > 0:
            level = min(MAX_HINT_LEVEL, silence // self.silence_hint_ms)

        if self.miss_streak_hint > 0 and self.miss_streak > 0:
            level = max(level, min(MAX_HINT_LEVEL, self.miss_streak // self.miss_streak_hint))

        return level or None

    def check_auto_hint(self, silence_ms: int) -> int | None:
        """静音超阈值 或 连续漏字达阈值 → 返回应触发的提示档位（1..3），否则 `None`。

        **档位如何递增**：静音越久档位越高（`silence_ms // silence_hint_ms`），
        连续漏字越多档位越高（`miss_streak // miss_streak_hint`），取两者较大值，
        上限 3 档。这样同时满足两条验收：

        - U8：静音 8s→1 档、16s→2 档、24s→3 档、32s→仍 3 档（封顶）；
        - U9：同一位置反复报 8s → 只触发一次（已给过该档位，不再重复）。

        调用方（BE-06 的会话循环）负责维护静音计时并传入；引擎不自己计时。
        """
        level = self.peek_hint_level(silence_ms)
        if level is None:
            return None

        # 同一位置不重复触发同一档及更低档
        if self._hint_pos != self.pos:
            self._hint_pos = self.pos
            self._hint_ceiling = 0
        if level <= self._hint_ceiling:
            return None

        self._hint_ceiling = level
        return level

    def apply_hint(self, level: int, trigger_by: HintTrigger = "manual") -> str:
        """降一档提示，返回该档应展示的内容，并记一条 `hint` 事件。

        | 档位 | 返回 |
        |---|---|
        | 1 | `"_" × 剩余字数`（前端渲染成一排空框） |
        | 2 | 下一个字本身（中文按字粒度，「首字」就是它） |
        | 3 | 下一个字 + 后续 4 字 |

        ⚠️ **档位 3 的口径偏离**：需求 §5.7 写「三档：直接显示下一个字」，
        但在**中文单字粒度**下，档 2（给首字）与档 3（给完整字）会返回**完全相同**的内容 ——
        学生点了三档却拿到与二档一样的提示，却要背更高的扣分，属产品缺陷。
        故档 3 扩展为「给出下一个字及其后续 4 字的短语」，保证三档有真实放水梯度
        （英文粒度下不存在此问题，首字母 vs 完整单词本就不同）。
        该偏离已上报。
        """
        level = int(level)
        if level < 1 or level > MAX_HINT_LEVEL:
            raise ValueError(f"提示档位只能是 1..{MAX_HINT_LEVEL}，收到 {level}")

        key = str(level)
        self.hint_count[key] = self.hint_count.get(key, 0) + 1

        upcoming = self.target_chars[self.pos : self.pos + 1]
        self._push_event(
            "hint",
            pos=self.pos,
            char=upcoming[0] if upcoming else None,
            hint_level=level,
            trigger_by=trigger_by if trigger_by in ("manual", "auto") else "manual",
        )
        return self.hint_text(level)

    def hint_text(self, level: int) -> str:
        """纯计算：某档提示该展示什么（不计数、不写事件）。"""
        left = self.remaining()
        if left <= 0:
            return ""
        if int(level) == 1:
            return "_" * left
        if int(level) == 2:
            return self.target_chars[self.pos]
        return "".join(self.target_chars[self.pos : self.pos + _HINT3_CONTEXT])

    # ------------------------------------------------------------------
    # 重来与结算
    # ------------------------------------------------------------------

    def restart(self) -> None:
        """「重新开始本段」（需求 §5.6）：指针与已点亮记录全部归零。"""
        self.pos = 0
        self._consumed_len = 0
        self._matched.clear()
        self._missed.clear()
        self.miss_streak = 0
        self._hint_pos = None
        self._hint_ceiling = 0
        self.restart_count += 1
        self._push_event("restart", pos=0)

    def finish(
        self,
        reason: FinishReason = "manual",
        *,
        duration_ms: int = 0,
        first_voice_ms: int | None = None,
        weights: tuple[float, float, float] | None = None,
    ) -> ReciteResult:
        """结算（需求 §6.4）。

        **未背到的字全部计入漏字**（U12：背到一半结束，未背部分算漏）。
        所以 `missed_chars = 全部未点亮的字`，与 `matched_chars` 严格互补，
        `covered_ratio` 就是「亮了多少」。

        `duration_ms` / `first_voice_ms` 由调用方（BE-06 / settle 入参）提供，
        引擎只负责搬运 —— 它不掌握真实时钟。
        """
        if reason not in ("completed", "manual", "timeout"):
            raise ValueError(f"finish_reason 只支持 completed/manual/timeout，收到 {reason}")

        total = self.total_chars
        matched = len(self._matched)
        missed = sorted(i for i in range(total) if i not in self._matched)

        covered = (matched / total) if total > 0 else 0.0
        hints = scoring.hint_total(self.hint_count)
        score, _accuracy, grade = scoring.compute_score(
            covered_ratio=covered,
            missed_count=len(missed),
            total_chars=total,
            hints=hints,
            weights=weights,
        )

        return ReciteResult(
            total_chars=total,
            matched_chars=matched,
            missed_chars=missed,
            covered_ratio=round(covered, 4),
            hint_count=dict(self.hint_count),
            duration_ms=max(0, int(duration_ms or 0)),
            first_voice_ms=first_voice_ms,
            score=score,
            grade=grade,
            finish_reason=reason,
        )

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """进度快照（供 HTTP / WebSocket 回执，不落库）。"""
        return {
            "pos": self.pos,
            "total_chars": self.total_chars,
            "matched_chars": len(self._matched),
            "remaining": self.remaining(),
            "hint_count": dict(self.hint_count),
            "restart_count": self.restart_count,
            "miss_streak": self.miss_streak,
            "finished": self.finished,
        }
