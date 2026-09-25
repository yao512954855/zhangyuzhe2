#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Word(.docx) → Excel(.xlsx) 转换脚本（WP-2B，二期）。

为什么有这个脚本
----------------
背记系统一期已有成熟的 Excel 导入链路（`backend/app/api/imports.py` +
`backend/app/services/import_service.py`）。二期需求 §12.2 把「Word 导入」列为
可选项，**姚工 2026-09-25 裁决二期全做**。最省的做法不是重写导入逻辑，而是把
`.docx` 转成**符合现有 Excel 模板**的 `.xlsx`，再走现成的导入接口。

本脚本因此**不新增任何后端接口、不改导入链路、不改迁移**，只负责「docx → xlsx」。

契约来源（权威）
----------------
列名 / 必填 / 枚举口径**严格对齐** `backend/app/services/import_service.py`：
- 词条模板 14 列（`WORDS_HEADERS`），必需 6 列。
- 课文模板 8 列（`TEXTS_HEADERS`），必需 6 列。
- 学科 ∈ {语文, 英语}；年级 1–9；学期 ∈ {上学期, 下学期}（导入端 also 收 first/second）；
  类型 ∈ {单词, 短语, 句子}；「是否拼读」∈ {是, 否}。

> 注意：需求说明书《附录 A》给出的是**简化示意图**（词条只列 10 列、用「单元」「标题」
> 等别名）。真正导入端强制校验的是上面 14/8 列契约。若按附录 A 直接产文件，会因缺
> 「单元名 / 单元序号 / 例句英 / 例句 / 是否拼读」或列名不匹配而导入失败。本脚本以
> `import_service.py` 为唯一权威，确保产出**立即可被现有导入接口消费**。

设计原则
--------
- **宁可在转换时报错，也不产出导入必失败的 xlsx**：列名非法、必填缺失、年级越界、
  学期/类型/学科枚举非法、背诵范围格式无法解析 —— 一律 `SystemExit(1)` 并给出可改的提示。
- 不硬编码任何路径：输入 / 输出 / 默认元数据全部走命令行参数。
- 两种 Word 结构都支持：**表格**（推荐，1:1 映射）与**标签块**（自由段落）。

支持的 Word 结构
----------------
1) 表格（推荐）
   - 文档里每个表格独立解析：首行=表头（列名），其后每行=一条记录。
   - 表头列名可用别名（见 `COLUMN_ALIASES`），如「单元→单元名」「标题→课文标题」
     「正文→段落正文」「en→英文」。
   - 文档里所有表格的行**累加**到同一个 xlsx（例如一个单元一个表）。
   - 若表格缺「学科/年级/学期/单元名」，用 `--subject/--grade/--semester/--unit` 补全。

2) 标签块（无表格时回退）
   - 整篇文档按「空段落」切成若干记录；每条记录内每行形如 `列名：值`（中/英文冒号均可）。
   - 一条只含共享元数据（学科/年级/学期/单元名/单元序号）的记录会被当作**全局默认值**，
     应用到其后的所有记录；记录自身也可覆盖。
   - 无冒号的续行会**拼接到当前字段**（用于「段落正文」跨多段的正文）。

不支持 / 会报错的结构
---------------------
- 文本框、页眉页脚、文本框内表格、图文混排里的文字框（python-docx 读不到）。
- 嵌套表格、合并单元格跨多列的取值（仅取该单元格文本，跨列语义不展开）。
- 表格里的「单元格内换行」会保留为换行符，但通常没问题。
- 一个 docx 同时混用「表格 + 标签块」且希望二者合并：默认表格优先（auto 模式），
  标签块仅在文档**没有任何表格**时启用；若确有混合需求，请拆成两个 docx。

用法
----
    python word2excel.py INPUT.docx OUTPUT.xlsx --type words
    python word2excel.py INPUT.docx OUTPUT.xlsx --type texts
    python word2excel.py INPUT.docx OUTPUT.xlsx --type words \
        --subject 英语 --grade 3 --semester 上学期 --unit "Unit 1"
    python word2excel.py INPUT.docx OUTPUT.xlsx --type texts --format block

