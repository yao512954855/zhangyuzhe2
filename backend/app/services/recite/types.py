"""背诵引擎数据类型 —— WP-BE-05 / 契约 §7.3。

契约冻结的对外类型（**不得改名或改字段**）：
`FeedResult` / `ReciteResult`。

内部类型（`Alignment` / `ReciteEvent`）本包自用，供 `aligner` 与 `engine` 之间传递。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

# 结束方式（`recitations.finish_reason` 的 CHECK 约束，契约 §4.6）
FinishReason = Literal["completed", "manual", "timeout"]

# 提示触发方式（`recitation_events.trigger_by` 的 CHECK 约束）
HintTrigger = Literal["manual", "auto"]

# 事件类型（`recitation_events.event_type` 的 CHECK 约束）
EventType = Literal["matched", "missed", "hint", "restart", "noise"]

# 对齐逐位状态：match 点亮 / wrong 念错 / missing 漏掉 / extra 多念
AlignState = Literal["match", "wrong", "missing", "extra"]

# 结算评级（需求 §6.4）
GRADES: tuple[str, ...] = ("优秀", "良好", "合格", "待加强")


class FeedResult(BaseModel):
    """一次 `feed()` 的结果（契约 §7.3，字段名与顺序冻结）。"""

    pos: int
    newly_matched: list[int] = Field(default_factory=list)
    is_noise: bool = False


class ReciteResult(BaseModel):
    """`finish()` 的结算结果（契约 §7.3，字段名与顺序冻结）。"""

    total_chars: int
    matched_chars: int
    missed_chars: list[int] = Field(default_factory=list)
    covered_ratio: float
    hint_count: dict[str, int] = Field(default_factory=dict)
    duration_ms: int = 0
    first_voice_ms: int | None = None
    score: float = 0.0
    grade: str = "待加强"
    finish_reason: FinishReason = "manual"


@dataclass
class AlignStep:
    """序列对齐的一步。

    `ti` / `ai` 是**相对于各自序列**的下标（不是绝对位置）：
    `extra` 的 `ti` 为 `None`，`missing` 的 `ai` 为 `None`。
    """

    state: AlignState
    ti: int | None = None
    ai: int | None = None


@dataclass
class Alignment:
    """一次「窗口内局部最优对齐」的结果。

    `start` / `end` 都是**目标序列上的绝对下标**，区间为半开 `[start, end)`。
    `matched` / `missed` 也是绝对下标，可直接落 `recitation_events.pos`。

    ### `end` 与 `claimed_end` 的区别（关键）

    对齐片段会**刻意比识别文本长一截**（见 `aligner.best_alignment` 的 `allowance`），
    好让「学生跳过几个字继续往下背」能被识别出来。但代价是：
    片段尾部会挂着一串没人认领的目标位 ——
    那意思是「学生**还没背到这儿**」，而不是「学生跳过了」。

    两者必须分开，否则 U1（只念了前 4 个字）会把后面 3 个字判成漏字、指针直接冲到末尾：

    | 字段 | 含义 | 谁用它 |
    |---|---|---|
    | `end` | 对齐片段被完整消费到的位置 | 内部计算 |
    | `claimed_end` | **最后一个 `match`/`wrong` 之后**的位置 | 推进 `pos`、统计漏字、数连续漏字 |
    """

    start: int
    end: int
    matched: list[int] = field(default_factory=list)
    missed: list[int] = field(default_factory=list)
    extra: int = 0
    distance: int = 0
    steps: list[AlignStep] = field(default_factory=list)
    claimed_end: int = -1

    def __post_init__(self) -> None:
        if self.claimed_end < 0:
            self.claimed_end = self.end

    @property
    def consumed(self) -> int:
        """目标侧被消费的元素个数（= `end - start`）。"""
        return max(0, self.end - self.start)

    @property
    def claimed(self) -> int:
        """**认领**的目标元素个数（= `claimed_end - start`）。

        这就是「学生这次一口气背到了第几个字」。
        """
        return max(0, self.claimed_end - self.start)

    @property
    def trailing_miss(self) -> int:
        """**认领区间内**尾部连续未点亮的个数。

        用于「连续漏字 ≥ N 判定卡壳」（需求 §6.3）。只数尾部，
        因为中间的漏字之后学生自己接上了，不算卡壳；
        尾部未认领的 `missing` 也不算（那是「还没背到」）。
        """
        count = 0
        for step in reversed(self.steps):
            if step.state == "extra":
                continue
            index = self.start + int(step.ti)
            if index >= self.claimed_end:
                continue
            if step.state in ("missing", "wrong"):
                count += 1
            else:
                break
        return count


@dataclass
class ReciteEvent:
    """待落库的一条 `recitation_events`（内存暂存，结算时批量提交）。"""

    seq: int
    pos: int
    event_type: EventType
    char: str | None = None
    hint_level: int | None = None
    trigger_by: HintTrigger | None = None
    elapsed_ms: int | None = None

    def to_row(self) -> dict[str, object]:
        return {
            "seq": self.seq,
            "pos": self.pos,
            "char": self.char,
            "event_type": self.event_type,
            "hint_level": self.hint_level,
            "trigger_by": self.trigger_by,
            "elapsed_ms": self.elapsed_ms,
        }
