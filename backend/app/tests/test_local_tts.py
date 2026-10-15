"""批次B 验收 —— 本地 TTS 三模式路由（docs/localize-dev/11-后端-TTS-本地.md §6）。

全部 Mock：monkeypatch tts_local.synthesize_local / tts_service._synthesize_remote
注入假引擎；模式用 monkeypatch tts_service.resolve_mode 直接控制（**不写库**，
避免污染共享 SettingsService 单例 / DB，影响 test_s2_tts 等基线测试）。
不起真引擎、不起真服务合成。

覆盖契约 §1.1 行为矩阵关键不变量，尤其 local 模式失败**绝不碰云端**（红线）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_local_tts_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import BASE_DIR, get_settings, reset_config_cache  # noqa: E402
from app.core.errors import BizError, ErrorCode  # noqa: E402
from app.core.rate_limit import clear_all  # noqa: E402
from app.main import app  # noqa: E402
from app.services import tts_local, tts_service  # noqa: E402

# 假 WAV 字节（RIFF 头 + 足够长度，确保缓存判定 >128 命中）
_FAKE_WAV = b"RIFF" + b"\x00" * 200


@pytest.fixture(scope="module", autouse=True)
def _fresh_config():
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE
    clear_all()
    yield


@pytest.fixture(autouse=True)
def _no_cap_env(monkeypatch):
    """清除外部 CAP_TTS 锁定，保证模式由 resolve_mode 桩控制。"""
    monkeypatch.delenv("CAP_TTS", raising=False)


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前清空 TTS 磁盘缓存，保证缓存命中测试独立。"""
    cache_dir = BASE_DIR / "data" / "tts_cache"
    if cache_dir.is_dir():
        for f in cache_dir.glob("*.wav"):
            f.unlink()
    yield
    if cache_dir.is_dir():
        for f in cache_dir.glob("*.wav"):
            f.unlink()


@pytest.fixture(autouse=True)
def _local_ready_true(monkeypatch):
    """路由测试默认假设「本地 TTS 能力已就绪」（两模型齐备），由本夹具模拟。

    真实探活（engine_ready）依赖 melo 中文模型是否下载；本测试只验证路由逻辑，
    故直接桩成就绪。test_s2_tts 等基线测试不桩，会按真实探活（melo 缺失）走云端。
    """
    monkeypatch.setattr(tts_service, "engine_ready", lambda cap: (True, "mock ready"))


@pytest.fixture(scope="module")
def client():
    """autouse：触发 app lifespan → 执行数据库迁移，建出 settings 等表。"""
    with TestClient(app) as c:
        yield c


def _fake_local_factory(calls: dict, fail: bool):
    def _fake(text, lang="zh-CN"):
        calls["local"] += 1
        if fail:
            raise RuntimeError("mock local engine failure")
        return _FAKE_WAV
    return _fake


def _fake_remote_factory(calls: dict, fail: bool):
    def _fake(text, api_key):
        calls["remote"] += 1
        if fail:
            raise BizError(ErrorCode.TTS_SYNTH_FAILED)
        return _FAKE_WAV
    return _fake


# ---------------------------------------------------------------------------
# 1. cloud 模式 → 只走 remote，local 函数零调用
# ---------------------------------------------------------------------------

def test_cloud_only_remote(monkeypatch):
    calls = {"local": 0, "remote": 0}
    monkeypatch.setattr(tts_local, "synthesize_local", _fake_local_factory(calls, fail=False))
    monkeypatch.setattr(tts_service, "_synthesize_remote", _fake_remote_factory(calls, fail=False))
    monkeypatch.setattr(tts_service, "resolve_mode", lambda cap: "cloud")

    out = tts_service.synthesize("hello", "en-US")
    assert out == _FAKE_WAV
    assert calls["local"] == 0          # 契约：cloud 不碰本地
    assert calls["remote"] == 1


# ---------------------------------------------------------------------------
# 2. local 模式成功 → 走 local 且不碰 remote；缓存落盘（engine=local 键）
# ---------------------------------------------------------------------------

def test_local_success_no_remote(monkeypatch):
    calls = {"local": 0, "remote": 0}
    monkeypatch.setattr(tts_local, "synthesize_local", _fake_local_factory(calls, fail=False))
    monkeypatch.setattr(tts_service, "_synthesize_remote", _fake_remote_factory(calls, fail=False))
    monkeypatch.setattr(tts_service, "resolve_mode", lambda cap: "local")

    text, lang = "apple", "en-US"
    out = tts_service.synthesize(text, lang)
    assert out == _FAKE_WAV
    assert calls["local"] == 1
    assert calls["remote"] == 0        # 契约：local 不碰云端

    cache = tts_service._cache_path("local", text, lang)
    assert cache.exists() and cache.read_bytes() == _FAKE_WAV


