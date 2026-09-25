"""JudgeService —— 判分引擎门面（契约《01-共享契约冻结》§7.1，**签名已冻结**）。

调用方：BE-06（WebSocket 判定）、BE-04 自身（HTTP 答题接口）、管理端申诉复核。

```python
judge = JudgeService()
verdict = judge.judge_text(answer="classroom", expected="classroom")
verdict = judge.judge_spell(asr_text="see ell aye ess ess", target="class")
```

⚠️ 三个方法都实现为 `staticmethod`：契约写的是实例方法签名（`self, ...`），
做成静态方法后 **`JudgeService().judge_text(...)` 与 `JudgeService.judge_text(...)`
两种写法都能用**，调用方不必关心有没有实例化。
"""

from __future__ import annotations

from app.services.judge import spell_judge as _spell
from app.services.judge import text_judge as _text
from app.services.judge.normalize import normalize_text as _normalize_text
from app.services.judge.normalize import strip_punctuation
from app.services.judge.spell_judge import align_diff
from app.services.judge.types import DiffItem, MatchType, Verdict

__all__ = ["JudgeService", "Verdict", "DiffItem", "MatchType"]


class JudgeService:
    """判分引擎。无状态，可安全并发调用。"""

    # ------------------------------------------------------------------ 归一化

    @staticmethod
    def normalize_text(s: str) -> str:
        """英语说词 / 英译中的归一化（需求 §6.1 全部四条）。

        暴露给 BE-06 与统计模块复用，**不要在别处再写一份**。
        """
        return _normalize_text(s)

    @staticmethod
    def strip_punctuation(s: str) -> str:
        """只去标点、保留大小写与空格（管理端搜索/展示场景用）。"""
        return strip_punctuation(s)

    # ------------------------------------------------------------------ 判定

    @staticmethod
    def judge_text(
        *,
        answer: str | None,
        expected: str,
        accepted: list[str] | None = None,
        asr_confidence: float | None = None,
    ) -> Verdict:
        """英语说词 / 英译中判定。规则见需求 §6.1（五级顺序，命中即停）。

        `answer` 为 `None` 或空 → `match_type='empty'`、`is_correct=False`
        （**不计错**，不进错题本，不更新掌握度）。
        """
        return _text.judge_text(
            answer=answer,
            expected=expected,
            accepted=accepted,
            asr_confidence=asr_confidence,
        )

    @staticmethod
    def judge_spell(
        *,
        asr_text: str | None,
        target: str,
        asr_confidence: float | None = None,
    ) -> Verdict:
        """拼读判定。谐音映射 → 去非字母 → 序列比对 + 编辑距离回溯出 `diff`。

        规则见需求 §6.2。目标长度 ≥6 且仅 1 处差异 → `fuzzy` + `can_appeal=True`。
        """
        return _spell.judge_spell(
            asr_text=asr_text,
            target=target,
            asr_confidence=asr_confidence,
        )

    # ------------------------------------------------------------------ 辅助

    @staticmethod
    def build_diff(target: str, letter_seq: str) -> list[DiffItem]:
        """只算差异对齐（管理端「回看错在哪一位」用）。"""
        return align_diff(target, letter_seq)

    @staticmethod
    def similarity(answer: str | None, expected: str) -> float:
        """归一化相似度（申诉复核页展示，只读）。"""
        return _text.similarity(answer, expected)
