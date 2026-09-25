"""文本判定 —— WP-BE-04 / 需求 §6.1（英语说词、英译中）。

五级顺序，**命中即停**：

| 序 | 规则 | 结果 |
|---|---|---|
| 1 | 完全一致（归一化后） | `exact` |
| 2 | 命中词条的「可接受答案」 | `exact` |
| 3 | **包含**某个候选答案，且长度不超过其 **2 倍** | `loose`（可申诉） |
| 4 | 相似度 ≥ **0.85** 且目标长度 ≥ **5** | `fuzzy`（可申诉） |
| 5 | 其余 | `wrong` |

两条容易写错的规则：

- **同音词不自动等同**（需求 §6.1 明文）：`there` / `their` 判 `wrong`，
  只有词条显式配了 `accepted` 才算对。同音词正是英语学习的重点，不能放过。
- **短词不走模糊**：`SequenceMatcher` 在 ≤4 字符上区分度极差
  （`cat` vs `car` 相似度 0.67 尚可，但 3 字母里错 1 位就接近 0.8），
  需求把门槛定在长度 ≥5，不要自作主张放宽。
"""

from __future__ import annotations

from difflib import SequenceMatcher

from app.services.judge.normalize import is_empty_answer, normalize_text
from app.services.judge.types import Verdict

# 需求 §6.1 第 4 条的门限，**不要随意调**（阈值最终值待 ASR 第三批真人录音后回写）
FUZZY_RATIO_THRESHOLD = 0.85
FUZZY_MIN_TARGET_LEN = 5
# 需求 §6.1 第 3 条：学生答案长度不得超过候选答案的 2 倍
LOOSE_MAX_LENGTH_RATIO = 2


def _similarity(left: str, right: str) -> float:
    """归一化后的相似度（`difflib.SequenceMatcher.ratio()`，需求 §6.1 指定）。"""
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def judge_text(
    *,
    answer: str | None,
    expected: str,
    accepted: list[str] | None = None,
    asr_confidence: float | None = None,
) -> Verdict:
    """英语说词 / 英译中判定（契约 §7.1 冻结签名）。

    Args:
        answer: 学生答案的识别文本（`None` 或空 → `empty`，**不计错**）。
        expected: 标准答案（词条的 `en` 或 `cn`）。
        accepted: 可接受答案列表（词条的 `accepted`）。
        asr_confidence: 厂商返回的识别置信度，仅透传留存。

    Returns:
        `Verdict`；`normalized` 为归一化后的学生答案，`expected` 原样回填。
    """
    # F3 / 契约 §7.1：识别为空绝不能算错
    if is_empty_answer(answer):
        return Verdict(
            is_correct=False,
            match_type="empty",
            expected=expected,
            normalized=None,
            asr_text=answer or None,
            can_appeal=False,
            asr_confidence=asr_confidence,
        )

    normalized = normalize_text(answer)
    target = normalize_text(expected)

    base = {
        "expected": expected,
        "normalized": normalized,
        "asr_text": answer,
        "asr_confidence": asr_confidence,
    }

    # 防御：目标答案本身归一化后为空（配置错误），任何答案都不可能判对
    if not target:
        return Verdict(is_correct=False, match_type="wrong", **base)

    # ---- 1. 完全一致 ----
    if normalized == target:
        return Verdict(is_correct=True, match_type="exact", **base)

    # ---- 2. 命中可接受答案 ----
    candidates: list[str] = [target]
    for item in accepted or []:
        norm_item = normalize_text(item)
        if norm_item:
            candidates.append(norm_item)

    for candidate in candidates[1:]:
        if normalized == candidate:
            return Verdict(is_correct=True, match_type="exact", can_appeal=False, **base)

    # ---- 3. 包含匹配（长度 ≤ 2 倍） ----
    for candidate in candidates:
        if candidate and candidate in normalized:
            if len(normalized) <= LOOSE_MAX_LENGTH_RATIO * len(candidate):
                return Verdict(is_correct=True, match_type="loose", can_appeal=True, **base)

    # ---- 4. 相似度达标（且目标足够长） ----
    if len(target) >= FUZZY_MIN_TARGET_LEN:
        if _similarity(normalized, target) >= FUZZY_RATIO_THRESHOLD:
            return Verdict(is_correct=True, match_type="fuzzy", can_appeal=True, **base)

    # ---- 5. 其余 ----
    return Verdict(is_correct=False, match_type="wrong", **base)


def similarity(answer: str | None, expected: str) -> float:
    """归一化后的相似度，供管理端申诉复核页展示（只读辅助）。"""
    return round(_similarity(normalize_text(answer), normalize_text(expected)), 4)
