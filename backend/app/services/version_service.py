"""内容版本快照服务 —— WP-2I（二期工作包 9）。

写入方唯一性（契约 §15.1）：`content_versions` 表**只由本模块写入**。
`units` / `words` / `texts` 三张表仍由 BE-03 的 `ContentService` 独占，
本服务只在其写操作后**追加一条快照**，**不碰那三张表的业务语义**。

### 快照语义

- `snapshot` = 实体**当前全字段行**的 JSON（仅真实列，由 `PRAGMA table_info` 过滤）。
- 删除操作**在删除前**捕获「删除前」状态；回滚到删除快照 = 重建该行（保留 id）。
- 回滚**绝不删除**任何实体，故天然尊重「有记录只能停用不能删」（契约 §4.5 约束 3 / `30003`）。

### 接口层调用约定

- 写点在 `app/api/units.py` / `words.py` / `texts.py` 调用便捷方法 `record_now(...)`。
- 回滚接口 `app/api/content_versions.py` 调用 `rollback(conn, ...)`。
- 两个方法都要求调用方在写事务内 / 自管事务（契约 §4.3 写操作串行化）。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.db import read_conn, utc_now_iso, write_tx
from app.core.errors import BizError, ErrorCode

# 实体类型 → 真实表名（不允许其它类型写入本表）
_TABLES: dict[str, str] = {"unit": "units", "word": "words", "text": "texts"}

# 合约允许的版本操作（与 `content_versions.op` 的 CHECK 约束一致）
_OPS: tuple[str, ...] = ("create", "update", "disable", "delete")


class VersionService:
    """内容版本快照的写入与读取（content_versions 表唯一写入方）。"""

    @staticmethod
    def _table(entity_type: str) -> str:
        """实体类型 → 表名，非法类型直接拒绝（避免拼接任意表名）。"""
        try:
            return _TABLES[entity_type]
        except KeyError:
            raise BizError(ErrorCode.PARAM_INVALID, f"不支持的实体类型：{entity_type}")

    @staticmethod
    def _real_columns(conn, table: str) -> list[str]:
        """取表的真实列名（含 id），用于过滤快照与还原，避免写入派生/不存在的列。"""
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [r["name"] for r in rows]

    @staticmethod
    def capture_raw(entity_type: str, entity_id: int) -> dict[str, Any] | None:
        """读取实体的**原始行**（仅真实列），用于做快照。

        读不到（已被物理删除）返回 None —— 删除类操作在删除**前**调用本函数，
        一定能拿到；回滚到「删除快照」时不需要它（快照已随版本存好）。
        """
        table = VersionService._table(entity_type)
        with read_conn() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def record_version(
        conn,
        entity_type: str,
        entity_id: int,
        snapshot: dict[str, Any],
        op: str,
        operator_id: int | None = None,
    ) -> int:
        """写一条版本快照，`version` = 该实体当前最大 version + 1。

        必须在调用方已有的 `write_tx` 内调用（契约 §4.3 写操作串行化）。
        `op` 必须是 `create|update|disable|delete`，否则拒绝。

        Returns:
            写入的版本号。
        """
        if op not in _OPS:
            raise BizError(ErrorCode.PARAM_INVALID, f"不支持的版本操作：{op}")

        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS m "
            "FROM content_versions WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        ).fetchone()
        next_version = int(row["m"]) + 1

        conn.execute(
            """
            INSERT INTO content_versions
                (entity_type, entity_id, version, snapshot, op, operator_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_type,
                entity_id,
                next_version,
                json.dumps(snapshot, ensure_ascii=False),
                op,
                operator_id,
                utc_now_iso(),
            ),
        )
        return next_version

    @staticmethod
    def record_now(
        *,
        entity_type: str,
        entity_id: int,
        op: str,
        operator_id: int | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> int:
        """便捷方法：自动捕获快照并开启一个写事务。供接口层钩子调用。

        删除类操作应在删除**前**传入 `snapshot`（即删除前的整行）；
        其余操作可不传，本函数自动读取当前（最新）状态做快照。
        """
        if snapshot is None:
            snapshot = VersionService.capture_raw(entity_type, entity_id)
            if snapshot is None:
                # 实体已不存在（理论上删除前不会走到这里）—— 回退为空快照，保证写链不断
                snapshot = {}
        with write_tx() as conn:
            return VersionService.record_version(
                conn, entity_type, entity_id, snapshot, op, operator_id
            )

    @staticmethod
    def list_versions(
        *,
        entity_type: str,
        entity_id: int,
        with_snapshot: bool = False,
    ) -> list[dict[str, Any]]:
        """查询某实体的版本历史（读，供 `GET /api/content/versions`）。

        - `operator_name`：LEFT JOIN users 带出操作人姓名（人已删显示 NULL）。
        - `with_snapshot=True` 时附带解析后的快照 JSON（版本页 diff 展示用，
          仅单实体查询，量可控；默认不带，避免列表接口搬运大 JSON）。
        """
        VersionService._table(entity_type)  # 校验实体类型
        snap_col = ", cv.snapshot" if with_snapshot else ""
        with read_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT cv.id, cv.entity_type, cv.entity_id, cv.version, cv.op,
                       cv.operator_id, cv.created_at,
                       ou.real_name AS operator_name{snap_col}
                FROM content_versions cv
                LEFT JOIN users ou ON ou.id = cv.operator_id
                WHERE cv.entity_type = ? AND cv.entity_id = ?
                ORDER BY cv.version ASC
                """,
                (entity_type, entity_id),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            if with_snapshot:
                raw = item.pop("snapshot", None)
                item["snapshot"] = json.loads(raw) if raw else {}
            items.append(item)
        return items

    # 实体名兜底取法：实体行还在用活表字段；已被物理删除（delete 快照）从
    # 快照 JSON 里捞 —— json_extract 需要 SQLite JSON1（Python 3.13 自带，已启用）。
    _RECENT_SELECT = """
        SELECT cv.id, cv.entity_type, cv.entity_id, cv.version, cv.op,
               cv.operator_id, cv.created_at,
               ou.real_name AS operator_name,
               CASE cv.entity_type
                   WHEN 'unit' THEN COALESCE(u.name, json_extract(cv.snapshot, '$.name'))
                   WHEN 'word' THEN COALESCE(w.en || ' ' || w.cn,
                                             json_extract(cv.snapshot, '$.en'))
                   ELSE COALESCE(t.title, json_extract(cv.snapshot, '$.title'))
               END AS entity_name,
               CASE WHEN cv.entity_type = 'unit' THEN u.subject ELSE wu.subject END AS subject,
               CASE WHEN cv.entity_type = 'unit' THEN NULL ELSE wu.grade END AS grade,
               CASE WHEN cv.entity_type = 'unit' THEN NULL ELSE wu.semester END AS semester,
               CASE WHEN cv.entity_type = 'unit' THEN NULL ELSE wu.name END AS unit_name
    """

    # 词/课文挂的单元单独再 JOIN 一次 units（wu），与单元实体自身的 u 区分开。
    _RECENT_FROM = """
        FROM content_versions cv
        LEFT JOIN units u  ON cv.entity_type = 'unit' AND u.id = cv.entity_id
        LEFT JOIN words w  ON cv.entity_type = 'word' AND w.id = cv.entity_id
        LEFT JOIN texts t  ON cv.entity_type = 'text' AND t.id = cv.entity_id
        LEFT JOIN units wu ON cv.entity_type != 'unit'
                           AND wu.id = COALESCE(w.unit_id, t.unit_id)
        LEFT JOIN users ou ON ou.id = cv.operator_id
    """

    @staticmethod
    def list_recent(
        *,
        entity_type: str | None = None,
        op: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """全局最近变更列表（供版本页默认视图，2026-09-30 方案 B）。

        - 按版本记录 id 倒序（即最新写入在前）；
        - 每行 JOIN 带出 `entity_name`（含已删除实体从快照兜底）、
          `subject / grade / semester / unit_name`（词条/课文的归属上下文）、
          `operator_name`；
        - `entity_type` / `op` 可选过滤，非法值 `10001`。
        """
        if entity_type is not None and entity_type not in _TABLES:
            raise BizError(ErrorCode.PARAM_INVALID, f"不支持的实体类型：{entity_type}")
        if op is not None and op not in _OPS:
            raise BizError(ErrorCode.PARAM_INVALID, f"不支持的版本操作：{op}")

        where = ["1=1"]
        params: list[Any] = []
        if entity_type:
            where.append("cv.entity_type = ?")
            params.append(entity_type)
        if op:
            where.append("cv.op = ?")
            params.append(op)
        where_sql = " AND ".join(where)

        offset = (page - 1) * page_size
        with read_conn() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS c {VersionService._RECENT_FROM} WHERE {where_sql}",
                    params,
                ).fetchone()["c"]
            )
            rows = conn.execute(
                f"{VersionService._RECENT_SELECT} {VersionService._RECENT_FROM} "
                f"WHERE {where_sql} ORDER BY cv.id DESC LIMIT ? OFFSET ?",
                (*params, page_size, offset),
            ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @staticmethod
    def rollback(
        conn,
        *,
        version_id: int,
        operator_id: int | None = None,
    ) -> dict[str, Any]:
        """回滚到指定版本快照。

        语义：把实体恢复到该快照捕获时的状态（不存在则 INSERT、存在则 UPDATE）。
        **本方法绝不删除任何实体**，故天然尊重「有记录只能停用不能删」（30003）。

        回滚本身会再写一条 `update` 版本的快照（记录「回滚自 version X」），
        保证操作链连续可审计。

        Raises:
            BizError: `70005` 版本记录不存在。
        """
        row = conn.execute("SELECT * FROM content_versions WHERE id = ?", (version_id,)).fetchone()
        if row is None:
            raise BizError(ErrorCode.VERSION_NOT_FOUND, "版本记录不存在")

        entity_type = row["entity_type"]
        entity_id = int(row["entity_id"])
        snapshot = json.loads(row["snapshot"]) if row["snapshot"] else {}

        # 还原实体（INSERT 或 UPDATE，不删除）
        VersionService._restore(conn, entity_type, entity_id, snapshot)

        # 回滚留痕：捕获还原后的状态，新写一条 update 版本
        restored = VersionService._capture_in(conn, entity_type, entity_id)
        new_version = VersionService.record_version(
            conn,
            entity_type,
            entity_id,
            restored if restored is not None else snapshot,
            "update",
            operator_id,
        )
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "rolled_back_to": int(row["version"]),
            "version": new_version,
        }

    # -----------------------------------------------------------------------
    # 内部：实体还原
    # -----------------------------------------------------------------------

    @staticmethod
    def _restore(conn, entity_type: str, entity_id: int, snapshot: dict[str, Any]) -> None:
        """把实体恢复到 `snapshot` 捕获时的状态。

        只写真实列（按 `PRAGMA table_info` 过滤），`id` 用于定位 / 重建，
        `updated_at` 始终刷新为当前时间。
        """
        table = VersionService._table(entity_type)
        cols = VersionService._real_columns(conn, table)
        restore = {c: snapshot[c] for c in cols if c in snapshot}

        existing = conn.execute(f"SELECT id FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        if existing:
            set_cols = [c for c in restore if c != "id"]
            if set_cols:
                conn.execute(
                    f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in set_cols)}, "
                    f"updated_at = ? WHERE id = ?",
                    (*[restore[c] for c in set_cols], utc_now_iso(), entity_id),
                )
        else:
            ins_cols = [c for c in restore if c != "id"]
            if ins_cols:
                placeholders = ", ".join("?" for _ in ins_cols)
                conn.execute(
                    f"INSERT INTO {table} (id, {', '.join(ins_cols)}) VALUES (?, {placeholders})",
                    (entity_id, *[restore[c] for c in ins_cols]),
                )

    @staticmethod
    def _capture_in(conn, entity_type: str, entity_id: int) -> dict[str, Any] | None:
        """在已有 `conn` 内读取实体原始行（回滚留痕用）。"""
        table = VersionService._table(entity_type)
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        return dict(row) if row is not None else None
