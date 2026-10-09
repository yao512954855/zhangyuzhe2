"""内容条目服务 —— WP-A，契约《01-共享契约冻结》§3.4 / §4.1。

要点：
- `items.en` 非空去首尾空格；同 unit 同 type 下 `en` 重复 → `20003`
  （软约束：DB 不加 UNIQUE，Excel 导入需要合并语义，判重只在服务层这一处做）
- Excel 导入 / 导出 / 模板复用 `core/excel.py` 基建；模板列冻结为
  `英文 | 词性 | 中文 | 其他可接受答案`（§4.1 export 列）
- 中文释义与「其他可接受答案」是展示型文本：只 trim、**不做全角转半角**
  （否则中文标点会被改坏，见 core/excel.py::clean_raw 说明）
"""

from __future__ import annotations

from typing import Any

from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode
from app.core.excel import build_template, read_xlsx, write_xlsx, xlsx_response_headers
from app.core.logging import get_logger
from app.services import unit_service

logger = get_logger(__name__)

#: 导出 / 模板的冻结列序（契约 §4.1：列：英文|词性|中文|其他可接受答案）
EXPORT_HEADERS: list[str] = ["英文", "词性", "中文", "其他可接受答案"]

#: 展示型列（只 trim，不转全角）
_RAW_COLUMNS = frozenset({"中文", "其他可接受答案"})

#: 模板文件落盘位置（由代码生成，见 10-后端 §WP-A 要点）
TEMPLATE_PATH_RELATIVE = "templates/items_template.xlsx"

_VALID_TYPES = ("word", "phrase", "sentence")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _require_type(item_type: str | None) -> str:
    if item_type not in _VALID_TYPES:
        raise BizError(ErrorCode.PARAM_INVALID, "类型必须是 word / phrase / sentence")
    return item_type


def _require_en(en: str | None) -> str:
    en = (en or "").strip()
    if not en:
        raise BizError(ErrorCode.PARAM_INVALID, "英文不能为空")
    return en


def _require_zh(zh: str | None) -> str:
    zh = (zh or "").strip()
    if not zh:
        raise BizError(ErrorCode.PARAM_INVALID, "中文释义不能为空")
    return zh


def _require_unit(unit_id: int) -> None:
    unit_service.get(unit_id)  # 不存在 → 30001