详见同目录 `README.md`。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    from docx import Document
except ImportError:  # pragma: no cover - 依赖缺失时给出明确指引
    sys.stderr.write(
        "缺少依赖 python-docx。请在 backend/.venv 中安装：\n"
        "  env -u PYTHONPATH backend/.venv/Scripts/python.exe -m pip install python-docx\n"
    )
    sys.exit(2)

from openpyxl import Workbook


# ===========================================================================
# 一、模板契约（与 backend/app/services/import_service.py 严格一致）
# ===========================================================================

WORDS_HEADERS: list[str] = [
    "学科", "年级", "学期", "单元名", "单元序号", "类型", "英文", "中文",
    "可接受答案", "音标", "词性", "例句英", "例句中", "是否拼读",
]
TEXTS_HEADERS: list[str] = [
    "学科", "年级", "学期", "单元名", "课文标题", "作者", "段落正文", "背诵范围",
]

WORDS_REQUIRED: list[str] = ["学科", "年级", "学期", "单元名", "英文", "中文"]
TEXTS_REQUIRED: list[str] = ["学科", "年级", "学期", "单元名", "课文标题", "段落正文"]

# 仅作「全局默认」的字段（标签块模式下单独成段时视为元数据）
METADATA_FIELDS = {"学科", "年级", "学期", "单元名", "单元序号"}

# 枚举归一化目标（与导入模板下拉选项一致）：用中文展示值，导入端两种都收。
_SUBJECT_TO_CN = {"语文": "语文", "中文": "语文", "chinese": "语文", "c": "语文",
                  "英语": "英语", "英文": "英语", "english": "英语", "e": "英语"}
_SEMESTER_TO_CN = {"上学期": "上学期", "上": "上学期", "first": "上学期", "1": "上学期",
                   "下学期": "下学期", "下": "下学期", "second": "下学期", "2": "下学期"}
_TYPE_TO_CN = {"单词": "单词", "word": "单词",
               "短语": "短语", "phrase": "短语",
               "句子": "句子", "sentence": "句子"}
_TRUE = {"是", "true", "1", "y", "yes", "需要", "要", "√", "✓", "t"}
_FALSE = {"否", "false", "0", "n", "no", "不需要", "不用", "×", "x", "f"}
_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}

# 列名别名 → 规范列名（用于 Word 表格表头 / 标签块行首匹配）
COLUMN_ALIASES: dict[str, str] = {
    # 学科
    "学科": "学科", "subject": "学科", "科目": "学科", "课程": "学科",
    # 年级
    "年级": "年级", "grade": "年级", "gra de": "年级",
    # 学期
    "学期": "学期", "semester": "学期",
    # 单元名
    "单元名": "单元名", "单元": "单元名", "unit": "单元名", "unit_name": "单元名",
    "单元标题": "单元名", "单元名称": "单元名",
    # 单元序号
    "单元序号": "单元序号", "order_no": "单元序号", "单元顺序": "单元序号", "单元排序": "单元序号",
    # 类型
    "类型": "类型", "type": "类型", "词类": "类型", "词条类型": "类型",
    # 英文
    "英文": "英文", "en": "英文", "单词": "英文", "word": "英文", "词条": "英文", "英文单词": "英文",
    # 中文
    "中文": "中文", "cn": "中文", "释义": "中文", "意思": "中文", "翻译": "中文", "中文释义": "中文",
    # 可接受答案
    "可接受答案": "可接受答案", "accepted": "可接受答案", "近义词": "可接受答案", "可接受": "可接受答案",
    # 音标
    "音标": "音标", "phonetic": "音标", "音标标注": "音标",
    # 词性
    "词性": "词性", "pos": "词性", "词性标注": "词性",
    # 例句英
    "例句英": "例句英", "example_en": "例句英", "例句": "例句英", "英文例句": "例句英",
    # 例句中
    "例句中": "例句中", "example_cn": "例句中", "例句翻译": "例句中", "中文例句": "例句中",
    # 是否拼读
    "是否拼读": "是否拼读", "require_spell": "是否拼读", "拼读": "是否拼读", "是否拼音": "是否拼读",
    # 课文标题
    "课文标题": "课文标题", "标题": "课文标题", "title": "课文标题", "课文名": "课文标题",
    "篇名": "课文标题", "课文": "课文标题",
    # 作者
    "作者": "作者", "author": "作者",
    # 段落正文
    "段落正文": "段落正文", "正文": "段落正文", "content": "段落正文", "课文内容": "段落正文",
    "全文": "段落正文", "课文正文": "段落正文",
    # 背诵范围
    "背诵范围": "背诵范围", "recite_scope": "背诵范围", "背诵段": "背诵范围", "范围": "背诵范围",
}


