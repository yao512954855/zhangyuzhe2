"""背诵结算评分 —— WP-BE-05 / 需求 §6.4。

三部分权重（`settings` 可配，契约 §8）：

| 指标 | 权重 | 说明 |
|---|---|---|
| 覆盖比例 | 60% | `已背字数 / 总字数` |
| 正确率 | 25% | `1 − 漏字数 / 总字数` |
| 提示惩罚 | 15% | **每次提示扣 5 分，扣完为止**（15 分额度 = 3 次） |

评级：`≥90 优秀` / `75–89 良好` / `60–74 合格` / `<60 待加强`。

---

### ⚠️ 关于提示惩罚：按需求原文实现，不按 10b 正文的公式

10b 规格正文给的是：

```
score = 60×cover + 25×accuracy + 15×max(0, 1 − 提示总次数×0.05)
```

这个式子里 `1 − n×0.05` 是「每次扣 **5%**」（3 次还剩 12.75 分），
与它自己下一句「**每次扣 5 分、扣完为止**」以及需求 §6.4 的表述**并不等价**。
且用它算 U15（全覆盖、零漏字、提示 3 次）会得到 `97.75`，
而 U15 明确要求 `85`（= 60 + 25 + 0）。

正确语义是「15 分额度内每次扣 5 分，3 次扣完」：

```
hint_part = max(0, 15 − 5 × 提示总次数)
```

本实现按**需求 §6.4 + U15 预期**执行，算 U15 得 `85` → 「良好」，与验收用例一致。
该矛盾已作为偏离项上报（见 `docs/dev/02-实现约定与复用经验.md`）。
"""

from __future__ import annotations

from typing import Mapping

from app.core.logging import get_logger

logger = get_logger(__name__)

# 需求 §6.4 的评级门槛
GRADE_EXCELLENT = 90.0
GRADE_GOOD = 75.0
GRADE_PASS = 60.0

# 默认权重（与 settings 种子的 `recite_weight_*` 一致）
DEFAULT_WEIGHTS = (0.6, 0.25, 0.15)

# 提示惩罚额度被扣完所需的次数（每次扣 1/3 的额度 → 3 次扣完）
_HINT_PENALTY_STEPS = 3


def grade_of(score: float) -> str:
    """分数 → 评级（需求 §6.4）。"""
    value = float(score or 0.0)
    if value >= GRADE_EXCELLENT:
        return "优秀"
    if value >= GRADE_GOOD:
        return "良好"
    if value >= GRADE_PASS:
        return "合格"
    return "待加强"


def hint_total(hint_count: Mapping[str, int] | None) -> int:
    """`{"1":2,"2":1,"3":0}` → `3`。

    容错：DB 里 `hint_count` 是 TEXT，历史数据可能是 `null` 或值不是整数。
    """
    if not hint_count:
        return 0
    total = 0
    for value in hint_count.values():
        try:
            total += int(value)
        except (TypeError, ValueError):
            continue
    return max(0, total)


def compute_score(
    *,
    covered_ratio: float,
    missed_count: int,
    total_chars: int,
    hints: int,
    weights: tuple[float, float, float] | None = None,
) -> tuple[float, float, str]:
    """算总分与评级。

    Args:
        covered_ratio: 覆盖比例（`已背字数 / 总字数`）。
        missed_count: 漏字数（**未点亮的字数**，含念错与跳过）。
        total_chars: 目标总字数。
        hints: 提示总次数（三档合计）。
        weights: `(覆盖, 正确率, 提示)`，默认 `(0.6, 0.25, 0.15)`。

    Returns:
        `(score, accuracy, grade)`。

    Notes:
        `total_chars == 0`（空课文）时覆盖与正确率都记 0，不抛异常 ——
        空课文本来就该在导入期被拦下，这里只做兜底。
    """
    w_cover, w_accuracy, w_hint = weights or DEFAULT_WEIGHTS
    total = max(0, int(total_chars or 0))

    # 空课文：三部分都无从谈起。若不特判，「零提示」会把 15 分提示额度白送给它，
    # 于是「一个字都没背」拿到 15 分 —— 显然不对。
    if total <= 0:
        return 0.0, 0.0, grade_of(0.0)

    cover = min(1.0, max(0.0, float(covered_ratio or 0.0)))
    accuracy = min(1.0, max(0.0, 1.0 - int(missed_count or 0) / total))

    capacity = float(w_hint) * 100.0
    per_hint = capacity / _HINT_PENALTY_STEPS
    hint_part = max(0.0, capacity - per_hint * max(0, int(hints or 0)))

    raw = float(w_cover) * 100.0 * cover + float(w_accuracy) * 100.0 * accuracy + hint_part
    score = round(min(100.0, max(0.0, raw)), 2)
    return score, round(accuracy, 4), grade_of(score)
