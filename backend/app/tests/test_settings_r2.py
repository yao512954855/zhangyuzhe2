"""BE-12 验收 —— settings GET/PUT 接口（U12-7/U12-8 + 值域校验）。

对应《50-二轮后端工作包规格》BE-12 F12-6，契约《01-共享契约冻结》v1.3 §4.1 设置段。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_settings_r2_"))
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


def test_get_full_settings(client: TestClient):
    r = client.get("/api/settings")
    assert r.json()["code"] == 0
    data = r.json()["data"]
    # 005 迁移 3 键默认值（契约 §3.1b / §4.1 v1.3）
    assert data["dictation_countdown_s"] == 3
    assert data["spell_verdict_countdown_s"] == 3
    assert data["dictation_hint"] == "zh"
    # 006 迁移：cloud_api_key 种子为空
    assert data["cloud_api_key"] == ""
    # 既有键也在，且按 value_type 解析（int）
    assert isinstance(data["dictation_countdown_s"], int)
    assert data["retry_score_penalty"] == 10


# ===========================================================================
# U12-7：倒计时段值域 1~10
# ===========================================================================

def test_u12_7_countdown_range(client: TestClient):
    # =0 → 10001，且 GET 值不变
    r = client.put("/api/settings", json={"spell_verdict_countdown_s": 0})
    assert r.json()["code"] == 10001
    assert client.get("/api/settings").json()["data"]["spell_verdict_countdown_s"] == 3

    # =5 → 成功，GET 返回 5（响应体也是全量）
    r = client.put("/api/settings", json={"spell_verdict_countdown_s": 5})
    assert r.json()["code"] == 0
    assert r.json()["data"]["spell_verdict_countdown_s"] == 5
    assert client.get("/api/settings").json()["data"]["spell_verdict_countdown_s"] == 5

    # 边界：1 与 10 合法，11 非法
    assert client.put("/api/settings", json={"dictation_countdown_s": 1}).json()["code"] == 0
    assert client.put("/api/settings", json={"dictation_countdown_s": 10}).json()["code"] == 0
    assert client.put("/api/settings", json={"dictation_countdown_s": 11}).json()["code"] == 10001
    assert client.put("/api/settings", json={"dictation_countdown_s": -1}).json()["code"] == 10001

    # 非整数类型 → 10001（字符串 / 布尔）
    assert client.put("/api/settings", json={"dictation_countdown_s": "5"}).json()["code"] == 10001
    assert client.put("/api/settings", json={"dictation_countdown_s": True}).json()["code"] == 10001


# ===========================================================================
# U12-8：未知键 → 10001，其余键不受影响
# ===========================================================================

def test_u12_8_unknown_key_rejected_whole(client: TestClient):
    before = client.get("/api/settings").json()["data"]
    # 混合合法键 + 未知键 → 整体拒绝，合法键也不生效
    r = client.put(
        "/api/settings",
        json={"no_such_key": "x", "dictation_countdown_s": 8},
    )
    assert r.json()["code"] == 10001
    after = client.get("/api/settings").json()["data"]
    assert after == before, "PUT 含未知键时不得有任何键被修改"

    # 纯未知键
    r = client.put("/api/settings", json={"mystery": 1})
    assert r.json()["code"] == 10001
    # 空对象
    r = client.put("/api/settings", json={})
    assert r.json()["code"] == 10001


# ===========================================================================
# dictation_hint 值域 zh | en；PUT 局部更新返回全量
# ===========================================================================

def test_hint_value_domain(client: TestClient):
    r = client.put("/api/settings", json={"dictation_hint": "fr"})
    assert r.json()["code"] == 10001
    r = client.put("/api/settings", json={"dictation_hint": "EN"})
    assert r.json()["code"] == 10001, "枚举严格小写"

    r = client.put("/api/settings", json={"dictation_hint": "en"})
    assert r.json()["code"] == 0
    assert r.json()["data"]["dictation_hint"] == "en"


def test_put_partial_updates_only_given_keys(client: TestClient):
    r = client.put(
        "/api/settings",
        json={"dictation_countdown_s": 7, "dictation_hint": "zh"},
    )
    assert r.json()["code"] == 0
    data = r.json()["data"]
    assert data["dictation_countdown_s"] == 7
    assert data["dictation_hint"] == "zh"
    assert data["spell_verdict_countdown_s"] == 5, "未提交的键保持原值"


# ===========================================================================
# cloud_api_key（006 迁移）：PUT 实时生效 / GET 掩码 / 空串=清除覆盖
# ===========================================================================

def test_apikey_default_empty_and_put_masked(client: TestClient):
    # 默认空（006 种子），GET 返回空串
    assert client.get("/api/settings").json()["data"]["cloud_api_key"] == ""

    # PUT 后立即 GET：值生效且**掩码**（前4后4，明文不出服务）
    r = client.put("/api/settings", json={"cloud_api_key": "sk-live-1234567890abcd"})
    assert r.json()["code"] == 0
    assert r.json()["data"]["cloud_api_key"] == "sk-l****abcd"
    got = client.get("/api/settings").json()["data"]["cloud_api_key"]
    assert got == "sk-l****abcd"
    assert "sk-live-1234567890abcd" not in got

    # 空串 = 清除覆盖 → 回落 asr.yaml 种子（此处 DB 无 asr.yaml → 空串）
    r = client.put("/api/settings", json={"cloud_api_key": ""})
    assert r.json()["code"] == 0
    assert client.get("/api/settings").json()["data"]["cloud_api_key"] == ""


def test_apikey_validation(client: TestClient):
    # 非字符串 → 10001
    assert client.put("/api/settings", json={"cloud_api_key": 123}).json()["code"] == 10001
    # 含换行 → 10001
    assert client.put(
        "/api/settings", json={"cloud_api_key": "sk-a\nsk-b"}
    ).json()["code"] == 10001
    # 超 200 字符 → 10001
    assert client.put(
        "/api/settings", json={"cloud_api_key": "k" * 201}
    ).json()["code"] == 10001


def test_apikey_realtime_provider_resolution(client: TestClient):
    """实时生效核心：Provider 构造后改 settings，下次请求读到的就是新 key。"""
    from app.services.asr.aliyun_provider import AliyunProvider
    from app.services.asr.arbitrator import ArbitratorProvider
    from app.services.asr.fallback import FallbackProvider
    from app.services.asr.key_store import mask
    from app.services.settings_service import SettingsService

    SettingsService.set("cloud_api_key", "")  # 清覆盖，回到种子
    p = AliyunProvider({"aliyun": {"api_key": "sk-seed-0000"}})
    fb = FallbackProvider({"fallback": {}, "aliyun": {"api_key": "sk-seed-0000"}})
    arb = ArbitratorProvider({"arbitrator": {"enabled": True},
                              "aliyun": {"api_key": "sk-seed-0000"}})
    assert p.api_key == "sk-seed-0000"
    assert fb.api_key == "sk-seed-0000"
    assert arb.api_key == "sk-seed-0000"

    # 管理端改 key（不重启、不重建实例）→ 三个 Provider 立刻读到新值
    SettingsService.set("cloud_api_key", "sk-live-9999")
    assert p.api_key == "sk-live-9999"
    assert fb.api_key == "sk-live-9999"
    assert arb.api_key == "sk-live-9999"

    # 清除覆盖 → 回落 asr.yaml 种子
    SettingsService.set("cloud_api_key", "")
    assert p.api_key == "sk-seed-0000"

    # 掩码规则
    assert mask("sk-live-1234567890abcd") == "sk-l****abcd"
    assert mask("short") == "*****"
    assert mask("") == ""