# ===========================================================================
# 二、归一化（对齐现有导入校验）
# ===========================================================================

class ConvertError(Exception):
    """转换期错误：统一以 SystemExit(1) 退出并带可改提示。"""


def _norm_key(text: str) -> str:
    """把列名/标签归一为小写、去空格、转半角，便于别名匹配。"""
    t = text.strip().lower()
    # 简单全角→半角（仅 ASCII 可视段）
    out = []
    for ch in t:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        elif code == 0x3000:
            code = 0x20
        out.append(chr(code))
    return "".join(out).replace(" ", "")


# 构建「归一化别名 → 规范列名」反向表（一次）
_ALIAS_LOOKUP: dict[str, str] = {_norm_key(k): v for k, v in COLUMN_ALIASES.items()}


def match_column(label: str) -> str | None:
    """把表格表头 / 标签块行首匹配到规范列名；不认识返回 None。"""
    return _ALIAS_LOOKUP.get(_norm_key(label))


def normalize_subject(raw: str) -> str:
    key = _norm_key(raw)
    val = _SUBJECT_TO_CN.get(key)
    if val is None:
        raise ConvertError(f"学科「{raw}」非法，应为 语文/英语（或 chinese/english）")
    return val


def normalize_grade(raw: str) -> int:
    t = (raw or "").strip()
    if t == "":
        raise ConvertError("年级不能为空")
    num = None
    m = re.search(r"\d+", t)
    if m:
        num = int(m.group())
    else:
        # 支持「三年级」「三」这类中文数字（先匹配长的，避免『十』类误判）
        for cn in sorted(_CN_NUM, key=len, reverse=True):
            if cn in t:
                num = _CN_NUM[cn]
                break
    if num is None or num < 1 or num > 9:
        raise ConvertError(f"年级「{raw}」非法，应为 1–9（可写 三年级 / grade 3）")
    return num


def normalize_semester(raw: str) -> str:
    key = _norm_key(raw)
    val = _SEMESTER_TO_CN.get(key)
    if val is None:
        raise ConvertError(f"学期「{raw}」非法，应为 上学期/下学期（或 first/second）")
    return val


def normalize_type(raw: str) -> str:
    key = _norm_key(raw)
    val = _TYPE_TO_CN.get(key)
    if val is None:
        raise ConvertError(f"类型「{raw}」非法，应为 单词/短语/句子（或 word/phrase/sentence）")
    return val


def normalize_spell(raw: str | None, default: bool) -> str:
    if raw is None or str(raw).strip() == "":
        return "是" if default else "否"
    key = _norm_key(raw)
    if key in _TRUE:
        return "是"
    if key in _FALSE:
        return "否"
    raise ConvertError(f"是否拼读「{raw}」非法，应为 是/否")


def normalize_int(raw: str | None, *, low: int, high: int, what: str) -> int | None:
    t = (raw or "").strip()
    if t == "":
        return None
    try:
        num = int(float(t))
    except ValueError:
        raise ConvertError(f"{what}「{raw}」非法，应为整数")
    if num < low or num > high:
        raise ConvertError(f"{what}「{raw}」超出范围（{low}–{high}）")
    return num


