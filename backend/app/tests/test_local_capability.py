"""批次A 验收 —— AI 能力三模式框架（docs/localize-dev/10-后端-能力框架.md）。

覆盖：resolve_mode 优先级 / env 锁定 / 非法值容错 / is_locked /
GET+PUT /api/capabilities / ASR order 映射（localize-dev/01 §1.3）。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_local_cap_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.capability import resolve_mode  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.deps import CurrentUser  # noqa: E402
from app.main import app  # noqa: E402
from app.services.asr.session_manager import ASRSession  # noqa: E402
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
    yield
    # 模块收尾还原模式：本模块大量 set capability_mode，残留会毒害后续模块
    # （后跑的模块若没重设 DB_PATH 会复用本库）——隔离纪律，见 localize-dev 批次 A。
    try:
        SettingsService._cache = None
        SettingsService().set("capability_mode", "hybrid")
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _no_cap_env(monkeypatch):
    """确保测试不受外部 CAP_* 环境变量污染（用完即恢复）。"""
    for key in ("CAP_TTS", "CAP_OCR", "CAP_ASR"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(scope="module")
def client(_fresh_config):
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# resolve_mode / is_locked（纯逻辑）
# ---------------------------------------------------------------------------

def test_default_mode_is_hybrid(client):
    """无 env、settings 未设 → 三能力都 hybrid（契约 §2 规则 3）。"""
    SettingsService._cache = None
    for cap in ("tts", "ocr", "asr"):
        assert resolve_mode(cap) == "hybrid"


def test_settings_mode_wins(client):
    """管理端按钮写的 settings.capability_mode 对三能力生效。"""
    SettingsService().set("capability_mode", "local")
    SettingsService._cache = None
    for cap in ("tts", "ocr", "asr"):
        assert resolve_mode(cap) == "local"


def test_env_lock_overrides_settings(client, monkeypatch):
    """.env 锁定键优先于 settings（契约 §2 规则 1），且只影响该能力。"""
    SettingsService().set("capability_mode", "local")
    SettingsService._cache = None
    monkeypatch.setenv("CAP_TTS", "cloud")
    assert resolve_mode("tts") == "cloud"
    assert resolve_mode("ocr") == "local"
    assert resolve_mode("asr") == "local"


def test_invalid_env_value_falls_back_hybrid(client, monkeypatch):
    """env 锁定值非法（如 auto）→ 按 hybrid，不抛异常（契约 §2）。"""
    monkeypatch.setenv("CAP_OCR", "auto")
    assert resolve_mode("ocr") == "hybrid"


def test_invalid_settings_value_falls_back_hybrid(client):
    """settings 值非法 → 按 hybrid，不抛异常。"""
    SettingsService().set("capability_mode", "auto")
    SettingsService._cache = None
    assert resolve_mode("asr") == "hybrid"


def test_is_locked_semantics(client, monkeypatch):
    """is_locked = 对应 env 键非空。"""
    monkeypatch.setenv("CAP_ASR", "local")
    from app.core.capability import is_locked

    assert is_locked("asr") is True
    assert is_locked("tts") is False
    assert is_locked("ocr") is False


def test_unknown_cap_raises(client):
    """未知能力名 → ValueError（编程错误要立刻暴露）。"""
    with pytest.raises(ValueError):
        resolve_mode("stt")


# ---------------------------------------------------------------------------
# GET / PUT /api/capabilities
# ---------------------------------------------------------------------------

def test_get_capabilities_structure(client):
    """GET 结构逐字段断言（契约 §5）。"""
    SettingsService().set("capability_mode", "hybrid")
    SettingsService._cache = None
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["mode"] == "hybrid"
    for cap in ("tts", "ocr", "asr"):
        e = data["engines"][cap]
        assert e["mode"] == "hybrid"
        assert e["locked"] is False
        assert isinstance(e["local_ready"], bool)
        assert isinstance(e["local_detail"], str) and e["local_detail"]


def test_put_mode_roundtrip(client):
    """PUT 合法值写入 → GET 可见（热生效）。"""
    r = client.put("/api/capabilities/mode", json={"mode": "local"})
    assert r.status_code == 200
    assert r.json()["code"] == 0
    assert r.json()["data"]["mode"] == "local"
    assert client.get("/api/capabilities").json()["data"]["mode"] == "local"
    SettingsService().set("capability_mode", "hybrid")  # 还原，别影响别的测试


def test_put_mode_invalid(client):
    """PUT 非法值 → 200 + 非零 code（业务失败 envelope，契约 §5）。"""
    r = client.put("/api/capabilities/mode", json={"mode": "auto"})
    assert r.status_code == 200
    assert r.json()["code"] != 0
    # 坏值没有写进去
    assert client.get("/api/capabilities").json()["data"]["mode"] == "hybrid"


# ---------------------------------------------------------------------------
# ASR order 映射（契约 §1.3）
# ---------------------------------------------------------------------------

class _FakeSend:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def __call__(self, msg: dict) -> None:  # noqa: D401
        self.sent.append(msg)


def _make_session(raw: dict | None = None) -> ASRSession:
    user = CurrentUser(id=1, role="student", username="stu", real_name="同学")
    # 显式传 raw_config：不依赖 backend/asr.yaml / ASR_CONFIG_PATH（前面的测试模块
    # 会把 ASR_CONFIG_PATH 指到 mock yaml 且不恢复——污染隔离，order 语义只看本参数）。
    return ASRSession(user=user, send=_FakeSend(), raw_config=raw)


_RAW = {"provider": "aliyun", "backup_provider": ""}


def test_asr_order_cloud(client):
    """settings=cloud → 原样用 raw（primary=aliyun 无备，现状行为）。"""
    SettingsService().set("capability_mode", "cloud")
    SettingsService._cache = None
    session = _make_session(dict(_RAW))
    assert session.router.primary_name == "aliyun"
    assert session.router.has_backup is False


def test_asr_order_hybrid(client):
    """settings=hybrid → primary=local 备=原主厂商 aliyun。"""
    SettingsService().set("capability_mode", "hybrid")
    SettingsService._cache = None
    session = _make_session(dict(_RAW))
    assert session.router.primary_name == "local"
    assert session.router.backup_name == "aliyun"


def test_asr_order_hybrid_falls_back_to_raw_primary(client):
    """hybrid 回落顺位 = raw 原主厂商（非写死 aliyun；契约 §1.3 v1.0.1）。"""
    SettingsService().set("capability_mode", "hybrid")
    SettingsService._cache = None
    session = _make_session({"provider": "volcengine", "backup_provider": ""})
    assert session.router.primary_name == "local"
    assert session.router.backup_name == "volcengine"


def test_asr_order_local(client):
    """settings=local → primary=local 无备（断了也不许偷用云端）。"""
    SettingsService().set("capability_mode", "local")
    SettingsService._cache = None
    session = _make_session(dict(_RAW))
    assert session.router.primary_name == "local"
    assert session.router.has_backup is False


def test_asr_order_env_lock(client, monkeypatch):
    """env 锁 CAP_ASR=cloud → 即使 settings=local，会话仍走 raw 原配置。"""
    SettingsService().set("capability_mode", "local")
    SettingsService._cache = None
    monkeypatch.setenv("CAP_ASR", "cloud")
    session = _make_session(dict(_RAW))
    assert session.router.primary_name == "aliyun"
