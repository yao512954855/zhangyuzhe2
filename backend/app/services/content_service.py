"""内容服务 —— WP-BE-03（10a F1 ~ F11）。

`units` / `words` / `texts` **三张表的唯一写入方**（契约 §4.1）。
其他包（BE-04 判分、BE-05 背诵、BE-07 统计）只读，不得直接改这三张表。

---

### 本模块承载的四条契约级约束

**① 删除保护（契约 §4.5 约束 3，10a E）**

有答题记录的内容**只能停用不能删除**，否则历史统计的分子分母对不上。

| 层级 | 判据 | 影响表 |
|---|---|---|
| 词条 | `answers.word_id` 引用 | `answers` |
| 课文 | `recitations.text_id` 引用 | `recitations` |
| 单元 | 单元下**任一**词条或课文有引用 | 两张都要查 |

用的是**两级判定**：先用 `EXISTS` 快速判「有没有」（无记录的主路径不必 COUNT），
命中后才 `COUNT(*)` 取准确的 `record_count` 回传给前端。
注意 `answers.word_id` / `recitations.text_id` 都是 `ON DELETE SET NULL` ——
**数据库不会替我们拦删除**，这个保护必须在服务层做，且必须做对。

**② 停用内容不参与出题（10a D）**

所有「给练习用」的查询（学生端词条 / 课文列表）**固定带 `status='active'`**；
管理端列表默认查全部（含停用），靠 `status` 参数筛。
两条路径的分叉点是 `for_student` / `status` 参数，**不要合并成一个查询**。

**③ 掌握度只读（契约 §4.2）**

`word_mastery` 的**唯一写入方是 BE-04**。本模块只读它，
用于学生端词条的 `mastery` 字段与 `only=wrong|unmastered` 筛选。
`MasteryService` 尚未就绪时会自动降级（见 `_statuses_from_mastery_service`）。

**④ 双角色同一路径（契约 §14 裁决 3）**

`/api/units/{id}/words` 与 `/api/units/{id}/texts` 按角色裁剪字段，
裁剪在两个 `list_*` 方法的返回结构上完成，**接口层不做字段级判断**。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from app.core.audit import write_audit
from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode
from app.core.logging import get_logger
from app.schemas.content import dump_accepted

logger = get_logger(__name__)

# 可被 PATCH 的列（白名单 —— 防止请求体里塞入 id / unit_id / created_at 改结构）
_WORD_PATCHABLE = (
    "type",
    "en",
    "cn",
    "accepted",
    "phonetic",
    "pos",
    "example_en",
    "example_cn",
    "require_spell",
    "order_no",
    "status",
)
_UNIT_PATCHABLE = ("name", "order_no", "status", "remark")
_TEXT_PATCHABLE = ("title", "author", "order_no", "status")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _like_param(keyword: str) -> str:
    """构造 `LIKE` 参数并转义通配符。

    不转义的话，管理者搜 `100%` 会变成「任意开头」，
    搜 `a_b` 会匹配 `axb` —— 搜索结果莫名其妙，且没有报错可循。
    """
    kw = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{kw}%"


def _status_filter(column: str, status: str | None) -> tuple[str, list[Any]]:
    """把 `status` 查询参数翻译成 SQL 片段。

    | 入参 | 语义 |
    |---|---|
    | 缺失 / 空 / `all` | **不过滤**（管理端默认看到全部，含停用） |
    | `active` / `inactive` | 精确过滤 |

    ⚠️ 注意与「学生端固定只出 active」是两件事：这里只处理**显式传入**的筛选，
    学生端的 `status='active'` 由 `for_student=True` 强制加，不受本函数影响。
    """
    if status is None or status.strip() == "" or status.strip().lower() == "all":
        return "", []
    return f" AND {column} = ?", [status.strip()]


def _derive_status(row: dict[str, Any] | None) -> str:
    """词条级掌握状态派生 —— 契约 §7.2，**严格按序，命中即停**。

    | 序 | 条件 | 结果 |
    |---|---|---|
    | 1 | `total_count == 0` | `not_started` |
    | 2 | `mastered_at` 非空 | `mastered` |
    | 3 | `wrong_count > 0` | `practiced_with_error` |
    | 4 | 其余（练过且无错） | `practiced_ok` |

    顺序不可换：一个词条可能「曾经掌握过、后来又练错了」，
    换序会得出完全不同的结论。
    """
    if not row or not row.get("total_count"):
        return "not_started"
    if row.get("mastered_at"):
        return "mastered"
    if (row.get("wrong_count") or 0) > 0:
        return "practiced_with_error"
    return "practiced_ok"


def _statuses_from_mastery_service(student_id: int, word_ids: list[int]) -> dict[int, str]:
    """优先走 BE-04 的 `MasteryService.get_status()`（契约 §14 裁决 3 明文要求）。

    ⚠️ **BE-04 尚未开工时该模块不存在**，此时返回空字典，由调用方降级到
    `_derive_status()`（规则与契约 §7.2 逐条一致）。
    这条降级路径让 BE-03 可以独立验收，**不是**要在 BE-03 里另立一套掌握度算法；
    BE-04 到位后自动让位，调用点**不要删**。
    """
    try:
        from app.services.mastery_service import MasteryService
    except ImportError:
        return {}
    try:
        return {int(k): str(v) for k, v in dict(MasteryService.get_status(student_id, word_ids)).items()}
    except Exception as exc:  # noqa: BLE001 - 跨包桥接，不能因对方异常拖垮列表接口
        logger.warning("MasteryService.get_status 调用失败，降级本地派生：%s", exc)
        return {}


# ---------------------------------------------------------------------------
# 主服务
# ---------------------------------------------------------------------------


class ContentService:
    """单元 / 词条 / 课文的读写。"""

    # ================================================================== 单元

    _UNIT_SELECT = """
        SELECT
            u.*,
            (SELECT COUNT(*) FROM words w
              WHERE w.unit_id = u.id) AS word_count,
            (SELECT COUNT(*) FROM texts t
              WHERE t.unit_id = u.id) AS text_count,
            (SELECT COUNT(*) FROM words w
              WHERE w.unit_id = u.id AND w.status = 'active') AS active_word_count,
            (SELECT COUNT(*) FROM texts t
              WHERE t.unit_id = u.id AND t.status = 'active') AS active_text_count
        FROM units u
    """

    @staticmethod
    def list_units(
        *,
        subject: str | None = None,
        grade: int | None = None,
        semester: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """`GET /api/units`（10a F1）。返回 `(items, total)`，`items` 含 4 个派生计数。"""
        where: list[str] = []
        params: list[Any] = []

        if subject:
            where.append("u.subject = ?")
            params.append(subject)
        if grade is not None:
            where.append("u.grade = ?")
            params.append(grade)
        if semester:
            where.append("u.semester = ?")
            params.append(semester)

        clause, extra = _status_filter("u.status", status)
        if clause:
            where.append(clause.removeprefix(" AND "))
            params.extend(extra)

        sql_where = f" WHERE {' AND '.join(where)}" if where else ""

        with read_conn() as conn:
            total = int(
                conn.execute(f"SELECT COUNT(*) AS c FROM units u{sql_where}", params).fetchone()["c"]
            )
            rows = conn.execute(
                f"""{ContentService._UNIT_SELECT}{sql_where}
                    ORDER BY u.grade, u.semester, u.order_no, u.id
                    LIMIT ? OFFSET ?""",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()

        return [dict(r) for r in rows], total

    @staticmethod
    def get_unit(unit_id: int) -> dict[str, Any]:
        """单个单元（带派生计数）。

        Raises:
            BizError: `30001` 单元不存在。
        """
        with read_conn() as conn:
            row = conn.execute(
                f"{ContentService._UNIT_SELECT} WHERE u.id = ?", (unit_id,)
            ).fetchone()
        if row is None:
            raise BizError(ErrorCode.UNIT_NOT_FOUND, f"单元不存在（id={unit_id}）")
        return dict(row)

    @staticmethod
    def create_unit(
        *,
        subject: str,
        grade: int,
        semester: str,
        name: str,
        order_no: int = 0,
        remark: str | None = None,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """`POST /api/units`（10a F2）。

        `(subject, grade, semester, name)` 重复 → `10001`（U19）。

        ⚠️ 靠库里 `uk_units` 唯一索引兜底，**不能**只做「先 SELECT 再 INSERT」：
        两个管理端标签页同时提交时，先查后插会双双通过检查、双双写入。
        所以真防线是 `IntegrityError` 捕获。
        """
        try:
            with write_tx() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO units (subject, grade, semester, name, order_no,
                                       remark, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (subject, grade, semester, name, order_no, remark, utc_now_iso()),
                )
                unit_id = int(cur.lastrowid or 0)
                write_audit(
                    conn,
                    user_id=operator_id,
                    action="unit.create",
                    target_type="unit",
                    target_id=unit_id,
                    detail={"subject": subject, "grade": grade, "semester": semester, "name": name},
                    ip=ip,
                )
        except sqlite3.IntegrityError as exc:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"已存在同名单元「{name}」（同学科 / 同年级 / 同学期），"
                "请换个名称，或直接把内容导入到该单元",
            ) from exc

        return ContentService.get_unit(unit_id)

    @staticmethod
    def update_unit(
        unit_id: int,
        changes: dict[str, Any],
        *,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """`PATCH /api/units/{id}`（10a F3）。

        `changes` 是**已经按 `model_fields_set` 过滤过**的字典：
        键存在 = 要改，键缺失 = 不修改。`None` 值的含义由外部（Schema）决定。
        """
        ContentService.get_unit(unit_id)  # 不存在 → 30001

        sets: list[str] = []
        params: list[Any] = []
        for col in _UNIT_PATCHABLE:
            if col not in changes:
                continue
            sets.append(f"{col} = ?")
            params.append(changes[col])

        if not sets:
            return ContentService.get_unit(unit_id)

        sets.append("updated_at = ?")
        params.append(utc_now_iso())

        try:
            with write_tx() as conn:
                conn.execute(
                    f"UPDATE units SET {', '.join(sets)} WHERE id = ?", (*params, unit_id)
                )
                write_audit(
                    conn,
                    user_id=operator_id,
                    action="unit.update",
                    target_type="unit",
                    target_id=unit_id,
                    detail={"changes": {k: changes[k] for k in changes}},
                    ip=ip,
                )
        except sqlite3.IntegrityError as exc:
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"已存在同名单元「{changes.get('name')}」，请换个名称",
            ) from exc

        return ContentService.get_unit(unit_id)

    @staticmethod
    def delete_unit(
        unit_id: int,
        *,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """`DELETE /api/units/{id}`（10a F4）。

        单元下**任一**内容有答题记录 → `30003` + `record_count`（U11）。
        真正删除时 `words` / `texts` 由 `ON DELETE CASCADE` 一并清掉 ——
        只有「整单元零记录」才走到这里，所以级联是安全的。
        """
        ContentService.get_unit(unit_id)

        if ContentService.unit_has_records(unit_id):
            counts = ContentService.unit_record_count(unit_id)
            total = counts["answer_count"] + counts["recitation_count"]
            raise BizError(
                ErrorCode.CONTENT_HAS_RECORDS,
                f"该单元下已有 {total} 条练习记录，为保证统计完整只能停用不能删除",
                data={"record_count": total, **counts},
            )

        with write_tx() as conn:
            w = conn.execute("SELECT COUNT(*) AS c FROM words WHERE unit_id = ?", (unit_id,)).fetchone()
            t = conn.execute("SELECT COUNT(*) AS c FROM texts WHERE unit_id = ?", (unit_id,)).fetchone()
            deleted_words, deleted_texts = int(w["c"]), int(t["c"])

            conn.execute("DELETE FROM units WHERE id = ?", (unit_id,))
            write_audit(
                conn,
                user_id=operator_id,
                action="unit.delete",
                target_type="unit",
                target_id=unit_id,
                detail={"deleted_words": deleted_words, "deleted_texts": deleted_texts},
                ip=ip,
            )

        logger.info("单元 %s 已删除（词条 %s / 课文 %s）", unit_id, deleted_words, deleted_texts)
        return {
            "unit_id": unit_id,
            "deleted_words": deleted_words,
            "deleted_texts": deleted_texts,
        }

    # ---------------------------------------------------------------- 删除保护

    @staticmethod
    def unit_has_records(unit_id: int) -> bool:
        """单元下是否有任何练习记录。**用 `EXISTS`，不 COUNT**（10a E）。"""
        with read_conn() as conn:
            row = conn.execute(
                """
                SELECT EXISTS(
                           SELECT 1 FROM answers a
                             JOIN words w ON w.id = a.word_id
                            WHERE w.unit_id = ?
                       )
                       OR EXISTS(
                           SELECT 1 FROM recitations r
                             JOIN texts t ON t.id = r.text_id
                            WHERE t.unit_id = ?
                       ) AS has_record
                """,
                (unit_id, unit_id),
            ).fetchone()
        return bool(row["has_record"])

    @staticmethod
    def unit_record_count(unit_id: int) -> dict[str, int]:
        """单元下记录的精确条数（仅在确认有记录后调用，用于回传 `record_count`）。"""
        with read_conn() as conn:
            row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM answers a
                       JOIN words w ON w.id = a.word_id
                      WHERE w.unit_id = ?) AS answer_count,
                    (SELECT COUNT(*) FROM recitations r
                       JOIN texts t ON t.id = r.text_id
                      WHERE t.unit_id = ?) AS recitation_count
                """,
                (unit_id, unit_id),
            ).fetchone()
        return {
            "answer_count": int(row["answer_count"]),
            "recitation_count": int(row["recitation_count"]),
        }

    @staticmethod
    def word_has_records(word_id: int) -> bool:
        """词条是否有答题记录（`EXISTS`）。"""
        with read_conn() as conn:
            row = conn.execute(
                "SELECT EXISTS(SELECT 1 FROM answers WHERE word_id = ?) AS has_record",
                (word_id,),
            ).fetchone()
        return bool(row["has_record"])

    @staticmethod
    def word_record_count(word_id: int) -> int:
        """词条的答题记录条数（U9 要回传 `record_count=37`）。"""
        with read_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM answers WHERE word_id = ?", (word_id,)
            ).fetchone()
        return int(row["c"])

    @staticmethod
    def text_has_records(text_id: int) -> bool:
        """课文是否有背诵记录（`EXISTS`）。"""
        with read_conn() as conn:
            row = conn.execute(
                "SELECT EXISTS(SELECT 1 FROM recitations WHERE text_id = ?) AS has_record",
                (text_id,),
            ).fetchone()
        return bool(row["has_record"])

    @staticmethod
    def text_record_count(text_id: int) -> int:
        """课文的背诵记录条数。"""
        with read_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM recitations WHERE text_id = ?", (text_id,)
            ).fetchone()
        return int(row["c"])

    # ================================================================== 词条

    @staticmethod
    def get_word(word_id: int) -> dict[str, Any]:
        """单个词条。

        Raises:
            BizError: `30002` 词条不存在。
        """
        with read_conn() as conn:
            row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        if row is None:
            raise BizError(ErrorCode.CONTENT_NOT_FOUND, f"词条不存在（id={word_id}）")
        return dict(row)

    @staticmethod
    def list_words_admin(
        unit_id: int,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """管理端词条列表（10a F5 的 admin 分支 / 接口设计 §5.5）。

        **默认不过滤 `status`**：管理端要能看到并恢复已停用的词条（10a D）。
        """
        ContentService.get_unit(unit_id)

        where = ["w.unit_id = ?"]
        params: list[Any] = [unit_id]

        clause, extra = _status_filter("w.status", status)
        if clause:
            where.append(clause.removeprefix(" AND "))
            params.extend(extra)

        if keyword and keyword.strip():
            where.append("(w.en LIKE ? ESCAPE '\\' OR w.cn LIKE ? ESCAPE '\\')")
            kw = _like_param(keyword.strip())
            params.extend([kw, kw])

        sql_where = " WHERE " + " AND ".join(where)

        with read_conn() as conn:
            total = int(
                conn.execute(f"SELECT COUNT(*) AS c FROM words w{sql_where}", params).fetchone()["c"]
            )
            rows = conn.execute(
                f"""SELECT w.* FROM words w{sql_where}
                    ORDER BY w.order_no, w.id
                    LIMIT ? OFFSET ?""",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()

        return [dict(r) for r in rows], total

    @staticmethod
    def list_words_admin_global(
        *,
        subject: str | None = None,
        grade: int | None = None,
        semester: str | None = None,
        unit_id: int | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """跨单元词条列表（英语内容管理页，2026-09-30 新增）。

        与 `list_words_admin`（按单元）的区别：JOIN `units` 带出
        `unit_name / grade / semester`，支持按 年级/学期/单元/关键词 组合筛选 ——
        「点年级看全部词」时每行要能看出归属，这是页面定稿要求的字段。
        默认不过滤 `status`（管理端要能看并恢复停用词条，10a D，同上）。
        """
        where = ["1=1"]
        params: list[Any] = []

        if unit_id is not None:
            where.append("w.unit_id = ?")
            params.append(unit_id)
        if subject:
            where.append("u.subject = ?")
            params.append(subject)
        if grade is not None:
            where.append("u.grade = ?")
            params.append(grade)
        if semester:
            where.append("u.semester = ?")
            params.append(semester)

        clause, extra = _status_filter("w.status", status)
        if clause:
            where.append(clause.removeprefix(" AND "))
            params.extend(extra)

        if keyword and keyword.strip():
            where.append("(w.en LIKE ? ESCAPE '\\' OR w.cn LIKE ? ESCAPE '\\')")
            kw = _like_param(keyword.strip())
            params.extend([kw, kw])

        sql_where = " WHERE " + " AND ".join(where)

        with read_conn() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS c FROM words w JOIN units u ON u.id = w.unit_id{sql_where}",
                    params,
                ).fetchone()["c"]
            )
            rows = conn.execute(
                f"""SELECT w.*, u.name AS unit_name, u.grade, u.semester, u.subject AS unit_subject
                    FROM words w JOIN units u ON u.id = w.unit_id{sql_where}
                    ORDER BY u.grade, u.semester, u.order_no, w.order_no, w.id
                    LIMIT ? OFFSET ?""",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()

        return [dict(r) for r in rows], total

    @staticmethod
    def create_word(
        *,
        unit_id: int,
        en: str,
        cn: str,
        type_: str = "word",
        accepted: list[str] | None = None,
        phonetic: str | None = None,
        pos: str | None = None,
        example_en: str | None = None,
        example_cn: str | None = None,
        require_spell: bool = True,
        order_no: int | None = None,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """单条新增词条（`POST /api/words`，2026-09-30）。

        - 单元必须是**英语**单元（语文没有词条，参数错了就明确报 10001）
        - 同单元同 `en` 视为重复 → `10001`（导入链路的 upsert 语义不适用于手工单条：
          手工场景下「覆盖」多半是误操作，宁可让管理者自己去看）
        - `order_no` 缺省排到该单元末尾（max+1），新词不出现在最前面打断顺序
        """
        unit = ContentService.get_unit(unit_id)  # 不存在 → 30002
        if unit["subject"] != "english":
            raise BizError(ErrorCode.PARAM_INVALID, f"「{unit['name']}」是语文单元，词条只能添加到英语单元")

        try:
            with write_tx() as conn:
                dup = conn.execute(
                    "SELECT id FROM words WHERE unit_id = ? AND en = ?", (unit_id, en)
                ).fetchone()
                if dup is not None:
                    raise BizError(
                        ErrorCode.PARAM_INVALID,
                        f"该单元已有英文为「{en}」的词条，可直接编辑它",
                    )
                if order_no is None:
                    row = conn.execute(
                        "SELECT COALESCE(MAX(order_no), -1) AS m FROM words WHERE unit_id = ?",
                        (unit_id,),
                    ).fetchone()
                    order_no = int(row["m"]) + 1
                cur = conn.execute(
                    """
                    INSERT INTO words (unit_id, type, en, cn, accepted, phonetic, pos,
                                       example_en, example_cn, require_spell, order_no,
                                       status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (
                        unit_id,
                        type_,
                        en,
                        cn,
                        dump_accepted(accepted or []),
                        phonetic,
                        pos,
                        example_en,
                        example_cn,
                        1 if require_spell else 0,
                        int(order_no),
                        utc_now_iso(),
                    ),
                )
                word_id = int(cur.lastrowid or 0)
                write_audit(
                    conn,
                    user_id=operator_id,
                    action="word.create",
                    target_type="word",
                    target_id=word_id,
                    detail={"unit_id": unit_id, "en": en, "cn": cn},
                    ip=ip,
                )
        except sqlite3.IntegrityError as exc:
            raise BizError(ErrorCode.PARAM_INVALID, f"该单元已有英文为「{en}」的词条") from exc

        return ContentService.get_word(word_id)

    @staticmethod
    def mastery_map(student_id: int | None, word_ids: list[int]) -> dict[int, dict[str, Any]]:
        """批量取掌握度摘要（**一次 SQL，O(1) 批量，禁止循环单查**）。

        `status` 优先取 `MasteryService`（BE-04），缺失时本地派生（见 `_derive_status`）；
        `streak` / `wrong_count` 直接读 `word_mastery`（本模块是只读方，契约 §4.2）。
        """
        if not student_id or not word_ids:
            return {}

        unique_ids = list(dict.fromkeys(int(i) for i in word_ids))
        placeholders = ",".join("?" * len(unique_ids))
        with read_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT word_id, streak, total_count, correct_count, wrong_count, mastered_at
                FROM word_mastery
                WHERE student_id = ? AND word_id IN ({placeholders})
                """,
                (student_id, *unique_ids),
            ).fetchall()
        raw = {int(r["word_id"]): dict(r) for r in rows}

        remote = _statuses_from_mastery_service(student_id, unique_ids)

        out: dict[int, dict[str, Any]] = {}
        for wid in unique_ids:
            row = raw.get(wid)
            status = remote.get(wid) or _derive_status(row)
            out[wid] = {
                "status": status,
                "streak": int(row["streak"]) if row else 0,
                "wrong_count": int(row["wrong_count"]) if row else 0,
                "mastered": status == "mastered",
            }
        return out

    @staticmethod
    def list_words_student(
        unit_id: int,
        *,
        student_id: int,
        mode: str | None = None,
        only: str | None = None,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        """学生端词条列表（10a F5 的 student 分支 / 接口设计 §8.5）。

        - **固定 `status='active'`**（10a D / U12：停用词条不出现在学生端）
        - `only=wrong` → 练过且有错；`only=unmastered` → 尚未掌握（含没练过的）
        - 返回**已裁剪**的行（去 `accepted` / `status`），掌握度由接口层附加

        Returns:
            `word_mastery` 相关筛选已在 SQL 里完成，返回的是 `words` 的原始行 + `_mastery`。
        """
        ContentService.get_unit(unit_id)

        where = ["w.unit_id = ?", "w.status = 'active'"]
        params: list[Any] = [unit_id]

        if keyword and keyword.strip():
            where.append("(w.en LIKE ? ESCAPE '\\' OR w.cn LIKE ? ESCAPE '\\')")
            kw = _like_param(keyword.strip())
            params.extend([kw, kw])

        join = ""
        if only == "wrong":
            join = "JOIN word_mastery m ON m.word_id = w.id AND m.student_id = ?"
            params.insert(0, student_id)
            where.append("m.wrong_count > 0")
        elif only == "unmastered":
            join = "LEFT JOIN word_mastery m ON m.word_id = w.id AND m.student_id = ?"
            params.insert(0, student_id)
            # 没有记录（not_started）或未掌握都算「未掌握」
            where.append("(m.word_id IS NULL OR m.mastered_at IS NULL)")
        elif only is not None and only != "":
            raise BizError(ErrorCode.PARAM_INVALID, "only 只能是 wrong 或 unmastered")

        sql = f"""SELECT w.* FROM words w {join} WHERE {' AND '.join(where)}
                  ORDER BY w.order_no, w.id"""
        with read_conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        items = [dict(r) for r in rows]
        if not items:
            return []

        mastery = ContentService.mastery_map(student_id, [int(i["id"]) for i in items])
        for item in items:
            item["_mastery"] = mastery.get(int(item["id"]))
        return items

    @staticmethod
    def patch_word(
        word_id: int,
        changes: dict[str, Any],
        *,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """`PATCH /api/words/{id}`（10a F6）。

        Returns:
            `(word, history_affected)`。`history_affected=True` 表示该词条已有答题记录，
            前端应弹确认 —— 因为 `answers.expected` 是**写记录时的快照**，
            改词条后新旧记录的「标准答案」会不一致（U15 验证旧记录不受影响）。

        ⚠️ **绝不回写历史记录**。这是刻意的：历史记录是「学生当时听到的题」，
        改判分依据等于篡改成绩。
        """
        before = ContentService.get_word(word_id)

        sets: list[str] = []
        params: list[Any] = []
        for col in _WORD_PATCHABLE:
            if col not in changes:
                continue
            value = changes[col]
            if col == "accepted":
                value = dump_accepted(value)
            elif col == "require_spell":
                value = 1 if value else 0
            sets.append(f"{col} = ?")
            params.append(value)

        history_affected = ContentService.word_has_records(word_id)

        if not sets:
            return before, history_affected

        sets.append("updated_at = ?")
        params.append(utc_now_iso())

        try:
            with write_tx() as conn:
                conn.execute(
                    f"UPDATE words SET {', '.join(sets)} WHERE id = ?", (*params, word_id)
                )
                write_audit(
                    conn,
                    user_id=operator_id,
                    action="word.update",
                    target_type="word",
                    target_id=word_id,
                    detail={
                        "unit_id": before["unit_id"],
                        "changes": {k: changes[k] for k in changes},
                        "history_affected": history_affected,
                    },
                    ip=ip,
                )
        except sqlite3.IntegrityError as exc:
            # uk_words = (unit_id, en)，同单元内英文不可重复
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"该单元下已存在英文为「{changes.get('en')}」的词条",
            ) from exc

        return ContentService.get_word(word_id), history_affected

    @staticmethod
    def batch_status(
        word_ids: Iterable[int],
        status: str,
        *,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> int:
        """`POST /api/words/batch-status`（10a F7）。返回实际更新的条数。

        停用（而非删除）是「内容下线」的标准手段：历史记录完整保留，
        学生端立即不再出现（`list_words_student` 固定带 `status='active'`）。
        """
        ids = [int(i) for i in dict.fromkeys(word_ids)]
        if not ids:
            return 0

        placeholders = ",".join("?" * len(ids))
        with write_tx() as conn:
            cur = conn.execute(
                f"UPDATE words SET status = ?, updated_at = ? WHERE id IN ({placeholders})",
                (status, utc_now_iso(), *ids),
            )
            affected = int(cur.rowcount or 0)
            write_audit(
                conn,
                user_id=operator_id,
                action="word.batch_status",
                target_type="word",
                target_id=None,
                detail={"word_ids": ids, "status": status, "affected": affected},
                ip=ip,
            )
        return affected

    @staticmethod
    def delete_word(
        word_id: int,
        *,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """`DELETE /api/words/{id}`（10a F8）。

        有记录 → `30003` + `record_count`（U9）；无记录才物理删除（U10）。
        """
        word = ContentService.get_word(word_id)

        if ContentService.word_has_records(word_id):
            count = ContentService.word_record_count(word_id)
            raise BizError(
                ErrorCode.CONTENT_HAS_RECORDS,
                f"该词条已有 {count} 条练习记录，为保证统计完整只能停用",
                data={"record_count": count},
            )

        with write_tx() as conn:
            conn.execute("DELETE FROM words WHERE id = ?", (word_id,))
            write_audit(
                conn,
                user_id=operator_id,
                action="word.delete",
                target_type="word",
                target_id=word_id,
                detail={"unit_id": word["unit_id"], "en": word["en"], "cn": word["cn"]},
                ip=ip,
            )

        return {"word_id": word_id, "deleted": True}

    # ================================================================== 课文

    @staticmethod
    def get_text_row(text_id: int) -> dict[str, Any]:
        """课文原始行（含 `content_plain` / `content_pinyin`，**内部用**）。

        Raises:
            BizError: `30002` 课文不存在。
        """
        with read_conn() as conn:
            row = conn.execute("SELECT * FROM texts WHERE id = ?", (text_id,)).fetchone()
        if row is None:
            raise BizError(ErrorCode.CONTENT_NOT_FOUND, f"课文不存在（id={text_id}）")
        return dict(row)

    @staticmethod
    def parse_paragraphs(raw: Any) -> list[dict[str, Any]]:
        """`texts.paragraphs` 的 JSON 字符串 → 列表。损坏时返回空表（不抛异常）。"""
        if not raw:
            return []
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            logger.warning("paragraphs 不是合法 JSON，已按空处理")
            return []
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "idx": int(item.get("idx", i)),
                    "text": str(item.get("text", "")),
                    "chars": int(item.get("chars", 0) or 0),
                    "recite": bool(item.get("recite", True)),
                }
            )
        return out

    @staticmethod
    def scope_snapshot(paragraphs: list[dict[str, Any]], scope: list[int]) -> dict[str, int]:
        """算背诵范围在 `content_plain` 上的字符区间 `{start, end}`（接口设计 §8.7）。

        对齐引擎要按范围裁剪 `content_plain`，所以这里给的是**半开区间**的
        `[start, end)`，与 Python 切片语义一致，避免前后端各按各的约定差一位。

        范围为空 = 全文背诵。
        """
        picked = sorted({int(i) for i in scope}) if scope else list(range(len(paragraphs)))
        offset = 0
        start = 0
        end = 0
        seen = False
        for p in paragraphs:
            chars = int(p.get("chars", 0) or 0)
            if int(p.get("idx", -1)) in picked:
                if not seen:
                    start = offset
                    seen = True
                end = offset + chars
            offset += chars
        if not seen:
            return {"start": 0, "end": 0}
        return {"start": start, "end": end}

    @staticmethod
    def _last_grades(student_id: int, text_ids: list[int]) -> dict[int, str | None]:
        """批量取每个课文的**最近一次**背诵评级（O(1) 批量）。

        用窗口函数而不是 `GROUP BY` + bare column：后者是 SQLite 的扩展行为，
        换数据库或用上 `HAVING` 就不可靠了。契约已要求 SQLite ≥ 3.38（有窗口函数）。
        """
        if not text_ids:
            return {}
        unique = list(dict.fromkeys(int(i) for i in text_ids))
        placeholders = ",".join("?" * len(unique))
        with read_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT text_id, grade FROM (
                    SELECT text_id, grade,
                           ROW_NUMBER() OVER (
                               PARTITION BY text_id ORDER BY created_at DESC, id DESC
                           ) AS rn
                    FROM recitations
                    WHERE student_id = ? AND text_id IN ({placeholders})
                )
                WHERE rn = 1
                """,
                (student_id, *unique),
            ).fetchall()
        return {int(r["text_id"]): r["grade"] for r in rows}

    @staticmethod
    def list_texts(
        unit_id: int,
        *,
        for_student: bool = False,
        student_id: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """`GET /api/units/{id}/texts`（10a F9 / 接口设计 §8.6）。

        **不含正文** —— 学生选定后再走 `GET /api/texts/{id}`，减少流量。

        双角色（契约 §14 裁决 3）：
        - `for_student=True`：**固定只出 `status='active'`**，附 `last_grade`，
          且 `/api/units` 那套管理字段（`status` / `order_no` / `created_at`）不返回。
        - 管理端：不过滤 `status`（能看到停用的），不查 `last_grade`（那是「某学生」的视角）。
        """
        ContentService.get_unit(unit_id)

        where = ["t.unit_id = ?"]
        params: list[Any] = [unit_id]

        if for_student:
            where.append("t.status = 'active'")
        else:
            clause, extra = _status_filter("t.status", status)
            if clause:
                where.append(clause.removeprefix(" AND "))
                params.extend(extra)

        with read_conn() as conn:
            rows = conn.execute(
                f"""SELECT t.* FROM texts t
                    WHERE {' AND '.join(where)}
                    ORDER BY t.order_no, t.id""",
                params,
            ).fetchall()

        items = [dict(r) for r in rows]
        if not items:
            return []

        grades: dict[int, str | None] = {}
        if for_student and student_id:
            grades = ContentService._last_grades(student_id, [int(i["id"]) for i in items])

        out: list[dict[str, Any]] = []
        for row in items:
            paras = ContentService.parse_paragraphs(row.get("paragraphs"))
            scope = _parse_scope(row.get("recite_scope"))
            picked = scope if scope else [int(p["idx"]) for p in paras]
            recite_chars = sum(
                int(p.get("chars", 0) or 0) for p in paras if int(p.get("idx", -1)) in set(picked)
            )
            out.append(
                {
                    "id": int(row["id"]),
                    "unit_id": int(row["unit_id"]),
                    "title": row["title"],
                    "author": row.get("author"),
                    "status": row.get("status", "active"),
                    "order_no": int(row.get("order_no") or 0),
                    "total_chars": int(row.get("total_chars") or 0),
                    "recite_scope_chars": recite_chars,
                    "paragraph_count": len(paras),
                    "recite_paragraph_count": len(picked),
                    "recite_scope": scope,
                    "last_grade": grades.get(int(row["id"])),
                    "created_at": row.get("created_at"),
                }
            )
        return out

    @staticmethod
    def list_texts_admin_global(
        *,
        subject: str | None = None,
        grade: int | None = None,
        semester: str | None = None,
        unit_id: int | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """跨单元课文列表（语文内容管理页，2026-09-30 新增）。

        列表项形态与 `list_texts`（admin 分支）一致，额外 JOIN `units`
        带出 `unit_name / grade / semester`；按 标题/作者 模糊搜 `keyword`。
        """
        where = ["1=1"]
        params: list[Any] = []

        if unit_id is not None:
            where.append("t.unit_id = ?")
            params.append(unit_id)
        if subject:
            where.append("u.subject = ?")
            params.append(subject)
        if grade is not None:
            where.append("u.grade = ?")
            params.append(grade)
        if semester:
            where.append("u.semester = ?")
            params.append(semester)

        clause, extra = _status_filter("t.status", status)
        if clause:
            where.append(clause.removeprefix(" AND "))
            params.extend(extra)

        if keyword and keyword.strip():
            where.append("(t.title LIKE ? ESCAPE '\\' OR t.author LIKE ? ESCAPE '\\')")
            kw = _like_param(keyword.strip())
            params.extend([kw, kw])

        sql_where = " WHERE " + " AND ".join(where)

        with read_conn() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS c FROM texts t JOIN units u ON u.id = t.unit_id{sql_where}",
                    params,
                ).fetchone()["c"]
            )
            rows = conn.execute(
                f"""SELECT t.*, u.name AS unit_name, u.grade, u.semester, u.subject AS unit_subject
                    FROM texts t JOIN units u ON u.id = t.unit_id{sql_where}
                    ORDER BY u.grade, u.semester, u.order_no, t.order_no, t.id
                    LIMIT ? OFFSET ?""",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()

        items = [dict(r) for r in rows]
        out: list[dict[str, Any]] = []
        for row in items:
            paras = ContentService.parse_paragraphs(row.get("paragraphs"))
            scope = _parse_scope(row.get("recite_scope"))
            picked = scope if scope else [int(p["idx"]) for p in paras]
            recite_chars = sum(
                int(p.get("chars", 0) or 0) for p in paras if int(p.get("idx", -1)) in set(picked)
            )
            out.append(
                {
                    "id": int(row["id"]),
                    "unit_id": int(row["unit_id"]),
                    "title": row["title"],
                    "author": row.get("author"),
                    "status": row.get("status", "active"),
                    "order_no": int(row.get("order_no") or 0),
                    "total_chars": int(row.get("total_chars") or 0),
                    "recite_scope_chars": recite_chars,
                    "paragraph_count": len(paras),
                    "recite_paragraph_count": len(picked),
                    "recite_scope": scope,
                    "unit_name": row.get("unit_name"),
                    "grade": int(row["grade"]) if row.get("grade") is not None else None,
                    "semester": row.get("semester"),
                    "created_at": row.get("created_at"),
                }
            )
        return out, total

    @staticmethod
    def create_text(
        *,
        unit_id: int,
        title: str,
        content_raw: str,
        author: str | None = None,
        recite_scope: list[int] | None = None,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """单条新增课文（`POST /api/texts`，2026-09-30）。

        派生序列（`paragraphs / content_plain / content_pinyin / total_chars`）
        与导入走**同一套拼音服务**（`split_paragraphs / build_scoped`），
        口径不一致判分就会错 —— 不要在这里另写一份解析。
        正文含无法拼音化的字 → `40003`（同导入 U7，拒收入库）。
        """
        from app.services import pinyin_service  # 延迟导入，避免 services 内循环依赖

        unit = ContentService.get_unit(unit_id)  # 不存在 → 30002
        if unit["subject"] != "chinese":
            raise BizError(ErrorCode.PARAM_INVALID, f"「{unit['name']}」是英语单元，课文只能添加到语文单元")

        para_texts = pinyin_service.split_paragraphs(content_raw)
        if not para_texts:
            raise BizError(ErrorCode.PARAM_INVALID, "段落正文里没有可用的文字")

        paragraphs = [
            {
                "idx": i,
                "text": text,
                "chars": pinyin_service.count_han(text),
                "recite": True,
            }
            for i, text in enumerate(para_texts)
        ]

        if recite_scope is None or len(recite_scope) == 0:
            scope = [p["idx"] for p in paragraphs]
        else:
            out_of_range = [i for i in recite_scope if i < 0 or i >= len(paragraphs)]
            if out_of_range:
                raise BizError(
                    ErrorCode.PARAM_INVALID,
                    f"背诵范围里的段落号 {out_of_range} 超出实际段落数（共 {len(paragraphs)} 段，从 0 数起）",
                )
            scope = sorted({int(i) for i in recite_scope})

        build = pinyin_service.build_scoped(para_texts, scope)
        if not build.ok:
            positions_text = "、".join(str(p) for p in build.mismatch_positions[:10])
            raise BizError(
                ErrorCode.IMPORT_VALIDATION_FAILED,
                f"正文里有无法拼音化的字（第 {positions_text} 个字），语文对齐会在那里漏字，请修改后再保存",
                data={"mismatch_positions": build.mismatch_positions[:20], "mismatches": build.mismatches[:10]},
            )

        try:
            with write_tx() as conn:
                dup = conn.execute(
                    "SELECT id FROM texts WHERE unit_id = ? AND title = ?", (unit_id, title)
                ).fetchone()
                if dup is not None:
                    raise BizError(
                        ErrorCode.PARAM_INVALID,
                        f"该单元已有课文《{title}》，可重新导入覆盖它，或换个标题",
                    )
                row = conn.execute(
                    "SELECT COALESCE(MAX(order_no), -1) AS m FROM texts WHERE unit_id = ?",
                    (unit_id,),
                ).fetchone()
                order_no = int(row["m"]) + 1
                paragraphs_json = json.dumps(
                    [{**p, "recite": p["idx"] in set(scope)} for p in paragraphs],
                    ensure_ascii=False,
                )
                cur = conn.execute(
                    """
                    INSERT INTO texts (unit_id, title, author, content_raw,
                                       content_plain, content_pinyin, paragraphs,
                                       recite_scope, total_chars, order_no, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (
                        unit_id,
                        title,
                        author,
                        content_raw,
                        build.plain,
                        build.pinyin,
                        paragraphs_json,
                        json.dumps(scope, ensure_ascii=False),
                        build.length,
                        order_no,
                        utc_now_iso(),
                    ),
                )
                text_id = int(cur.lastrowid or 0)
                write_audit(
                    conn,
                    user_id=operator_id,
                    action="text.create",
                    target_type="text",
                    target_id=text_id,
                    detail={"unit_id": unit_id, "title": title, "total_chars": build.length},
                    ip=ip,
                )
        except sqlite3.IntegrityError as exc:
            raise BizError(ErrorCode.PARAM_INVALID, f"该单元已有课文《{title}》") from exc

        return ContentService.get_text_detail(text_id)

    @staticmethod
    def get_text_detail(
        text_id: int,
        *,
        for_student: bool = False,
        student_id: int | None = None,
    ) -> dict[str, Any]:
        """`GET /api/texts/{id}`（10a F10 / 接口设计 §8.7）。

        ⚠️ **不下发拼音**（§8.7 明文）—— 拼音对齐在服务端做。
        返回体里**没有** `content_plain` / `content_pinyin`，只有展示用的 `content_raw`
        与段落切片。
        """
        row = ContentService.get_text_row(text_id)
        paras = ContentService.parse_paragraphs(row.get("paragraphs"))
        scope = _parse_scope(row.get("recite_scope"))

        data: dict[str, Any] = {
            "id": int(row["id"]),
            "unit_id": int(row["unit_id"]),
            "title": row["title"],
            "author": row.get("author"),
            "status": row.get("status", "active"),
            "order_no": int(row.get("order_no") or 0),
            "paragraphs": paras,
            "recite_scope": scope,
            "scope_snapshot": ContentService.scope_snapshot(paras, scope),
            "total_chars": int(row.get("total_chars") or 0),
            "content_raw": row.get("content_raw"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        if for_student:
            # 学生端不需要管理字段（接口设计 §8.7 的字段集）
            for key in ("status", "order_no", "created_at", "updated_at"):
                data.pop(key, None)
        return data

    @staticmethod
    def patch_text(
        text_id: int,
        changes: dict[str, Any],
        *,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """`PATCH /api/texts/{id}`（契约未定义，见 `schemas/content.py::TextPatchIn` 说明）。

        2026-09-30 起支持改正文 / 背诵范围（语文内容管理页行内「编辑」）：
        派生序列与 `create_text` 走**同一套拼音服务**；**已有背诵记录 → 30003 拒绝**
        （历史记录的对齐口径会被破坏，同「有记录只能停用」原则）。
        `recite_scope` 缺省沿用现有范围；正文改短导致越界的段被裁掉，裁空则回退全文。

        Returns:
            `(text_detail, history_affected)`。
        """
        row = ContentService.get_text_row(text_id)

        history_affected = ContentService.text_has_records(text_id)

        sets: list[str] = []
        params: list[Any] = []

        # ---- 正文 / 背诵范围改动（先于元信息列处理）----
        body_changed = "content_raw" in changes or "recite_scope" in changes
        if body_changed:
            if history_affected:
                raise BizError(
                    ErrorCode.CONTENT_HAS_RECORDS,
                    "该课文已有背诵记录，改正文或背诵范围会让历史记录的判分口径失效；请停用后另建新课文",
                )
            from app.services import pinyin_service  # 延迟导入，同 create_text

            content_raw = changes.pop("content_raw", None)
            if content_raw is None:
                content_raw = row.get("content_raw") or ""
            para_texts = pinyin_service.split_paragraphs(content_raw)
            if not para_texts:
                raise BizError(ErrorCode.PARAM_INVALID, "段落正文里没有可用的文字")

            paragraphs = [
                {"idx": i, "text": t, "chars": pinyin_service.count_han(t), "recite": True}
                for i, t in enumerate(para_texts)
            ]

            scope_in = changes.pop("recite_scope", None) or _parse_scope(row.get("recite_scope"))
            if not scope_in:
                scope = [p["idx"] for p in paragraphs]
            else:
                scope = sorted({int(i) for i in scope_in if 0 <= int(i) < len(paragraphs)})
                if not scope:
                    scope = [p["idx"] for p in paragraphs]

            build = pinyin_service.build_scoped(para_texts, scope)
            if not build.ok:
                positions_text = "、".join(str(p) for p in build.mismatch_positions[:10])
                raise BizError(
                    ErrorCode.IMPORT_VALIDATION_FAILED,
                    f"正文里有无法拼音化的字（第 {positions_text} 个字），语文对齐会在那里漏字，请修改后再保存",
                    data={"mismatch_positions": build.mismatch_positions[:20], "mismatches": build.mismatches[:10]},
                )

            sets += [
                "content_raw = ?",
                "content_plain = ?",
                "content_pinyin = ?",
                "paragraphs = ?",
                "recite_scope = ?",
                "total_chars = ?",
            ]
            params += [
                content_raw,
                build.plain,
                build.pinyin,
                json.dumps(
                    [{**p, "recite": p["idx"] in set(scope)} for p in paragraphs], ensure_ascii=False
                ),
                json.dumps(scope, ensure_ascii=False),
                build.length,
            ]

        for col in _TEXT_PATCHABLE:
            if col not in changes:
                continue
            sets.append(f"{col} = ?")
            params.append(changes[col])

        if not sets:
            return ContentService.get_text_detail(text_id), history_affected

        sets.append("updated_at = ?")
        params.append(utc_now_iso())

        try:
            with write_tx() as conn:
                conn.execute(
                    f"UPDATE texts SET {', '.join(sets)} WHERE id = ?", (*params, text_id)
                )
                write_audit(
                    conn,
                    user_id=operator_id,
                    action="text.update",
                    target_type="text",
                    target_id=text_id,
                    detail={
                        "changes": {k: changes[k] for k in changes},
                        # 正文/范围改动在上方已 pop 出 changes，这里补个标记便于审计检索
                        "body_changed": body_changed,
                    },
                    ip=ip,
                )
        except sqlite3.IntegrityError as exc:
            # uk_texts = (unit_id, title)
            raise BizError(
                ErrorCode.PARAM_INVALID,
                f"该单元下已存在标题为「{changes.get('title')}」的课文",
            ) from exc

        return ContentService.get_text_detail(text_id), history_affected

    @staticmethod
    def delete_text(
        text_id: int,
        *,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """`DELETE /api/texts/{id}`（契约未定义，形态对齐 F8）。"""
        row = ContentService.get_text_row(text_id)

        if ContentService.text_has_records(text_id):
            count = ContentService.text_record_count(text_id)
            raise BizError(
                ErrorCode.CONTENT_HAS_RECORDS,
                f"该课文已有 {count} 条背诵记录，为保证统计完整只能停用",
                data={"record_count": count},
            )

        with write_tx() as conn:
            conn.execute("DELETE FROM texts WHERE id = ?", (text_id,))
            write_audit(
                conn,
                user_id=operator_id,
                action="text.delete",
                target_type="text",
                target_id=text_id,
                detail={"unit_id": row["unit_id"], "title": row["title"]},
                ip=ip,
            )

        return {"text_id": text_id, "deleted": True}


def _parse_scope(raw: Any) -> list[int]:
    """`recite_scope` JSON 字符串 → 升序去重的下标数组。"""
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: set[int] = set()
    for x in data:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return sorted(out)
