"""Excel 读写与模板生成 —— 10a F14。

三个能力：
- `read_xlsx()`：xlsx → `list[dict]`，**按列名匹配**而非固定列序（用户可能调换列）
- `write_xlsx()`：`headers` + `rows` → xlsx 字节流
- `build_template()`：生成带**数据有效性下拉**的模板（BE-02 / BE-03 共用）

容错约定（10a F14 / F（Excel 解析容错））：
- 字符串去首尾空格
- 全角数字 / 字母 / 括号 → 半角
- 空行跳过
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import quote

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

# 模板表头样式
_HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
_HEADER_FONT = Font(bold=True)

# 下拉有效性的作用行数上限
_VALIDATION_ROWS = 2000


def to_halfwidth(text: str) -> str:
    """全角字符转半角（数字、字母、括号、空格等）。"""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            code = 0x20
        elif 0xFF01 <= code <= 0xFF5E:  # 全角 ! ~
            code -= 0xFEE0
        out.append(chr(code))
    return "".join(out)


def clean_cell(value: Any) -> Any:
    """单元格值清洗：字符串 trim + 全角转半角；空串归一为 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        s = to_halfwidth(value).strip()
        return s if s else None
    return value


def clean_text(value: Any) -> str:
    """清洗并强制转字符串（None → 空串）。"""
    v = clean_cell(value)
    return "" if v is None else str(v)


def clean_raw(value: Any) -> Any:
    """**只 trim、不转全角**的清洗（展示型文本用）。

    为什么需要它：`clean_cell` 的 `to_halfwidth` 覆盖整个全角 ASCII 段
    （`U+FF01`~`U+FF5E`），把中文标点一并转了 ——
    `我与父亲，不相见` 会变成 `我与父亲,不相见`，`（节选）` 会变成 `(节选)`。

    对**标识型字段**（学号、英文、枚举、单元名）这是好事：中文输入法下
    全角数字/字母是高频输入错误，要归一才能匹配。
    但对**展示型字段**（课文正文、中文释义、例句）是纯副作用 ——
    《数据库设计》§3.5 明确 `content_raw` 的用途是「保留标点与换行，用于展示」。

    调用方通过 `read_xlsx(raw_columns=...)` 声明哪些列属于后者。
    """
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s if s else None
    return value


def read_xlsx(
    source: bytes | Path | str,
    *,
    header_row: int = 1,
    sheet: str | None = None,
    raw_columns: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """读取 xlsx 为 `list[dict]`，键为首行（表头行）列名。

    Args:
        source: 文件字节流或路径。
        header_row: 表头所在行号（1 起）。
        sheet: 工作表名；None 表示活动表。
        raw_columns: 这些列**只 trim、不转全角**（展示型文本，见 `clean_raw`）。
            别名叫法也可以 —— 传入的名字本身也会被归一后比较。

    Raises:
        ValueError: 文件不是合法 xlsx（调用方应转成 `40001`）。
    """
    if isinstance(source, (bytes, bytearray)):
        wb = load_workbook(BytesIO(source), read_only=True, data_only=True)
    else:
        wb = load_workbook(str(source), read_only=True, data_only=True)

    # 表头可能被写成全角，比对前先归一
    raw_set = {to_halfwidth(str(c)).strip() for c in (raw_columns or ())}

    try:
        ws = wb[sheet] if sheet else wb.active
        if ws is None:
            return []

        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < header_row:
            return []

        header_values = rows[header_row - 1]
        headers = [clean_text(h) for h in header_values]

        result: list[dict[str, Any]] = []
        for row in rows[header_row:]:
            # 整行为空则跳过
            if all(clean_text(c) == "" for c in row):
                continue
            item: dict[str, Any] = {}
            for idx, header in enumerate(headers):
                if not header:
                    continue
                raw = row[idx] if idx < len(row) else None
                item[header] = clean_raw(raw) if header in raw_set else clean_cell(raw)
            result.append(item)
        return result
    finally:
        wb.close()


def write_xlsx(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    sheet_name: str = "Sheet1",
) -> bytes:
    """生成 xlsx 字节流（首行加粗并填充底色）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name

    ws.append(list(headers))
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row in rows:
        ws.append(list(row))

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_template(
    headers: Sequence[str],
    *,
    validations: dict[str, Sequence[str]] | None = None,
    sheet_name: str = "Sheet1",
    column_widths: dict[str, int] | None = None,
) -> bytes:
    """生成导入模板：表头 + 下拉有效性。

    Args:
        headers: 列名列表。
        validations: `{列名: [可选值, ...]}`，为该列第 2 行起添加下拉。
        sheet_name: 工作表名。
        column_widths: `{列名: 宽度}`。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name

    ws.append(list(headers))
    ws.freeze_panes = "A2"
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # 下拉有效性
    for col_name, options in (validations or {}).items():
        if col_name not in headers or not options:
            continue
        col_letter = get_column_letter(list(headers).index(col_name) + 1)
        joined = ",".join(str(o) for o in options)
        dv = DataValidation(
            type="list",
            formula1=f'"{joined}"',
            allow_blank=True,
            showDropDown=False,  # openpyxl 语义取反：False 才是「显示下拉箭头」
        )
        ws.add_data_validation(dv)
        dv.add(f"{col_letter}2:{col_letter}{_VALIDATION_ROWS}")

    for col_name, width in (column_widths or {}).items():
        if col_name in headers:
            ws.column_dimensions[get_column_letter(list(headers).index(col_name) + 1)].width = width

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def xlsx_response_headers(filename: str) -> dict[str, str]:
    """下载 xlsx 时的响应头（含中文文件名的 RFC 5987 编码）。"""
    quoted = quote(filename)
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