_SCOPE_ALL = {"全部", "全", "all", "whole", "*", "所有"}


def normalize_scope(raw: str | None) -> str:
    """背诵范围 → 导入端接受的 0-based 逗号列表；「全部/空」→ 空串（表示全部段落）。

    约定（人写 1-based，导入端按 0-based 索引段落）：
      输入「全部」/「全」/ 空      → 空串（后端=全背）
      输入「1,3」                  → "0,2"
      输入「1-3」                  → "0,1,2"（含端点，自动排序去重）
    无法解析的标记直接报错，不静默产出废文件。
    """
    t = (raw or "").strip()
    if t == "" or _norm_key(t) in _SCOPE_ALL:
        return ""
    parts = re.split(r"[，、;,；\s/]+", t)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if p == "":
            continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", p)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            out.extend(str(i - 1) for i in range(a, b + 1))
        elif re.match(r"^\d+$", p):
            out.append(str(int(p) - 1))
        else:
            raise ConvertError(f"背诵范围「{raw}」含无法识别的段落标记「{p}」（应为 1-based 段落号，如 1,2,3 或 1-3，或「全部」）")
    seen: set[str] = set()
    res: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return ",".join(res)


def clean_text(value: Any) -> str:
    """展示型文本：只 trim，不做全角转半角（与导入端 raw_columns 口径一致，
    避免把中文标点改坏）。"""
    if value is None:
        return ""
    return str(value).strip()


# ===========================================================================
# 三、Word 解析
# ===========================================================================

_LABEL_RE = re.compile(r"^([^:：]+)[:：]\s*(.*)$")


def _cell_text(cell: Any) -> str:
    return clean_text(cell.text) if cell is not None else ""


def _paragraphs_text(doc: Any) -> list[str]:
    return [clean_text(p.text) for p in doc.paragraphs]


def parse_tables(doc: Any, kind: str) -> list[dict[str, str]]:
    """解析文档里所有表格，累加为记录列表。"""
    rows: list[dict[str, str]] = []
    for table in doc.tables:
        grid = [[_cell_text(c) for c in row.cells] for row in table.rows]
        if not grid:
            continue
        header_cells = grid[0]
        col_map: dict[int, str] = {}
        for idx, h in enumerate(header_cells):
            if h == "":
                continue
            canon = match_column(h)
            if canon is None:
                raise ConvertError(
                    f"表格表头含无法识别的列名「{h}」。"
                    f"可用列名见 README（如 单元名/课文标题/段落正文/英文/中文…）"
                )
            col_map[idx] = canon
        for r in grid[1:]:
            if all((r[i] if i < len(r) else "") == "" for i in range(len(r))):
                continue  # 整行空跳过
            rec: dict[str, str] = {}
            for idx, canon in col_map.items():
                val = r[idx] if idx < len(r) else ""
                if val != "":
                    rec[canon] = val
            if rec:
                rows.append(rec)
    return rows


def parse_blocks(doc: Any, kind: str) -> list[dict[str, str]]:
    """标签块模式：空段落切分记录，行内 `列名：值`。"""
    paras = _paragraphs_text(doc)
    records: list[list[str]] = []
    buf: list[str] = []
    for p in paras:
        if p == "":
            if buf:
                records.append(buf)
                buf = []
        else:
            buf.append(p)
    if buf:
        records.append(buf)

    out: list[dict[str, str]] = []
    for rec in records:
        current: str | None = None
        item: dict[str, str] = {}
        for line in rec:
            m = _LABEL_RE.match(line)
            if m:
                label, value = m.group(1).strip(), m.group(2).strip()
                canon = match_column(label)
                if canon is None:
                    raise ConvertError(
                        f"无法识别的字段「{label}」。可用字段见 README；"
                        f"若这是「段落正文」的续行，请确保它不带「列名：」前缀"
                    )
                current = canon
                item[canon] = value
            else:
                # 续行：拼到当前字段（用于多段正文）
                if current is not None:
                    sep = "\n" if item[current] else ""
                    item[current] = item[current] + sep + line.strip()
                # 首行就不是标签行则忽略（避免把标题当数据）
        if item:
            out.append(item)
    return out


