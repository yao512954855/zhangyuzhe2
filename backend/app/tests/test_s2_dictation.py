"""WP-D 验收 —— 听写批改（后端部分 U1-U2 / U6 + 对齐与归一化单测）。

对应《10-后端工作包规格》WP-D 验收用例：
- U1 Mock 全对照片 → correct 全 true；
- U2 Mock 错一词 → correct=false 且 recognized 非空；
- 附加：uncertain（置信度 <85）、坏图 20006、超时 20005、
  Data 二次 json.loads、(y 行带, x) 排序、归一化（大小写/空格/连字符/题号）。

红线（契约 §10.8）：本文件**全部 Mock** OCR 客户端（`_OcrClient.recognize`），
不出现任何真实网络调用与 OCR 端点字面量（U10 grep 项）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_s2_dictation_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.main import app  # noqa: E402
from app.services import dictation_service  # noqa: E402
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


# ------------------------------------------------------------------ Mock 基建

_JPEG_HEAD = b"\xff\xd8\xff\xe0"  # JPEG 魔数 + 占位
_PNG_HEAD = b"\x89PNG\r\n\x1a\n"


class FakeOcrClient:
    """替身客户端：返回预设原始结构，或抛预设异常（绝不联网）。"""

    def __init__(
        self,
        raw: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.raw = raw if raw is not None else {}
        self.error = error
        self.calls: list[bytes] = []

    def recognize(self, image_bytes: bytes) -> dict[str, Any]:
        self.calls.append(image_bytes)
        if self.error is not None:
            raise self.error
        return self.raw


def make_raw(blocks: list[dict[str, Any]], content: str = "") -> dict[str, Any]:
    """构造 SDK 原始返回：注意 `Data` 是 **JSON 字符串**（需二次 json.loads）。"""
    return {
        "RequestId": "req-mock",
        "Data": json.dumps({"content": content, "prism_wordsInfo": blocks}, ensure_ascii=False),
        "Code": "0",
    }


def block(word: str, prob: int = 95, x: int = 0, y: int = 0) -> dict[str, Any]:
    return {"word": word, "prob": prob, "x": x, "y": y}


@pytest.fixture(autouse=True)
def _reset_singleton():
    """每个用例后还原单例，避免 fake 泄漏到其他测试文件。"""
    yield
    dictation_service._client = None


WORDS6 = ["apple", "banana", "cat", "dog", "school", "happy"]


# ------------------------------------------------------------------ 服务级单测


class TestGradeHandwriting:
    def test_u1_all_correct(self):
        """U1：Mock 全对照片 → correct 全 true，recognized 即原文。"""
        blocks = [
            block("apple", 96, x=80, y=90),
            block("banana", 95, x=80, y=250),
            block("cat", 97, x=80, y=410),
            block("dog", 94, x=500, y=90),
            block("school", 96, x=500, y=250),
            block("happy", 98, x=500, y=410),
        ]
        dictation_service._client = FakeOcrClient(make_raw(blocks, "apple banana cat dog school happy"))
        result = dictation_service.grade_handwriting(_JPEG_HEAD + b"fake", WORDS6)
        assert [r["correct"] for r in result["results"]] == [True] * 6
        assert all(r["recognized"] == w for r, w in zip(result["results"], WORDS6))
        assert all(r["uncertain"] is False for r in result["results"])
        assert result["ocr_content"] == "apple banana cat dog school happy"

    def test_u2_one_word_wrong(self):
        """U2：Mock 错一词 → 该词 correct=false 且 recognized 非空。"""
        blocks = [
            block("apple", 96, x=80, y=90),
            block("bnana", 92, x=80, y=250),  # 学生把 banana 写错 / 识别为 bnana
            block("cat", 97, x=80, y=410),
            block("dog", 94, x=500, y=90),
            block("school", 96, x=500, y=250),
            block("happy", 98, x=500, y=410),
        ]
        dictation_service._client = FakeOcrClient(make_raw(blocks))
        result = dictation_service.grade_handwriting(_JPEG_HEAD + b"fake", WORDS6)
        flags = [r["correct"] for r in result["results"]]
        assert flags.count(True) == 5 and flags.count(False) == 1
        banana = result["results"][1]
        assert banana["word"] == "banana"
        assert banana["correct"] is False
        assert banana["recognized"] == "bnana", "错词必须带出实际识别值"
        assert banana["confidence"] == 92

    def test_uncertain_low_confidence(self):
        """认错但 uncertain：对齐块置信度 <85 → uncertain=true（拿不准）。"""
        blocks = [block("aple", 70, x=80, y=90)]
        dictation_service._client = FakeOcrClient(make_raw(blocks))
        result = dictation_service.grade_handwriting(_JPEG_HEAD + b"fake", ["apple"])
        r = result["results"][0]
        assert r["correct"] is False and r["recognized"] == "aple"
        assert r["uncertain"] is True and r["confidence"] == 70

    def test_uncertain_even_when_matched(self):
        """写对了但置信度低（字迹潦草）也标 uncertain，提示老师复核。"""
        blocks = [block("apple", 84, x=80, y=90)]
        dictation_service._client = FakeOcrClient(make_raw(blocks))
        result = dictation_service.grade_handwriting(_JPEG_HEAD + b"fake", ["apple"])
        r = result["results"][0]
        assert r["correct"] is True and r["uncertain"] is True

    def test_missing_word_recognized_empty(self):
        """块少于词数：缺的词 recognized=""（契约 §4.1：无对齐则空串）。"""
        blocks = [block("apple", 96, x=80, y=90), block("cat", 96, x=80, y=410)]
        dictation_service._client = FakeOcrClient(make_raw(blocks))
        result = dictation_service.grade_handwriting(_JPEG_HEAD + b"fake", ["apple", "banana", "cat"])
        assert result["results"][0]["correct"] is True
        assert result["results"][1] == {
            "word": "banana", "recognized": "", "correct": False,
            "confidence": 0, "uncertain": False,
        }
        assert result["results"][2]["correct"] is True

    def test_sort_reading_order(self):
        """乱序返回的块按 (round(y/60) 行带, x) 排序后再对齐。"""
        blocks = [
            block("dog", 95, x=500, y=100),      # 第二行右
            block("apple", 95, x=80, y=95),      # 第一行左
            block("cat", 95, x=500, y=230),      # 第二行? y=230 → 带 round(230/60)=4
            block("banana", 95, x=80, y=220),    # round(220/60)=4，第一行? 带 4 内 x=80 在前
        ]
        dictation_service._client = FakeOcrClient(make_raw(blocks))
        result = dictation_service.grade_handwriting(_JPEG_HEAD + b"fake", ["apple", "banana", "dog", "cat"])
        assert [r["correct"] for r in result["results"]] == [True, True, True, True]

    def test_normalize_case_space_hyphen_numbering(self):
        """归一化：大小写 / 空格 / 连字符忽略；「1.」题号前缀剥除。"""
        blocks = [
            block("1. Apple", 95, x=80, y=90),
            block("BIG-CAT", 95, x=80, y=220),
            block("well done", 95, x=80, y=350),
        ]
        dictation_service._client = FakeOcrClient(make_raw(blocks))
        result = dictation_service.grade_handwriting(
            _JPEG_HEAD + b"fake", ["apple", "bigcat", "welldone"]
        )
        assert all(r["correct"] for r in result["results"])

    def test_bad_image_empty(self):
        """坏图（空字节）→ 20006，且不触发 OCR 调用。"""
        fake = FakeOcrClient(make_raw([]))
        dictation_service._client = fake
        with pytest.raises(dictation_service.BizError) as ei:
            dictation_service.grade_handwriting(b"", ["apple"])
        assert ei.value.code == 20006
        assert fake.calls == []

    def test_bad_image_too_large(self):
        """超过 10MB → 20006。"""
        fake = FakeOcrClient(make_raw([]))
        dictation_service._client = fake
        with pytest.raises(dictation_service.BizError) as ei:
            dictation_service.grade_handwriting(_JPEG_HEAD + b"x" * (10 * 1024 * 1024 + 1), ["apple"])
        assert ei.value.code == 20006
        assert fake.calls == []

    def test_bad_image_format(self):
        """非 JPEG/PNG/WebP（魔数不符）→ 20006。"""
        dictation_service._client = FakeOcrClient(make_raw([]))
        with pytest.raises(dictation_service.BizError) as ei:
            dictation_service.grade_handwriting(b"GIF89a" + b"fake", ["apple"])
        assert ei.value.code == 20006

    def test_png_and_webp_accepted(self):
        """PNG / WebP 魔数应放行到 OCR 调用。"""
        fake = FakeOcrClient(make_raw([block("apple")]))
        dictation_service._client = fake
        dictation_service.grade_handwriting(_PNG_HEAD + b"fake", ["apple"])
        dictation_service.grade_handwriting(b"RIFF1234WEBPVP8 fake", ["apple"])
        assert len(fake.calls) == 2

    def test_ocr_timeout_maps_20005(self):
        """超时（SDK 抛异常）→ 20005。"""
        dictation_service._client = FakeOcrClient(error=TimeoutError("read timeout"))
        with pytest.raises(dictation_service.BizError) as ei:
            dictation_service.grade_handwriting(_JPEG_HEAD + b"fake", ["apple"])
        assert ei.value.code == 20005

    def test_ocr_error_maps_20005(self):
        """云端报错（SDK 异常）→ 20005。"""
        dictation_service._client = FakeOcrClient(error=RuntimeError("sdk boom"))
        with pytest.raises(dictation_service.BizError) as ei:
            dictation_service.grade_handwriting(_JPEG_HEAD + b"fake", ["apple"])
        assert ei.value.code == 20005

    def test_data_second_json_loads(self):
        """Data 为 JSON 字符串必须二次解析（实测坑）；坏 Data → 20005。"""
        dictation_service._client = FakeOcrClient({"Data": "{not-json"})
        with pytest.raises(dictation_service.BizError) as ei:
            dictation_service.grade_handwriting(_JPEG_HEAD + b"fake", ["apple"])
        assert ei.value.code == 20005

    def test_empty_blocks_all_miss(self):
        """OCR 成功但一块没识别到：全部词判错、recognized 为空（不算 20005）。"""
        dictation_service._client = FakeOcrClient(make_raw([]))
        result = dictation_service.grade_handwriting(_JPEG_HEAD + b"fake", ["apple", "cat"])
        assert all(r["correct"] is False and r["recognized"] == "" for r in result["results"])

    def test_timeout_config_default(self):
        """OCR_TIMEOUT_S 缺省 15（契约 §8）。"""
        from app.services.dictation_service import _DEFAULT_TIMEOUT_S

        assert _DEFAULT_TIMEOUT_S == 15


# ------------------------------------------------------------------ API 层


class TestGradeApi:
    def _post(self, client: TestClient, image: bytes, words: list[str]):
        return client.post(
            "/api/dictation/grade",
            files={"file": ("photo.jpg", image, "image/jpeg")},
            data={"words": words},
        )

    def test_ok_envelope(self, client: TestClient):
        """成功：HTTP 200 + code=0 + results/ocr_content 字段齐全。"""
        dictation_service._client = FakeOcrClient(
            make_raw([block("apple", 96, x=80, y=90), block("cat", 95, x=80, y=250)])
        )
        r = self._post(client, _JPEG_HEAD + b"fake", ["apple", "cat"])
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert set(body["data"].keys()) == {"results", "ocr_content"}
        assert [it["word"] for it in body["data"]["results"]] == ["apple", "cat"]
        assert set(body["data"]["results"][0].keys()) == {
            "word", "recognized", "correct", "confidence", "uncertain",
        }

    def test_bad_image_http200_code20006(self, client: TestClient):
        """坏图：HTTP 仍 200，业务码 20006（契约 §4：业务失败不走 HTTP 错误码）。"""
        r = self._post(client, b"not-an-image", ["apple"])
        assert r.status_code == 200
        assert r.json()["code"] == 20006

    def test_ocr_failure_http200_code20005(self, client: TestClient):
        """OCR 挂掉：HTTP 仍 200，业务码 20005。"""
        dictation_service._client = FakeOcrClient(error=RuntimeError("boom"))
        r = self._post(client, _JPEG_HEAD + b"fake", ["apple"])
        assert r.status_code == 200
        assert r.json()["code"] == 20005

    def test_words_required(self, client: TestClient):
        """缺 words 表单字段 → 参数校验 10001。"""
        r = client.post(
            "/api/dictation/grade",
            files={"file": ("photo.jpg", _JPEG_HEAD + b"fake", "image/jpeg")},
        )
        assert r.json()["code"] == 10001
