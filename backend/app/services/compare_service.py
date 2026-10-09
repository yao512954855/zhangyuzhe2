"""中文释义比对 —— WP-A，契约《01-共享契约冻结》§6.1（签名冻结，逐字实现）。

场景：`spell_en2zh`（看英文背中文）—— 学生说出中文释义，云 ASR 回传文本，
服务端把它与 `items.zh` / `items.alt_zh` 归一化后比对。

三个归一化动作（契约 docstring 原文）：
1. 剥词性前缀（adj./v./n. 等）
2. 去空白与标点
3. 去尾缀「的/了/儿」

结果：小写、无标点的纯文本。

⚠️ 本模块被 C/E 经 HTTP 间接使用，不需要 import；签名一旦冻结不得改动
（发现契约问题只准报告，见 01-契约 §11）。
"""

from __future__ import annotations

import re

# 词性前缀：常见英语词性缩写（带或不带「.」），出现在释义开头时剥掉。
# ⚠️ 必须有边界（「.」或空白）才认，否则 "very good" 会被误当 "v" + "ery good"。
_POS_PREFIX_RE = re.compile(
    r"^(adj|adv|v|n|prep|conj|pron|num|art|aux|vi|vt|int|interj|abbr|pl|det|modal)"
    r"(?:\.|(?=\s))\s*",
    re.IGNORECASE,
)

# 标点与空白：中英文标点 + 一切空白字符
_PUNCT_SPACE_RE = re.compile(r"[\s，。、；：？！“”‘’（）《》〈〉【】…—·,.:;?!()<>{}\[\]\"'/\\|`~\-_]+")

# 尾缀：反复剥，如「美丽的了」→「美丽」
_TAIL_SUFFIXES = ("的", "了", "儿")


def normalize_zh(text: str, pos: str = "") -> str:
    """归一化学生答案：剥词性前缀（adj./v./n. 等）、去空白与标点、
    去尾缀「的/了/儿」。返回小写、无标点的纯文本。"""
    result = (text or "").strip()

    # 1) 剥词性前缀：优先剥条目自带的 pos（如 "adj."），再兜底剥答案里
    #    学生照着词典释义念出来的前缀（如 "adj 大的"）
    pos_clean = (pos or "").strip().lower().rstrip(".")
    if pos_clean and result.lower().startswith(pos_clean):
        remainder = result[len(pos_clean):]
        if remainder.startswith("."):
            remainder = remainder.lstrip(".")
        if remainder[:1].isspace() or not remainder[:1].isascii():
            result = remainder.lstrip()
    result = _POS_PREFIX_RE.sub("", result, count=1)

    # 2) 去空白与标点
    result = _PUNCT_SPACE_RE.sub("", result)

    # 3) 去尾缀（循环剥：「快乐的了」这类连写）
    changed = True
    while changed and result:
        changed = False
        for suffix in _TAIL_SUFFIXES:
            if result.endswith(suffix) and len(result) > len(suffix):
                result = result[: -len(suffix)]
                changed = True

    return result.lower()


def zh_matches(student_answer: str, item_zh: str, item_alt_zh: str = "") -> bool:
    """先与 item_zh 精确比（归一化后），再按顿号拆 alt_zh 逐个比。"""
    normalized = normalize_zh(student_answer)
    if not normalized:
        return False

    if normalized == normalize_zh(item_zh):
        return True

    for alt in (item_alt_zh or "").split("、"):
        alt = alt.strip()
        if alt and normalized == normalize_zh(alt):
            return True

    return False