# ---------------------------------------------------------------------------
# 3. local 模式引擎失败 → 50006，且 remote 调用次数=0（契约红线）
# ---------------------------------------------------------------------------

def test_local_failure_raises_50006_and_no_remote(monkeypatch):
    calls = {"local": 0, "remote": 0}
    monkeypatch.setattr(tts_local, "synthesize_local", _fake_local_factory(calls, fail=True))
    monkeypatch.setattr(tts_service, "_synthesize_remote", _fake_remote_factory(calls, fail=False))
    monkeypatch.setattr(tts_service, "resolve_mode", lambda cap: "local")

    with pytest.raises(BizError) as exc:
        tts_service.synthesize("apple", "en-US")
    assert exc.value.code == ErrorCode.LOCAL_ENGINE_UNAVAILABLE
    assert calls["remote"] == 0        # 红线：local 失败绝不允许回落云端


# ---------------------------------------------------------------------------
# 4. hybrid 本地失败 → 自动走 remote 成功
# ---------------------------------------------------------------------------

def test_hybrid_local_fail_falls_back_remote(monkeypatch):
    calls = {"local": 0, "remote": 0}
    monkeypatch.setattr(tts_local, "synthesize_local", _fake_local_factory(calls, fail=True))
    monkeypatch.setattr(tts_service, "_synthesize_remote", _fake_remote_factory(calls, fail=False))
    monkeypatch.setattr(tts_service, "resolve_mode", lambda cap: "hybrid")

    out = tts_service.synthesize("apple", "en-US")
    assert out == _FAKE_WAV
    assert calls["local"] == 1
    assert calls["remote"] == 1        # 回落云端成功


# ---------------------------------------------------------------------------
# 5. hybrid 两者都失败 → 50005
# ---------------------------------------------------------------------------

def test_hybrid_both_fail_50005(monkeypatch):
    calls = {"local": 0, "remote": 0}
    monkeypatch.setattr(tts_local, "synthesize_local", _fake_local_factory(calls, fail=True))
    monkeypatch.setattr(tts_service, "_synthesize_remote", _fake_remote_factory(calls, fail=True))
    monkeypatch.setattr(tts_service, "resolve_mode", lambda cap: "hybrid")

    with pytest.raises(BizError) as exc:
        tts_service.synthesize("apple", "en-US")
    assert exc.value.code == ErrorCode.TTS_SYNTH_FAILED


# ---------------------------------------------------------------------------
# 6. 缓存命中：同 text+lang+engine 第二次调用引擎函数次数不增加
# ---------------------------------------------------------------------------

def test_cache_hit_skips_engine(monkeypatch):
    calls = {"local": 0, "remote": 0}
    monkeypatch.setattr(tts_local, "synthesize_local", _fake_local_factory(calls, fail=False))
    monkeypatch.setattr(tts_service, "_synthesize_remote", _fake_remote_factory(calls, fail=False))
    monkeypatch.setattr(tts_service, "resolve_mode", lambda cap: "local")

    text, lang = "good morning", "en-US"
    tts_service.synthesize(text, lang)
    tts_service.synthesize(text, lang)   # 第二次应命中本地缓存
    assert calls["local"] == 1          # 引擎只真正合成一次


# ---------------------------------------------------------------------------
# 7. 接口层：POST /api/tts 在 local 成功时返回 audio/wav 且内容 RIFF
# ---------------------------------------------------------------------------

def test_api_tts_local_returns_wav(client, monkeypatch):
    calls = {"local": 0, "remote": 0}
    monkeypatch.setattr(tts_local, "synthesize_local", _fake_local_factory(calls, fail=False))
    monkeypatch.setattr(tts_service, "_synthesize_remote", _fake_remote_factory(calls, fail=False))
    monkeypatch.setattr(tts_service, "resolve_mode", lambda cap: "local")

    r = client.post("/api/tts", json={"text": "apple", "lang": "en-US"})
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("audio/wav")
    assert r.content.startswith(b"RIFF")
    assert r.content == _FAKE_WAV
    assert calls["remote"] == 0
