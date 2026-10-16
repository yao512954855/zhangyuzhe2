"""BE-12 验收 —— 练习数组化 + answer 判对留痕 + settle JSON（U12-1/2/3/4/9）。

对应《50-二轮后端工作包规格》BE-12 验收用例，契约《01-共享契约冻结》v1.3 §4.1。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_practice_r2_"))
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


def _mk_item(client: TestClient, unit_id: int, t: str, en: str, sort: int = 0) -> int:
    r = client.post(
        "/api/items",
        json={"unit_id": unit_id, "type": t, "en": en, "zh": f"zh-{en}", "sort": sort},
    )
    assert r.json()["code"] == 0, r.json()
    return r.json()["data"]["id"]


@pytest.fixture(scope="module")
def seed(client: TestClient) -> dict:
    """两个单元若干词条：A 有 word×2(sort 乱序)+phrase×1，B 有 word×1+phrase×2。"""
    ua = client.post("/api/units", json={"name": "P2-A", "sort": 1}).json()["data"]["id"]
    ub = client.post("/api/units", json={"name": "P2-B", "sort": 2}).json()["data"]["id"]
    return {
        "ua": ua,
        "ub": ub,
        "a1": _mk_item(client, ua, "word", "a-word-1", sort=1),
        "a2": _mk_item(client, ua, "word", "a-word-2", sort=2),
        "ap": _mk_item(client, ua, "phrase", "a-phrase", sort=0),
        "b1": _mk_item(client, ub, "word", "b-word-1", sort=1),
        "bp0": _mk_item(client, ub, "phrase", "b-phrase-0", sort=0),
        "bp1": _mk_item(client, ub, "phrase", "b-phrase-1", sort=1),
    }


# ===========================================================================
# U12-1：数组多选组卷 —— types 按 word→phrase→sentence 分组，
#        组内按请求中 unit_ids 顺序 → sort → id
# ===========================================================================

def test_u12_1_array_start_order(client: TestClient, seed: dict):
    r = client.post(
        "/api/practice/start",
        json={
            "mode": "spell_zh2en",
            "unit_ids": [seed["ub"], seed["ua"]],   # 请求顺序 B 在前（非 unit.sort 序）
            "types": ["phrase", "word"],            # 请求顺序 phrase 在前
        },
    )
    assert r.json()["code"] == 0, r.json()
    items = r.json()["data"]["items"]
    # 先全部 word（组内按请求中单元顺序 B→A，组内 sort→id），再全部 phrase
    assert [i["id"] for i in items] == [
        seed["b1"],
        seed["a1"], seed["a2"],   # A 组内按 sort
        seed["bp0"], seed["bp1"],  # B 组内按 sort
        seed["ap"],
    ]
    assert set(items[0].keys()) == {"id", "en", "pos", "zh", "alt_zh"}


def test_u12_1_empty_array_and_missing_unit(client: TestClient, seed: dict):
    # 空 unit_ids → 10001
    r = client.post("/api/practice/start", json={"mode": "listen", "unit_ids": [], "types": ["word"]})
    assert r.json()["code"] == 10001
    r = client.post("/api/practice/start", json={"mode": "listen", "unit_ids": [seed["ua"]], "types": []})
    assert r.json()["code"] == 10001
    # 单元不存在 → 30001
    r = client.post(
        "/api/practice/start",
        json={"mode": "listen", "unit_ids": [seed["ua"], 99999999], "types": ["word"]},
    )
    assert r.json()["code"] == 30001
    # 非法 type → 10001
    r = client.post(
        "/api/practice/start", json={"mode": "listen", "unit_ids": [seed["ua"]], "types": ["dictation"]}
    )
    assert r.json()["code"] == 10001


# ===========================================================================
# U12-2：单数 unit_id/type 行为与一轮完全一致（回归）
# ===========================================================================

def test_u12_2_singular_regression(client: TestClient, seed: dict):
    r = client.post(
        "/api/practice/start",
        json={"unit_id": seed["ua"], "type": "word", "mode": "spell_en2zh"},
    )
    assert r.json()["code"] == 0
    items = r.json()["data"]["items"]
    assert [i["id"] for i in items] == [seed["a1"], seed["a2"]], "全量下发，sort→id 序"
    assert set(items[0].keys()) == {"id", "en", "pos", "zh", "alt_zh"}

    # 两者都传 → 以数组为准
    r = client.post(
        "/api/practice/start",
        json={"unit_id": seed["ua"], "unit_ids": [seed["ub"]],
              "type": "word", "types": ["word"], "mode": "listen"},
    )
    assert r.json()["code"] == 0
    assert [i["id"] for i in r.json()["data"]["items"]] == [seed["b1"]]

    # 单数也不传 → 10001
    r = client.post("/api/practice/start", json={"mode": "listen", "types": ["word"]})
    assert r.json()["code"] == 10001
    # 单数 + 空单元 → 20004（与一轮一致）
    empty = client.post("/api/units", json={"name": "P2-空", "sort": 9}).json()["data"]["id"]
    r = client.post(
        "/api/practice/start", json={"unit_id": empty, "type": "word", "mode": "listen"}
    )
    assert r.json()["code"] == 20004


# ===========================================================================
# U12-3：answer(correct=true) → wrong_book 写 mastered_at（幂等不覆盖）
# ===========================================================================

def test_u12_3_answer_correct_marks_mastered(client: TestClient, seed: dict, monkeypatch):
    sid = client.post(
        "/api/practice/start",
        json={"unit_id": seed["ua"], "type": "word", "mode": "spell_zh2en"},
    ).json()["data"]["session_id"]

    r = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": seed["a1"], "mode": "spell_zh2en",
              "answer": "a-word-1", "elapsed_ms": 500, "correct": True},
    )
    assert r.json()["code"] == 0 and r.json()["data"]["correct"] is True

    with read_conn() as conn:
        row = conn.execute(
            "SELECT * FROM wrong_book WHERE item_id = ? AND mode = 'spell_zh2en'",
            (seed["a1"],),
        ).fetchone()
    assert row is not None
    assert row["mastered_at"] is not None, "判对必须写 mastered_at"
    assert row["resolved_at"] is None, "判对不再自动移出（废除 v1.1 #4）"
    first_ts = row["mastered_at"]

    # 重复判对 → 幂等不覆盖：把时钟拨到 2030 再判对一次，mastered_at 必须还是原值
    patched = "2030-01-01T00:00:00Z"
    monkeypatch.setattr("app.services.wrongbook_service.utc_now_iso", lambda: patched)
    r = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": seed["a1"], "mode": "spell_zh2en",
              "answer": "a-word-1", "elapsed_ms": 500, "correct": True},
    )
    assert r.json()["data"]["correct"] is True
    with read_conn() as conn:
        row = conn.execute(
            "SELECT mastered_at FROM wrong_book WHERE item_id = ? AND mode = 'spell_zh2en'",
            (seed["a1"],),
        ).fetchone()
    assert row["mastered_at"] == first_ts, "已有 mastered_at 不得覆盖"
    assert first_ts != patched, "哨兵时间必须与真实时间不同，否则本断言无效"


def test_u12_3_spell_zh2en_correct_required(client: TestClient, seed: dict):
    """契约 v1.3：spell_zh2en 恢复必走 HTTP，correct 必填。"""
    sid = client.post(
        "/api/practice/start",
        json={"unit_id": seed["ua"], "type": "word", "mode": "spell_zh2en"},
    ).json()["data"]["session_id"]
    r = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": seed["a2"], "mode": "spell_zh2en",
              "answer": "a-word-2", "elapsed_ms": 100},
    )
    assert r.json()["code"] == 10001, "不传 correct 必须 10001"
    # listen 仍不走本接口
    r = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": seed["a2"], "mode": "listen",
              "answer": "x", "correct": True},
    )
    assert r.json()["code"] == 10001


# ===========================================================================
# U12-4：answer(correct=false) → auto-add 语义不变（wrong_count+1）
# ===========================================================================

def test_u12_4_answer_wrong_then_auto_add_chain(client: TestClient, seed: dict):
    """answer(correct=false) 本身不写 wrong_book（App 负责调 auto-add，§6.4），
    auto-add 语义不变（wrong_count+1）——服务端若自动加会与 App 上报双重计数。"""
    sid = client.post(
        "/api/practice/start",
        json={"unit_id": seed["ua"], "type": "word", "mode": "spell_zh2en"},
    ).json()["data"]["session_id"]

    # 判错：服务端不动 wrong_book
    r = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": seed["a2"], "mode": "spell_zh2en",
              "answer": "wrong", "correct": False},
    )
    assert r.json()["data"]["correct"] is False
    with read_conn() as conn:
        row = conn.execute(
            "SELECT * FROM wrong_book WHERE item_id = ? AND mode = 'spell_zh2en'",
            (seed["a2"],),
        ).fetchone()
    assert row is None, "answer(correct=false) 不得自动写 wrong_book（App 调 auto-add）"

    # App 侧判错上报链路：auto-add 语义不变
    r = client.post("/api/wrongbook/auto-add", json={"item_id": seed["a2"], "mode": "spell_zh2en"})
    assert r.json()["data"]["wrong_count"] == 1
    r = client.post("/api/wrongbook/auto-add", json={"item_id": seed["a2"], "mode": "spell_zh2en"})
    assert r.json()["data"]["wrong_count"] == 2

    # 已掌握的 a1 判错 + App auto-add → wrong_count 累加，mastered_at 保留
    r = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": seed["a1"], "mode": "spell_zh2en",
              "answer": "wrong", "correct": False},
    )
    assert r.json()["data"]["correct"] is False
    client.post("/api/wrongbook/auto-add", json={"item_id": seed["a1"], "mode": "spell_zh2en"})
    with read_conn() as conn:
        row = conn.execute(
            "SELECT * FROM wrong_book WHERE item_id = ? AND mode = 'spell_zh2en'",
            (seed["a1"],),
        ).fetchone()
    assert row["wrong_count"] == 2
    assert row["mastered_at"] is not None, "判错不得清掉已有 mastered_at"
    assert row["resolved_at"] is None


def test_u12_4_en2zh_server_judges(client: TestClient, seed: dict):
    """spell_en2zh 不传 correct → 服务端 compare_service 判定（一轮行为不变）。"""
    sid = client.post(
        "/api/practice/start",
        json={"unit_id": seed["ua"], "type": "word", "mode": "spell_en2zh"},
    ).json()["data"]["session_id"]
    r = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": seed["a1"], "mode": "spell_en2zh",
              "answer": "zh-a-word-1", "elapsed_ms": 100},
    )
    assert r.json()["data"]["correct"] is True
    assert r.json()["data"]["matched_zh"] == "zh-a-word-1"


# ===========================================================================
# U12-9：settle 落库 JSON 数组；不传 → 回退 start 快照
# ===========================================================================

def test_u12_9_settle_json_and_fallback(client: TestClient, seed: dict):
    # 1) settle 传 unit_ids/types → 落库与请求一致
    sid = client.post(
        "/api/practice/start",
        json={"unit_id": seed["ua"], "type": "word", "mode": "spell_zh2en"},
    ).json()["data"]["session_id"]
    r = client.post(
        "/api/practice/settle",
        json={"session_id": sid, "duration_ms": 500,
              "unit_ids": [seed["ub"], seed["ua"]], "types": ["word", "phrase"]},
    )
    assert r.json()["code"] == 0 and r.json()["data"] == {}
    with read_conn() as conn:
        row = dict(conn.execute(
            "SELECT * FROM practice_logs ORDER BY id DESC LIMIT 1"
        ).fetchone())
    assert json.loads(row["unit_ids"]) == [seed["ub"], seed["ua"]]
    assert json.loads(row["types"]) == ["word", "phrase"]
    assert row["mode"] == "spell_zh2en" and row["total"] == 2 and row["wrong"] == 2
    assert row["correct"] + row["wrong"] == row["total"]

    # 2) settle 不传 → 回退 start 快照（单数 start 快照为单元素数组）
    sid = client.post(
        "/api/practice/start",
        json={"unit_id": seed["ub"], "type": "phrase", "mode": "listen"},
    ).json()["data"]["session_id"]
    r = client.post("/api/practice/settle", json={"session_id": sid, "duration_ms": 1})
    assert r.json()["code"] == 0
    with read_conn() as conn:
        row = dict(conn.execute(
            "SELECT * FROM practice_logs ORDER BY id DESC LIMIT 1"
        ).fetchone())
    assert json.loads(row["unit_ids"]) == [seed["ub"]]
    assert json.loads(row["types"]) == ["phrase"]

    # 3) 数组 start 快照：多单元多类型
    sid = client.post(
        "/api/practice/start",
        json={"mode": "listen", "unit_ids": [seed["ub"], seed["ua"]],
              "types": ["word", "phrase"]},
    ).json()["data"]["session_id"]
    client.post("/api/practice/settle", json={"session_id": sid, "duration_ms": 1})
    with read_conn() as conn:
        row = dict(conn.execute(
            "SELECT * FROM practice_logs ORDER BY id DESC LIMIT 1"
        ).fetchone())
    assert json.loads(row["unit_ids"]) == [seed["ub"], seed["ua"]]
    assert json.loads(row["types"]) == ["word", "phrase"]
    assert row["total"] == 6  # b1+a1+a2+bp0+bp1+ap 全量

    # 4) 非法 types → 10001
    sid = client.post(
        "/api/practice/start",
        json={"unit_id": seed["ua"], "type": "word", "mode": "listen"},
    ).json()["data"]["session_id"]
    r = client.post(
        "/api/practice/settle",
        json={"session_id": sid, "duration_ms": 1, "types": ["bogus"]},
    )
    assert r.json()["code"] == 10001
