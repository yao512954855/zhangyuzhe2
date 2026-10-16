"""BE-12 验收 —— 错题本三态过滤 + 批量移出（U12-5/U12-6）。

对应《50-二轮后端工作包规格》BE-12，契约《01-共享契约冻结》v1.3 §3.4 / §4.1。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_wrongbook_r2_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
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
    """1 单元 3 词条；apple listen 错 2 次、cat zh2en 错 1 次后判对（已掌握）。"""
    uid = client.post("/api/units", json={"name": "WB2-U", "sort": 1}).json()["data"]["id"]

    def mk(t: str, en: str) -> int:
        r = client.post("/api/items", json={"unit_id": uid, "type": t, "en": en, "zh": f"zh-{en}"})
        assert r.json()["code"] == 0
        return r.json()["data"]["id"]

    apple, cat, hi = mk("word", "apple"), mk("word", "cat"), mk("phrase", "say hi")

    assert client.post("/api/wrongbook/auto-add", json={"item_id": apple, "mode": "listen"}).json()["data"]["wrong_count"] == 1
    assert client.post("/api/wrongbook/auto-add", json={"item_id": apple, "mode": "listen"}).json()["data"]["wrong_count"] == 2
    client.post("/api/wrongbook/auto-add", json={"item_id": cat, "mode": "spell_zh2en"})
    client.post("/api/wrongbook/auto-add", json={"item_id": hi, "mode": "spell_en2zh"})

    # 判对 cat：practice start → answer(correct=true) → mastered_at 落库
    sid = client.post(
        "/api/practice/start",
        json={"unit_id": uid, "type": "word", "mode": "spell_zh2en"},
    ).json()["data"]["session_id"]
    r = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": cat, "mode": "spell_zh2en",
              "answer": "cat", "correct": True},
    )
    assert r.json()["data"]["correct"] is True

    return {"unit": uid, "apple": apple, "cat": cat, "hi": hi}


# ===========================================================================
# U12-5：status / mode 三态过滤
# ===========================================================================

def _list(client: TestClient, **params) -> dict:
    r = client.get("/api/wrongbook", params=params)
    assert r.json()["code"] == 0, r.json()
    return r.json()["data"]


def test_u12_5_status_filters(client: TestClient, seed: dict):
    # 默认 status=unmastered：apple(listen) + hi(en2zh)，已掌握的 cat 不在列
    body = _list(client, page=1, page_size=50)
    assert body["total"] == 2
    assert {(i["item_id"], i["mode"]) for i in body["items"]} == {
        (seed["apple"], "listen"), (seed["hi"], "spell_en2zh"),
    }
    assert body["items"][0]["mastered_at"] is None, "未掌握行 mastered_at 为 null"

    # status=mastered + mode=spell_zh2en → 只返回已掌握的 cat
    body = _list(client, mode="spell_zh2en", status="mastered", page=1, page_size=50)
    assert body["total"] == 1
    row = body["items"][0]
    assert row["item_id"] == seed["cat"]
    assert row["mastered_at"] is not None
    assert row["wrong_count"] == 1, "判对不累加 wrong_count"
    # 响应字段（契约 §4.1 v1.3：含 mastered_at）
    assert set(row.keys()) == {
        "item_id", "en", "pos", "zh", "mode", "wrong_count",
        "last_wrong_at", "mastered_at", "unit_id", "unit_name", "type",
    }

    # status=all（未移出全集）→ 未掌握 + 已掌握
    body = _list(client, status="all", page=1, page_size=50)
    assert body["total"] == 3
    assert {(i["item_id"], i["mode"]) for i in body["items"]} == {
        (seed["apple"], "listen"), (seed["cat"], "spell_zh2en"),
        (seed["hi"], "spell_en2zh"),
    }

    # mode 三类 tab 过滤（unmastered 下）
    assert _list(client, mode="listen")["total"] == 1
    assert _list(client, mode="spell_zh2en")["total"] == 0, "zh2en 那条已掌握，unmastered 下不出现"
    assert _list(client, mode="spell_en2zh")["total"] == 1
    # 非法 mode / status → 10001
    r = client.get("/api/wrongbook", params={"mode": "dictation"})
    assert r.json()["code"] == 10001
    r = client.get("/api/wrongbook", params={"status": "resolved"})
    assert r.json()["code"] == 10001


def test_u12_5_group_by_regression(client: TestClient, seed: dict):
    body = _list(client, group_by="type", status="all", page=1, page_size=50)
    assert body["total"] == 3
    # group_by=type：按 type 字母序排（phrase < word），组内再按单元/条目序
    assert [i["type"] for i in body["items"]] == sorted(i["type"] for i in body["items"])
    r = client.get("/api/wrongbook", params={"group_by": "whatever"})
    assert r.json()["code"] == 10001


# ===========================================================================
# U12-6：batch-resolve 批量移出
# ===========================================================================

def test_u12_6_batch_resolve(client: TestClient, seed: dict):
    r = client.post(
        "/api/wrongbook/batch-resolve",
        json={"entries": [
            {"item_id": seed["apple"], "mode": "listen"},        # 存在 → 计数
            {"item_id": 99999999, "mode": "listen"},             # 不存在 → 跳过
            {"item_id": seed["cat"], "mode": "spell_zh2en"},     # 已掌握也可手动移出 → 计数
            {"item_id": seed["apple"], "mode": "listen"},        # 重复条目：第二条已移出 → 跳过
            {"item_id": seed["hi"], "mode": "spell_en2zh"},      # 存在 → 计数
        ]},
    )
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["resolved"] == 3, "resolved 只计成功置 resolved_at 的条数"

    # 全部移出后 status=all 为空；已移出（resolved_at 非空）不属于错题本列表
    body = _list(client, status="all", page=1, page_size=50)
    assert body["total"] == 0

    # 再移出一次（已 resolved）→ 0，幂等跳过不报错
    r = client.post(
        "/api/wrongbook/batch-resolve",
        json={"entries": [{"item_id": seed["apple"], "mode": "listen"}]},
    )
    assert r.json()["code"] == 0 and r.json()["data"]["resolved"] == 0

    # entries 为空 → 10001（pydantic → RequestValidationError → 10001）
    r = client.post("/api/wrongbook/batch-resolve", json={"entries": []})
    assert r.json()["code"] == 10001
    # entries 每项缺 mode → 10001
    r = client.post("/api/wrongbook/batch-resolve", json={"entries": [{"item_id": 1}]})
    assert r.json()["code"] == 10001
    # 非法 mode → 10001
    r = client.post(
        "/api/wrongbook/batch-resolve",
        json={"entries": [{"item_id": seed["apple"], "mode": "dictation"}]},
    )
    assert r.json()["code"] == 10001


def test_u12_6_auto_add_after_resolve_creates_new_row(client: TestClient, seed: dict):
    """移出后再错 → UPSERT 新开一行（同 (item,mode) 只一条未移出，§3.4 红线）。"""
    client.post("/api/wrongbook/auto-add", json={"item_id": seed["apple"], "mode": "listen"})
    body = _list(client, mode="listen", status="unmastered", page=1, page_size=50)
    assert body["total"] == 1
    assert body["items"][0]["wrong_count"] == 1, "新行从 1 计数"
