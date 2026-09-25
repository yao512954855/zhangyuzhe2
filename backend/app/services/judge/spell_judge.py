"""拼读判定 —— WP-BE-04 / 需求 §6.2（英语拼字母）。

流程：**谐音映射 → 去非字母 → 序列比对 + 编辑距离回溯 → `diff[]`**

判定三级（需求 §6.2，命中即停）：

| 序 | 规则 | 结果 |
|---|---|---|
| 1 | 字母序列与目标完全一致 | `exact` |
| 2 | 目标长度 ≥ **6** 且仅 **1 处差异** | `fuzzy`（可申诉） |
| 3 | 其余 | `wrong` |

> 门槛是 6 而不是 5（文本判定是 5）：拼读串更短、字母更易混，
> 短串上「差 1 位」不足以说明学生真会拼。

`diff[]` 是**硬需求**（需求 §5.4）：不论对错都必须产出，学生要知道错在哪一位。

对齐算法的两个坑：

1. **长度相等时直接逐位比**。若走 DP，`class` / `cless` 也可能被解释成
   「少 a 多 e」，产出 `missing` + `extra` 两个条目，而学生端要的是
   「第 3 位听成 e，应为 a」这一条 `wrong`。
2. **长度不等时回溯偏好 `extra` > `missing` > 对角**。`classs` vs `class`
   两种路径编辑距离都是 1，但「末位多念了 s」才是学生能理解的说法；
   若让「对角优先」胜出，会得出「第 2 位多念了 l」，与学生的实际行为不符。
"""

from __future__ import annotations

from app.services.judge.homophone import get_map
from app.services.judge.normalize import is_empty_answer, normalize_letters
from app.services.judge.types import DiffItem, Verdict

# 需求 §6.2：目标长度 ≥ 6 且仅 1 处差异才算 fuzzy
SPELL_FUZZY_MIN_TARGET_LEN = 6
SPELL_FUZZY_MAX_DISTANCE = 1


# ---------------------------------------------------------------------------
# 编辑距离与对齐
# ---------------------------------------------------------------------------


def edit_distance(target: str, actual: str) -> int:
    """标准 Levenshtein 距离。"""
    if target == actual:
        return 0
    if not target:
        return len(actual)
    if not actual:
        return len(target)

    previous = list(range(len(actual) + 1))
    for i, tc in enumerate(target, start=1):
        current = [i]
        for j, ac in enumerate(actual, start=1):
            cost = 0 if tc == ac else 1
            current.append(
                min(
                    previous[j] + 1,        # 删除 target 字符（missing）
                    current[j - 1] + 1,     # 插入 actual 字符（extra）
                    previous[j - 1] + cost,  # 替换 / 相同
                )
            )
        previous = current
    return previous[-1]


