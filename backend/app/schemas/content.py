"""内容 Schema —— WP-BE-03（10a F1 ~ F10，契约 §8 枚举、§14 裁决 3）。

覆盖三张表：`units`（单元）/ `words`（英语词条）/ `texts`（语文课文）。

### 布尔字段的分层（契约 §4.6 / §14 裁决 2）

DB 存 `INTEGER 0/1`，**API 一律 `true` / `false`**，转换在本层完成。
涉及 `require_spell`。

> ⚠️ **与《接口设计》示例不一致之处**：§5.6 / §8.5 的示例把 `require_spell` 写成 `1`。
> 契约 §4.6 明确把它列入「API 层用 true/false」的字段清单，且 §14 裁决 2 重申
> 「DB 用 0/1、API 用 true/false，不是两边不一致，而是分层职责」。
> 按优先级（契约 > 接口设计）**实现为 bool**，接口设计示例需回写。

### `accepted` 的形态（契约 §4.5 硬性约束第 2 条）

DB 里是「合法 JSON 数组字符串或 `NULL`」；API 层是数组。
`NULL` 与 `[]` 都表示「没有额外可接受答案」，出口统一成 `[]`（前端不用判 two states）。

### 双角色词条（契约 §14 裁决 3）

`/api/units/{id}/words` 同一路径按角色裁剪字段，因此有**两个出参模型**：
`WordOut`（admin，全字段）与 `WordStudentOut`（student，隐藏 `accepted`
与 `status`，按 `mode` 隐藏 `en` 或 `cn`，附 `mastery`）。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, field_serializer

from app.core.excel import to_halfwidth
from app.schemas.auth import Semester  # `first` / `second`，与账号域共用一份定义

# ---------------------------------------------------------------------------
# 共用枚举（取值来自契约 §8，**不得新增别名**）
# ---------------------------------------------------------------------------

Subject = Literal["chinese", "english"]
ContentStatus = Literal["active", "inactive"]
WordType = Literal["word", "phrase", "sentence"]
PromptMode = Literal["cn2en", "en2cn"]

#: 练习模式 → 「要隐藏的那一侧」。中→英时藏英文（否则直接看到答案）
_MODE_HIDES: dict[str, str] = {"cn2en": "en", "en2cn": "cn"}


def _parse_accepted(raw: Any) -> list[str]:
    """`accepted` 入口归一：JSON 字符串 / 数组 / `NULL` / 裸串 → `list[str]`。

    容错「裸字符串」是刻意的：手工改库或早期数据可能直接存 `课室`，
    解析失败时按单个答案处理，**不要抛异常把整条记录读废**。
    """
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        text = raw.strip()
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return [text] if text else []
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
        if isinstance(parsed, str):
            return [parsed.strip()] if parsed.strip() else []
        return []
    return []


def dump_accepted(items: list[str] | None) -> str | None:
    """`list[str]` → 入库用的 JSON 字符串；空列表 → `None`（契约 §4.5 允许 NULL）。"""
    cleaned = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not cleaned:
        return None
    return json.dumps(cleaned, ensure_ascii=False)


def _clean_text(v: Any) -> str | None:
    """**标识型**文本清洗：全角转半角 + trim + 空串归 None。

    只用于需要「归一后比较 / 匹配」的字段：英文、单元名、枚举、学号。
    中文输入法打出的全角 `ａｂｃ` / `１２３` 是高频输入错误，不转就会莫名匹配不上。

    ⚠️ **展示型字段不要用它**，用 `_clean_show` —— 见下。
    """
    if v is None:
        return None
    text = to_halfwidth(str(v)).strip()
    return text or None


def _clean_show(v: Any) -> str | None:
    """**展示型**文本清洗：只 trim + 空串归 None，**不做全角转半角**。

    `to_halfwidth` 覆盖整个全角 ASCII 段（`U+FF01`~`U+FF5E`），
    会把中文标点一并转掉：`教室（改）` → `教室(改)`、`我与父亲，不相见` → `我与父亲,不相见`。

    对标识型字段这是必要的归一；对中文释义 / 例句 / 课文正文纯属副作用，
    而且与《数据库设计》§3.5「`content_raw` 保留标点用于展示」直接冲突，
    更会让 Excel 导入与 API 修改产生**两种结果**（同一句中文，走导入是 `（）`、
    走 PATCH 变成 `()`），排查起来极其费解。
    """
    if v is None:
        return None
    text = str(v).strip()
    return text or None


# ---------------------------------------------------------------------------
# units — 单元（F1 ~ F4）
# ---------------------------------------------------------------------------


class UnitOut(BaseModel):
    """`GET /api/units` 列表项（接口设计 §5.1）。

    `word_count` / `text_count` / `active_word_count` / `active_text_count`
    是列表查询时聚合出来的派生值，不是表字段。
    """

    id: int
    subject: Subject
    grade: int
    semester: Semester
    name: str
    order_no: int = 0
    remark: str | None = None
    status: ContentStatus = "active"
    created_at: str | None = None
    updated_at: str | None = None
    # 派生
    word_count: int = 0
    text_count: int = 0
    active_word_count: int = 0
    active_text_count: int = 0


class UnitCreateIn(BaseModel):
    """`POST /api/units`（接口设计 §5.2，10a F2）。

    `(subject, grade, semester, name)` 唯一冲突 → `10001` + 明确提示（U19）。
    注意库里是 `uk_units` 唯一索引兜底，不能只靠服务层先查后插
    （并发下会双写成功）。
    """

    subject: Subject
    grade: int = Field(ge=1, le=9, description="1–9（小学 1–6 + 初中 7–9）")
    semester: Literal["first", "second"]
    name: str = Field(min_length=1, max_length=60)
    order_no: int = Field(default=0, ge=0)
    remark: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        text = _clean_text(v)
        if not text:
            raise ValueError("单元名称不能为空")
        if len(text) > 60:
            raise ValueError("单元名称最多 60 个字符")
        return text

    @field_validator("remark")
    @classmethod
    def _v_remark(cls, v: str | None) -> str | None:
        return _clean_show(v)


class UnitPatchIn(BaseModel):
    """`PATCH /api/units/{id}`（接口设计 §5.3，10a F3）。

    仅这 4 个字段可改；`subject`/`grade`/`semester` **不允许改** ——
    它们决定内容的归属语境，改了会让已导入的词条「投错年级」。
    需要改语境请新建单元（契约冻结的 `uk_units` 也依赖这三列）。
    """

    name: str | None = Field(default=None, min_length=1, max_length=60)
    order_no: int | None = Field(default=None, ge=0)
    status: ContentStatus | None = None
    remark: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        text = _clean_text(v)
        if not text:
            raise ValueError("单元名称不能为空")
        return text

    @field_validator("remark")
    @classmethod
    def _v_remark(cls, v: str | None) -> str | None:
        # 显式传 null 表示「清空备注」，因此这里保留 None（不做「None 即不修改」处理）
        return _clean_show(v)


# ---------------------------------------------------------------------------
# words — 英语词条（F5 ~ F8）
# ---------------------------------------------------------------------------


class MasteryBrief(BaseModel):
    """学生端词条的掌握度摘要（接口设计 §8.5 的 `mastery` 字段）。

    口径见契约 §7.2：`not_started` / `practiced_with_error` / `practiced_ok` / `mastered`。
    """

    status: str = "not_started"
    streak: int = 0
    wrong_count: int = 0
    mastered: bool = False


class WordOut(BaseModel):
    """admin 角色 / 管理端词条（接口设计 §5.5，契约 §14 裁决 3：**全字段**）。"""

    id: int
    unit_id: int
    type: WordType = "word"
    en: str
    cn: str
    accepted: list[str] = Field(default_factory=list)
    phonetic: str | None = None
    pos: str | None = None
    example_en: str | None = None
    example_cn: str | None = None
    require_spell: bool = True
    order_no: int = 0
    status: ContentStatus = "active"
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("accepted", mode="before")
    @classmethod
    def _v_accepted(cls, v: Any) -> list[str]:
        return _parse_accepted(v)

    @field_validator("require_spell", mode="before")
    @classmethod
    def _v_require_spell(cls, v: Any) -> bool:
        """DB 的 `0` / `1` → bool（契约 §4.6）。"""
        if isinstance(v, bool):
            return v
        if v is None:
            return True
        return str(v).strip() not in ("0", "false", "False", "")


class WordStudentOut(BaseModel):
    """student 角色词条（接口设计 §8.5，契约 §14 裁决 3：**裁剪字段**）。

    裁剪规则：
    - **不返回 `accepted`** —— 那是判分依据，下发等于泄题（契约禁止清单第 4 条）。
    - **不返回 `status`** —— 学生端查询本就带 `status='active'`，返回是噪声。
    - 按 `mode` 隐藏出题侧：`cn2en` 藏 `en`、`en2cn` 藏 `cn`（隐藏字段为 `None`，
      键仍在，前端不用判字段是否存在）。
    - 附 `mastery`（唯一来源 `MasteryService`）。
    """

    id: int
    unit_id: int
    type: WordType = "word"
    en: str | None = None
    cn: str | None = None
    phonetic: str | None = None
    pos: str | None = None
    example_en: str | None = None
    example_cn: str | None = None
    require_spell: bool = True
    order_no: int = 0
    mastery: MasteryBrief = Field(default_factory=MasteryBrief)

    @field_validator("require_spell", mode="before")
    @classmethod
    def _v_require_spell(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return True
        return str(v).strip() not in ("0", "false", "False", "")

    @classmethod
    def from_row(
        cls,
        row: dict[str, Any],
        *,
        mode: PromptMode | None = None,
        mastery: MasteryBrief | None = None,
    ) -> "WordStudentOut":
        """按练习模式裁剪后构造。"""
        hides = _MODE_HIDES.get(mode or "", None)
        data = dict(row)
        if hides:
            data[hides] = None
        data.pop("accepted", None)
        data.pop("status", None)
        data["mastery"] = mastery or MasteryBrief()
        return cls.model_validate(data)


class WordPatchIn(BaseModel):
    """`PATCH /api/words/{id}`（接口设计 §5.6，10a F6）。

    ⚠️ **改词条不影响历史记录**：`answers.expected` 存的是**标准答案快照**
    （数据库设计 §3.7），历史记录永远保留当时的答案。
    但若该词条已有答题记录，响应会附 `history_affected: true`，
    提示管理者「改完之后，新旧记录的标准答案会不一致」，由前端弹确认（U15）。
    """

    type: WordType | None = None
    en: str | None = Field(default=None, min_length=1, max_length=200)
    cn: str | None = Field(default=None, min_length=1, max_length=200)
    accepted: list[str] | None = None
    phonetic: str | None = Field(default=None, max_length=200)
    pos: str | None = Field(default=None, max_length=40)
    example_en: str | None = Field(default=None, max_length=500)
    example_cn: str | None = Field(default=None, max_length=500)
    require_spell: bool | None = None
    order_no: int | None = Field(default=None, ge=0)
    status: ContentStatus | None = None

    @field_validator("en")
    @classmethod
    def _v_en(cls, v: str | None) -> str | None:
        """英文是判分目标，走归一（全角字母必须转，否则 `ｃｌａｓｓ` 永远判不对）。"""
        if v is None:
            return None
        text = _clean_text(v)
        if not text:
            raise ValueError("英文不能为空")
        return text

    @field_validator("cn")
    @classmethod
    def _v_cn(cls, v: str | None) -> str | None:
        """中文释义是展示型，不转全角（见 `_clean_show`）。"""
        if v is None:
            return None
        text = _clean_show(v)
        if not text:
            raise ValueError("中文不能为空")
        return text

    @field_validator("phonetic", "pos", "example_en", "example_cn")
    @classmethod
    def _v_optional_text(cls, v: str | None) -> str | None:
        return _clean_show(v)

    @field_validator("accepted", mode="before")
    @classmethod
    def _v_accepted(cls, v: Any) -> list[str] | None:
        # 字段不出现 → None（不修改）；显式传 null / [] → 清空
        if v is None:
            return None
        return _parse_accepted(v)

    @field_validator("accepted")
    @classmethod
    def _v_accepted_len(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and len(v) > 20:
            raise ValueError("可接受答案最多 20 条")
        return v


class WordBatchStatusIn(BaseModel):
    """`POST /api/words/batch-status`（接口设计 §5.7，10a F7）。"""

    word_ids: list[int] = Field(min_length=1, max_length=1000)
    status: ContentStatus


# ---------------------------------------------------------------------------
# texts — 语文课文（F9 ~ F10）
# ---------------------------------------------------------------------------


class ParagraphOut(BaseModel):
    """段落。`text` 保留标点用于展示；`chars` 只数汉字（与 `content_plain` 同口径）。"""

    idx: int
    text: str
    chars: int = 0
    recite: bool = True


class TextBriefOut(BaseModel):
    """`GET /api/units/{id}/texts` 列表项（接口设计 §8.6）。

    **不含正文** —— 学生选定后再单独取（§8.7），减少流量。
    """

    id: int
    unit_id: int
    title: str
    author: str | None = None
    status: ContentStatus = "active"
    order_no: int = 0
    total_chars: int = 0
    recite_scope_chars: int = 0
    paragraph_count: int = 0
    recite_paragraph_count: int = 0
    recite_scope: list[int] = Field(default_factory=list)
    last_grade: str | None = None
    created_at: str | None = None

    @field_validator("recite_scope", mode="before")
    @classmethod
    def _v_scope(cls, v: Any) -> list[int]:
        return _parse_int_list(v)


class TextDetailOut(BaseModel):
    """`GET /api/texts/{id}` 课文详情（接口设计 §8.7，10a F10）。

    ⚠️ **不下发拼音序列**（§8.7 明文）：拼音对齐在服务端做，
    既避免暴露内部实现，也减小传输体积。因此本模型**没有** `content_pinyin`。
    """

    id: int
    unit_id: int
    title: str
    author: str | None = None
    status: ContentStatus = "active"
    order_no: int = 0
    paragraphs: list[ParagraphOut] = Field(default_factory=list)
    recite_scope: list[int] = Field(default_factory=list)
    scope_snapshot: dict[str, int] = Field(default_factory=dict)
    total_chars: int = 0
    content_raw: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("recite_scope", mode="before")
    @classmethod
    def _v_scope(cls, v: Any) -> list[int]:
        return _parse_int_list(v)


class TextPatchIn(BaseModel):
    """`PATCH /api/texts/{id}` —— 改标题 / 作者 / 状态 / 序号。

    ⚠️ **本接口在《接口设计》与 10a 的 F 表中都没有列出**（F9 / F10 只有两个 GET）。
    但需求 §4.6「内容操作：修改 / 停用 / 删除」与「有答题记录的内容只能停用不能删除」
    对课文同样成立，缺了它课文就是「只能建不能管」。故按 units / words 的同一形态补齐，
    **已列入待姚工确认的契约补充项**。

    正文（`content_raw` 及派生序列）**不在此接口修改** ——
    改正文要重算 `content_plain` / `content_pinyin` / `total_chars`，
    属于「重新导入」的范畴，走 F14 / F15 更安全。
    """

    title: str | None = Field(default=None, min_length=1, max_length=100)
    author: str | None = Field(default=None, max_length=60)
    order_no: int | None = Field(default=None, ge=0)
    status: ContentStatus | None = None
    # 2026-09-30 追加：语文内容管理页行内「编辑」需要改正文。
    # ⚠️ 正文/背诵范围改动会让历史背诵记录的对齐口径失效 —— 服务层强守卫：
    #    已有背诵记录 → 30003 拒绝（与「有记录只能停用」同一原则）。
    content_raw: str | None = Field(default=None, min_length=1)
    recite_scope: list[int] | None = None

    @field_validator("title")
    @classmethod
    def _v_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        text = _clean_show(v)
        if not text:
            raise ValueError("课文标题不能为空")
        return text

    @field_validator("author")
    @classmethod
    def _v_author(cls, v: str | None) -> str | None:
        return _clean_show(v)


class WordCreateIn(BaseModel):
    """`POST /api/words` —— 管理端单条新增词条（英语内容管理页，2026-09-30）。

    与导入链路（F12/F13）并存：导入管批量，本接口管「漏了一个词随手补」。
    `en` / `cn` 必填；`accepted` 传数组；`order_no` 缺省由服务端排到单元末尾。
    """

    unit_id: int
    type: WordType = "word"
    en: str = Field(min_length=1, max_length=100)
    cn: str = Field(min_length=1, max_length=100)
    accepted: list[str] = Field(default_factory=list)
    phonetic: str | None = Field(default=None, max_length=100)
    pos: str | None = Field(default=None, max_length=30)
    example_en: str | None = Field(default=None, max_length=500)
    example_cn: str | None = Field(default=None, max_length=500)
    require_spell: bool = True
    order_no: int | None = Field(default=None, ge=0)

    @field_validator("en", "cn")
    @classmethod
    def _v_text(cls, v: str) -> str:
        text = _clean_show(v)
        if not text:
            raise ValueError("不能为空")
        return text


class TextCreateIn(BaseModel):
    """`POST /api/texts` —— 管理端单条新增课文（语文内容管理页，2026-09-30）。

    `content_raw` 按空行/换行分段（与导入同一套 `split_paragraphs`）；
    `recite_scope` 传段落号数组（**从 0 数起**，与导入预览口径一致），
    缺省 = 全文背诵。正文会经拼音服务校验，含无法拼音化的字 → `40003`。
    """

    unit_id: int
    title: str = Field(min_length=1, max_length=100)
    author: str | None = Field(default=None, max_length=60)
    content_raw: str = Field(min_length=1)
    recite_scope: list[int] | None = None

    @field_validator("title")
    @classmethod
    def _v_title(cls, v: str) -> str:
        text = _clean_show(v)
        if not text:
            raise ValueError("课文标题不能为空")
        return text

    @field_validator("author")
    @classmethod
    def _v_author(cls, v: str | None) -> str | None:
        return _clean_show(v)


def _parse_int_list(raw: Any) -> list[int]:
    """JSON 字符串 / 数组 → `list[int]`（`recite_scope` 用）。解析失败返回空表。"""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(str(raw))
        except (ValueError, TypeError):
            return []
    if not isinstance(items, list):
        return []
    out: list[int] = []
    for x in items:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


__all__ = [
    "Semester",
    "Subject",
    "ContentStatus",
    "WordType",
    "PromptMode",
    "dump_accepted",
    "UnitOut",
    "UnitCreateIn",
    "UnitPatchIn",
    "MasteryBrief",
    "WordOut",
    "WordStudentOut",
    "WordPatchIn",
    "WordBatchStatusIn",
    "ParagraphOut",
    "TextBriefOut",
    "TextDetailOut",
    "TextPatchIn",
    "WordCreateIn",
    "TextCreateIn",
]