def split_metadata(records: list[dict[str, str]]) -> tuple[dict[str, str], list[dict[str, str]]]:
    """标签块模式：把「仅含元数据的记录」拆成全局默认，其余为数据记录。"""
    defaults: dict[str, str] = {}
    data: list[dict[str, str]] = []
    for rec in records:
        is_meta = all(k in METADATA_FIELDS for k in rec.keys())
        if is_meta and rec:
            defaults.update(rec)
        else:
            data.append(rec)
    return defaults, data


# ===========================================================================
# 四、校验 + 归一化 + 写出
# ===========================================================================

def build_rows(records: list[dict[str, str]], kind: str,
               defaults: dict[str, str]) -> list[dict[str, str]]:
    headers = WORDS_HEADERS if kind == "words" else TEXTS_HEADERS
    # 注意：import_service 在逐行校验里要求「类型」非空（缺则报「类型不能为空」），
    # 虽不在 WORDS_REQUIRED 的表头检查清单中，但产出可导入文件就必须把它当必需。
    required = (WORDS_REQUIRED + ["类型"]) if kind == "words" else TEXTS_REQUIRED

    out: list[dict[str, str]] = []
    for i, rec in enumerate(records):
        merged = {**defaults, **rec}  # 默认值打底，记录自身可覆盖
        row: dict[str, str] = {}

        # 学科 / 年级 / 学期 / 单元名 / 单元序号
        subj = merged.get("学科")
        if subj is not None and subj != "":
            row["学科"] = normalize_subject(subj)
        grade = merged.get("年级")
        if grade is not None and str(grade).strip() != "":
            row["年级"] = str(normalize_grade(str(grade)))
        sem = merged.get("学期")
        if sem is not None and sem != "":
            row["学期"] = normalize_semester(sem)
        unit = merged.get("单元名")
        if unit is not None and unit != "":
            row["单元名"] = unit
        uorder = merged.get("单元序号")
        if uorder is not None and str(uorder).strip() != "":
            row["单元序号"] = str(normalize_int(str(uorder), low=0, high=9999, what="单元序号"))

        if kind == "words":
            wtype = merged.get("类型")
            if wtype is not None and wtype != "":
                row["类型"] = normalize_type(wtype)
            en = merged.get("英文")
            if en is not None and en != "":
                row["英文"] = en
            cn = merged.get("中文")
            if cn is not None and cn != "":
                row["中文"] = cn
            if merged.get("可接受答案") is not None:
                row["可接受答案"] = merged["可接受答案"]
            if merged.get("音标") is not None:
                row["音标"] = merged["音标"]
            if merged.get("词性") is not None:
                row["词性"] = merged["词性"]
            if merged.get("例句英") is not None:
                row["例句英"] = merged["例句英"]
            if merged.get("例句中") is not None:
                row["例句中"] = merged["例句中"]
            spell_default = (row.get("类型") == "单词")
            row["是否拼读"] = normalize_spell(merged.get("是否拼读"), spell_default)
        else:
            title = merged.get("课文标题")
            if title is not None and title != "":
                row["课文标题"] = title
            if merged.get("作者") is not None:
                row["作者"] = merged["作者"]
            body = merged.get("段落正文")
            if body is not None and body != "":
                row["段落正文"] = body
            row["背诵范围"] = normalize_scope(merged.get("背诵范围"))

        # 必填校验
        missing = [h for h in required if not row.get(h)]
        if missing:
            where = f"第 {i + 1} 条记录"
            raise ConvertError(f"{where}缺少必需字段：{'、'.join(missing)}。请补全或用 --subject/--grade/--semester/--unit 提供默认值")

        # 按模板列序整理（缺失列留空串）
        out.append({h: row.get(h, "") for h in headers})
    return out


