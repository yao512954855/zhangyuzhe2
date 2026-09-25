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
    def list_versions(*, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
        """查询某实体的版本历史（读，供 `GET /api/content/versions`）。"""
        VersionService._table(entity_type)  # 校验实体类型
        with read_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, entity_type, entity_id, version, op, operator_id, created_at
                FROM content_versions
                WHERE entity_type = ? AND entity_id = ?
                ORDER BY version ASC
                """,
                (entity_type, entity_id),
            ).fetchall()
        return [dict(r) for r in rows]

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
