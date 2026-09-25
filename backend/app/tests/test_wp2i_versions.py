"""WP-2I 内容版本快照 —— 验收测试（二期工作包 9）。

覆盖（对应 50-二期工作包拆解 §3 / 契约 §15.3 错误码 70005）：
- V1 新增写快照 + version 从 1 递增
- V2 修改 / 停用各写一条版本（op 区分 update / disable）
- V3 删除写一条 delete 版本，且实体被物理删除
- V4 回滚把实体恢复到目标快照状态
- V5 回滚**不会删除**有记录的实体（天然尊重 30003，不绕过）
- V6 回滚到 delete 快照可重建被删实体
- V7 权限（非 admin → 10003）/ 不存在版本 → 70005 / 非法 entity_type → 10001
- V8 反向验证：operator_id 必须落库；version 必须单调递增（撤掉任一项即转红）

环境处理同 test_be03_content.py（os.environ 进程级、SettingsService 缓存类变量）。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_wp2i_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "wp2i-test-secret-0123456789abcdef"
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "wp2i-Admin-Pass"
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import preview_store, rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx  # noqa: E402
from app.main import app  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402


@pytest.fixture()
def client():
    """每个测试一个全新的空库 + 干净的限流桶 + 干净的 settings 缓存。"""
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = "wp2i-test-secret-0123456789abcdef"
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = "admin"
    os.environ["ADMIN_INIT_PASSWORD"] = "wp2i-Admin-Pass"
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE, "DB_PATH 被同进程里别的测试模块改掉了"

    for suffix in ("", "-wal", "-shm"):
        path = Path(str(_DB_FILE) + suffix)
        if path.exists():
            path.unlink()

    SettingsService._cache = None
    rate_limit.clear_all()
    preview_store.clear_all()

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin(client: TestClient) -> tuple[str, int]:
    """登录 admin，返回 `(token, admin_id)`。"""
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wp2i-Admin-Pass", "role": "admin"},
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["access_token"], int(body["data"]["user"]["id"])


def _new_student(client: TestClient, username: str = "20260001") -> str:
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "real_name": "小明", "password": "student123"},
    )
    assert resp.json()["code"] == 0, resp.json()
    return resp.json()["data"]["access_token"]


def _make_unit(client: TestClient, token: str, name: str = "Unit 1") -> int:
    resp = client.post(
        "/api/units",
        json={"subject": "english", "grade": 3, "semester": "first", "name": name},
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    return int(body["data"]["id"])


def _sql(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    with read_conn() as conn:
        return conn.execute(query, params).fetchall()


def _versions(entity_type: str, entity_id: int) -> list[dict]:
    rows = _sql(
        "SELECT id, version, op, operator_id, snapshot FROM content_versions "
        "WHERE entity_type=? AND entity_id=? ORDER BY version ASC",
        (entity_type, entity_id),
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# V1 / V2 写快照 + 版本递增 + op 区分
# ---------------------------------------------------------------------------


def test_v1_create_records_version(client):
    """新增单元 → content_versions 落一条 op=create、version=1 的快照。"""
    token, admin_id = _admin(client)
    unit_id = _make_unit(client, token)

    vers = _versions("unit", unit_id)
    assert len(vers) == 1, vers
    assert vers[0]["version"] == 1
    assert vers[0]["op"] == "create"
    import json

    snap = json.loads(vers[0]["snapshot"])
    assert snap["name"] == "Unit 1"
    assert snap["subject"] == "english"


def test_v2_update_and_disable_increment(client):
    """改名称（update）+ 停用（disable）→ version 2、3，op 正确。"""
    token, _ = _admin(client)
    unit_id = _make_unit(client, token)

    r1 = client.patch(f"/api/units/{unit_id}", json={"name": "Unit 1 改"}, headers=_auth(token))
    assert r1.json()["code"] == 0, r1.json()

    r2 = client.patch(f"/api/units/{unit_id}", json={"status": "inactive"}, headers=_auth(token))
    assert r2.json()["code"] == 0, r2.json()

    vers = _versions("unit", unit_id)
    assert [v["version"] for v in vers] == [1, 2, 3]
    assert [v["op"] for v in vers] == ["create", "update", "disable"]


def test_v2b_word_batch_status_records_per_word(client):
    """批量停用两个词 → 每个词各落一条 disable 版本。"""
    token, _ = _admin(client)
    unit_id = _make_unit(client, token)
    # 直接落两个词（绕过导入，仅用于造数据；版本钩子在 router 上）
    with write_tx() as conn:
        for en in ("apple", "banana"):
            conn.execute(
                "INSERT INTO words (unit_id, type, en, cn, status, created_at) "
                "VALUES (?, 'word', ?, '苹果', 'active', '2026-09-25T00:00:00Z')",
                (unit_id, en),
            )
    ids = [int(r["id"]) for r in _sql("SELECT id FROM words")]

    resp = client.post(
        "/api/words/batch-status",
        json={"word_ids": ids, "status": "inactive"},
        headers=_auth(token),
    )
    assert resp.json()["code"] == 0, resp.json()

    for wid in ids:
        vers = _versions("word", wid)
        assert len(vers) == 1, vers
        assert vers[0]["op"] == "disable"


# ---------------------------------------------------------------------------
# V3 删除写版本（实体被物理删除）
# ---------------------------------------------------------------------------


def test_v3_delete_records_version_and_removes_entity(client):
    """删除零记录单元 → delete 版本落库，单元被物理删除。"""
    token, _ = _admin(client)
    unit_id = _make_unit(client, token)
    client.patch(f"/api/units/{unit_id}", json={"name": "X"}, headers=_auth(token))  # v2 update

    resp = client.delete(f"/api/units/{unit_id}", headers=_auth(token))
    assert resp.json()["code"] == 0, resp.json()

    vers = _versions("unit", unit_id)
    assert [v["op"] for v in vers] == ["create", "update", "delete"]
    assert _sql("SELECT COUNT(*) AS c FROM units WHERE id=?", (unit_id,))[0]["c"] == 0


# ---------------------------------------------------------------------------
# V4 回滚恢复快照状态
# ---------------------------------------------------------------------------


def test_v4_rollback_restores_fields(client):
    """改了名称后回滚到 v1（create 快照）→ 名称恢复原始值。"""
    token, _ = _admin(client)
    unit_id = _make_unit(client, token)  # v1 name=Unit 1
    client.patch(f"/api/units/{unit_id}", json={"name": "改名了"}, headers=_auth(token))  # v2

    vers = _versions("unit", unit_id)
    v1_id = vers[0]["id"]

    resp = client.post(f"/api/content/versions/{v1_id}/rollback", headers=_auth(token))
    assert resp.json()["code"] == 0, resp.json()
    assert resp.json()["data"]["rolled_back_to"] == 1

    name = _sql("SELECT name FROM units WHERE id=?", (unit_id,))[0]["name"]
    assert name == "Unit 1"


# ---------------------------------------------------------------------------
# V5 回滚不删除有记录的实体（尊重 30003）
# ---------------------------------------------------------------------------


def test_v5_rollback_keeps_entity_with_records(client):
    """单元下有带记录的词；回滚到某个版本后单元仍在、词与记录都完好。

    回滚接口**绝不删除**任何实体，因此「有记录只能停用不能删」的约束天然成立。
    """
    token, _ = _admin(client)
    unit_id = _make_unit(client, token)  # v1
    # 直接落一个词并造答题记录 → 单元有记录，不能被删
    with write_tx() as conn:
        cur = conn.execute(
            "INSERT INTO words (unit_id, type, en, cn, status, created_at) "
            "VALUES (?, 'word', 'apple', '苹果', 'active', '2026-09-25T00:00:00Z')",
            (unit_id,),
        )
        word_id = int(cur.lastrowid)
        cur.execute(
            "INSERT INTO sessions (student_id, unit_id, content_type, strategy) "
            "VALUES (1, ?, 'english', '{}')",
            (unit_id,),
        )
        sid = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO answers (session_id, student_id, word_id, mode, step, expected, "
            "is_correct, match_type) VALUES (?, 1, ?, 'cn2en', 1, '苹果', 1, 'exact')",
            (sid, word_id),
        )

    # 停用 + 再改名，产生 v2/v3
    client.patch(f"/api/units/{unit_id}", json={"status": "inactive"}, headers=_auth(token))
    client.patch(f"/api/units/{unit_id}", json={"name": "改过了"}, headers=_auth(token))

    # 回滚到 v1（active + 原名）
    v1_id = _versions("unit", unit_id)[0]["id"]
    resp = client.post(f"/api/content/versions/{v1_id}/rollback", headers=_auth(token))
    assert resp.json()["code"] == 0, resp.json()

    # 实体仍在（未被删除）
    assert _sql("SELECT COUNT(*) AS c FROM units WHERE id=?", (unit_id,))[0]["c"] == 1
    assert _sql("SELECT status FROM units WHERE id=?", (unit_id,))[0]["status"] == "active"
    # 词与练习记录都完好（未被回滚波及）
    assert _sql("SELECT COUNT(*) AS c FROM words WHERE id=?", (word_id,))[0]["c"] == 1
    assert _sql("SELECT COUNT(*) AS c FROM answers WHERE word_id=?", (word_id,))[0]["c"] == 1


# ---------------------------------------------------------------------------
# V6 回滚重建被删实体
# ---------------------------------------------------------------------------


def test_v6_rollback_recreates_deleted_entity(client):
    """删除零记录词（留下 delete 快照）→ 回滚到该 delete 快照可重建该词。"""
    token, _ = _admin(client)
    unit_id = _make_unit(client, token)
    with write_tx() as conn:
        conn.execute(
            "INSERT INTO words (unit_id, type, en, cn, status, created_at) "
            "VALUES (?, 'word', 'apple', '苹果', 'active', '2026-09-25T00:00:00Z')",
            (unit_id,),
        )
    word_id = int(_sql("SELECT id FROM words")[0]["id"])

    # 删除（零记录）→ 落 delete 版本
    resp = client.delete(f"/api/words/{word_id}", headers=_auth(token))
    assert resp.json()["code"] == 0, resp.json()
    assert _sql("SELECT COUNT(*) AS c FROM words WHERE id=?", (word_id,))[0]["c"] == 0

    delete_ver_id = _versions("word", word_id)[0]["id"]
    roll = client.post(f"/api/content/versions/{delete_ver_id}/rollback", headers=_auth(token))
    assert roll.json()["code"] == 0, roll.json()

    assert _sql("SELECT COUNT(*) AS c FROM words WHERE id=?", (word_id,))[0]["c"] == 1
    assert _sql("SELECT en FROM words WHERE id=?", (word_id,))[0]["en"] == "apple"


# ---------------------------------------------------------------------------
# V7 权限 / 错误码
# ---------------------------------------------------------------------------


def test_v7_requires_admin(client):
    """非 admin 查版本 / 回滚 → 10003。"""
    token, _ = _admin(client)
    unit_id = _make_unit(client, token)
    student = _new_student(client)

    list_resp = client.get(
        f"/api/content/versions?entity_type=unit&entity_id={unit_id}", headers=_auth(student)
    )
    assert list_resp.json()["code"] == 10003, list_resp.json()

    roll_resp = client.post(
        f"/api/content/versions/999999/rollback", headers=_auth(student)
    )
    assert roll_resp.json()["code"] == 10003, roll_resp.json()


def test_v7b_invalid_entity_type(client):
    """entity_type 非法 → 10001。"""
    token, _ = _admin(client)
    resp = client.get("/api/content/versions?entity_type=bogus&entity_id=1", headers=_auth(token))
    assert resp.json()["code"] == 10001, resp.json()


def test_v7c_rollback_not_found(client):
    """回滚不存在的版本 → 70005。"""
    token, _ = _admin(client)
    resp = client.post("/api/content/versions/999999/rollback", headers=_auth(token))
    assert resp.json()["code"] == 70005, resp.json()


# ---------------------------------------------------------------------------
# V8 反向验证：operator_id 落库 + version 单调递增（撤掉任一项必转红）
# ---------------------------------------------------------------------------


def test_v8_operator_id_recorded(client):
    """版本行的 operator_id 必须等于操作的管理员 id（不落库则转红）。"""
    token, admin_id = _admin(client)
    unit_id = _make_unit(client, token)

    vers = _versions("unit", unit_id)
    assert vers[0]["operator_id"] == admin_id, "operator_id 未正确落库"


def test_v8b_version_monotonic(client):
    """同一实体多次写操作，version 必须严格 1,2,3... 递增（不递增则转红）。"""
    token, _ = _admin(client)
    unit_id = _make_unit(client, token)
    client.patch(f"/api/units/{unit_id}", json={"name": "a"}, headers=_auth(token))
    client.patch(f"/api/units/{unit_id}", json={"name": "b"}, headers=_auth(token))
    client.patch(f"/api/units/{unit_id}", json={"status": "inactive"}, headers=_auth(token))

    vers = _versions("unit", unit_id)
    assert [v["version"] for v in vers] == [1, 2, 3, 4], "version 未严格递增"
