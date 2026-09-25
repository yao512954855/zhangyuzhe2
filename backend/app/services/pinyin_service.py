"""拼音服务 —— WP-BE-03 F17（10a「关键实现要点 A / B」）。

**职责**：把课文正文变成对齐引擎需要的两条严格等长序列。

    content_plain  ：纯汉字序列，**无空格**，如 `我与父亲不相见`
    content_pinyin ：对应拼音，**空格分隔**，如 `wo yu fu qin bu xiang jian`

契约《01-共享契约冻结》§4.5 硬性数据约束第 1 条：
`content_plain` 与 `content_pinyin` **按空格切分后长度必须严格相等**，
否则「语文对齐全盘错位」。本模块是这条约束的**唯一校验实现**。

---

### 为什么「防漂移」比「等长」更重要

契约 §1 锁定 `pypinyin==0.51.0`，正是因为它一换版本，
同一个字的拼音可能变，历史 `content_pinyin` 与新导入的就对不齐了。

实测（`pypinyin 0.51.0`，2026-09-24）发现一个**必须防御的行为**：

| 输入 | `lazy_pinyin` 返回 | 含义 |
|---|---|---|
| `汉` | `['han']` | 正常 |
| `〇` | `['ling']` | 正常（虽是符号，但拼音表里有） |
| `㐀`（Ext-A） | `['qiu']` | 正常 |
| **`𠮷`（Ext-B 生僻字）** | **`['𠮷']`** | ⚠️ **返回原字符** —— 拼音表里没有它 |
| `々` / `〆` / `😀` / `A` / `1` | 返回原字符 | 非汉字，进不了 plain |

对不认识的字符，pypinyin **不报错、也不返回空串，而是把原字符原样返回**。
这种 token 在长度上仍然 1:1（所以单看「等长」是查不出来的），
但它**不是拼音**，学生无论怎么读都不可能对齐到它 ——
对齐引擎会在那里永久漏字，而且**不会报任何错**。

因此本模块的校验规则比「等长」更严：

> **每个汉字必须产出一个只由 ASCII 小写字母组成的拼音 token。**
> 否则记 `mismatch`，并给出**具体位置**（`plain` 下标 + 字符），
> 让管理者回 Excel 改内容，而不是让一条坏数据静默入库。

这也是 U7「制造 plain/pinyin 不等长」的真实触发路径：
正文里放一个生僻字即可，不需要人造脏数据。

---

### 多音字

用 `pypinyin` 的默认行为（取常用音）。**禁止** `heteronym=True` ——
它会让同一个字产出多个候选音，`content_pinyin` 直接膨胀，与 `plain` 失配。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pypinyin import Style, lazy_pinyin

# ---------------------------------------------------------------------------
# 汉字判定
# ---------------------------------------------------------------------------

# CJK 统一表意文字各平面。**必须包含扩展区**：中小学古诗文里
# `𠮷`（U+20BB7）这类字偶尔出现，少了它就等于把生僻字当标点丢掉，
# plain 会静默少字 —— 与「去标点」是两回事，不能混为一谈。
_HAN_RANGES: tuple[tuple[int, int], ...] = (
    (0x3400, 0x4DBF),  # CJK 扩展 A
    (0x4E00, 0x9FFF),  # CJK 基本区
    (0xF900, 0xFAFF),  # CJK 兼容表意文字
    (0x20000, 0x2A6DF),  # CJK 扩展 B
    (0x2A700, 0x2EBEF),  # CJK 扩展 C ~ F
    (0x30000, 0x3134F),  # CJK 扩展 G
)

# 「合法拼音 token」：只允许 ASCII 小写字母（无声调、无空格、无数字）
_PINYIN_TOKEN_RE = re.compile(r"^[a-z]+$")

# 段落切分：Excel 单元格内换行（\r\n / \n / \r）都视为段落边界
_PARAGRAPH_SPLIT_RE = re.compile(r"\r\n|\r|\n")


def is_han(ch: str) -> bool:
    """单个字符是否属于 CJK 汉字（含扩展区）。"""
    if not ch:
        return False
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _HAN_RANGES)


def han_only(text: str) -> str:
    """提取纯汉字序列（去标点 / 空白 / 数字 / 英文字母），字与字之间不留空格。

    这是 `content_plain` 的**唯一生成方式**（10a「关键实现要点 A」）。
    """
    if not text:
        return ""
    return "".join(ch for ch in str(text) if is_han(ch))


def count_han(text: str) -> int:
    """统计汉字个数（与 `content_plain` 同口径，**不含标点**）。"""
    return len(han_only(text))


def split_paragraphs(raw: str) -> list[str]:
    """按换行切分段落，忽略空行（10a「关键实现要点 B」）。

    ⚠️ 返回的是**已 strip 的段落文本**，标点保留（用于展示）；
    要算汉字数请另调 `count_han()`。
    """
    if not raw:
        return []
    parts = _PARAGRAPH_SPLIT_RE.split(str(raw))
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# 单字拼音
# ---------------------------------------------------------------------------


def char_pinyin(ch: str) -> str:
    """单字 → 无声调拼音。

    pypinyin 不认识该字时**原样返回该字**（实测行为，见模块 docstring），
    调用方用 `_PINYIN_TOKEN_RE` 判定其合法性。
    """
    parts = lazy_pinyin(ch, style=Style.NORMAL)
    return parts[0] if parts else ""


# ---------------------------------------------------------------------------
# 校验结果
# ---------------------------------------------------------------------------


@dataclass
class PinyinBuild:
    """一次 `build()` 的完整结果。"""

    plain: str
    """纯汉字序列（无空格）。"""

    pinyin: str
    """空格分隔的拼音序列。"""

    tokens: list[str] = field(default_factory=list)
    """拼音 token 列表（`pinyin.split()` 的结果，缓存下来避免重复切分）。"""

    ok: bool = True
    """是否通过校验。False = **禁止入库**（10a F14 / U7）。"""

    mismatches: list[dict] = field(default_factory=list)
    """失配明细：`[{"pos": 12, "char": "𠮷", "pinyin": "𠮷", "reason": "..."}]`。"""

    @property
    def mismatch_positions(self) -> list[int]:
        """失配位置列表（`plain` 的 0 基下标）。便于接口直接回传前端。"""
        return [int(m["pos"]) for m in self.mismatches]

    @property
    def length(self) -> int:
        """汉字个数。"""
        return len(self.plain)


def check(plain: str, pinyin: str) -> tuple[bool, list[dict]]:
    """校验 plain / pinyin 是否严格对齐。**纯函数，可独立单测。**

    Args:
        plain: 纯汉字序列（应无空格）。
        pinyin: 空格分隔的拼音序列。

    Returns:
        `(ok, mismatches)`。`mismatches` 每项含 `pos` / `char` / `pinyin` / `reason`。

    判定两条（任一不满足即 `ok=False`）：
    1. **个数相等** —— `len(pinyin.split()) == len(plain)`
    2. **每个 token 都是合法拼音**（`^[a-z]+$`）—— 见模块 docstring 的
       「为什么防漂移比等长更重要」
    """
    tokens = pinyin.split() if pinyin else []
    mismatches: list[dict] = []

    if len(tokens) != len(plain):
        # 长度不等是契约 §4.5 明令拒绝的情形，先按长度报，位置取首个错位点
        shorter = min(len(tokens), len(plain))
        first_bad = None
        for i in range(shorter):
            if tokens[i] != char_pinyin(plain[i]):
                first_bad = i
                break
        if first_bad is None:
            first_bad = shorter
        mismatches.append(
            {
                "pos": first_bad,
                "char": plain[first_bad] if first_bad < len(plain) else None,
                "pinyin": tokens[first_bad] if first_bad < len(tokens) else None,
                "reason": (
                    f"汉字 {len(plain)} 个、拼音 {len(tokens)} 个，数量不一致"
                ),
            }
        )
        # 长度不等时仍把「非拼音 token」一并报出，便于一次性修完
        for i, tok in enumerate(tokens):
            if i < len(plain) and not _PINYIN_TOKEN_RE.match(tok):
                mismatches.append(
                    {
                        "pos": i,
                        "char": plain[i] if i < len(plain) else None,
                        "pinyin": tok,
                        "reason": f"「{plain[i] if i < len(plain) else ''}」没有可用拼音",
                    }
                )
        return False, mismatches

    for i, (ch, tok) in enumerate(zip(plain, tokens)):
        if not _PINYIN_TOKEN_RE.match(tok):
            mismatches.append(
                {
                    "pos": i,
                    "char": ch,
                    "pinyin": tok,
                    "reason": f"「{ch}」没有可用拼音（识别为 {tok!r}），对齐会在此处漏字",
                }
            )
    return (not mismatches), mismatches


def build(text: str) -> PinyinBuild:
    """从一段文本生成 `content_plain` + `content_pinyin` 并自检。

    这是导入链路调用的**唯一入口**。
    """
    plain = han_only(text)
    tokens = [char_pinyin(ch) for ch in plain]
    pinyin = " ".join(tokens)

    ok, mismatches = check(plain, pinyin)
    return PinyinBuild(
        plain=plain,
        pinyin=pinyin,
        tokens=tokens,
        ok=ok,
        mismatches=mismatches,
    )


def build_scoped(paragraphs: list[str], scope: list[int]) -> PinyinBuild:
    """按**背诵范围**拼接多段后生成（U8：改范围要重算 `total_chars`）。

    Args:
        paragraphs: 段落纯文本列表。
        scope: 背诵段落下标数组，如 `[0, 1]`；空列表表示全部。
    """
    picked = scope if scope else list(range(len(paragraphs)))
    merged = "".join(paragraphs[i] for i in picked if 0 <= i < len(paragraphs))
    return build(merged)
