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

#: 导出 / 模板的列序（todo13 #3：加「类型」列，选中分类三种混导；
#: 旧文件无此列 → 导入回落表单 type，兼容不破）
EXPORT_HEADERS: list[str] = ["英文", "词性", "中文", "其他可接受答案", "类型"]

#: 展示型列（只 trim，不转全角）
_RAW_COLUMNS = frozenset({"中文", "其他可接受答案"})

#: 模板文件落盘位置（由代码生成，见 10-后端 §WP-A 要点）
TEMPLATE_PATH_RELATIVE = "templates/items_template.xlsx"

_VALID_TYPES = ("word", "phrase", "sentence")

#: 「类型」列中文 ↔ code（todo13 #3/#4：Excel 类型列与竖线导入用中文，
#: 直接写英文 code 也接受）
_TYPE_CN_TO_CODE = {"单词": "word", "词组": "phrase", "短句": "sentence"}
_TYPE_CODE_TO_CN = {v: k for k, v in _TYPE_CN_TO_CODE.items()}


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


def _require_leaf_unit(unit_id: int) -> None:
    """有下级分类的节点不能直接录内容（todo13 #1，2026-10-19 甲方反馈）。

    一级分组只是组织结构，内容（单词/词组/短句）只能挂在末级分类下，
    否则树形统计与 App 端两级选择会出现「内容悬在分组上」的歧义。
    """
    _require_unit(unit_id)
    with read_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM book_units WHERE parent_id = ? LIMIT 1", (unit_id,)
        ).fetchone()
    if row is not None:
        raise BizError(
            ErrorCode.PARAM_INVALID, "该分类下有下级分类，请选择末级分类后再录入内容"
        )


def _row_type(row: dict[str, Any], fallback: str) -> str:
    """从 Excel「类型」列解析行类型（todo13 #3）。

    - 列缺失（旧文件）或该行类型为空 → 回落表单 `fallback`；
    - 接受中文（单词/词组/短句）或英文 code（word/phrase/sentence）；
    - 写了但不认识 → 抛参数错（带列值，方便排查）。
    """
    raw = str(row.get("类型") or "").strip()
    if not raw:
        return fallback
    code = _TYPE_CN_TO_CODE.get(raw) or (raw if raw in _VALID_TYPES else None)
    if code is None:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            f"类型列值无法识别：{raw!r}（应为 单词/词组/短句）",
        )
    return code


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
    """`POST /api/items`。重复 en → `20003`；非叶分类 → 10001。"""
    _require_leaf_unit(unit_id)
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
        _require_leaf_unit(int(changes["unit_id"]))  # 移动目标也必须是末级分类
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


def batch_delete(ids: list[int]) -> dict[str, Any]:
    """`POST /api/items/batch-delete` {ids}（todo13 #2）。

    返回 `{deleted, missing}`：missing = 请求里不存在/已删的 id 列表。
    wrong_book 由 ON DELETE CASCADE 级联删。
    """
    unique = sorted({int(i) for i in ids})
    if not unique:
        raise BizError(ErrorCode.PARAM_INVALID, "ids 不能为空")
    with write_tx() as conn:
        marks = ",".join("?" for _ in unique)
        found = {
            int(r["id"])
            for r in conn.execute(
                f"SELECT id FROM items WHERE id IN ({marks})", unique
            ).fetchall()
        }
        missing = [i for i in unique if i not in found]
        if found:
            found_list = sorted(found)
            marks_found = ",".join("?" for _ in found_list)
            conn.execute(
                f"DELETE FROM items WHERE id IN ({marks_found})", found_list
            )
    logger.info("items 批量删除：%s 条（缺失 %s）", len(found), missing)
    return {"deleted": len(found), "missing": missing}


