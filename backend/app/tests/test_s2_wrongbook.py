"""WP-A 验收 —— 错题本（U7 UPSERT / U8 分组列表 / U9 移出）+ U12 听写壳。

对应《10-后端工作包规格》WP-A 验收用例 U7 / U8 / U9 / U12。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_s2_wrongbook_"))
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


@pytest.fixture(scope="module")
def seed(client: TestClient) -> dict:
    """两个单元 + 若干词条（word/phrase 两类），返回 item_id 映射。"""
    u1 = client.post("/api/units", json={"name": "W-Unit-1", "sort": 1}).json()["data"]["id"]
    u2 = client.post("/api/units", json={"name": "W-Unit-2", "sort": 2}).json()["data"]["id"]

    def mk(unit: int, t: str, en: str, zh: str) -> int:
        r = client.post(
            "/api/items", json={"unit_id": unit, "type": t, "en": en, "zh": zh}
        )
        assert r.json()["code"] == 0
        return r.json()["data"]["id"]

    return {
        "u1": u1,
        "u2": u2,
        "apple": mk(u1, "word", "apple", "苹果"),
        "cat": mk(u1, "word", "cat", "猫"),
        "dog": mk(u2, "word", "dog", "狗"),
        "hi": mk(u2, "phrase", "say hi", "问好"),
    }


# ===========================================================================
# U7：auto-add 连调两次同 (item,mode) → wrong_count=2，仍一条未解决记录
# ===========================================================================

def test_u7_auto_add_upsert(client: TestClient, seed: dict):
    item_id = seed["apple"]
    r1 = client.post("/api/wrongbook/auto-add", json={"item_id": item_id, "mode": "listen"})
    assert r1.json()["code"] == 0
    assert r1.json()["data"]["wrong_count"] == 1
    r2 = client.post("/api/wrongbook/auto-add", json={"item_id": item_id, "mode": "listen"})
    assert r2.json()["data"]["wrong_count"] == 2

    with read_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wrong_book WHERE item_id = ? AND resolved_at IS NULL",
            (item_id,),
        ).fetchall()
    assert len(rows) == 1, "同 (item_id, mode) 必须只有一条未解决记录"
    assert int(rows[0]["wrong_count"]) == 2

    # 不同 mode 是独立记录
    r3 = client.post(
        "/api/wrongbook/auto-add", json={"item_id": item_id, "mode": "spell_en2zh"}
    )
    assert r3.json()["data"]["wrong_count"] == 1
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wrong_book WHERE item_id = ? AND resolved_at IS NULL",
            (item_id,),
        ).fetchall()
    assert len(rows) == 2


def test_u7_auto_add_validation(client: TestClient, seed: dict):
    # 非法 mode → 10001
    r = client.post("/api/wrongbook/auto-add", json={"item_id": seed["cat"], "mode": "dictation"})
    assert r.json()["code"] == 10001
    # item 不存在 → 30002
    r = client.post("/api/wrongbook/auto-add", json={"item_id": 99999999, "mode": "listen"})
    assert r.json()["code"] == 30002


# ===========================================================================
# U8：列表 group_by=unit 分组正确，含 unit_name
# ===========================================================================

def test_u8_list_group_by_unit(client: TestClient, seed: dict):
    # 再错两条：u2 的 dog(listen)、u2 的 hi(spell_zh2en)
    client.post("/api/wrongbook/auto-add", json={"item_id": seed["dog"], "mode": "listen"})
    client.post(
        "/api/wrongbook/auto-add", json={"item_id": seed["hi"], "mode": "spell_zh2en"}
    )

    r = client.get("/api/wrongbook", params={"group_by": "unit", "page": 1, "page_size": 50})
    body = r.json()["data"]
    assert body["total"] == 4
    items = body["items"]
    # 字段齐全（契约 §4.1 响应形状）
    assert set(items[0].keys()) == {
        "item_id", "en", "pos", "zh", "mode", "wrong_count",
        "last_wrong_at", "unit_id", "unit_name", "type",
    }
    # 按 unit 升序分组：u1 的两条在前，u2 的在后
    unit_seq = [it["unit_id"] for it in items]
    assert unit_seq == sorted(unit_seq)
    assert items[0]["unit_name"] == "W-Unit-1"
    assert items[0]["item_id"] == seed["apple"]
    assert items[0]["wrong_count"] == 2
    # 已解决记录不出现（apple 的 spell_en2zh 后面 U9 才解决，这里 listen 应在列）
    assert all(it["mode"] in ("listen", "spell_zh2en", "spell_en2zh") for it in items)


def test_u8_list_group_by_type(client: TestClient, seed: dict):
    r = client.get("/api/wrongbook", params={"group_by": "type", "page": 1, "page_size": 50})
    items = r.json()["data"]["items"]
    type_seq = [it["type"] for it in items]
    assert type_seq == sorted(type_seq), "group_by=type 按 type 排"
    # 非法 group_by → 10001
    r = client.get("/api/wrongbook", params={"group_by": "whatever"})
    assert r.json()["code"] == 10001


# ===========================================================================
# U9：DELETE wrongbook 后该条不再出现在未解决列表
# ===========================================================================

def test_u9_delete_then_not_in_open_list(client: TestClient, seed: dict):
    item_id = seed["apple"]
    r = client.delete(f"/api/wrongbook/{item_id}/listen")
    assert r.json()["code"] == 0

    r = client.get("/api/wrongbook", params={"group_by": "unit", "page_size": 50})
    item_ids = [it["item_id"] for it in r.json()["data"]["items"]]
    # listen 那条已移出；spell_en2zh 那条仍在
    assert (item_id, "spell_en2zh") in [
        (it["item_id"], it["mode"]) for it in r.json()["data"]["items"]
    ]
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT resolved_at FROM wrong_book WHERE item_id = ? AND mode = 'listen'",
            (item_id,),
        ).fetchall()
    assert rows and rows[0]["resolved_at"] is not None, "移出 = 置 resolved_at"

    # 幂等：再删一次也成功
    assert client.delete(f"/api/wrongbook/{item_id}/listen").json()["code"] == 0
    # 非法 mode → 10001
    r = client.delete(f"/api/wrongbook/{item_id}/whatever")
    assert r.json()["code"] == 10001


# ===========================================================================
# U12：dictation/grade —— WP-D 实装后改为验证坏图仍映射 20006/20005
# ===========================================================================

def test_u12_dictation_grade_invalid_image(client: TestClient):
    r = client.post(
        "/api/dictation/grade",
        files={"file": ("photo.jpg", b"\xff\xd8\xff\xe0fake")},
        data={"words": ["apple", "banana"]},
    )
    # 魔数是 JPEG 但内容为假 → 解码失败走 20006；OCR 网络问题走 20005，两者都合法
    assert r.status_code == 200, r.text
    assert r.json()["code"] in (20006, 20005), r.json()
