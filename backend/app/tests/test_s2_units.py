"""WP-A 验收 —— 单元与数据契约（U1 表结构 / U2 单元树）。

用例对应《10-后端工作包规格》WP-A：
- U1：003 迁移后 `PRAGMA table_info` 四张表与契约《01-共享契约冻结》§3.1
      逐字一致（列名/类型/NOT NULL/DEFAULT/CHECK/索引）。
- U2：units CRUD + 树返回 counts（三类数量正确统计）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_s2_units_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn  # noqa: E402
from app.main import app  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _fresh_config():
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE
    SettingsService._cache = None
    rate_limit.clear_all()
    return _DB_FILE


@pytest.fixture(scope="module")
def client(_fresh_config):
    with TestClient(app) as c:
        yield c


# ===========================================================================
# U1：迁移后表结构与契约 §3.1 逐字一致
# ===========================================================================

def _table_info(table: str) -> list[dict]:
    with read_conn() as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [
        {
            "name": r["name"],
            "type": r["type"],
            "notnull": int(r["notnull"]),
            "default": r["dflt_value"],
            "pk": int(r["pk"]),
        }
        for r in rows
    ]


def _table_sql(table: str) -> str:
    with read_conn() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
    return row["sql"] if row else ""


def test_u1_book_units_columns(client: TestClient):
    """U1a：book_units 列 = id/name/sort/created_at/parent_id（v1.3 二级化），逐字一致。"""
    assert _table_info("book_units") == [
        {"name": "id", "type": "INTEGER", "notnull": 0, "default": None, "pk": 1},
        {"name": "name", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
        {"name": "sort", "type": "INTEGER", "notnull": 1, "default": "0", "pk": 0},
        {"name": "created_at", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
        {"name": "parent_id", "type": "INTEGER", "notnull": 0, "default": None, "pk": 0},
    ]


def test_u1_items_columns_and_check(client: TestClient):
    """U1b：items 列逐字一致 + type CHECK (word/phrase/sentence) + 外键级联。"""
    assert _table_info("items") == [
        {"name": "id", "type": "INTEGER", "notnull": 0, "default": None, "pk": 1},
        {"name": "unit_id", "type": "INTEGER", "notnull": 1, "default": None, "pk": 0},
        {"name": "type", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
        {"name": "en", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
        {"name": "pos", "type": "TEXT", "notnull": 1, "default": "''", "pk": 0},
        {"name": "zh", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
        {"name": "alt_zh", "type": "TEXT", "notnull": 1, "default": "''", "pk": 0},
        {"name": "sort", "type": "INTEGER", "notnull": 1, "default": "0", "pk": 0},
        {"name": "created_at", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
    ]
    sql = _table_sql("items")
    assert "CHECK (type IN ('word','phrase','sentence'))" in sql
    assert "REFERENCES book_units(id) ON DELETE CASCADE" in sql
    # 索引
    with read_conn() as conn:
        idx = {
            r["name"]: r["sql"]
            for r in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='items'"
            ).fetchall()
        }
    assert "idx_items_unit" in idx
    assert idx["idx_items_unit"] == "CREATE INDEX idx_items_unit ON items(unit_id, type, sort)"


def test_u1_wrong_book_columns_and_partial_index(client: TestClient):
    """U1c：wrong_book 列逐字一致（v1.3 + mastered_at）+ mode CHECK + 部分索引。"""
    assert _table_info("wrong_book") == [
        {"name": "id", "type": "INTEGER", "notnull": 0, "default": None, "pk": 1},
        {"name": "item_id", "type": "INTEGER", "notnull": 1, "default": None, "pk": 0},
        {"name": "mode", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
        {"name": "wrong_count", "type": "INTEGER", "notnull": 1, "default": "1", "pk": 0},
        {"name": "last_wrong_at", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
        {"name": "resolved_at", "type": "TEXT", "notnull": 0, "default": None, "pk": 0},
        {"name": "mastered_at", "type": "TEXT", "notnull": 0, "default": None, "pk": 0},
    ]
    sql = _table_sql("wrong_book")
    assert "CHECK (mode IN ('listen','spell_zh2en','spell_en2zh'))" in sql
    assert "REFERENCES items(id) ON DELETE CASCADE" in sql
    with read_conn() as conn:
        idx = {
            r["name"]: r["sql"]
            for r in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='wrong_book'"
            ).fetchall()
        }
    assert idx.get("idx_wrong_open") == (
        "CREATE INDEX idx_wrong_open ON wrong_book(item_id, mode) WHERE resolved_at IS NULL"
    )


def test_u1_practice_logs_columns_and_checks(client: TestClient):
    """U1d：practice_logs 重建后（v1.3 unit_ids/types JSON 数组）列逐字一致 + mode CHECK。"""
    assert _table_info("practice_logs") == [
        {"name": "id", "type": "INTEGER", "notnull": 0, "default": None, "pk": 1},
        {"name": "unit_ids", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
        {"name": "types", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
        {"name": "mode", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
        {"name": "total", "type": "INTEGER", "notnull": 1, "default": None, "pk": 0},
        {"name": "correct", "type": "INTEGER", "notnull": 1, "default": None, "pk": 0},
        {"name": "wrong", "type": "INTEGER", "notnull": 1, "default": None, "pk": 0},
        {"name": "duration_ms", "type": "INTEGER", "notnull": 1, "default": None, "pk": 0},
        {"name": "created_at", "type": "TEXT", "notnull": 1, "default": None, "pk": 0},
    ]
    sql = _table_sql("practice_logs")
    # v1.3：type 单值 CHECK 废除（types 为 JSON 数组，服务层校验），mode CHECK 保留
    assert "CHECK (mode IN ('listen','spell_zh2en','spell_en2zh'))" in sql
    assert "CHECK (type IN" not in sql


def test_u1_check_constraints_reject_bad_enum(client: TestClient):
    """U1e：DB 层 CHECK 真的生效（非法 type/mode 插入报错）。"""
    import sqlite3

    with read_conn() as conn:
        conn.execute("INSERT INTO book_units (name, sort, created_at) VALUES ('U1检查', 0, '2026-01-01T00:00:00Z')")
        unit_id = conn.execute("SELECT id FROM book_units WHERE name='U1检查'").fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError):
        with read_conn() as conn:
            conn.execute(
                "INSERT INTO items (unit_id, type, en, zh, created_at) VALUES (?, 'verb', 'x', 'y', '2026-01-01T00:00:00Z')",
                (unit_id,),
            )


# ===========================================================================
# U2：units CRUD + 树 counts
# ===========================================================================

def test_u2_units_crud_and_tree_counts(client: TestClient):
    """U2：建 → 树 counts 三类数量 → 改 → 删（items 级联消失）。"""
    # 建单元
    r = client.post("/api/units", json={"name": "Starter Unit 1", "sort": 1})
    assert r.status_code == 200 and r.json()["code"] == 0
    unit_id = r.json()["data"]["id"]

    # 建第二条，验证树排序
    r2 = client.post("/api/units", json={"name": "Unit 1", "sort": 2})
    unit2 = r2.json()["data"]["id"]

    # 灌三类内容
    for t, en, zh in (
        ("word", "hello", "你好"),
        ("word", "cat", "猫"),
        ("phrase", "good morning", "早上好"),
        ("sentence", "I am a boy.", "我是一个男孩。"),
    ):
        rr = client.post(
            "/api/items", json={"unit_id": unit_id, "type": t, "en": en, "zh": zh}
        )
        assert rr.json()["code"] == 0, rr.json()

    # 树：counts 统计正确
    r = client.get("/api/units")
    tree = r.json()["data"]
    first = next(u for u in tree if u["id"] == unit_id)
    assert first["counts"] == {"word": 2, "phrase": 1, "sentence": 1}
    sorts = [u["sort"] for u in tree]
    assert sorts == sorted(sorts), "按 sort 升序"

    # 改：只改 name
    r = client.put(f"/api/units/{unit_id}", json={"name": "Starter U1 改"})
    assert r.json()["data"]["name"] == "Starter U1 改"
    assert r.json()["data"]["sort"] == 1, "未传字段不被改动"
    # 改：只改 sort
    r = client.put(f"/api/units/{unit_id}", json={"sort": 0})
    assert r.json()["data"]["sort"] == 0

    # 删 unit2 → 树里消失
    assert client.delete(f"/api/units/{unit2}").json()["code"] == 0
    tree = client.get("/api/units").json()["data"]
    assert all(u["id"] != unit2 for u in tree)

    # 删 unit_id → items 级联删（DB CASCADE）
    assert client.delete(f"/api/units/{unit_id}").json()["code"] == 0
    r = client.get("/api/items", params={"unit_id": unit_id})
    assert r.json()["data"]["total"] == 0
    with read_conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM items WHERE unit_id = ?", (unit_id,)).fetchone()["n"]
    assert n == 0, "ON DELETE CASCADE 未生效"


def test_u2_unit_name_empty_rejected(client: TestClient):
    r = client.post("/api/units", json={"name": "  ", "sort": 0})
    assert r.json()["code"] == 10001