def batch_move(ids: list[int], *, target_unit_id: int) -> dict[str, Any]:
    """`POST /api/items/batch-move` {ids, target_unit_id}（todo13 #2）。

    目标必须是**末级分类**（todo13 #1 同口径）。逐行判重：
    目标 unit+type 下已有同 en → 跳过并汇报，其余移动。
    返回 `{moved, skipped: [{id, reason}]}`。
    """
    _require_leaf_unit(target_unit_id)
    unique = sorted({int(i) for i in ids})
    if not unique:
        raise BizError(ErrorCode.PARAM_INVALID, "ids 不能为空")
    moved = 0
    skipped: list[dict[str, Any]] = []
    with write_tx() as conn:
        marks = ",".join("?" for _ in unique)
        rows = conn.execute(
            f"SELECT id, type, en FROM items WHERE id IN ({marks})", unique
        ).fetchall()
        existing = {
            (str(r["type"]), str(r["en"]).strip().lower())
            for r in conn.execute(
                "SELECT type, en FROM items WHERE unit_id = ?", (target_unit_id,)
            ).fetchall()
        }
        for r in rows:
            key = (str(r["type"]), str(r["en"]).strip().lower())
            if key in existing:
                skipped.append(
                    {"id": int(r["id"]),
                     "reason": f"目标分类下已有同名{_TYPE_CODE_TO_CN.get(str(r['type']), '')}「{r['en']}」"}
                )
                continue
            conn.execute(
                "UPDATE items SET unit_id = ? WHERE id = ?", (target_unit_id, int(r["id"]))
            )
            existing.add(key)  # 同批内互斥
            moved += 1
    logger.info("items 批量移动 → unit=%s：移动 %s / 跳过 %s", target_unit_id, moved, len(skipped))
    return {"moved": moved, "skipped": skipped}


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
    _require_leaf_unit(unit_id)
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
    seen_in_file: set[tuple[str, str]] = set()

    with write_tx() as conn:
        existing = {
            (str(r["type"]), str(r["en"]).strip().lower())
            for r in conn.execute(
                "SELECT type, en FROM items WHERE unit_id = ?", (unit_id,)
            ).fetchall()
        }
        now = utc_now_iso()

        for idx, row in enumerate(rows):
            excel_row = idx + 2  # 第 1 行是表头
            en = str(row.get("英文") or "").strip()
            zh = str(row.get("中文") or "").strip()
            pos = str(row.get("词性") or "").strip()
            alt = str(row.get("其他可接受答案") or "").strip()
            # todo13 #3：行类型——「类型」列缺失/为空回落表单 type（旧文件兼容）
            try:
                row_type = _row_type(row, item_type)
            except BizError as exc:
                failed.append({"row": excel_row, "reason": str(exc)})
                continue

            reasons: list[str] = []
            if not en:
                reasons.append("英文不能为空")
            if not zh:
                reasons.append("中文不能为空")
            if not reasons:
                if (row_type, en.lower()) in seen_in_file or (row_type, en) in existing:
                    duplicated += 1
                    continue
                seen_in_file.add((row_type, en.lower()))

            if reasons:
                failed.append({"row": excel_row, "reason": "；".join(reasons)})
                continue

            conn.execute(
                """
                INSERT INTO items (unit_id, type, en, pos, zh, alt_zh, sort, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (unit_id, row_type, en, pos, zh, alt, now),
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
        [
            (r["en"], r["pos"], r["zh"], r["alt_zh"], _TYPE_CODE_TO_CN.get(r["type"], r["type"]))
            for r in rows
        ],
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
            "· 类型：单词/词组/短句（todo13 #3：一列混导）；留空 = 按导入时选择的类型",
        ],
        example_rows=[
            ["hello", "感叹词", "你好", "喂、哈罗", "单词"],
            ["good morning", "", "早上好", "", "词组"],
            ["How are you", "", "你好", "", "短句"],
        ],
        column_widths={"英文": 24, "词性": 12, "中文": 24, "其他可接受答案": 32, "类型": 10},
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


# ---------------------------------------------------------------------------
# 竖线粘贴导入（todo13 #4，Excel 导入的补充）
# ---------------------------------------------------------------------------

def import_text(text: str, *, unit_id: int, item_type: str) -> dict[str, Any]:
    """`POST /api/items/import-text` {text, unit_id, type}。

    格式（一行一条，竖线「|」分隔，段数自适应）：
    - 2 段：`英文 | 中文`；
    - 3 段：`英文 | 词性 | 中文`；**短句（sentence）没有词性**，此时中间段并入中文；
    - 1 段 / 超 3 段：该行失败，报错带行号（原始行号，含空行）。

    判重/合并语义与 `import_items` 完全一致（同 unit+type 下 en，文件内也算）。
    返回 `{inserted, duplicated, failed: [{row, reason}]}`。
    """
    _require_leaf_unit(unit_id)
    _require_type(item_type)

    inserted = 0
    duplicated = 0
    failed: list[dict[str, Any]] = []
    seen_in_file: set[tuple[str, str]] = set()

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    with write_tx() as conn:
        existing = {
            (str(r["type"]), str(r["en"]).strip().lower())
            for r in conn.execute(
                "SELECT type, en FROM items WHERE unit_id = ?", (unit_id,)
            ).fetchall()
        }
        now = utc_now_iso()

        for line_no, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]

            if len(parts) == 2:
                en, pos, zh = parts[0], "", parts[1]
            elif len(parts) == 3:
                en, mid, zh = parts[0], parts[1], parts[2]
                if item_type == "sentence":
                    # 短句没有词性：中间段视为中文的一部分（todo13 #4 确认口径）
                    zh = f"{mid} {zh}".strip() if mid else zh
                    pos = ""
                else:
                    pos = mid
            else:
                failed.append({
                    "row": line_no,
                    "reason": "格式无法识别：需要 2~3 段（英文|中文 或 英文|词性|中文）",
                })
                continue

            reasons: list[str] = []
            if not en:
                reasons.append("英文不能为空")
            if not zh:
                reasons.append("中文不能为空")
            if not reasons:
                if (item_type, en.lower()) in seen_in_file or (item_type, en) in existing:
                    duplicated += 1
                    continue
                seen_in_file.add((item_type, en.lower()))
            if reasons:
                failed.append({"row": line_no, "reason": "；".join(reasons)})
                continue

            conn.execute(
                """
                INSERT INTO items (unit_id, type, en, pos, zh, alt_zh, sort, created_at)
                VALUES (?, ?, ?, ?, ?, '', 0, ?)
                """,
                (unit_id, item_type, en, pos, zh, now),
            )
            inserted += 1

    logger.info(
        "items 竖线导入 unit=%s type=%s：新增 %s / 重复 %s / 失败 %s",
        unit_id, item_type, inserted, duplicated, len(failed),
    )
    return {"inserted": inserted, "duplicated": duplicated, "failed": failed}
