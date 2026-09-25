"""UserService —— WP-BE-02 账号域（登录 / 注册 / 学生名单 / 名单导入 / 偏好）。

**本模块是 `users` 表（学生侧）与 `GET|PUT /api/me/profile` 的唯一写入方。**

几个必须记住的口径：

1. **正确率**（契约 §8.1）：`(exact + loose + fuzzy) / (总数 − empty)`，
   分母为 0 时返回 `0.0`。全项目只在这里算，前端不重复计算（契约禁止清单第 8 条）。
2. **登录防枚举**（接口设计 §2.1 + 10a F1）：账号不存在 / 密码错 / 角色不匹配
   一律返回同一个 `20002`，且**都真的跑一次 bcrypt**（时序对齐），
   否则攻击者能靠响应耗时把账号列表枚举出来。
3. **名单导入是两步式**（10a F7/F8）：`preview` 只解析校验、**绝不落库**，
   结果暂存到 `core/preview_store`（30 分钟，BE-01 提供、BE-02/BE-03 共用）；
   `commit` 凭 token 一次性入库。
4. **重复导入 = 更新，且不重置密码**：已存在的学号只更新姓名/班级，
   **除非该行在 Excel 里显式填了「初始密码」**。否则一次手滑重导就会把
   全班密码刷回弱口令 —— 属安全事故，比「少更新一个字段」严重得多。
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from typing import Any, Iterable

from app.core.audit import write_audit
from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode
from app.core.excel import clean_text, to_halfwidth
from app.core.logging import get_logger
from app.core.preview_store import get as preview_get
from app.core.preview_store import pop as preview_pop
from app.core.preview_store import put as preview_put
from app.core.security import hash_password, verify_password
from app.schemas.auth import clean_username
from app.services.class_service import ClassService
from app.services.settings_service import SettingsService

logger = get_logger(__name__)

# ---- 契约 §8.1：正确率口径（唯一实现）----
CORRECT_MATCH_TYPES = ("exact", "loose", "fuzzy")

# ---- 需求 §4.2：未填初始密码时系统生成的弱口令 ----
DEFAULT_STUDENT_PASSWORD = "123456"

# ---- 接口设计 §2.1：连续 5 次密码错误锁定 10 分钟 ----
LOGIN_FAIL_THRESHOLD = 5
LOGIN_LOCK_SECONDS = 600

# ---- 名单导入 ----
MAX_IMPORT_ROWS = 5000
_PREVIEW_PAYLOAD_KIND = "students_import"
_CREDENTIALS_PAYLOAD_KIND = "student_credentials"
_PREVIEW_EXPIRED_MESSAGE = "导入凭证已失效（有效期 30 分钟），请重新上传名单文件"

# 模板列名（第一个是模板导出用的标准名，其余为兼容别名）
COL_USERNAME = ("学号", "账号", "用户名")
COL_REAL_NAME = ("姓名", "名字", "学生姓名")
COL_CLASS = ("班级", "班级名称", "所在班级")
COL_PASSWORD = ("初始密码", "密码")
COL_GRADE = ("年级",)
COL_SEMESTER = ("学期",)

_SEMESTER_MAP = {
    "first": "first",
    "second": "second",
    "上": "first",
    "下": "second",
    "上学期": "first",
    "下学期": "second",
    "1": "first",
    "2": "second",
}
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUMERIC_TAIL_RE = re.compile(r"^(\d+)\.0+$")

# 登录失败计数（内存，不落库 —— 10a「关键实现要点」：锁定期短，重启丢失可接受）
_lock_guard = threading.RLock()
_fail_times: dict[str, list[float]] = {}
_locked_until: dict[str, float] = {}

# 时序对齐用的假哈希（防枚举：账号不存在时也走一次 bcrypt 校验）
_timing_hash: str | None = None


def _get_timing_hash() -> str:
    global _timing_hash
    if _timing_hash is None:
        _timing_hash = hash_password("__timing_equalizer__")
    return _timing_hash


# ---------------------------------------------------------------------------
# 清洗小工具（Excel 里的坑都在这里）
# ---------------------------------------------------------------------------


def _numeric_aware(raw: Any) -> str:
    """Excel 数字单元格 → 字符串，**不产生 `.0` 尾巴**。

    学号 / 纯数字密码在 Excel 里都是数字类型：`123456` 会被 openpyxl 读成
    `123456.0`。直接 `str()` 会得到 `"123456.0"` —— 学号错、密码也错。
    """
    if isinstance(raw, bool):
        return str(raw)
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    if isinstance(raw, int):
        return str(raw)
    s = to_halfwidth(clean_text(raw)).strip()
    m = _NUMERIC_TAIL_RE.match(s)
    return m.group(1) if m else s


def _pick(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for a in aliases:
        if a in row:
            return row[a]
    return None


def _has_column(rows: list[dict[str, Any]], aliases: tuple[str, ...]) -> bool:
    return any(a in r for r in rows for a in aliases)


def _parse_grade(raw: Any) -> int | None:
    s = _numeric_aware(raw)
    if not s:
        return None
    if s.isdigit():
        g = int(s)
        return g if 1 <= g <= 9 else None
    for ch, num in _CN_NUM.items():
        if ch in s:
            return num
    return None


def _parse_semester(raw: Any) -> str | None:
    return _SEMESTER_MAP.get(_numeric_aware(raw).strip())


# ---------------------------------------------------------------------------
# UserService
# ---------------------------------------------------------------------------


class UserService:
    """账号域业务。全部为类方法 / 静态方法。"""

    # ================================================================== 登录锁定

    @staticmethod
    def _lock_key(username: str) -> str:
        return clean_username(username).lower()

    @classmethod
    def lock_remaining_seconds(cls, username: str) -> int:
        """剩余锁定秒数；`0` 表示未被锁定。"""
        key = cls._lock_key(username)
        with _lock_guard:
            until = _locked_until.get(key, 0.0)
            if until <= time.time():
                _locked_until.pop(key, None)
                return 0
            return int(until - time.time())

    @classmethod
    def _record_failure(cls, key: str) -> None:
        now = time.time()
        with _lock_guard:
            arr = [t for t in _fail_times.get(key, []) if now - t <= LOGIN_LOCK_SECONDS]
            arr.append(now)
            if len(arr) >= LOGIN_FAIL_THRESHOLD:
                _locked_until[key] = now + LOGIN_LOCK_SECONDS
                _fail_times.pop(key, None)
                logger.warning(
                    "账号连续 %d 次密码错误，锁定 %d 秒（key=%s）",
                    LOGIN_FAIL_THRESHOLD,
                    LOGIN_LOCK_SECONDS,
                    key,
                )
            else:
                _fail_times[key] = arr

    @classmethod
    def _clear_failure(cls, key: str) -> None:
        with _lock_guard:
            _fail_times.pop(key, None)
            _locked_until.pop(key, None)

    @classmethod
    def reset_login_state(cls) -> None:
        """清空登录失败/锁定状态（测试用）。"""
        with _lock_guard:
            _fail_times.clear()
            _locked_until.clear()

    # ================================================================== 登录 / 注册

    @classmethod
    def authenticate(
        cls,
        *,
        username: str,
        password: str,
        role: str,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """校验账号密码，成功则更新 `last_login_at` 并返回用户信息。

        Raises:
            BizError: `10005` 账号被锁定 / `20002` 账号或密码不正确 / `20003` 账号已停用。
        """
        name = clean_username(username)
        key = cls._lock_key(name)

        remain = cls.lock_remaining_seconds(name)
        if remain > 0:
            minutes = max(1, remain // 60)
            raise BizError(
                ErrorCode.TOO_MANY_REQUESTS,
                f"密码错误次数过多，请 {minutes} 分钟后再试",
            )

        with read_conn() as conn:
            row = conn.execute(
                """
                SELECT id, role, username, real_name, password_hash, class_id,
                       register_source, preferred_grade, preferred_semester,
                       status, last_login_at, created_at
                FROM users
                WHERE username = ?
                """,
                (name,),
            ).fetchone()

        # 防枚举：账号不存在 / 角色不匹配时也走一次 bcrypt，让耗时与「密码错」一致
        if row is None or row["role"] != role:
            verify_password(password, _get_timing_hash())
            cls._record_failure(key)
            raise BizError(ErrorCode.BAD_CREDENTIALS)

        if not verify_password(password, row["password_hash"]):
            cls._record_failure(key)
            raise BizError(ErrorCode.BAD_CREDENTIALS)

        # 密码正确但账号被停用 → 20003（10a U3）
        if row["status"] != "active":
            raise BizError(ErrorCode.ACCOUNT_DISABLED)

        cls._clear_failure(key)

        now = utc_now_iso()
        with write_tx() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (now, now, row["id"]),
            )

        user = dict(row)
        user.pop("password_hash", None)
        user["last_login_at"] = now
        user["class_name"] = ClassService.name_map([row["class_id"]]).get(row["class_id"])
        logger.info("登录成功 user_id=%s role=%s username=%s", row["id"], role, row["username"])
        return user

    @classmethod
    def register_student(
        cls,
        *,
        username: str,
        real_name: str,
        password: str,
        class_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """学生自主注册：`role='student'`、`register_source='self'`、注册即可用。

        Raises:
            BizError: `20001` 学号已存在（**绝不覆盖原账号**，需求 §3.1）/
                      `20004` 所选班级不存在。
        """
        name = clean_username(username)
        now = utc_now_iso()

        with write_tx() as conn:
            # 校验班级存在性（不存在抛 20004）；顺带拿到年级/学期用于回填偏好
            grade, semester = ClassService.grade_semester(conn, class_id)

            exists = conn.execute(
                "SELECT id FROM users WHERE username = ?", (name,)
            ).fetchone()
            if exists is not None:
                raise BizError(
                    ErrorCode.USERNAME_EXISTS,
                    f"该学号「{name}」已被注册，请直接登录或联系管理者重置密码",
                )

            cur = conn.execute(
                """
                INSERT INTO users
                    (role, username, real_name, password_hash, class_id,
                     register_source, preferred_grade, preferred_semester,
                     status, created_at)
                VALUES ('student', ?, ?, ?, ?, 'self', ?, ?, 'active', ?)
                """,
                (name, real_name, hash_password(password), class_id, grade, semester, now),
            )
            new_id = int(cur.lastrowid or 0)
            write_audit(
                conn,
                user_id=new_id,
                action="user.register",
                target_type="student",
                target_id=new_id,
                detail={"username": name, "class_id": class_id},
                ip=ip,
            )

        logger.info("学生自主注册成功 id=%s username=%s class_id=%s", new_id, name, class_id)
        return cls.get_user(new_id)

    # ================================================================== 用户读取

    @staticmethod
    def get_user(user_id: int) -> dict[str, Any]:
        """按 ID 取用户（含 `class_name`）。不存在抛 `10004`。"""
        with read_conn() as conn:
            row = conn.execute(
                """
                SELECT id, role, username, real_name, class_id, register_source,
                       preferred_grade, preferred_semester, status,
                       last_login_at, created_at
                FROM users
                WHERE id = ?
                """,
                (int(user_id),),
            ).fetchone()
        if row is None:
            raise BizError(ErrorCode.NOT_FOUND, f"用户不存在（id={user_id}）")
        user = dict(row)
        user["class_name"] = ClassService.name_map([row["class_id"]]).get(row["class_id"])
        return user

    # ================================================================== 学生列表

    @staticmethod
    def list_students(
        *,
        class_id: int | None = None,
        unassigned_only: bool = False,
        status: str | None = None,
        register_source: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """分页查学生，附带 `practice_count` 与 `accuracy`。

        Args:
            unassigned_only: 为 `True` 时只查未分班学生（`class_id IS NULL`）；
                             与 `class_id` 互斥，优先本参数。

        Returns:
            `(items, total, unassigned_count)`。
        """
        where: list[str] = ["u.role = 'student'"]
        params: list[Any] = []

        if unassigned_only:
            where.append("u.class_id IS NULL")
        elif class_id is not None:
            where.append("u.class_id = ?")
            params.append(int(class_id))

        if status:
            where.append("u.status = ?")
            params.append(status)
        if register_source:
            where.append("u.register_source = ?")
            params.append(register_source)
        if keyword:
            where.append("(u.username LIKE ? OR u.real_name LIKE ?)")
            like = f"%{keyword.strip()}%"
            params.extend([like, like])

        where_sql = "WHERE " + " AND ".join(where)

        # 答题聚合：一次分组做完，避免 N+1（40 人 × 每页 20 行也不会有额外查询）
        stats_sql = f"""
            SELECT u.id, u.username, u.real_name, u.class_id,
                   c.name AS class_name,
                   u.register_source, u.status,
                   u.preferred_grade, u.preferred_semester,
                   u.last_login_at, u.created_at,
                   COALESCE(a.total, 0)   AS practice_count,
                   COALESCE(a.counted, 0) AS counted_count,
                   COALESCE(a.correct, 0) AS correct_count
            FROM users u
            LEFT JOIN classes c ON c.id = u.class_id
            LEFT JOIN (
                SELECT student_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN match_type <> 'empty' THEN 1 ELSE 0 END) AS counted,
                       SUM(CASE WHEN match_type IN ('exact', 'loose', 'fuzzy')
                                THEN 1 ELSE 0 END) AS correct
                FROM answers
                GROUP BY student_id
            ) a ON a.student_id = u.id
            {where_sql}
            ORDER BY u.id DESC
            LIMIT ? OFFSET ?
        """

        with read_conn() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM users u {where_sql}", params
                ).fetchone()["n"]
            )
            rows = conn.execute(
                stats_sql, [*params, page_size, (page - 1) * page_size]
            ).fetchall()
            unassigned = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM users
                    WHERE role = 'student' AND class_id IS NULL
                    """
                ).fetchone()["n"]
            )

        items: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            counted = int(d.pop("counted_count", 0) or 0)
            correct = int(d.pop("correct_count", 0) or 0)
            d["practice_count"] = int(d.get("practice_count", 0) or 0)
            d["accuracy"] = UserService._accuracy(correct, counted)
            items.append(d)

        return items, total, unassigned

    @staticmethod
    def _accuracy(correct: int, counted: int) -> float:
        """契约 §8.1：`(exact + loose + fuzzy) / (总数 − empty)`；分母 0 → `0.0`。

        **全项目唯一的正确率公式实现**（列表接口与单学生接口共用本函数）。
        """
        return round(correct / counted, 4) if counted > 0 else 0.0

    @staticmethod
    def student_stats(student_id: int) -> dict[str, Any]:
        """单个学生的 `practice_count` / `accuracy`（与列表接口同口径）。"""
        with read_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN match_type <> 'empty' THEN 1 ELSE 0 END) AS counted,
                       SUM(CASE WHEN match_type IN ('exact', 'loose', 'fuzzy')
                                THEN 1 ELSE 0 END) AS correct
                FROM answers
                WHERE student_id = ?
                """,
                (int(student_id),),
            ).fetchone()
        return {
            "practice_count": int(row["total"] or 0),
            "accuracy": UserService._accuracy(
                int(row["correct"] or 0), int(row["counted"] or 0)
            ),
        }

    # ================================================================== 名单导入

    @staticmethod
    def _resolve_class_id(
        index: list[dict[str, Any]],
        *,
        name: str,
        grade: int | None,
        semester: str | None,
    ) -> tuple[int | None, str | None]:
        """按「年级 + 学期 + 名称」匹配班级。

        Returns:
            `(class_id, error_reason)`；`class_id` 为 `None` 时看 `error_reason`：
            空串表示「该行没填班级」（合法，进未分班）。
        """
        if not name:
            return None, ""
        cands = [c for c in index if c["name"] == name]
        if grade is not None:
            cands = [c for c in cands if c["grade"] == grade]
        if semester is not None:
            cands = [c for c in cands if c["semester"] == semester]
        if not cands:
            return None, f"班级「{name}」不存在（名单导入不会自动建班，请先在班级管理中创建）"
        if len(cands) > 1:
            return None, f"班级「{name}」匹配到 {len(cands)} 个，请在名单中补充「年级」「学期」列"
        return int(cands[0]["id"]), None

    @classmethod
    def import_preview(
        cls,
        file_bytes: bytes,
        *,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """学生名单导入第一步：解析 + 校验，**不落库**（10a F7）。

        Raises:
            BizError: `40001` 不是合法 xlsx / `40002` 缺少必需列 / `40003` 行数超限。
        """
        from app.core.excel import read_xlsx  # 延迟导入：避免与 openpyxl 的加载顺序耦合

        try:
            rows = read_xlsx(file_bytes)
        except Exception as exc:  # noqa: BLE001 - openpyxl 抛什么都有可能
            logger.warning("学生名单解析失败：%s", exc)
            raise BizError(
                ErrorCode.FILE_FORMAT_INVALID,
                "文件无法解析，请下载最新模板后重新填写（仅支持 .xlsx）",
            ) from exc

        if not rows:
            raise BizError(ErrorCode.FILE_FORMAT_INVALID, "文件里没有数据行，请检查后重新上传")
        if len(rows) > MAX_IMPORT_ROWS:
            raise BizError(
                ErrorCode.IMPORT_VALIDATION_FAILED,
                f"单次最多导入 {MAX_IMPORT_ROWS} 行，当前 {len(rows)} 行，请拆分后分批导入",
            )
        if not _has_column(rows, COL_USERNAME):
            raise BizError(ErrorCode.TEMPLATE_COLUMN_MISSING, "缺少必需列「学号」，请下载最新模板")
        if not _has_column(rows, COL_REAL_NAME):
            raise BizError(ErrorCode.TEMPLATE_COLUMN_MISSING, "缺少必需列「姓名」，请下载最新模板")

        class_index = ClassService.active_index()

        # 库中已存在的学号（一次查完，避免逐行查库）
        with read_conn() as conn:
            existing = {
                r["username"].lower(): r["id"]
                for r in conn.execute(
                    "SELECT id, username FROM users WHERE role = 'student'"
                ).fetchall()
            }

        seen_in_file: dict[str, int] = {}
        valid_rows: list[dict[str, Any]] = []
        invalid_rows: list[dict[str, Any]] = []
        duplicated_rows: list[dict[str, Any]] = []

        for idx, row in enumerate(rows):
            row_no = idx + 2  # 表头占第 1 行
            raw_username = _pick(row, COL_USERNAME)
            raw_name = _pick(row, COL_REAL_NAME)
            raw_class = _pick(row, COL_CLASS)
            raw_pwd = _pick(row, COL_PASSWORD)
            raw_display = [
                _numeric_aware(raw_username),
                _numeric_aware(raw_name),
                _numeric_aware(raw_class),
                _numeric_aware(raw_pwd),
            ]

            def _bad(reason: str) -> None:
                invalid_rows.append({"row": row_no, "reason": reason, "raw": raw_display})

            username = _numeric_aware(raw_username)
            real_name = _numeric_aware(raw_name)
            class_name = _numeric_aware(raw_class)
            password = _numeric_aware(raw_pwd)

            if not username:
                _bad("学号不能为空")
                continue
            if len(username) > 32:
                _bad(f"学号「{username}」超过 32 个字符")
                continue
            if re.search(r"\s", username):
                _bad(f"学号「{username}」不能包含空格")
                continue
            if username.lower() in seen_in_file:
                _bad(f"学号「{username}」在本文件内重复（第 {seen_in_file[username.lower()]} 行已出现）")
                continue
            if not real_name:
                _bad("姓名不能为空")
                continue
            if len(real_name) > 20:
                _bad(f"姓名「{real_name}」超过 20 个字")
                continue
            if password and not (6 <= len(password) <= 20):
                _bad(f"初始密码需 6–20 位（学号 {username} 当前为 {len(password)} 位）")
                continue

            class_id, reason = cls._resolve_class_id(
                class_index,
                name=class_name,
                grade=_parse_grade(_pick(row, COL_GRADE)),
                semester=_parse_semester(_pick(row, COL_SEMESTER)),
            )
            if reason:
                _bad(reason)
                continue

            seen_in_file[username.lower()] = row_no
            exist_id = existing.get(username.lower())
            action = "update" if exist_id else "create"
            if exist_id:
                duplicated_rows.append(
                    {"row": row_no, "username": username, "user_id": exist_id, "action": "update"}
                )

            valid_rows.append(
                {
                    "row": row_no,
                    "username": username,
                    "real_name": real_name,
                    "class_id": class_id,
                    "class_name": class_name or None,
                    "password": password or None,
                    "action": action,
                    "user_id": exist_id,
                }
            )

        summary = {
            "total": len(rows),
            "valid": len(valid_rows),
            "invalid": len(invalid_rows),
            "duplicated": len(duplicated_rows),
        }

        token = preview_put(
            {
                "kind": _PREVIEW_PAYLOAD_KIND,
                "operator_id": operator_id,
                "ip": ip,
                "summary": summary,
                "rows": valid_rows,
                "invalid_rows": invalid_rows,
                "duplicated_rows": duplicated_rows,
            }
        )

        logger.info(
            "学生名单校验完成 operator=%s total=%s valid=%s invalid=%s duplicated=%s",
            operator_id,
            summary["total"],
            summary["valid"],
            summary["invalid"],
            summary["duplicated"],
        )

        return {
            "preview_token": token,
            "summary": summary,
            "invalid_rows": invalid_rows,
            "duplicated_rows": duplicated_rows,
            "error_report_url": f"/api/import/report/{token}",
        }

    @staticmethod
    def load_import_preview(token: str) -> dict[str, Any]:
        """取回预览数据（错误报告接口用）。

        Raises:
            BizError: `40003` token 不存在或已过期。
        """
        payload = preview_get(token)
        if not payload or payload.get("kind") != _PREVIEW_PAYLOAD_KIND:
            raise BizError(
                ErrorCode.IMPORT_VALIDATION_FAILED,
                _PREVIEW_EXPIRED_MESSAGE,
                data={"reason": "preview_token_expired"},
            )
        return payload

    @classmethod
    def import_commit(
        cls,
        *,
        preview_token: str,
        default_password: str | None = None,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """学生名单导入第二步：凭 token 入库（10a F8）。

        Returns:
            `{created, updated, skipped, credentials_url, classes}`。
        """
        # pop：取出即删除，防止同一个 token 被提交两次
        payload = preview_pop(preview_token)
        if not payload or payload.get("kind") != _PREVIEW_PAYLOAD_KIND:
            raise BizError(
                ErrorCode.IMPORT_VALIDATION_FAILED,
                _PREVIEW_EXPIRED_MESSAGE,
                data={"reason": "preview_token_expired"},
            )

        rows: list[dict[str, Any]] = payload.get("rows") or []
        fallback_pwd = default_password or DEFAULT_STUDENT_PASSWORD
        now = utc_now_iso()
        created = 0
        updated = 0
        class_ids: set[int] = set()
        credentials: list[dict[str, Any]] = []

        with write_tx() as conn:
            for r in rows:
                class_id = r.get("class_id")
                if class_id is not None:
                    class_ids.add(int(class_id))

                if r.get("action") == "update":
                    sets = ["real_name = ?", "class_id = ?", "updated_at = ?"]
                    params: list[Any] = [r["real_name"], class_id, now]

                    # ⚠️ 只在该行显式填了密码时才改密码（见模块 docstring 第 4 条）
                    if r.get("password"):
                        sets.append("password_hash = ?")
                        params.append(hash_password(r["password"]))

                    # 归班后按班级回填年级学期偏好（为空才回填，不覆盖学生自己的选择）
                    if class_id is not None:
                        grade, semester = ClassService.grade_semester(conn, int(class_id))
                        if grade is not None:
                            sets.append("preferred_grade = COALESCE(preferred_grade, ?)")
                            params.append(grade)
                        if semester is not None:
                            sets.append("preferred_semester = COALESCE(preferred_semester, ?)")
                            params.append(semester)

                    params.append(int(r["user_id"]))
                    conn.execute(
                        f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params
                    )
                    updated += 1
                    # 更新行不重置密码：对照表里留空，并标注原因
                    credentials.append(
                        {
                            "username": r["username"],
                            "real_name": r["real_name"],
                            "class_id": class_id,
                            "class_name": r.get("class_name"),
                            "password": r.get("password") or "",
                            "note": "" if r.get("password") else "已存在账号，密码未变更",
                        }
                    )
                else:
                    pwd = r.get("password") or fallback_pwd
                    grade = semester = None
                    if class_id is not None:
                        grade, semester = ClassService.grade_semester(conn, int(class_id))
                    new_id = conn.execute(
                        """
                        INSERT INTO users
                            (role, username, real_name, password_hash, class_id,
                             register_source, preferred_grade, preferred_semester,
                             status, created_at)
                        VALUES ('student', ?, ?, ?, ?, 'import', ?, ?, 'active', ?)
                        """,
                        (
                            r["username"],
                            r["real_name"],
                            hash_password(pwd),
                            class_id,
                            grade,
                            semester,
                            now,
                        ),
                    ).lastrowid
                    created += 1
                    credentials.append(
                        {
                            "username": r["username"],
                            "real_name": r["real_name"],
                            "class_id": class_id,
                            "class_name": r.get("class_name"),
                            "password": pwd,
                            "note": "",
                        }
                    )
                    # user_id 只用于审计追溯，此处不额外写库
                    _ = new_id

            single_class = next(iter(class_ids)) if len(class_ids) == 1 else None
            write_audit(
                conn,
                user_id=operator_id,
                action="student.import",
                target_type="class" if single_class else "student",
                target_id=single_class,
                detail={
                    "created": created,
                    "updated": updated,
                    "class_ids": sorted(class_ids),
                    "source_ip": ip,
                },
                ip=ip,
            )

        # ---- 生成「学号-初始密码」对照表快照 ----
        # bcrypt 是单向的，事后无法反推初始密码，因此在这一刻把明文快照存进
        # preview_store（30 分钟自动过期，落盘文件在 .gitignore 内）。
        cred_token = preview_put(
            {
                "kind": _CREDENTIALS_PAYLOAD_KIND,
                "class_id": single_class,
                "items": credentials,
            }
        )
        class_for_url = single_class if single_class is not None else 0
        credentials_url = (
            f"/api/classes/{class_for_url}/students/credentials?token={cred_token}"
        )

        logger.info(
            "学生名单入库完成 operator=%s created=%s updated=%s classes=%s",
            operator_id,
            created,
            updated,
            sorted(class_ids),
        )

        return {
            "created": created,
            "updated": updated,
            "skipped": len(payload.get("invalid_rows") or []),
            "classes": sorted(class_ids),
            "credentials_url": credentials_url,
        }

    @staticmethod
    def credentials_items(token: str) -> list[dict[str, Any]]:
        """取回对照表快照。

        Raises:
            BizError: `40003` token 不存在或已过期。
        """
        payload = preview_get(token)
        if not payload or payload.get("kind") != _CREDENTIALS_PAYLOAD_KIND:
            raise BizError(
                ErrorCode.IMPORT_VALIDATION_FAILED,
                "对照表已过期（有效期 30 分钟），请重新导入名单或到学生列表重置密码",
                data={"reason": "credentials_token_expired"},
            )
        return payload.get("items") or []

    @staticmethod
    def class_students_brief(class_id: int | None) -> list[dict[str, Any]]:
        """某班学生简表（`class_id=None` 表示全部班级），用于重新导出名单。"""
        where = "WHERE u.role = 'student'"
        params: list[Any] = []
        if class_id is not None:
            where += " AND u.class_id = ?"
            params.append(int(class_id))
        with read_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT u.username, u.real_name, u.class_id, c.name AS class_name
                FROM users u
                LEFT JOIN classes c ON c.id = u.class_id
                {where}
                ORDER BY u.class_id ASC, u.id ASC
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # ================================================================== 学生写操作

    @classmethod
    def update_student(
        cls,
        *,
        student_id: int,
        fields: dict[str, Any],
        action: str | None = None,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """`PATCH /api/students/{id}` —— 改姓名 / 状态 / 班级（10a F12）。

        Args:
            fields: **只包含调用方显式传了的字段**（由接口层用
                    `model_fields_set` 过滤），`class_id=None` 表示移出班级。
            action: 审计动作名；不传时按改动内容推断
                    （停用 → `student.disable`，其余 → `student.update`）。

        Raises:
            BizError: `10004` 学生不存在 / `10001` 该用户不是学生 / `20004` 班级不存在。
        """
        if not fields:
            raise BizError(ErrorCode.PARAM_INVALID, "没有需要修改的内容")

        now = utc_now_iso()
        with write_tx() as conn:
            row = conn.execute(
                "SELECT id, role, real_name, status, class_id FROM users WHERE id = ?",
                (int(student_id),),
            ).fetchone()
            if row is None:
                raise BizError(ErrorCode.NOT_FOUND, f"学生不存在（id={student_id}）")
            if row["role"] != "student":
                raise BizError(ErrorCode.PARAM_INVALID, "该账号不是学生账号")

            sets: list[str] = []
            params: list[Any] = []
            detail: dict[str, Any] = {}

            if "real_name" in fields:
                sets.append("real_name = ?")
                params.append(fields["real_name"])
                detail["real_name"] = fields["real_name"]
            if "status" in fields:
                sets.append("status = ?")
                params.append(fields["status"])
                detail["status"] = fields["status"]
            if "class_id" in fields:
                new_class = fields["class_id"]
                # 归班顺带回填偏好；移出班级不回退偏好（学生的选择应当保留）
                if new_class is not None:
                    grade, semester = ClassService.grade_semester(conn, int(new_class))
                    if grade is not None:
                        sets.append("preferred_grade = COALESCE(preferred_grade, ?)")
                        params.append(grade)
                    if semester is not None:
                        sets.append("preferred_semester = COALESCE(preferred_semester, ?)")
                        params.append(semester)
                sets.append("class_id = ?")
                params.append(new_class)
                detail["class_id"] = new_class

            sets.append("updated_at = ?")
            params.append(now)
            params.append(int(student_id))
            conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)

            if action is None:
                # 停用是敏感操作（会让该生所有旧 token 立即失效），审计里单独区分
                action = (
                    "student.disable"
                    if fields.get("status") == "disabled"
                    else "student.update"
                )
            write_audit(
                conn,
                user_id=operator_id,
                action=action,
                target_type="student",
                target_id=int(student_id),
                detail=detail,
                ip=ip,
            )

        return cls.get_user(int(student_id))

    @classmethod
    def assign_class(
        cls,
        *,
        student_id: int,
        class_id: int | None,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """单个归班（10a F9）。`class_id=None` 表示移出班级。"""
        return cls.update_student(
            student_id=student_id,
            fields={"class_id": class_id},
            action="student.assign_class",
            operator_id=operator_id,
            ip=ip,
        )

    @classmethod
    def batch_assign_class(
        cls,
        *,
        student_ids: Iterable[int],
        class_id: int | None,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """批量归班（10a F10）。返回成功 / 失败明细，**部分失败不回滚全部**。

        逐条处理的取舍：一条失败（比如 ID 不存在）不该把整批 40 人退回去，
        否则管理者要自己找出是哪一条坏的。明细里逐条给出原因即可。
        """
        ok_ids: list[int] = []
        failed: list[dict[str, Any]] = []

        # 班级先校验一次，避免每条都查（不存在时整批都没有意义）
        if class_id is not None:
            with read_conn() as conn:
                ClassService.grade_semester(conn, int(class_id))

        for sid in student_ids:
            try:
                cls.assign_class(
                    student_id=int(sid),
                    class_id=class_id,
                    operator_id=operator_id,
                    ip=ip,
                )
                ok_ids.append(int(sid))
            except BizError as exc:
                failed.append({"student_id": int(sid), "code": exc.code, "message": exc.message})

        return {
            "success_count": len(ok_ids),
            "failed_count": len(failed),
            "success_ids": ok_ids,
            "failed": failed,
            "unassigned_count": ClassService.unassigned_count(),
        }

    @classmethod
    def reset_password(
        cls,
        *,
        student_id: int,
        new_password: str,
        operator_id: int | None = None,
        ip: str | None = None,
    ) -> None:
        """重置学生密码（10a F11）。必须写审计日志。"""
        now = utc_now_iso()
        with write_tx() as conn:
            row = conn.execute(
                "SELECT id, role, username FROM users WHERE id = ?", (int(student_id),)
            ).fetchone()
            if row is None:
                raise BizError(ErrorCode.NOT_FOUND, f"学生不存在（id={student_id}）")
            if row["role"] != "student":
                raise BizError(ErrorCode.PARAM_INVALID, "该账号不是学生账号")

            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(new_password), now, int(student_id)),
            )
            write_audit(
                conn,
                user_id=operator_id,
                action="student.reset_password",
                target_type="student",
                target_id=int(student_id),
                detail={"username": row["username"]},
                ip=ip,
            )
        logger.info("重置学生密码 operator=%s student_id=%s", operator_id, student_id)

    # ================================================================== 个人偏好

    @staticmethod
    def get_profile(user_id: int) -> dict[str, Any]:
        """`GET /api/me/profile` —— 个人信息 + 年级学期偏好 + 护眼设置。"""
        user = UserService.get_user(user_id)
        user["eye_rest_enabled"] = SettingsService.get_bool("eye_rest_enabled", True)
        user["eye_warm_mode_auto"] = SettingsService.get_bool("eye_warm_mode_auto", True)
        return user

    @staticmethod
    def update_profile(
        *,
        user_id: int,
        preferred_grade: int | None = None,
        preferred_semester: str | None = None,
    ) -> dict[str, Any]:
        """`PUT /api/me/profile`（接口设计 §8.2）—— 只更新传了的字段。"""
        sets: list[str] = []
        params: list[Any] = []
        if preferred_grade is not None:
            sets.append("preferred_grade = ?")
            params.append(int(preferred_grade))
        if preferred_semester is not None:
            sets.append("preferred_semester = ?")
            params.append(preferred_semester)
        if not sets:
            raise BizError(ErrorCode.PARAM_INVALID, "没有需要修改的内容")

        sets.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(int(user_id))

        with write_tx() as conn:
            cur = conn.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params
            )
            if cur.rowcount == 0:
                raise BizError(ErrorCode.NOT_FOUND, f"用户不存在（id={user_id}）")

        return UserService.get_profile(user_id)

    # ================================================================== 导入错误报告

    @staticmethod
    def build_error_report(payload: dict[str, Any]) -> bytes:
        """把 `invalid_rows` 渲染成 xlsx 错误报告（10a F7 的「错误报告地址」）。

        `report_headers` 若存在则用它（BE-03 的内容导入表头各不相同：词条 14 列、
        课文 8 列）；不存在则用学生名单的固定 6 列，**行为与改造前完全一致**。
        """
        from app.core.excel import write_xlsx

        headers = payload.get("report_headers") or [
            "原文件行号",
            "错误原因",
            "学号",
            "姓名",
            "班级",
            "初始密码",
        ]
        extra_cols = max(len(headers) - 2, 0)
        rows = [
            [
                item.get("row"),
                item.get("reason"),
                *list(item.get("raw") or [])[:extra_cols],
            ]
            for item in (payload.get("invalid_rows") or [])
        ]
        return write_xlsx(headers, rows, sheet_name="错误明细")
