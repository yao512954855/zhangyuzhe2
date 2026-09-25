"""内容导入服务 —— WP-BE-03 F11 ~ F16（接口设计 §6.1 ~ §6.5）。

管理者用 Excel 把词条 / 课文灌进来。**两步式**：
`preview` 只解析校验（存 `preview_store`，TTL 30 分钟，BE-01 提供、BE-02/BE-03 共用），
`commit` 凭一次性 token 入库。

---

### 一、为什么非要两步

1. **单元可能不存在**：文件里出现未建的单元时，必须让管理者先确认「要不要自动建」，
   不能默认给人凭空造一堆单元（U3 就是验证这个）。
2. **背诵范围要人工勾选**：段落号让管理者手填 Excel 太容易错，
   先解析出 `paragraphs` 回传前端渲染勾选框，再把调整后的结果回传 commit（§6.4）。
3. **错误报告**：校验结果里带行号与原因，管理者改完 Excel 再来一次，不用重头猜。

### 二、Excel 解析的容错（10a F）

- **表头按列名匹配，不按列序** —— 用户会调换列顺序，写死列号必炸。
- 字符串 trim + 全角转半角（`core/excel.py` 已做）。
- 枚举列**中英文都收**：`单词` / `word`、`上学期` / `first`。
- 单文件 10MB（`40004`，接口层拦），行数上限 5000（本层拦）。

### 三、词条 upsert（10a C）

| 情形 | `overwrite` | `skip` |
|---|---|---|
| 库中已有 `(unit_id, en)` | 更新内容字段，**`status` 不覆盖** | 完全跳过，计入 `skipped` |

**`status` 不覆盖**是硬要求：管理者可能特意停用了某个词条，
重导一次文件就把它悄悄启用，等于绕过停用决策。

**绝不物理删除已有内容** —— 那会打断 `answers` 的历史引用。

### 四、判定「重复」的两种，别混（BE-02 同款裁决）

| 概念 | 含义 | 计入 `invalid`？ |
|---|---|---|
| `duplicated` | **库里已存在**，将走更新 | ❌ 否 |
| 文件内重复 | 同一文件里 `(单元, 英文)` 出现两次 | ✅ 是（后者报错） |

《接口设计》§4.4 的示例把这两者混在一起（`duplicated=1` 却给 `created=43`），
按需求 §4.2 与 10a F8 的语义实现，**已列入待确认项**。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.core.audit import write_audit
from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode
from app.core.excel import build_template, read_xlsx, write_xlsx
from app.core.logging import get_logger
from app.core.preview_store import pop as preview_pop
from app.core.preview_store import put as preview_put
from app.schemas.content import dump_accepted
from app.services import pinyin_service

logger = get_logger(__name__)

#: 单文件行数上限（10a F：「超行数（建议 5000 行）给出明确提示」）
MAX_ROWS = 5000

_PREVIEW_KIND_WORDS = "words_import"
_PREVIEW_KIND_TEXTS = "texts_import"

# ---------------------------------------------------------------------------
# 模板列定义（F11 —— 列名即契约，改动要同步前端与文档）
# ---------------------------------------------------------------------------

WORDS_HEADERS: list[str] = [
    "学科",
    "年级",
    "学期",
    "单元名",
    "单元序号",
    "类型",
    "英文",
    "中文",
    "可接受答案",
    "音标",
    "词性",
    "例句英",
    "例句中",
    "是否拼读",
]

TEXTS_HEADERS: list[str] = [
    "学科",
    "年级",
    "学期",
    "单元名",
    "课文标题",
    "作者",
    "段落正文",
    "背诵范围",
]

_TEXTS_EXTRA_HEADERS: list[str] = ["单元序号"]

#: 必需列（缺任一 → `40002`，提示下载最新模板）
WORDS_REQUIRED = ["学科", "年级", "学期", "单元名", "英文", "中文"]
TEXTS_REQUIRED = ["学科", "年级", "学期", "单元名", "课文标题", "段落正文"]

# ---------------------------------------------------------------------------
# 展示型列：**只 trim、不转全角**
#
# 「全角转半角」是为了救「中文输入法下打出的全角学号 / 英文 / 枚举」——
# 那是归一化，用于**匹配**。而中文释义、例句、课文正文不做匹配，
# 转了只会把中文标点改坏（`教室（改）` → `教室(改)`、`，` → `,`），
# 与《数据库设计》§3.5「`content_raw` 保留标点用于展示」直接冲突。
# ---------------------------------------------------------------------------

RAW_COLUMNS_WORDS: frozenset[str] = frozenset({"中文", "例句英", "例句中", "可接受答案"})
RAW_COLUMNS_TEXTS: frozenset[str] = frozenset({"课文标题", "作者", "段落正文"})

# ---------------------------------------------------------------------------
# 枚举映射（中英文都收 —— 导入的人可能填「英语」，也可能填中文）
# ---------------------------------------------------------------------------

_SUBJECT_MAP = {
    "语文": "chinese",
    "英语": "english",
    "chinese": "chinese",
    "english": "english",
    "中文": "chinese",
    "英文": "english",
}

_SEMESTER_MAP = {
    "上学期": "first",
    "下学期": "second",
    "上": "first",
    "下": "second",
    "first": "first",
    "second": "second",
    "1": "first",
    "2": "second",
}

_TYPE_MAP = {
    "单词": "word",
    "短语": "phrase",
    "句子": "sentence",
    "word": "word",
    "phrase": "phrase",
    "sentence": "sentence",
}

_TRUE_VALUES = {"是", "true", "1", "y", "yes", "需要", "要", "√", "✓"}
_FALSE_VALUES = {"否", "false", "0", "n", "no", "不需要", "不用", "×", "x"}


# ---------------------------------------------------------------------------
# 解析小工具
# ---------------------------------------------------------------------------


def _col(row: dict[str, Any], *names: str) -> Any:
    """按列名取值，支持多个候选名。"""
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def _text(value: Any) -> str:
    """取值并转字符串（已由 `core/excel.py` 做过 trim 与全角转半角）。"""
    return "" if value is None else str(value).strip()


def _parse_int(value: Any, *, low: int, high: int) -> int | None:
    """解析整数并校验范围。非法返回 None。

    Excel 里 `3.0` 会被读成 float，`３` 全角已由 `core/excel.py` 转半角。
    """
    if value is None or value == "":
        return None
    try:
        num = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    if num < low or num > high:
        return None
    return num


def _parse_bool(value: Any, default: bool) -> bool | None:
    """解析「是否拼读」这类开关。无法识别返回 None（由调用方报错）。"""
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return None


def _split_accepted(value: Any) -> list[str]:
    """「可接受答案」列 → 列表。

    分隔符收 `|` / `,` / `，` / `、` / `;` / `；` —— 用户不会统一用哪个。
    """
    text = _text(value)
    if not text:
        return []
    for sep in ("|", "；", ";", "、", "，", ","):
        text = text.replace(sep, "|")
    return [p.strip() for p in text.split("|") if p.strip()]


def _parse_scope_cell(value: Any) -> list[int] | None:
    """「背诵范围」列 → 段落下标数组。空单元格返回 `None`（= 全部）。"""
    text = _text(value)
    if not text:
        return None
    for sep in ("，", "、", ";", "；", " ", "/"):
        text = text.replace(sep, ",")
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(float(part)))
        except ValueError:
            return None  # 非法格式，让调用方报错
    return sorted(set(out))


def _unit_index() -> dict[tuple[str, int, str, str], int]:
    """全量单元索引 `{(subject, grade, semester, name): id}`。

    单元表只有几十到几百行，一次全取比按组合拼 SQL 更简单也更稳
    （组合查询要处理「部分字段相同」的匹配，容易漏）。
    """
    with read_conn() as conn:
        rows = conn.execute("SELECT id, subject, grade, semester, name FROM units").fetchall()
    return {
        (r["subject"], int(r["grade"]), r["semester"], r["name"]): int(r["id"]) for r in rows
    }


def _check_rows_limit(rows: list[dict[str, Any]]) -> None:
    if len(rows) > MAX_ROWS:
        raise BizError(
            ErrorCode.IMPORT_VALIDATION_FAILED,
            f"文件有 {len(rows)} 行，超过单次上限 {MAX_ROWS} 行，请拆分成多个文件",
            data={"row_count": len(rows), "max_rows": MAX_ROWS},
        )


def _load_xlsx(raw: bytes, *, raw_columns: frozenset[str] | None = None) -> list[dict[str, Any]]:
    """把上传字节读成行列表。非法 xlsx → `40001`（U16）。

    `raw_columns` 见 `core/excel.py::clean_raw` 的说明 —— 展示型文本
    （课文正文 / 中文释义 / 例句）不能做全角转半角，否则中文标点会被改掉。
    """
    try:
        return read_xlsx(raw, raw_columns=raw_columns)
    except BizError:
        raise
    except Exception as exc:  # noqa: BLE001 - openpyxl 对损坏文件抛的异常类型不稳定
        logger.warning("xlsx 解析失败：%s", exc)
        raise BizError(
            ErrorCode.FILE_FORMAT_INVALID,
            "文件打不开，请确认是 Excel（.xlsx）格式，或重新下载模板再填",
        ) from exc


def _check_headers(rows: list[dict[str, Any]], required: list[str]) -> None:
    """校验必需列存在（U17）。

    空文件与「缺列」都归到 `40002`，但**提示语要分开**：
    管理者收到的如果是「请下载最新模板」，而他其实只是没填数据行，
    会去下模板重填一遍，白费功夫。
    """
    present: set[str] = set()
    for row in rows:
        present.update(row.keys())
    if not present:
        raise BizError(
            ErrorCode.TEMPLATE_COLUMN_MISSING,
            "文件里没有可读的内容，请确认已按模板填好数据行（第 1 行是表头，不要删）",
            data={"empty_file": True},
        )
    missing = [h for h in required if h not in present]
    if missing:
        raise BizError(
            ErrorCode.TEMPLATE_COLUMN_MISSING,
            f"文件缺少必需的列：{'、'.join(missing)}。请下载最新模板后重新填写",
            data={"missing_columns": missing},
        )


def _expired() -> BizError:
    return BizError(
        ErrorCode.IMPORT_VALIDATION_FAILED,
        "导入预览已失效（有效期 30 分钟），请重新上传文件",
        data={"reason": "preview_token_expired"},
    )


# ---------------------------------------------------------------------------
# 主服务
# ---------------------------------------------------------------------------


class ImportService:
    """词条 / 课文的模板、预览、入库。"""

    # ================================================================== 模板

    @staticmethod
    def words_template() -> bytes:
        """`GET /api/import/template/words`（10a F11）。带 4 个下拉有效性（U1）。"""
        return build_template(
            WORDS_HEADERS,
            validations={
                "学科": ["语文", "英语"],
                "年级": [str(i) for i in range(1, 10)],
                "学期": ["上学期", "下学期"],
                "类型": ["单词", "短语", "句子"],
                "是否拼读": ["是", "否"],
            },
            sheet_name="英语词条",
            column_widths={
                "学科": 10,
                "年级": 8,
                "学期": 10,
                "单元名": 22,
                "单元序号": 10,
                "类型": 10,
                "英文": 20,
                "中文": 18,
                "可接受答案": 24,
                "音标": 20,
                "词性": 10,
                "例句英": 30,
                "例句中": 30,
                "是否拼读": 10,
            },
        )

    @staticmethod
    def texts_template() -> bytes:
        """`GET /api/import/template/texts`（10a F11）。

        「背诵范围」列**留空即为全部**（§6.1「留空表示全部」）——
        提示语写进表头注释式宽度，减少管理者填错段号的概率。
        """
        return build_template(
            TEXTS_HEADERS,
            validations={
                "学科": ["语文", "英语"],
                "年级": [str(i) for i in range(1, 10)],
                "学期": ["上学期", "下学期"],
            },
            sheet_name="语文课文",
            column_widths={
                "学科": 10,
                "年级": 8,
                "学期": 10,
                "单元名": 22,
                "课文标题": 22,
                "作者": 14,
                "段落正文": 60,
                "背诵范围": 16,
            },
        )

    # ================================================================== 词条

    @staticmethod
    def words_preview(raw: bytes, *, file_name: str | None = None) -> dict[str, Any]:
        """`POST /api/import/words/preview`（10a F12 / 接口设计 §6.2）。

        返回 `preview_token` + `summary` + `new_units` + `valid_rows` + `invalid_rows`，**不落库**。

        U2：120 行里 2 行非法 → `summary.valid=118`、`invalid=2`，错误行带原因。
        """
        rows = _load_xlsx(raw, raw_columns=RAW_COLUMNS_WORDS)
        _check_rows_limit(rows)
        _check_headers(rows, WORDS_REQUIRED)

        unit_index = _unit_index()

        valid_rows: list[dict[str, Any]] = []
        invalid_rows: list[dict[str, Any]] = []
        raw_rows: list[list[Any]] = []
        seen_keys: dict[tuple[str, int, str, str, str], int] = {}
        new_units: dict[tuple[str, int, str, str], dict[str, Any]] = {}

        for idx, row in enumerate(rows):
            excel_row = idx + 2  # 第 1 行是表头
            raw_rows.append([_text(row.get(h)) for h in WORDS_HEADERS])
            reasons: list[str] = []

            subject_raw = _text(_col(row, "学科", "subject"))
            subject = _SUBJECT_MAP.get(subject_raw.lower() if subject_raw.isascii() else subject_raw)
            if subject is None:
                reasons.append(
                    f"学科「{subject_raw}」非法，应为 语文/英语"
                    if subject_raw
                    else "学科不能为空"
                )

            grade_raw = _col(row, "年级", "grade")
            grade = _parse_int(grade_raw, low=1, high=9)
            if grade is None:
                reasons.append(
                    f"年级「{_text(grade_raw)}」超出范围（1-9）"
                    if _text(grade_raw)
                    else "年级不能为空"
                )

            semester_raw = _text(_col(row, "学期", "semester"))
            semester = _SEMESTER_MAP.get(
                semester_raw.lower() if semester_raw.isascii() else semester_raw
            )
            if semester is None:
                reasons.append(
                    f"学期「{semester_raw}」非法，应为 上学期/下学期"
                    if semester_raw
                    else "学期不能为空"
                )

            unit_name = _text(_col(row, "单元名", "单元", "unit_name"))
            if not unit_name:
                reasons.append("单元名不能为空")

            type_raw = _text(_col(row, "类型", "type"))
            word_type = _TYPE_MAP.get(type_raw.lower() if type_raw.isascii() else type_raw)
            if word_type is None:
                reasons.append(
                    f"类型「{type_raw}」非法，应为 单词/短语/句子"
                    if type_raw
                    else "类型不能为空"
                )

            en = _text(_col(row, "英文", "en", "单词"))
            if not en:
                reasons.append("英文不能为空")

            cn = _text(_col(row, "中文", "cn", "释义"))
            if not cn:
                reasons.append("中文不能为空")

            accepted = _split_accepted(_col(row, "可接受答案", "accepted"))
            if len(accepted) > 20:
                reasons.append(f"可接受答案有 {len(accepted)} 条，最多 20 条")

            # 是否拼读：默认为「单词=是、短语/句子=否」（数据库设计 §3.4 的建议值）
            default_spell = word_type == "word"
            require_spell = _parse_bool(_col(row, "是否拼读", "require_spell"), default_spell)
            if require_spell is None:
                reasons.append("是否拼读只能填 是/否")

            unit_order_no = _parse_int(_col(row, "单元序号", "order_no"), low=0, high=9999) or 0
            word_order_no = _parse_int(_col(row, "词条序号"), low=0, high=99999) or 0

            # 文件内查重：同 (单元, 英文) 出现两次，后者报错
            if not reasons and subject and grade and semester and unit_name and en:
                key = (subject, grade, semester, unit_name, en.lower())
                if key in seen_keys:
                    reasons.append(f"与第 {seen_keys[key]} 行的单元与英文重复，同一个单元里英文不能重复")
                else:
                    seen_keys[key] = excel_row

            if reasons:
                invalid_rows.append({"row": excel_row, "reason": "；".join(reasons)})
                continue

            assert subject and grade and semester and word_type is not None
            unit_key = (subject, grade, semester, unit_name)
            if unit_key not in unit_index and unit_key not in new_units:
                new_units[unit_key] = {
                    "subject": subject,
                    "grade": grade,
                    "semester": semester,
                    "name": unit_name,
                    "order_no": unit_order_no,
                    "row": excel_row,
                }

            valid_rows.append(
                {
                    "row": excel_row,
                    "subject": subject,
                    "grade": grade,
                    "semester": semester,
                    "unit_name": unit_name,
                    "unit_order_no": unit_order_no,
                    "type": word_type,
                    "en": en,
                    "cn": cn,
                    "accepted": accepted,
                    "phonetic": _text(_col(row, "音标", "phonetic")) or None,
                    "pos": _text(_col(row, "词性", "pos")) or None,
                    "example_en": _text(_col(row, "例句英", "example_en")) or None,
                    "example_cn": _text(_col(row, "例句中", "example_cn")) or None,
                    "require_spell": require_spell,
                    "order_no": word_order_no,
                }
            )

        # 已在库中的行数（将走更新，**不算 invalid**）。
        # 一次 SQL 查完涉及到的单元，**不做逐行 EXISTS** —— 5000 行的文件
        # 逐行查就是 5000 次往返，导入预览会明显卡顿。
        target_unit_ids = {
            unit_index[(item["subject"], item["grade"], item["semester"], item["unit_name"])]
            for item in valid_rows
            if (item["subject"], item["grade"], item["semester"], item["unit_name"]) in unit_index
        }
        existing: set[tuple[int, str]] = set()
        if target_unit_ids:
            ids = list(target_unit_ids)
            placeholders = ",".join("?" * len(ids))
            with read_conn() as conn:
                for r in conn.execute(
                    f"SELECT unit_id, en FROM words WHERE unit_id IN ({placeholders})", ids
                ).fetchall():
                    existing.add((int(r["unit_id"]), r["en"]))

        duplicated = sum(
            1
            for item in valid_rows
            if (
                unit_index.get(
                    (item["subject"], item["grade"], item["semester"], item["unit_name"])
                ),
                item["en"],
            )
            in existing
        )

        token = preview_put(
            {
                "kind": _PREVIEW_KIND_WORDS,
                "file_name": file_name,
                "rows": valid_rows,
                "invalid_rows": invalid_rows,
                "new_units": list(new_units.values()),
                "raw_rows": raw_rows,
                "report_headers": ["原文件行号", "错误原因", *WORDS_HEADERS],
            }
        )

        return {
            "preview_token": token,
            "summary": {
                "total": len(rows),
                "valid": len(valid_rows),
                "invalid": len(invalid_rows),
                "duplicated": duplicated,
            },
            "new_units": list(new_units.values()),
            # 有效行明细（契约 §6.2）：与 `invalid_rows` 对称，让预览页能把
            # 「将入库什么」摆给管理者核对。**只给关键字段** ——
            # `accepted`（最多 20 条/词）+ 例句 + 音标 + 词性留在 token payload 里不入响应，
            # 否则一个几百行的文件响应会膨胀好几倍。
            "valid_rows": [
                {
                    "row": item["row"],
                    "subject": item["subject"],
                    "grade": item["grade"],
                    "semester": item["semester"],
                    "unit_name": item["unit_name"],
                    "type": item["type"],
                    "en": item["en"],
                    "cn": item["cn"],
                }
                for item in valid_rows
            ],
            "invalid_rows": invalid_rows,
            "error_report_url": f"/api/import/report/{token}",
        }

    @staticmethod
    def words_commit(
        *,
        preview_token: str,
        create_missing_units: bool = True,
        on_duplicate: str = "overwrite",
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """`POST /api/import/words/commit`（10a F13 / 接口设计 §6.3）。

        **单事务**：要么整份文件进去，要么一条都不进 ——
        中途失败留半份数据，管理者根本不知道该重导哪些行。

        Returns:
            `{created, updated, skipped, units_created}`。
        """
        payload = preview_pop(preview_token)
        if not payload or payload.get("kind") != _PREVIEW_KIND_WORDS:
            raise _expired()

        rows: list[dict[str, Any]] = payload.get("rows") or []
        new_units: list[dict[str, Any]] = payload.get("new_units") or []

        # U3：不允许自动建单元，但文件里确实有新单元 → 明确报错，**不建单元不落词条**
        if new_units and not create_missing_units:
            names = "、".join(f"{u['name']}（{u['grade']}年级）" for u in new_units[:5])
            raise BizError(
                ErrorCode.IMPORT_VALIDATION_FAILED,
                f"文件里有 {len(new_units)} 个单元尚未创建：{names}。"
                "请先创建这些单元，或勾选「自动创建单元」后重新提交",
                data={"new_units": new_units, "reason": "create_missing_units_false"},
            )

        created = updated = skipped = units_created = 0

        with write_tx() as conn:
            # ---- 1) 补建缺失单元
            unit_id_map: dict[tuple[str, int, str, str], int] = {
                (r["subject"], int(r["grade"]), r["semester"], r["name"]): int(r["id"])
                for r in conn.execute(
                    "SELECT id, subject, grade, semester, name FROM units"
                ).fetchall()
            }
            for unit in new_units:
                key = (unit["subject"], int(unit["grade"]), unit["semester"], unit["name"])
                if key in unit_id_map:
                    continue
                cur = conn.execute(
                    """
                    INSERT INTO units (subject, grade, semester, name, order_no,
                                       status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (unit["subject"], unit["grade"], unit["semester"], unit["name"],
                     int(unit.get("order_no") or 0), utc_now_iso()),
                )
                unit_id_map[key] = int(cur.lastrowid or 0)
                units_created += 1

            # ---- 2) 逐行 upsert
            now = utc_now_iso()
            for item in rows:
                key = (item["subject"], int(item["grade"]), item["semester"], item["unit_name"])
                unit_id = unit_id_map.get(key)
                if unit_id is None:  # 理论上不可达（new_units 已补建）
                    skipped += 1
                    continue

                exist = conn.execute(
                    "SELECT id, status FROM words WHERE unit_id = ? AND en = ?",
                    (unit_id, item["en"]),
                ).fetchone()

                if exist is not None:
                    if on_duplicate == "skip":
                        skipped += 1
                        continue
                    # overwrite：**不覆盖 status**（10a C）
                    conn.execute(
                        """
                        UPDATE words
                           SET type = ?, cn = ?, accepted = ?, phonetic = ?, pos = ?,
                               example_en = ?, example_cn = ?, require_spell = ?,
                               order_no = ?, updated_at = ?
                         WHERE id = ?
                        """,
                        (
                            item["type"],
                            item["cn"],
                            dump_accepted(item["accepted"]),
                            item["phonetic"],
                            item["pos"],
                            item["example_en"],
                            item["example_cn"],
                            1 if item["require_spell"] else 0,
                            item["order_no"],
                            now,
                            int(exist["id"]),
                        ),
                    )
                    updated += 1
                    continue

                conn.execute(
                    """
                    INSERT INTO words (unit_id, type, en, cn, accepted, phonetic, pos,
                                       example_en, example_cn, require_spell, order_no,
                                       status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (
                        unit_id,
                        item["type"],
                        item["en"],
                        item["cn"],
                        dump_accepted(item["accepted"]),
                        item["phonetic"],
                        item["pos"],
                        item["example_en"],
                        item["example_cn"],
                        1 if item["require_spell"] else 0,
                        item["order_no"],
                        now,
                    ),
                )
                created += 1

            write_audit(
                conn,
                user_id=operator_id,
                action="content.import_words",
                target_type="words",
                target_id=None,
                detail={
                    "file_name": payload.get("file_name"),
                    "created": created,
                    "updated": updated,
                    "skipped": skipped,
                    "units_created": units_created,
                    "on_duplicate": on_duplicate,
                },
                ip=ip,
            )

        logger.info(
            "词条导入完成：新增 %s / 更新 %s / 跳过 %s / 新建单元 %s",
            created, updated, skipped, units_created,
        )
        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "units_created": units_created,
        }

    # ================================================================== 课文

    @staticmethod
    def texts_preview(raw: bytes, *, file_name: str | None = None) -> dict[str, Any]:
        """`POST /api/import/texts/preview`（10a F14 / 接口设计 §6.4）。

        做四件事：**段落切分 → 逐段数汉字 → 拼音转换 + 等长校验 → 汇总**。

        `pinyin_check='mismatch'` 的行**不能入库**，但**照常返回** `paragraphs` 与
        失配明细 —— 管理者要看到「第几个字出的问题」才能去改正文（U7）。
        """
        rows = _load_xlsx(raw, raw_columns=RAW_COLUMNS_TEXTS)
        _check_rows_limit(rows)
        _check_headers(rows, TEXTS_REQUIRED)

        unit_index = _unit_index()

        texts: list[dict[str, Any]] = []
        invalid_rows: list[dict[str, Any]] = []
        pinyin_issues: list[dict[str, Any]] = []
        raw_rows: list[list[Any]] = []
        new_units: dict[tuple[str, int, str, str], dict[str, Any]] = {}
        seen_titles: dict[tuple[str, int, str, str, str], int] = {}

        for idx, row in enumerate(rows):
            excel_row = idx + 2
            raw_rows.append([_text(row.get(h)) for h in TEXTS_HEADERS])
            reasons: list[str] = []

            subject_raw = _text(_col(row, "学科", "subject"))
            subject = _SUBJECT_MAP.get(subject_raw.lower() if subject_raw.isascii() else subject_raw)
            if subject is None:
                reasons.append(f"学科「{subject_raw}」非法，应为 语文/英语" if subject_raw else "学科不能为空")

            grade_raw = _col(row, "年级", "grade")
            grade = _parse_int(grade_raw, low=1, high=9)
            if grade is None:
                reasons.append(
                    f"年级「{_text(grade_raw)}」超出范围（1-9）" if _text(grade_raw) else "年级不能为空"
                )

            semester_raw = _text(_col(row, "学期", "semester"))
            semester = _SEMESTER_MAP.get(
                semester_raw.lower() if semester_raw.isascii() else semester_raw
            )
            if semester is None:
                reasons.append(
                    f"学期「{semester_raw}」非法，应为 上学期/下学期" if semester_raw else "学期不能为空"
                )

            unit_name = _text(_col(row, "单元名", "单元", "unit_name"))
            if not unit_name:
                reasons.append("单元名不能为空")

            title = _text(_col(row, "课文标题", "标题", "title"))
            if not title:
                reasons.append("课文标题不能为空")

            body = _text(_col(row, "段落正文", "正文", "content"))
            if not body:
                reasons.append("段落正文不能为空")

            scope_cell = _col(row, "背诵范围", "recite_scope")
            explicit_scope = _parse_scope_cell(scope_cell)
            if _text(scope_cell) and explicit_scope is None:
                reasons.append(f"背诵范围「{_text(scope_cell)}」格式不对，应填段落号如 0,1,2（留空表示全部）")

            if not reasons and subject and grade and semester and unit_name and title:
                key = (subject, grade, semester, unit_name, title)
                if key in seen_titles:
                    reasons.append(f"与第 {seen_titles[key]} 行的单元与标题重复，同一单元里课文标题不能重复")
                else:
                    seen_titles[key] = excel_row

            if reasons:
                invalid_rows.append({"row": excel_row, "reason": "；".join(reasons)})
                continue

            assert subject and grade and semester

            # ---- 段落切分
            para_texts = pinyin_service.split_paragraphs(body)
            if not para_texts:
                invalid_rows.append(
                    {"row": excel_row, "reason": "段落正文里没有可用的文字"}
                )
                continue

            paragraphs = [
                {
                    "idx": i,
                    "text": text,
                    "chars": pinyin_service.count_han(text),
                    "recite": True,
                }
                for i, text in enumerate(para_texts)
            ]

            # ---- 背诵范围（默认全部）
            if explicit_scope is None:
                scope = [p["idx"] for p in paragraphs]
            else:
                out_of_range = [i for i in explicit_scope if i >= len(paragraphs)]
                if out_of_range:
                    invalid_rows.append(
                        {
                            "row": excel_row,
                            "reason": (
                                f"背诵范围里的段落号 {out_of_range} 超出实际段落数"
                                f"（共 {len(paragraphs)} 段）"
                            ),
                        }
                    )
                    continue
                scope = explicit_scope

            # ---- 拼音转换 + 等长校验（只对背诵范围内校验，与入库口径一致）
            build = pinyin_service.build_scoped(para_texts, scope)
            pinyin_check = "ok" if build.ok else "mismatch"
            total_chars = build.length

            # mismatch 的行**照常放进 `texts`**（前端要拿 paragraphs 与位置去显示），
            # 但单独记进 `pinyin_issues`，**不要**再塞进 `invalid_rows` ——
            # 那样 summary.invalid 会把同一行算两次。
            if not build.ok:
                positions_text = "、".join(str(p) for p in build.mismatch_positions[:10])
                pinyin_issues.append(
                    {
                        "row": excel_row,
                        "title": title,
                        "positions": build.mismatch_positions[:20],
                        "reason": (
                            f"正文里有无法拼音化的字（第 {positions_text} 个字），"
                            "语文对齐会在那里漏字，必须替换后再导入"
                        ),
                        "details": build.mismatches[:10],
                    }
                )

            unit_key = (subject, grade, semester, unit_name)
            if unit_key not in unit_index and unit_key not in new_units:
                new_units[unit_key] = {
                    "subject": subject,
                    "grade": grade,
                    "semester": semester,
                    "name": unit_name,
                    "order_no": _parse_int(_col(row, "单元序号"), low=0, high=9999) or 0,
                    "row": excel_row,
                }

            texts.append(
                {
                    "row": excel_row,
                    "subject": subject,
                    "grade": grade,
                    "semester": semester,
                    "unit_name": unit_name,
                    "unit_order_no": _parse_int(_col(row, "单元序号"), low=0, high=9999) or 0,
                    "title": title,
                    "author": _text(_col(row, "作者", "author")) or None,
                    "content_raw": body,
                    "paragraphs": paragraphs,
                    "recite_scope": scope,
                    "pinyin_check": pinyin_check,
                    "total_chars": total_chars,
                    "content_plain": build.plain,
                    "content_pinyin": build.pinyin,
                    "mismatches": build.mismatches[:20],
                    "mismatch_positions": build.mismatch_positions[:20],
                    "ok": bool(build.ok),
                }
            )

        return {
            "preview_token": preview_put(
                {
                    "kind": _PREVIEW_KIND_TEXTS,
                    "file_name": file_name,
                    "texts": texts,
                    "invalid_rows": invalid_rows,
                    "new_units": list(new_units.values()),
                    "raw_rows": raw_rows,
                    "report_headers": ["原文件行号", "错误原因", *TEXTS_HEADERS],
                }
            ),
            # valid 只数「能入库」的行；invalid 含字段错误 + 拼音失配，两者不重叠，
            # 因此恒有 valid + invalid == total（管理者一眼能核对上）
            "summary": {
                "total": len(rows),
                "valid": sum(1 for t in texts if t["ok"]),
                "invalid": len(invalid_rows) + len(pinyin_issues),
            },
            "new_units": list(new_units.values()),
            "texts": [_text_public(t) for t in texts],
            "invalid_rows": invalid_rows,
            "pinyin_issues": pinyin_issues,
        }

    @staticmethod
    def texts_commit(
        *,
        preview_token: str,
        scopes: dict[int, list[int]] | None = None,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """`POST /api/import/texts/commit`（10a F15 / 接口设计 §6.5）。

        `scopes` 是前端回传的调整结果，**按 Excel 行号索引**（`{row: [0,1]}`）。
        用行号而不是数组下标：preview 里不合法的行不会出现在提交数组里，
        用下标会把范围张冠李戴到别的课文上 —— 这是本接口最容易写错的地方。

        改范围后 `total_chars` / `content_plain` / `content_pinyin` **全部重算**（U8）：
        `content_plain` 是对齐基准，只装背诵范围内的字，范围变了它就必须变。
        """
        payload = preview_pop(preview_token)
        if not payload or payload.get("kind") != _PREVIEW_KIND_TEXTS:
            raise _expired()

        texts: list[dict[str, Any]] = payload.get("texts") or []
        new_units: list[dict[str, Any]] = payload.get("new_units") or []
        scopes = scopes or {}

        # 有拼音失配的行：**拒绝入库**（U7），且要说清是哪一行、哪个字
        bad = [t for t in texts if not t.get("ok")]
        if bad:
            first = bad[0]
            positions = "、".join(str(p) for p in first.get("mismatch_positions", [])[:10]) or "?"
            raise BizError(
                ErrorCode.IMPORT_VALIDATION_FAILED,
                f"有 {len(bad)} 篇课文的正文存在无法拼音化的字，不能入库"
                f"（如第 {first['row']} 行《{first['title']}》第 {positions} 个字）。"
                "请修改正文后重新上传",
                data={
                    "reason": "pinyin_mismatch",
                    "rows": [t["row"] for t in bad],
                    "mismatches": first.get("mismatches", []),
                },
            )

        created = updated = units_created = 0

        with write_tx() as conn:
            unit_id_map: dict[tuple[str, int, str, str], int] = {
                (r["subject"], int(r["grade"]), r["semester"], r["name"]): int(r["id"])
                for r in conn.execute(
                    "SELECT id, subject, grade, semester, name FROM units"
                ).fetchall()
            }

            # 课文导入不做「是否自动建单元」的开关：篇幅长、单次行数少，
            # 建单元的心理成本低；仍记入 units_created 以便前端提示。
            for unit in new_units:
                key = (unit["subject"], int(unit["grade"]), unit["semester"], unit["name"])
                if key in unit_id_map:
                    continue
                cur = conn.execute(
                    """
                    INSERT INTO units (subject, grade, semester, name, order_no,
                                       status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (unit["subject"], unit["grade"], unit["semester"], unit["name"],
                     int(unit.get("order_no") or 0), utc_now_iso()),
                )
                unit_id_map[key] = int(cur.lastrowid or 0)
                units_created += 1

            now = utc_now_iso()
            for item in texts:
                key = (item["subject"], int(item["grade"]), item["semester"], item["unit_name"])
                unit_id = unit_id_map.get(key)
                if unit_id is None:
                    continue

                # ---- 应用前端调整后的背诵范围，并**重算**派生序列
                scope = scopes.get(int(item["row"]), item.get("recite_scope") or [])
                scope = sorted({int(i) for i in scope if 0 <= int(i) < len(item["paragraphs"])})
                para_texts = [p["text"] for p in item["paragraphs"]]
                build = pinyin_service.build_scoped(para_texts, scope)

                if not build.ok:  # 理论上不可达（preview 已拦），双保险
                    raise BizError(
                        ErrorCode.IMPORT_VALIDATION_FAILED,
                        f"第 {item['row']} 行《{item['title']}》的背诵范围包含无法拼音化的字，已取消本次导入",
                        data={"reason": "pinyin_mismatch", "row": item["row"]},
                    )

                paragraphs = [
                    {**p, "recite": p["idx"] in set(scope)} for p in item["paragraphs"]
                ]

                exist = conn.execute(
                    "SELECT id FROM texts WHERE unit_id = ? AND title = ?",
                    (unit_id, item["title"]),
                ).fetchone()

                values = (
                    item["content_raw"],
                    build.plain,
                    build.pinyin,
                    json.dumps(paragraphs, ensure_ascii=False),
                    json.dumps(scope, ensure_ascii=False),
                    build.length,
                )

                if exist is not None:
                    # 重新导入同一篇课文 = 修正内容（正文/范围都可能变），走更新
                    conn.execute(
                        """
                        UPDATE texts
                           SET content_raw = ?, content_plain = ?, content_pinyin = ?,
                               paragraphs = ?, recite_scope = ?, total_chars = ?,
                               author = ?, updated_at = ?
                         WHERE id = ?
                        """,
                        (*values, item.get("author"), now, int(exist["id"])),
                    )
                    updated += 1
                else:
                    conn.execute(
                        """
                        INSERT INTO texts (unit_id, title, author, content_raw,
                                           content_plain, content_pinyin, paragraphs,
                                           recite_scope, total_chars, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                        """,
                        (
                            unit_id,
                            item["title"],
                            item.get("author"),
                            *values,
                            now,
                        ),
                    )
                    created += 1

            write_audit(
                conn,
                user_id=operator_id,
                action="content.import_texts",
                target_type="texts",
                target_id=None,
                detail={
                    "file_name": payload.get("file_name"),
                    "created": created,
                    "updated": updated,
                    "units_created": units_created,
                },
                ip=ip,
            )

        logger.info("课文导入完成：新增 %s / 更新 %s / 新建单元 %s", created, updated, units_created)
        return {"created": created, "updated": updated, "units_created": units_created}

    # ================================================================== 错误报告

    @staticmethod
    def build_error_report(payload: dict[str, Any]) -> bytes:
        """把 `invalid_rows` 渲染成 xlsx（10a F16 / 接口设计 §6.2 的 `error_report_url`）。

        `report_headers` 由 preview 时写进 payload —— 词条 14 列、课文 8 列，
        不能让渲染层去猜。

        ⚠️ 与 BE-02 的学生名单报告**共用同一个 HTTP 路径** `/api/import/report/{token}`，
        见 `app/api/students.py` 顶部说明。
        """
        headers = payload.get("report_headers") or ["原文件行号", "错误原因"]
        raw_rows = payload.get("raw_rows") or []
        # 行号 → 原始数据（Excel 行号从 2 起，raw_rows 是 0 基列表）
        row_map: dict[int, list[Any]] = {}
        for offset, raw in enumerate(raw_rows):
            row_map[offset + 2] = list(raw)

        rows: list[list[Any]] = []
        for item in payload.get("invalid_rows") or []:
            row_no = item.get("row")
            raw = item.get("raw")
            if raw is None and isinstance(row_no, int):
                raw = row_map.get(row_no)
            rows.append([row_no, item.get("reason"), *list(raw or [])])

        return write_xlsx(headers, rows, sheet_name="错误明细")


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _text_public(item: dict[str, Any]) -> dict[str, Any]:
    """preview 响应里的课文项（**去掉** `content_plain` / `content_pinyin`）。

    拼音序列是服务端内部数据，接口设计 §8.7 明确「不下发拼音」。
    preview 响应同样遵守 —— 前端只需要 `paragraphs` 去渲染勾选框。
    """
    return {
        k: v
        for k, v in item.items()
        if k not in ("content_plain", "content_pinyin")
    }