def _en_exists(conn: Any, unit_id: int, item_type: str, en: str, *,
               exclude_id: int | None = None) -> bool:
    """同 unit+type 下 en 是否已存在（软判重，契约 §3.4）。"""
    sql = "SELECT id FROM items WHERE unit_id = ? AND type = ? AND en = ?"
    params: list[Any] = [unit_id, item_type, en]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return conn.execute(sql, params).fetchone() is not None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_items(
    *,
    unit_id: int | None = None,
    item_type: str | None = None,
    keyword: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """`GET /api/items` —— 返回 (当前页行, total)。行内含 unit_name 便于列表展示。"""
    where: list[str] = ["1 = 1"]
    params: list[Any] = []
    if unit_id is not None:
        where.append("i.unit_id = ?")
        params.append(unit_id)
    if item_type:
        _require_type(item_type)
        where.append("i.type = ?")
        params.append(item_type)
    if keyword:
        where.append("(i.en LIKE ? OR i.zh LIKE ?)")
        like = f"%{keyword.strip()}%"
        params.extend([like, like])
    where_sql = " AND ".join(where)

    with read_conn() as conn:
        total = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM items i WHERE {where_sql}", params
            ).fetchone()["n"]
        )
        rows = conn.execute(
            f"""
            SELECT i.id, i.unit_id, u.name AS unit_name, i.type, i.en, i.pos,
                   i.zh, i.alt_zh, i.sort, i.created_at
              FROM items i
              JOIN book_units u ON u.id = i.unit_id
             WHERE {where_sql}
             ORDER BY u.sort, i.sort, i.id
             LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows], total


def get_item(item_id: int) -> dict[str, Any]:
    with read_conn() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise BizError(ErrorCode.CONTENT_NOT_FOUND, "内容不存在")
    return dict(row)


def create(
    *,
    unit_id: int,
    item_type: str,
    en: str,
    zh: str,
    pos: str = "",
    alt_zh: str = "",
    sort: int = 0,
) -> dict[str, Any]:
    """`POST /api/items`。重复 en → `20003`。"""
    _require_unit(unit_id)
    _require_type(item_type)
    en = _require_en(en)
    zh = _require_zh(zh)
    with write_tx() as conn:
        if _en_exists(conn, unit_id, item_type, en):
            raise BizError(ErrorCode.CONTENT_DUPLICATE)
        cur = conn.execute(
            """
            INSERT INTO items (unit_id, type, en, pos, zh, alt_zh, sort, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (unit_id, item_type, en, (pos or "").strip(), zh,
             (alt_zh or "").strip(), int(sort), utc_now_iso()),
        )
        item_id = int(cur.lastrowid or 0)
    return get_item(item_id)


def update(item_id: int, *, fields: dict[str, Any]) -> dict[str, Any]:
    """`PUT /api/items/{id}` —— 同上字段可选，只改传入的字段。"""
    get_item(item_id)  # 不存在 → 30002

    allowed = {"unit_id", "type", "en", "pos", "zh", "alt_zh", "sort"}
    changes = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not changes:
        return get_item(item_id)

    if "unit_id" in changes:
        _require_unit(int(changes["unit_id"]))
    if "type" in changes:
        _require_type(changes["type"])
    if "en" in changes:
        changes["en"] = _require_en(changes["en"])
    if "zh" in changes:
        changes["zh"] = _require_zh(changes["zh"])
    for key in ("pos", "alt_zh"):
        if key in changes:
            changes[key] = str(changes[key]).strip()

    with write_tx() as conn:
        # 判重口径：把改动合并到当前行后，按最终 (unit_id, type, en) 检查
        current = conn.execute(
            "SELECT unit_id, type, en FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        assert current is not None  # 前面 get_item 已保证
        final_unit = int(changes.get("unit_id", current["unit_id"]))
        final_type = changes.get("type", current["type"])
        final_en = changes.get("en", current["en"])
        if (
            "unit_id" in changes or "type" in changes or "en" in changes
        ) and _en_exists(conn, final_unit, final_type, final_en, exclude_id=item_id):
            raise BizError(ErrorCode.CONTENT_DUPLICATE)
        conn.execute(
            f"UPDATE items SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?",
            (*changes.values(), item_id),
        )
    return get_item(item_id)


def delete(item_id: int) -> None:
    """`DELETE /api/items/{id}` —— wrong_book 由 ON DELETE CASCADE 级联删。"""
    get_item(item_id)  # 不存在 → 30002
    with write_tx() as conn:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))


# ---------------------------------------------------------------------------
# Excel 导入 / 导出 / 模板
# ---------------------------------------------------------------------------