def _dp_table(target: str, actual: str) -> list[list[int]]:
    rows = len(target) + 1
    cols = len(actual) + 1
    table = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        table[i][0] = i
    for j in range(cols):
        table[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if target[i - 1] == actual[j - 1] else 1
            table[i][j] = min(
                table[i - 1][j] + 1,
                table[i][j - 1] + 1,
                table[i - 1][j - 1] + cost,
            )
    return table


def _diff_equal_length(target: str, actual: str) -> list[DiffItem]:
    """等长场景：逐位比，结果长度恒等于长度（学生最容易理解的形式）。"""
    return [
        DiffItem(
            pos=pos,
            expected=tc,
            actual=ac,
            state="match" if tc == ac else "wrong",
        )
        for pos, (tc, ac) in enumerate(zip(target, actual))
    ]


def _diff_via_dp(target: str, actual: str) -> list[DiffItem]:
    """不等长场景：DP 回溯对齐。"""
    table = _dp_table(target, actual)
    i, j = len(target), len(actual)
    steps: list[DiffItem] = []

    while i > 0 or j > 0:
        if j > 0 and table[i][j] == table[i][j - 1] + 1:
            # actual 多出的字符
            steps.append(DiffItem(pos=-1, expected=None, actual=actual[j - 1], state="extra"))
            j -= 1
        elif i > 0 and table[i][j] == table[i - 1][j] + 1:
            # target 有、actual 没听到
            steps.append(DiffItem(pos=-1, expected=target[i - 1], actual=None, state="missing"))
            i -= 1
        else:
            tc, ac = target[i - 1], actual[j - 1]
            steps.append(
                DiffItem(
                    pos=-1,
                    expected=tc,
                    actual=ac,
                    state="match" if tc == ac else "wrong",
                )
            )
            i -= 1
            j -= 1

    steps.reverse()
    return _renumber(_merge_adjacent_transpositions(steps))


def _merge_adjacent_transpositions(steps: list[DiffItem]) -> list[DiffItem]:
    """把相邻的 `missing` + `extra` 合并成一条 `wrong`。

    长度不等时，DP 偶尔会把「读错一个字母」拆成「少一个 + 多一个」。
    对学生来说那就是「读错了」，合并后差异位也更少、更好指。

    只有在两者紧挨着、且位次一一对应时才合并（`ab` vs `ba` 这类乱序），
    正常的「末尾漏一个」（U11）或「末尾多一个」（U12）不会被误合并。
    """
    out: list[DiffItem] = []
    index = 0
    while index < len(steps):
        current = steps[index]
        nxt = steps[index + 1] if index + 1 < len(steps) else None
        if (
            nxt is not None
            and {current.state, nxt.state} == {"missing", "extra"}
        ):
            missing = current if current.state == "missing" else nxt
            extra = nxt if current.state == "missing" else current
            out.append(
                DiffItem(
                    pos=-1,
                    expected=missing.expected,
                    actual=extra.actual,
                    state="wrong",
                )
            )
            index += 2
            continue
        out.append(current)
        index += 1
    return out


def _renumber(items: list[DiffItem]) -> list[DiffItem]:
    for pos, item in enumerate(items):
        item.pos = pos
    return items


def align_diff(target_seq: str, letter_seq: str) -> list[DiffItem]:
    """产出逐位对齐结果（长度尽量贴近 `max(len(target), len(actual))`）。

    Examples:
        >>> [d.state for d in align_diff("class", "cless")]
        ['match', 'match', 'wrong', 'match', 'match']
        >>> [d.state for d in align_diff("class", "clas")]
        ['match', 'match', 'match', 'match', 'missing']
        >>> [d.state for d in align_diff("class", "classs")]
        ['match', 'match', 'match', 'match', 'match', 'extra']
    """
    if target_seq == letter_seq:
        return [
            DiffItem(pos=pos, expected=ch, actual=ch, state="match")
            for pos, ch in enumerate(target_seq)
        ]
    if not letter_seq:
        return [
            DiffItem(pos=pos, expected=ch, actual=None, state="missing")
            for pos, ch in enumerate(target_seq)
        ]
    if not target_seq:
        return [
            DiffItem(pos=pos, expected=None, actual=ch, state="extra")
            for pos, ch in enumerate(letter_seq)
        ]
    if len(target_seq) == len(letter_seq):
        return _diff_equal_length(target_seq, letter_seq)
    return _diff_via_dp(target_seq, letter_seq)


# ---------------------------------------------------------------------------
# 对外判定
# ---------------------------------------------------------------------------


def judge_spell(
    *,
    asr_text: str | None,
    target: str,
    asr_confidence: float | None = None,
) -> Verdict:
    """拼读判定（契约 §7.1 冻结签名）。

    Args:
        asr_text: 云 ASR 返回的原始文本（如 `"see ell aye ess ess"`）。
        target: 目标拼写（词条的 `en`，如 `"class"`）。
        asr_confidence: 厂商返回的识别置信度，仅透传留存。

    Returns:
        `Verdict`；`diff` **一定非空**（除 `empty`），`normalized` 为归一化后的
        字母序列（供 `answers.letter_seq` 入库）。
    """
    target_seq = normalize_letters(target)
    letter_seq = get_map().to_letters(asr_text)

    # 识别为空 → 不计错（与文本判定同口径）
    if is_empty_answer(asr_text) or not letter_seq:
        return Verdict(
            is_correct=False,
            match_type="empty",
            expected=target,
            normalized=letter_seq or None,
            asr_text=asr_text or None,
            can_appeal=False,
            asr_confidence=asr_confidence,
        )

    base = {
        "expected": target,
        "normalized": letter_seq,
        "asr_text": asr_text,
        "asr_confidence": asr_confidence,
        "diff": align_diff(target_seq, letter_seq),
    }

    # 防御：目标本身没有字母（配置错误）→ 不可能判对
    if not target_seq:
        return Verdict(is_correct=False, match_type="wrong", **base)

    # ---- 1. 完全一致 ----
    if letter_seq == target_seq:
        return Verdict(is_correct=True, match_type="exact", can_appeal=False, **base)

    # ---- 2. 长度达标且仅差 1 处 ----
    if len(target_seq) >= SPELL_FUZZY_MIN_TARGET_LEN:
        if edit_distance(target_seq, letter_seq) <= SPELL_FUZZY_MAX_DISTANCE:
            return Verdict(is_correct=True, match_type="fuzzy", can_appeal=True, **base)

    # ---- 3. 其余 ----
    return Verdict(is_correct=False, match_type="wrong", can_appeal=False, **base)
