"""英语模式一「双口径」判定 —— 二期口径（2026-09-29 姚工拍板）。

学生**念整词**（`apple`）或**逐字母拼**（`A-P-P-L-E` / `see ell aye ess ess`）
都算对：先按整词文本口径判（需求 §6.1 五级顺序），**整词口径判 `wrong` 时**
再回退到拼读字母序列口径（需求 §6.2 三级）。两条口径都判错 → 保留整词口径的
判决（`expected` 仍是词条的 `en`，与旧行为逐字一致）。

为什么要有这个文件：口径只能有一份实现。REST（`SessionService.submit_answer`）
与 WS（`ASRSessionManager._judge_and_emit`）两条链路都要用，写在任一处都会
逼另一处复制一遍 —— 违反契约「客户端/各模块不得各自实现判分逻辑」的同一条原则。

⚠️ **忠实度**（需求 §6.2「判分模型必须忠实」，与 ASR 选型同一条红线）：

- 回退**只在整词口径判 `wrong` 时**发生 —— `empty`（没出声）与 `timeout` 不回退，
  否则会把「没答」变成「靠谐音表碰对」。
- 拼读口径本身是**严格的字母序列比对 + 谐音映射**，不做模糊猜测
  （`homophone.py` 的原则：宁可漏，不可把错读判对）。
- 目标不是英文（归一化后无 `a-z`）时**直接不回退** —— 拼读对中文毫无意义。
- 采纳前提是**拼读口径判对**（`exact` / `fuzzy`）；拼读也判错就退回整词判决。

回退**不额外扣分**：一个单词只有一条 `answers` 记录、一条判分（结算题量因此
从 2 条/词降到 1 条/词，是**预期行为**，不是丢数据）。
"""

from __future__ import annotations

from app.services.judge.normalize import normalize_letters
from app.services.judge.service import JudgeService
from app.services.judge.types import Verdict

__all__ = ["judge_word_or_spell"]


def judge_word_or_spell(
    *,
    answer: str | None,
    expected: str,
    accepted: list[str] | None = None,
    asr_confidence: float | None = None,
) -> tuple[Verdict, bool]:
    """英语说词的双口径判定（整词优先，字母序列回退）。

    Args:
        answer: 学生答案的识别文本（也用于键盘输入）。
        expected: 词条的 `en`（模式一的 target 永远是英文词）。
        accepted: 词条的「可接受答案」（只作用于整词口径）。
        asr_confidence: 厂商置信度，仅透传留存。

    Returns:
        `(verdict, used_spell)` —— `used_spell=True` 表示**最终采纳的是拼读口径**，
        调用方据此决定是否回填 `answers.letter_seq` / `target_seq`（只有真的是
        走了字母序列才有这两个值，念整词时它们应当为空）。
    """
    text_verdict = JudgeService.judge_text(
        answer=answer,
        expected=expected,
        accepted=accepted,
        asr_confidence=asr_confidence,
    )

    # 整词口径已命中（exact / loose / fuzzy）或识别为空 / 目标本身不可判 → 不回退
    if text_verdict.match_type != "wrong":
        return text_verdict, False
    if not normalize_letters(expected):
        return text_verdict, False

    spell_verdict = JudgeService.judge_spell(
        asr_text=answer,
        target=expected,
        asr_confidence=asr_confidence,
    )
    if spell_verdict.is_correct:
        return spell_verdict, True
    return text_verdict, False
