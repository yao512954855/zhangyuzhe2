"""归一化 —— WP-BE-04 / 需求 §6.1（文本）与 §6.2（拼读）。

两套归一化**用途不同，不可混用**：

| 函数 | 用途 | 结果 |
|---|---|---|
| `normalize_text` | 英语说词 / 英译中，**保留词间空格**（`a classroom` 要能与 `classroom` 做包含匹配） | 小写、单空格分隔、标点剥除、数字统一 |
| `normalize_letters` | 拼读，**只保留 a-z** | 纯字母序列（`class`） |

归一化在**服务端**做（契约禁止清单 7：客户端不得自行实现判分逻辑）。
"""

from __future__ import annotations

import re
import unicodedata

from app.core.excel import to_halfwidth

# ---------------------------------------------------------------------------
# 标点处理
# ---------------------------------------------------------------------------

# 视为「撇号」的字符：ASCII 撇号 + 弯引号 + 修饰字母撇号
_APOSTROPHES = frozenset({"'", "\u2018", "\u2019", "\u02bc"})

# 中文数字字符
_CN_DIGITS: dict[str, int] = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS: dict[str, int] = {"十": 10, "百": 100, "千": 1000}
_CN_BIG: dict[str, int] = {"万": 10000, "亿": 100000000}
_CN_NUMBER_CHARS = frozenset(_CN_DIGITS) | frozenset(_CN_UNITS) | frozenset(_CN_BIG)
_CN_NUMBER_RUN = re.compile(r"[零〇一二三四五六七八九十百千万亿两]+")

# @ 之类在英文里充当分隔符的符号 → 视为标点（统一交给 category 判断，此处仅留白名单）
_WHITESPACE_RUN = re.compile(r"\s+")
# 汉字之间的空格（中文不按空格分词，残留空格会让「我与 父亲」对不上「我与父亲」）
_CN_SPACE = re.compile(r"(?<=[\u3400-\u4dbf\u4e00-\u9fff])[ \t]+(?=[\u3400-\u4dbf\u4e00-\u9fff])")

_NOT_LETTER = re.compile(r"[^a-z]")


def _is_apostrophe_context(text: str, idx: int) -> bool:
    """判断 `text[idx]` 处的撇号是**真撇号**（`don't`）还是**引号**（`'你好'`）。

    规则：左右**紧邻都是 ASCII 字母**才算真撇号。这样 `don’t`（U+2019 弯引号）
    也能正确保留，而中文引号 `‘你好’` 会被剥掉。
    """
    prev_ch = text[idx - 1] if idx > 0 else ""
    next_ch = text[idx + 1] if idx + 1 < len(text) else ""
    return (
        prev_ch.isascii()
        and prev_ch.isalpha()
        and next_ch.isascii()
        and next_ch.isalpha()
    )


def strip_punctuation(text: str, *, keep_apostrophe: bool = True) -> str:
    """把标点与符号替换为空格（**不是直接删除**）。

    替换为空格而非删除，是为了避免 `a,classroom` 粘成 `aclassroom` 引入假匹配。
    用 `unicodedata.category` 判定，可一次覆盖半角 / 全角 / 中文标点。
    """
    out: list[str] = []
    for i, ch in enumerate(text):
        category = unicodedata.category(ch)
        if category.startswith("P") or category.startswith("S"):
            if keep_apostrophe and ch in _APOSTROPHES and _is_apostrophe_context(text, i):
                out.append("'")
            else:
                out.append(" ")
            continue
        out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# 中文数字 → 阿拉伯数字
# ---------------------------------------------------------------------------


def _cn_run_to_int(run: str) -> int | None:
    """把一段纯中文数字转成整数；无法确定语义时返回 None（保持原样）。

    ⚠️ **宁可返回 None，不要猜** —— 判定场景下把「两三」猜成 2 或 3 都可能
    造成「错读判对」，与「判分模型必须忠实」同一条原则。
    """
    if not run:
        return None
    total = 0
    section = 0
    number = 0
    for ch in run:
        if ch in _CN_DIGITS:
            # 「两三」「一二」是并列列举，不是数值 → 放弃转换
            if number != 0:
                return None
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if number == 0:
                # 「十一」= 10 + 1，「一百零五」的「零」不参与
                number = 1
            section += number * unit
            number = 0
        else:  # _CN_BIG
            section = (section + number) * _CN_BIG[ch]
            total += section
            section = 0
            number = 0
    return total + section + number


def cn_number_to_arabic(text: str) -> str:
    """把文本中的中文数字段统一成阿拉伯数字（需求 §6.1「数字统一转阿拉伯数字」）。

    ASR 对同一句话可能返回「三」也可能返回「3」，两条都算对，否则会凭
    无关紧要的书写差异判学生错。

    只处理**整段可解析**的中文数字，解析不出（`两三`、`一二三`）就原样保留。
    """
    if not _CN_NUMBER_RUN.search(text):
        return text

    def _replace(match: re.Match[str]) -> str:
        value = _cn_run_to_int(match.group(0))
        return str(value) if value is not None else match.group(0)

    return _CN_NUMBER_RUN.sub(_replace, text)


# ---------------------------------------------------------------------------
# 对外：两套归一化
# ---------------------------------------------------------------------------


def normalize_text(value: str | None) -> str:
    """英语说词 / 英译中的归一化（需求 §6.1）。

    1. 转小写、去首尾空格
    2. 全角 → 半角（数字 / 字母 / 空格）
    3. 中文数字 → 阿拉伯数字
    4. 标点 → 空格（**撇号保留**，`don't` 不被拆）
    5. 连续空白合一
    6. 汉字之间的空格去掉（中文不按空格分词）

    Examples:
        >>> normalize_text("  ClassRoom ")
        'classroom'
        >>> normalize_text("Don’t stop!")
        "don't stop"
        >>> normalize_text("三")
        '3'
    """
    if not value:
        return ""
    text = value.strip().lower()
    text = to_halfwidth(text)
    text = cn_number_to_arabic(text)
    text = strip_punctuation(text, keep_apostrophe=True)
    text = _WHITESPACE_RUN.sub(" ", text).strip()
    text = _CN_SPACE.sub("", text)
    return text


def normalize_letters(value: str | None) -> str:
    """拼读的归一化 —— 只保留 `a-z`（需求 §6.2 第 3 步）。

    ⚠️ 输入应当**已经过谐音映射**（`homophone.to_letters`）。本函数只负责
    「去除所有非字母字符（空格、连字符、点号）」，不做任何字母猜测。

    Examples:
        >>> normalize_letters("c-l-a-s-s")
        'class'
        >>> normalize_letters("C L A S S")
        'class'
    """
    if not value:
        return ""
    return _NOT_LETTER.sub("", to_halfwidth(value).lower())


def is_empty_answer(value: str | None) -> bool:
    """判定「识别为空」（需求 §6.1 / §6.2：`empty` 不计错）。

    只有真的没有任何字母/汉字/数字才算空；纯标点（如 ASR 只返回一个句号）
    同样视为空 —— 它不承载任何答案信息。
    """
    if value is None:
        return True
    if not value.strip():
        return True
    stripped = strip_punctuation(value, keep_apostrophe=True).strip()
    return not stripped