def import_items(raw: bytes, *, unit_id: int, item_type: str) -> dict[str, Any]:
    """`POST /api/items/import` multipart: file(.xlsx) + form unit_id/type。

    返回 `{inserted, duplicated, failed: [{row, reason}]}`（契约 §4.1）。

    语义（契约 §3.4「Excel 导入需要合并语义」）：
    - `inserted`  ：新插入的行数
    - `duplicated`：库内（同 unit+type）已有同 en 而跳过的行数；**文件内**
                    重复的行也算 duplicated（同一批内容重复，合并语义）
    - `failed`    ：缺英文 / 缺中文等无法入库的行，带 Excel 行号与原因
    """
    _require_unit(unit_id)
    _require_type(item_type)

    try:
        rows = read_xlsx(raw, raw_columns=_RAW_COLUMNS)
    except BizError:
        raise
    except Exception as exc:  # noqa: BLE001 - openpyxl 对损坏文件抛的异常类型不稳定
        logger.warning("items 导入 xlsx 解析失败：%s", exc)
        raise BizError(
            ErrorCode.FILE_FORMAT_INVALID, "文件打不开，请确认是 Excel（.xlsx）格式"
        ) from exc

    inserted = 0
    duplicated = 0
    failed: list[dict[str, Any]] = []
    seen_in_file: set[str] = set()

    with write_tx() as conn:
        existing = {
            str(r["en"]).strip().lower()
            for r in conn.execute(
                "SELECT en FROM items WHERE unit_id = ? AND type = ?",
                (unit_id, item_type),
            ).fetchall()
        }
        now = utc_now_iso()

        for idx, row in enumerate(rows):
            excel_row = idx + 2  # 第 1 行是表头
            en = str(row.get("英文") or "").strip()
            zh = str(row.get("中文") or "").strip()
            pos = str(row.get("词性") or "").strip()
            alt = str(row.get("其他可接受答案") or "").strip()

            reasons: list[str] = []
            if not en:
                reasons.append("英文不能为空")
            if not zh:
                reasons.append("中文不能为空")
            if not reasons:
                if en.lower() in seen_in_file or en in existing:
                    duplicated += 1
                    continue
                seen_in_file.add(en.lower())

            if reasons:
                failed.append({"row": excel_row, "reason": "；".join(reasons)})
                continue

            conn.execute(
                """
                INSERT INTO items (unit_id, type, en, pos, zh, alt_zh, sort, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (unit_id, item_type, en, pos, zh, alt, now),
            )
            inserted += 1

    logger.info(
        "items 导入完成 unit=%s type=%s：新增 %s / 重复 %s / 失败 %s",
        unit_id, item_type, inserted, duplicated, len(failed),
    )
    return {"inserted": inserted, "duplicated": duplicated, "failed": failed}


def export(*, unit_id: int | None = None, item_type: str | None = None) -> tuple[bytes, dict[str, str]]:
    """`GET /api/items/export` → (xlsx 字节, 响应头)。列序见 EXPORT_HEADERS。"""
    if item_type:
        _require_type(item_type)
    rows, _ = list_items(unit_id=unit_id, item_type=item_type, limit=-1, offset=0)
    data = write_xlsx(
        EXPORT_HEADERS,
        [(r["en"], r["pos"], r["zh"], r["alt_zh"]) for r in rows],
    )
    return data, xlsx_response_headers("items_export.xlsx")


def build_items_template() -> tuple[bytes, dict[str, str]]:
    """`GET /api/items/import-template` → (xlsx 字节, 响应头)。

    模板由代码生成（运行时生成，不依赖仓库里的静态文件），
    同时尽力落盘一份到 `backend/templates/items_template.xlsx` 供离线查阅。
    """
    data = build_template(
        EXPORT_HEADERS,
        validations={},
        sheet_name="内容导入",
        example_notes=[
            "· 必填：英文、中文；词性、其他可接受答案可以留空",
            "· 其他可接受答案：除「中文」外还想让学生答对的写法，多条用顿号「、」分隔",
        ],
        example_rows=[
            ["hello", "感叹词", "你好", "喂、哈罗"],
            ["good morning", "", "早上好", ""],
        ],
        column_widths={"英文": 24, "词性": 12, "中文": 24, "其他可接受答案": 32},
    )
    _persist_template_copy(data)
    return data, xlsx_response_headers("items_template.xlsx")


def _persist_template_copy(data: bytes) -> None:
    """把模板写到 backend/templates/（失败不影响下载 —— 只是一份离线副本）。"""
    try:
        from app.core.config import BASE_DIR

        target = BASE_DIR / "templates"
        target.mkdir(parents=True, exist_ok=True)
        (target / "items_template.xlsx").write_bytes(data)
    except OSError as exc:  # noqa: BLE001 - 磁盘只读等场景降级为仅在线下载
        logger.warning("模板副本落盘失败（忽略）：%s", exc)