def write_xlsx(headers: list[str], rows: list[dict[str, str]], out_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "英语词条" if headers is WORDS_HEADERS else "语文课文"
    ws.append(list(headers))
    from openpyxl.styles import Alignment, Font, PatternFill
    fill = PatternFill("solid", fgColor="DDEBF7")
    font = Font(bold=True)
    for cell in ws[1]:
        cell.font = font
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for r in rows:
        ws.append([r.get(h, "") for h in headers])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


# ===========================================================================
# 五、入口
# ===========================================================================

def convert(input_path: Path, output_path: Path, *, kind: str,
            fmt: str = "auto",
            defaults: dict[str, str] | None = None) -> int:
    if not input_path.exists():
        raise ConvertError(f"输入文件不存在：{input_path}")
    defaults = defaults or {}
    doc = Document(str(input_path))

    has_tables = len(doc.tables) > 0
    if fmt == "auto":
        use_tables = has_tables
    elif fmt == "table":
        use_tables = True
    else:
        use_tables = False

    if use_tables:
        records = parse_tables(doc, kind)
    else:
        raw = parse_blocks(doc, kind)
        meta, data = split_metadata(raw)
        # 标签块模式下，CLI 默认值优先级最高，其次文档内元数据，其次记录内字段
        cli_meta = {k: v for k, v in defaults.items() if k in METADATA_FIELDS}
        merged_defaults = {**meta, **cli_meta}
        records = data
        defaults = merged_defaults

    if not records:
        raise ConvertError("未从文档中解析出任何数据行（检查表格/标签块格式，见 README）")

    rows = build_rows(records, kind, defaults)
    write_xlsx(WORDS_HEADERS if kind == "words" else TEXTS_HEADERS, rows, output_path)
    return len(rows)


def _build_defaults(args: argparse.Namespace) -> dict[str, str]:
    d: dict[str, str] = {}
    if args.subject:
        d["学科"] = normalize_subject(args.subject)
    if args.grade:
        d["年级"] = str(normalize_grade(args.grade))
    if args.semester:
        d["学期"] = normalize_semester(args.semester)
    if args.unit:
        d["单元名"] = args.unit
    if args.unit_order is not None:
        d["单元序号"] = str(normalize_int(str(args.unit_order), low=0, high=9999, what="单元序号"))
    return d


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="word2excel.py",
        description="把 Word(.docx) 转换成符合背记系统 Excel 导入模板的 .xlsx（WP-2B）",
    )
    parser.add_argument("input", help="输入 .docx 路径")
    parser.add_argument("output", help="输出 .xlsx 路径")
    parser.add_argument("--type", required=True, choices=["words", "texts"],
                        help="words=英语词条(14列) / texts=语文课文(8列)")
    parser.add_argument("--format", default="auto", choices=["auto", "table", "block"],
                        help="auto=有表格用表格否则标签块；table=强制表格；block=强制标签块")
    parser.add_argument("--subject", help="默认学科（语文/英语），用于补全缺列")
    parser.add_argument("--grade", help="默认年级（1-9），用于补全缺列")
    parser.add_argument("--semester", help="默认学期（上学期/下学期），用于补全缺列")
    parser.add_argument("--unit", help="默认单元名，用于补全缺列")
    parser.add_argument("--unit-order", type=int, help="默认单元序号", default=None)
    args = parser.parse_args(argv)

    try:
        defaults = _build_defaults(args)
        n = convert(Path(args.input), Path(args.output),
                    kind=args.type, fmt=args.format, defaults=defaults)
    except ConvertError as e:
        sys.stderr.write(f"[转换失败] {e}\n")
        return 1
    except Exception as e:  # pragma: no cover - 兜底，避免堆栈吓到使用者
        sys.stderr.write(f"[转换失败] 未预期错误：{e}\n")
        return 1

    kind_cn = "英语词条" if args.type == "words" else "语文课文"
    print(f"成功：从 {args.input} 转换 {n} 行 → {args.output}（{kind_cn}模板）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
