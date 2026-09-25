"""判分引擎的对外数据结构 —— 契约《01-共享契约冻结》§7.1（**签名已冻结**）。

⚠️ 这两个模型的字段名、字段类型、字面量取值**不得改动** ——
BE-06（WebSocket 判定）、APP-02/APP-03 都按此消费。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# 契约 §8 / §8.1 冻结的六值，禁止增删
#   exact  正确（完全一致或命中可接受答案）
#   loose  正确（包含匹配）
#   fuzzy  正确（相似度达标 / 拼读差 1 位），允许申诉
#   wrong  错误
#   timeout 错误（限时到点，行为 A）
#   empty  不计错，也不计入正确率分母
MatchType = Literal["exact", "loose", "fuzzy", "wrong", "timeout", "empty"]

# 逐位对齐的四种状态（契约 §6.4），驱动学生端逐字母回显
DiffState = Literal["match", "wrong", "missing", "extra"]


class DiffItem(BaseModel):
    """逐位对齐结果（契约 §7.1 冻结）。

    Attributes:
        pos: 位次，从 0 起。
        expected: 该位应有的字母；`extra` 位为 None。
        actual: 学生实际念出的字母；`missing` 位为 None。
        state: `match` / `wrong` / `missing` / `extra`。
    """

    pos: int
    expected: str | None
    actual: str | None
    state: DiffState


class Verdict(BaseModel):
    """一次判定的结果（契约 §7.1 冻结）。"""

    is_correct: bool
    match_type: MatchType
    expected: str
    normalized: str | None = None
    asr_text: str | None = None
    can_appeal: bool = False
    asr_confidence: float | None = None
    diff: list[DiffItem] | None = None

    @property
    def counts_as_error(self) -> bool:
        """是否计入错题本 / `word_mastery`（契约 §8.1）。

        `empty` 单独统计，**不进错题本、不更新掌握度**。
        """
        return self.match_type != "empty"
