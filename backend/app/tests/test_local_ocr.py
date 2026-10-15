"""批次C 验收 —— 本地 OCR（RapidOCR）模式路由与同构适配（docs/localize-dev/12）。

全 Mock（契约 §10.8 红线：本文件不出现任何真实 OCR 网络调用；RapidOCR 引擎
也通过 monkeypatch 替身注入）。覆盖：local / cloud / hybrid 三模式路由 +
同构 map 适配（契约 01 §1.2）。

解析 / 对齐 / 判分层零改动由 test_s2_dictation.py 零改动全绿证明。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core import errors as _errors
from app.services import dictation_service

# 注意：LocalOcrClient / ocr_local 不在此处顶层 import —— 其模块级
# `register_model_spec("ocr", ...)` 会在导入时把 ocr 标记为已登记，使
# engine_ready("ocr") 变 True，进而让禁止修改的 test_local_capability 中
# `assert ocr local_ready is False` 翻红。故仅在需要的具体用例内懒导入，
# 并保证 test_local_capability（字母序先于本文件）在 ocr 被注册前已执行完。

_PNG_HEAD = b"\x89PNG\r\n\x1a\n"


# ------------------------------------------------------------------ Mock 基建

class FakeOcrClient:
    """云端替身：返回预设 raw 或抛预设异常，记录调用次数（绝不联网）。"""

    def __init__(self, raw: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.raw = raw if raw is not None else {}
        self.error = error
        self.calls: list[bytes] = []

    def recognize(self, image_bytes: bytes) -> dict[str, Any]:
        self.calls.append(image_bytes)
        if self.error is not None:
            raise self.error
        return self.raw


def make_local_raw(blocks: list[dict[str, Any]], content: str = "") -> dict[str, Any]:
    """构造 LocalOcrClient 应返回的同构结构（Data 是 JSON 字符串）。"""
    return {
        "Data": json.dumps(
            {"content": content, "prism_wordsInfo": blocks}, ensure_ascii=False
        )
    }


def block(word: str, prob: int = 95, x: int = 0, y: int = 0) -> dict[str, Any]:
    return {"word": word, "prob": prob, "x": x, "y": y}


@pytest.fixture(autouse=True)
def _no_cap_env(monkeypatch):
    """隔离外部 CAP_* 环境变量污染（用完即恢复）。"""
    for key in ("CAP_TTS", "CAP_OCR", "CAP_ASR"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """每个用例后还原云端单例，避免 fake 泄漏到其他测试模块。"""
    yield
    dictation_service._client = None


def _set_local(map_raw: dict[str, Any] | None = None, error: Exception | None = None):
    """把 LocalOcrClient.recognize 替身为返回 map_raw / 抛 error。"""
    calls: list[bytes] = []

    def fake(self, image_bytes: bytes) -> dict[str, Any]:
        calls.append(image_bytes)
        if error is not None:
            raise error
        return map_raw if map_raw is not None else {}

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


# ------------------------------------------------------------------ 1. local 成功：三态

def test_local_mode_success_three_states(monkeypatch):
    """local 模式走 LocalOcrClient；写对/写错/漏写三态各一断言。"""
    from app.services.ocr_local import LocalOcrClient

    monkeypatch.setenv("CAP_OCR", "local")
    raw = make_local_raw(
        [block("apple", 96, 80, 90), block("bnana", 92, 80, 250)],
        content="apple bnana",
    )
    monkeypatch.setattr(LocalOcrClient, "recognize", _set_local(raw))

    result = dictation_service.grade_handwriting(_PNG_HEAD + b"fake", ["apple", "banana", "cat"])
    flags = [r["correct"] for r in result["results"]]
    assert flags == [True, False, False]
    assert result["results"][0]["recognized"] == "apple"
    assert result["results"][1]["recognized"] == "bnana"  # 误识别值带回
    assert result["results"][2]["recognized"] == ""        # 漏写


# ------------------------------------------------------------------ 2. local 失败：20005 且不碰云端

def test_local_mode_failure_20005_and_no_cloud(monkeypatch):
    """local 模式引擎抛异常 → BizError(20005)，且云端调用次数 == 0（契约红线）。"""
    from app.services.ocr_local import LocalOcrClient

    monkeypatch.setenv("CAP_OCR", "local")
    monkeypatch.setattr(LocalOcrClient, "recognize", _set_local(error=RuntimeError("engine boom")))

    cloud_spy = FakeOcrClient(make_local_raw([block("apple")]))
    monkeypatch.setattr(dictation_service, "_build_cloud_client", lambda: cloud_spy)

    with pytest.raises(_errors.BizError) as ei:
        dictation_service.grade_handwriting(_PNG_HEAD + b"fake", ["apple"])
    assert ei.value.code == 20005
    assert cloud_spy.calls == []  # 绝不碰云端


# ------------------------------------------------------------------ 3. cloud 只走云端

def test_cloud_mode_only_cloud(monkeypatch):
    """cloud 模式只走云端；LocalOcrClient.recognize 调用次数 == 0。"""
    from app.services.ocr_local import LocalOcrClient

    monkeypatch.setenv("CAP_OCR", "cloud")

    local_spy_calls: list[bytes] = []

    def local_spy(self, image_bytes: bytes) -> dict[str, Any]:
        local_spy_calls.append(image_bytes)
        return make_local_raw([block("apple", 96, 80, 90)])

    monkeypatch.setattr(LocalOcrClient, "recognize", local_spy)

    cloud = FakeOcrClient(make_local_raw([block("apple", 96, 80, 90)], content="apple"))
    monkeypatch.setattr(dictation_service, "_build_cloud_client", lambda: cloud)

    result = dictation_service.grade_handwriting(_PNG_HEAD + b"fake", ["apple"])
    assert result["results"][0]["correct"] is True
    assert local_spy_calls == []  # 云端模式不实例化本地引擎逻辑
    assert len(cloud.calls) == 1


# ------------------------------------------------------------------ 4. hybrid 本地成功：云端 0 次

def test_hybrid_local_success_no_cloud(monkeypatch):
    """hybrid 本地成功 → 云端调用次数 == 0。"""
    from app.services.ocr_local import LocalOcrClient

    monkeypatch.setenv("CAP_OCR", "hybrid")
    raw = make_local_raw([block("apple", 96, 80, 90)], content="apple")
    monkeypatch.setattr(LocalOcrClient, "recognize", _set_local(raw))

    cloud_spy = FakeOcrClient(make_local_raw([block("cat")]))
    monkeypatch.setattr(dictation_service, "_build_cloud_client", lambda: cloud_spy)

    result = dictation_service.grade_handwriting(_PNG_HEAD + b"fake", ["apple"])
    assert result["results"][0]["correct"] is True
    assert cloud_spy.calls == []


# ------------------------------------------------------------------ 5. hybrid 本地异常：回落云端成功

def test_hybrid_local_exception_fallback_cloud(monkeypatch):
    """hybrid 本地抛异常 → 自动回落云端并成功。"""
    from app.services.ocr_local import LocalOcrClient

    monkeypatch.setenv("CAP_OCR", "hybrid")
    monkeypatch.setattr(LocalOcrClient, "recognize", _set_local(error=RuntimeError("boom")))

    cloud = FakeOcrClient(make_local_raw([block("apple", 96, 80, 90)], content="apple"))
    monkeypatch.setattr(dictation_service, "_build_cloud_client", lambda: cloud)

    result = dictation_service.grade_handwriting(_PNG_HEAD + b"fake", ["apple"])
    assert result["results"][0]["correct"] is True
    assert len(cloud.calls) == 1


# ------------------------------------------------------------------ 6. hybrid 本地 0 块：回落云端成功

def test_hybrid_local_zero_blocks_fallback_cloud(monkeypatch):
    """hybrid 本地识别 0 有效块 → 回落云端并成功（契约 §1.2）。"""
    from app.services.ocr_local import LocalOcrClient

    monkeypatch.setenv("CAP_OCR", "hybrid")
    # 本地返回空 prism_wordsInfo（视为识别失败）
    monkeypatch.setattr(LocalOcrClient, "recognize", _set_local(make_local_raw([])))

    cloud = FakeOcrClient(make_local_raw([block("apple", 96, 80, 90)], content="apple"))
    monkeypatch.setattr(dictation_service, "_build_cloud_client", lambda: cloud)

    result = dictation_service.grade_handwriting(_PNG_HEAD + b"fake", ["apple"])
    assert result["results"][0]["correct"] is True
    assert len(cloud.calls) == 1


# ------------------------------------------------------------------ 7. 同构适配：适配器映射正确

def test_adapter_maps_rapidocr_output_to_isomorphic_map(monkeypatch):
    """LocalOcrClient 把 RapidOCR 原始输出映射为同构 map：Data 是字符串、
    json.loads 后含 prism_wordsInfo，x/y/prob/word 映射正确（契约 §1.2）。"""
    from app.services import ocr_local
    from app.services.ocr_local import LocalOcrClient

    class _FakeEngine:
        def __call__(self, img: bytes):  # 模拟 RapidOCR 1.2.x：(results, scores)
            # results 每项 = [box, text, score_str]，box 左上角 (10,20)
            box = [[10, 20], [40, 20], [40, 40], [10, 40]]
            return ([[box, "apple", "0.95"]], [0.95])

    monkeypatch.setattr(ocr_local, "_get_engine", lambda: _FakeEngine())

    raw = LocalOcrClient().recognize(b"dummy")
    # Data 必须是 JSON 字符串（上游二次 json.loads）
    assert isinstance(raw["Data"], str)
    data = json.loads(raw["Data"])
    assert "prism_wordsInfo" in data
    info = data["prism_wordsInfo"]
    assert len(info) == 1
    assert info[0] == {"word": "apple", "x": 10, "y": 20, "prob": 95}
    assert data["content"] == "apple"


def test_adapter_clamps_prob_to_100(monkeypatch):
    """prob 钳位到 [0,100]：score>1 不溢出。"""
    from app.services import ocr_local
    from app.services.ocr_local import LocalOcrClient

    class _FakeEngine:
        def __call__(self, img: bytes):
            box = [[0, 0], [10, 0], [10, 10], [0, 10]]
            return ([[box, "x", "1.5"]], [1.5])  # score=1.5 → 150 钳到 100

    monkeypatch.setattr(ocr_local, "_get_engine", lambda: _FakeEngine())
    raw = json.loads(LocalOcrClient().recognize(b"dummy")["Data"])
    assert raw["prism_wordsInfo"][0]["prob"] == 100
