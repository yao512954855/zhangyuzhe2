"""契约 v1.2 验收 —— 云端 TTS 兜底端点（POST /api/tts）。

背景：部分国产 ROM 不预装 TTS 引擎（实测 MIUI `not bound to TTS engine`），
听写播报需要后端合成兜底。云端依赖用 monkeypatch 桩掉，只测端点行为与缓存。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_s2_tts_"))
os.environ["DB_PATH"] = str(_TMP_DIR / "test.db")
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"
# 本文件只测云端 TTS 端点行为（云端依赖已 monkeypatch 桩掉）：
# 本地 TTS 模型就位后 hybrid 会优先 local，必须按契约 §2 部署锁钉死 cloud。
os.environ["CAP_TTS"] = "cloud"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.errors import BizError, ErrorCode  # noqa: E402
from app.main import app  # noqa: E402
from app.services import tts_service  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402
from app.core.config import reset_config_cache  # noqa: E402

_FAKE_WAV = b"RIFF" + b"\x00" * 256


@pytest.fixture(scope="module", autouse=True)
def _fresh_config(tmp_path_factory):
    os.environ["DB_PATH"] = str(_TMP_DIR / "test.db")
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    reset_config_cache()
    SettingsService._cache = None
    rate_limit.clear_all()
    tts_service._CACHE_DIR = tmp_path_factory.mktemp("tts_cache")
    return None


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_tts_empty_text_rejected(client: TestClient):
    r = client.post("/api/tts", json={"text": "  "})
    assert r.status_code == 200
    assert r.json()["code"] == ErrorCode.PARAM_INVALID


def test_tts_text_too_long_rejected(client: TestClient):
    r = client.post("/api/tts", json={"text": "a" * 201})
    assert r.json()["code"] == ErrorCode.PARAM_INVALID


def test_tts_success_and_cache(client: TestClient, monkeypatch):
    calls = []

    def fake_remote(text: str, api_key: str) -> bytes:
        calls.append(text)
        return _FAKE_WAV

    monkeypatch.setattr(tts_service, "_synthesize_remote", fake_remote)
    r1 = client.post("/api/tts", json={"text": "apple", "lang": "en-US"})
    assert r1.status_code == 200
    assert r1.headers["content-type"] == "audio/wav"
    assert r1.content == _FAKE_WAV
    assert calls == ["apple"], "首次必须真实走远端（此处为桩）"

    # 第二次同一文本 → 命中磁盘缓存，不再调远端
    r2 = client.post("/api/tts", json={"text": "apple", "lang": "en-US"})
    assert r2.content == _FAKE_WAV
    assert calls == ["apple"]


def test_tts_cloud_failure_maps_to_50005(client: TestClient, monkeypatch):
    def boom(text: str, api_key: str) -> bytes:
        raise BizError(ErrorCode.TTS_SYNTH_FAILED)

    monkeypatch.setattr(tts_service, "_synthesize_remote", boom)
    r = client.post("/api/tts", json={"text": "banana", "lang": "en-US"})
    assert r.status_code == 200
    assert r.json()["code"] == ErrorCode.TTS_SYNTH_FAILED
