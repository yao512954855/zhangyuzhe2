"""WP-A 验收 —— 练习链路（U6 start→settle / U10 compare_service / WS letter）。

对应《10-后端工作包规格》WP-A 验收用例 U6 / U10，外加契约 §5 letter
流式判定的行为测试（spell_zh2en，用 MockProvider 不起真服务）。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_s2_practice_"))
_DB_FILE = _TMP_DIR / "test.db"
_ASR_YAML = _TMP_DIR / "asr-test.yaml"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["ASR_CONFIG_PATH"] = str(_ASR_YAML)
# 云端路径验收：部署锁钉死 cloud（本地模型就位后 hybrid 会优先 local，契约 §2）
os.environ["CAP_ASR"] = "cloud"
os.environ["CAP_TTS"] = "cloud"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn  # noqa: E402
from app.main import app  # noqa: E402
from app.services import compare_service  # noqa: E402
from app.services.asr.session_manager import ASRSession  # noqa: E402
from app.core.deps import CurrentUser  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _fresh_config():
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["ASR_CONFIG_PATH"] = str(_ASR_YAML)
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
def unit_with_items(client: TestClient) -> int:
    uid = client.post("/api/units", json={"name": "P-Unit", "sort": 1}).json()["data"]["id"]
    words = [("apple", "苹果", "苹果、红果"), ("cat", "猫", ""), ("dog", "狗", "犬")]
    for en, zh, alt in words:
        r = client.post(
            "/api/items",
            json={"unit_id": uid, "type": "word", "en": en, "zh": zh, "alt_zh": alt},
        )
        assert r.json()["code"] == 0
    return uid


# ===========================================================================
# U10：compare_service —— 剥词性/标点/的/了/儿/别名 各 ≥2 例
# ===========================================================================

class TestCompareService:
    def test_u10_strip_pos(self):
        """剥词性 ≥2 例。"""
        assert compare_service.normalize_zh("adj. 大的", pos="adj.") == "大"
        assert compare_service.normalize_zh("v. 跑") == "跑"
        assert compare_service.normalize_zh("n 苹果") == "苹果"
        # 不误伤普通单词
        assert compare_service.normalize_zh("very good") == "verygood"

    def test_u10_strip_punct_and_space(self):
        """标点 ≥2 例。"""
        assert compare_service.normalize_zh("苹果,香蕉、梨！") == "苹果香蕉梨"
        assert compare_service.normalize_zh("早上好。 ") == "早上好"
        assert compare_service.normalize_zh("你好　世界！") == "你好世界"

    def test_u10_strip_de_suffix(self):
        """「的」≥2 例。"""
        assert compare_service.normalize_zh("美丽的") == "美丽"
        assert compare_service.normalize_zh("大的") == "大"
        assert compare_service.zh_matches("美丽的", "美丽") is True

    def test_u10_strip_le_suffix(self):
        """「了」≥2 例。"""
        assert compare_service.normalize_zh("吃饭了") == "吃饭"
        assert compare_service.normalize_zh("睡着了") == "睡着"
        assert compare_service.zh_matches("吃饭了", "吃饭") is True

    def test_u10_strip_er_suffix(self):
        """「儿」≥2 例。"""
        assert compare_service.normalize_zh("花儿") == "花"
        assert compare_service.normalize_zh("小鸟儿") == "小鸟"
        assert compare_service.zh_matches("花儿", "花") is True

    def test_u10_alias_via_alt_zh(self):
        """别名 ≥2 例（按顿号拆 alt_zh 逐个比）。"""
        assert compare_service.zh_matches("红果", "苹果", "苹果、红果") is True
        assert compare_service.zh_matches("犬", "狗", "犬") is True
        assert compare_service.zh_matches("洋柿子", "西红柿", "西红柿、洋柿子") is True
        # 不在别名表 → False
        assert compare_service.zh_matches("香蕉", "苹果", "红果") is False

    def test_u10_lowercase_output(self):
        assert compare_service.normalize_zh("ABC") == "abc"


# ===========================================================================
# U6：practice start → answer → settle
# ===========================================================================

def test_u6_start_answer_settle_writes_log(client: TestClient, unit_with_items: int):
    r = client.post(
        "/api/practice/start",
        json={"unit_id": unit_with_items, "type": "word", "mode": "spell_en2zh"},
    )
    body = r.json()["data"]
    assert r.json()["code"] == 0
    sid = body["session_id"]
    items = body["items"]
    assert len(items) == 3
    assert {i["en"] for i in items} == {"apple", "cat", "dog"}
    assert set(items[0].keys()) == {"id", "en", "pos", "zh", "alt_zh"}

    # 逐题作答：2 对 1 错
    marks = {"apple": "红果", "cat": "狗", "dog": "犬"}
    for item in items:
        r = client.post(
            "/api/practice/answer",
            json={
                "session_id": sid,
                "item_id": item["id"],
                "mode": "spell_en2zh",
                "answer": marks[item["en"]],
                "elapsed_ms": 1000,
            },
        )
        payload = r.json()["data"]
        if item["en"] in ("apple", "dog"):
            assert payload["correct"] is True, payload
            assert payload.get("matched_zh") in ("苹果", "狗")
        else:
            assert payload["correct"] is False

    # settle → practice_logs 落一行，correct+wrong=total
    r = client.post("/api/practice/settle", json={"session_id": sid, "duration_ms": 12345})
    assert r.json()["code"] == 0 and r.json()["data"] == {}
    with read_conn() as conn:
        rows = conn.execute("SELECT * FROM practice_logs ORDER BY id DESC LIMIT 1").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["unit_id"] == unit_with_items
    assert row["type"] == "word" and row["mode"] == "spell_en2zh"
    assert row["total"] == 3 and row["correct"] == 2 and row["wrong"] == 1
    assert row["correct"] + row["wrong"] == row["total"]
    assert row["duration_ms"] == 12345

    # settle 后会话消失 → 再 settle 报 10004
    r = client.post("/api/practice/settle", json={"session_id": sid, "duration_ms": 1})
    assert r.json()["code"] == 10004


def test_u6_start_empty_unit_returns_20004(client: TestClient):
    uid = client.post("/api/units", json={"name": "空单元", "sort": 9}).json()["data"]["id"]
    r = client.post(
        "/api/practice/start", json={"unit_id": uid, "type": "word", "mode": "listen"}
    )
    assert r.json()["code"] == 20004, r.json()


def test_u6_answer_spell_zh2en_fallback(client: TestClient, unit_with_items: int):
    """spell_zh2en 兜底：字母序列归一后比对（本地判格在 App，服务端只兜底）。"""
    r = client.post(
        "/api/practice/start",
        json={"unit_id": unit_with_items, "type": "word", "mode": "spell_zh2en"},
    )
    sid = r.json()["data"]["session_id"]
    cat = next(i for i in r.json()["data"]["items"] if i["en"] == "cat")
    ok_resp = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": cat["id"], "mode": "spell_zh2en",
              "answer": "C A T", "elapsed_ms": 800},
    )
    assert ok_resp.json()["data"]["correct"] is True
    bad_resp = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": cat["id"], "mode": "spell_zh2en",
              "answer": "cap", "elapsed_ms": 800},
    )
    assert bad_resp.json()["data"]["correct"] is False
    # answer 里的 listen 模式没有在线判定口径 → 10001
    r = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": cat["id"], "mode": "listen",
              "answer": "x", "elapsed_ms": 1},
    )
    assert r.json()["code"] == 10001


# ===========================================================================
# 契约 §5：WS letter 逐格判定（spell_zh2en）
# ===========================================================================

def _write_asr_yaml() -> None:
    body = {
        "provider": "mock",
        "lang_map": {"word": "en", "spell": "zh", "zh_answer": "zh"},
        "mock": {
            "final_text": "see",
            "by_task": {"spell": {"final_text": "see aye tee"}},
        },
        "hotwords": {"enabled": False},
        "arbitrator": {"enabled": False},
        "limits": {"max_connections": 60, "queue_size": 120, "queue_wait_ms": 300,
                   "heartbeat_timeout_s": 60, "max_session_seconds": 300, "chunk_ms": 150},
    }
    _ASR_YAML.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


class _FakeSend:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)


def _make_session() -> tuple[ASRSession, _FakeSend]:
    send = _FakeSend()
    user = CurrentUser(id=1, role="student", username="stu", real_name="同学")
    return ASRSession(user=user, send=send), send


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_ws_letter_flow_judges_cells(client: TestClient, unit_with_items: int):
    """start(mode=spell_zh2en, item_id) → 音频 → end → 逐格 letter 消息。

    end 时 MockProvider.close 补发一条 utterance（模拟本地引擎语句级语义，
    契约 §5 修订 v1.0.3），判格由 utterance 通道完成。
    """
    _write_asr_yaml()
    reset_config_cache()
    from app.api import asr_ws as asr_ws_api
    asr_ws_api.reset_gate()

    r = client.get("/api/items", params={"keyword": "cat"})
    cat_id = r.json()["data"]["items"][0]["id"]

    session, send = _make_session()

    async def flow() -> None:
        # 一轮念「see aye tee」→ 谐音表 → 'cat'，三格全对
        await session.handle_text(json.dumps(
            {"type": "start", "mode": "spell_zh2en", "item_id": cat_id,
             "session_id": "practice-sess-1"}
        ))
        await session.flush()
        await session.handle_text(json.dumps({"type": "end"}))
        await session.flush()

    run(flow())
    letters = [m for m in send.sent if m["type"] == "letter"]
    assert letters, send.sent
    assert [(m["index"], m["letter"], m["correct"]) for m in letters] == [
        (0, "c", True), (1, "a", True), (2, "t", True)
    ]
    assert letters[0]["session_id"] == "practice-sess-1", "session_id 原样透传"
    assert letters[0]["item_id"] == cat_id


def test_ws_letter_wrong_cell_does_not_advance(client: TestClient):
    """判错的格不填：correct=False 且游标不动（App 侧按 §6.4 处理连错）。"""
    session, send = _make_session()
    session._spell_item_id = 7
    session._spell_target = "cat"
    session._spell_index = 0
    session._mode = "spell_zh2en"

    session._spell_feed("bee")           # 念错 → b ≠ c（'bee' 谐音表即字母 B 的读音）
    run(session.flush())
    assert [(m["index"], m["letter"], m["correct"]) for m in send.sent] == [
        (0, "b", False)
    ]
    assert session._spell_index == 0, "判错游标不得前移"

    session._spell_feed("see")           # 重念 c → 填第 0 格
    session._spell_feed("aye tee")       # 补完第 1、2 格
    run(session.flush())
    assert [(m["index"], m["letter"], m["correct"]) for m in send.sent[1:]] == [
        (0, "c", True), (1, "a", True), (2, "t", True)
    ]
    assert session._spell_index == 3


def test_ws_letter_flow_utterance_without_end(client: TestClient, unit_with_items: int):
    """契约 §5 修订 v1.0.3：utterance（Vosk 语句边界）即判格，**不等 end**。

    2026-10-13 真机根因：App「看中文背英文」没有 end 入口，原实现判格
    只在 `_on_end` 触发 → 字母格永远不亮、页面像卡死。现在学生念一个
    字母停一下，服务端立刻逐格判定下发 letter。
    """
    _write_asr_yaml()
    reset_config_cache()
    from app.api import asr_ws as asr_ws_api
    asr_ws_api.reset_gate()

    r = client.get("/api/items", params={"keyword": "cat"})
    cat_id = r.json()["data"]["items"][0]["id"]

    session, send = _make_session()

    async def flow() -> None:
        await session.handle_text(json.dumps(
            {"type": "start", "mode": "spell_zh2en", "item_id": cat_id,
             "session_id": "practice-sess-1"}
        ))
        await session.flush()
        # 念字母 → 每个语句边界一次 utterance（**全程没有 end**）
        session._on_utterance("see", 100)
        session._on_utterance("aye", 200)
        session._on_utterance("tee", 300)
        await session.flush()

    run(flow())
    letters = [m for m in send.sent if m["type"] == "letter"]
    assert [(m["index"], m["letter"], m["correct"]) for m in letters] == [
        (0, "c", True), (1, "a", True), (2, "t", True)
    ]
    assert letters[0]["session_id"] == "practice-sess-1"
    assert letters[0]["item_id"] == cat_id


def test_ws_utterance_message_relayed_to_app(client: TestClient, unit_with_items: int):
    """契约 §5 修订 v1.0.4：utterance 文本本身也透传 App。

    zh2en 页面「实时显示听到的字母」用：utterance 与随后的 letter 判格
    **同源同序**，学生看到的内容就是实际参与判格的文本。
    """
    _write_asr_yaml()
    reset_config_cache()
    from app.api import asr_ws as asr_ws_api
    asr_ws_api.reset_gate()

    r = client.get("/api/items", params={"keyword": "cat"})
    cat_id = r.json()["data"]["items"][0]["id"]

    session, send = _make_session()

    async def flow() -> None:
        await session.handle_text(json.dumps(
            {"type": "start", "mode": "spell_zh2en", "item_id": cat_id,
             "session_id": "practice-sess-1"}
        ))
        await session.flush()
        session._on_utterance("see", 100)
        await session.flush()

    run(flow())
    utts = [m for m in send.sent if m["type"] == "utterance"]
    assert len(utts) == 1, send.sent
    assert utts[0]["text"] == "see"
    assert utts[0]["item_id"] == cat_id
    # 同序性：utterance 必须排在其驱动的 letter 之前（App 先显示后亮格）
    ordered = [m["type"] for m in send.sent if m["type"] in ("utterance", "letter")]
    assert ordered[0] == "utterance"
    assert ordered.index("utterance") < ordered.index("letter")


def test_ws_utterance_ignored_outside_spell_zh2en(client: TestClient):
    """utterance 只驱动 zh2en 判格；其他模式忽略（final 仍走 end 收口），
    不破坏 en2zh / 听写 / 背诵既有链路。"""
    session, send = _make_session()
    session._mode = "spell_en2zh"
    session._spell_target = ""

    session._on_utterance("你好", 0)
    run(session.flush())
    assert not [m for m in send.sent if m["type"] == "letter"]
    # v1.0.4：非 zh2en 模式也不下发 utterance 消息（页面无「听到」区）
    assert not [m for m in send.sent if m["type"] == "utterance"]


def test_ws_spell_start_unknown_item(client: TestClient):
    """item 不存在 → CONTENT_NOT_FOUND(30002) error 帧。"""
    from app.api import asr_ws as asr_ws_api
    asr_ws_api.reset_gate()
    session, send = _make_session()

    async def flow() -> None:
        await session.handle_text(json.dumps(
            {"type": "start", "mode": "spell_zh2en", "item_id": 99999999}
        ))
        await session.flush()

    run(flow())
    errs = [m for m in send.sent if m["type"] == "error"]
    assert errs and errs[-1]["code"] == 30002


def test_ws_spell_en2zh_start_transcribes_only(client: TestClient, unit_with_items: int):
    """spell_en2zh：start 被接受 → final 转写原样下发；不发 letter、不发 verdict。

    网关只做转写（契约 §5 en2zh 语音转写链路）；判定由 App 拿转写文本走
    /api/practice/answer（compare_service）。
    """
    _write_asr_yaml()
    reset_config_cache()
    from app.api import asr_ws as asr_ws_api
    asr_ws_api.reset_gate()

    r = client.get("/api/items", params={"keyword": "cat"})
    cat_id = r.json()["data"]["items"][0]["id"]

    # 开真场次：WS 网关对 session_id 只透传，answer 判定要求场次真实存在
    sid = client.post(
        "/api/practice/start",
        json={"unit_id": unit_with_items, "type": "word", "mode": "spell_en2zh"},
    ).json()["data"]["session_id"]

    session, send = _make_session()

    async def flow() -> None:
        await session.handle_text(json.dumps(
            {"type": "start", "mode": "spell_en2zh", "item_id": cat_id,
             "session_id": sid}
        ))
        await session.flush()
        await session.handle_text(json.dumps({"type": "end"}))
        await session.flush()

    run(flow())
    types = [m["type"] for m in send.sent]
    assert "error" not in types, send.sent
    finals = [m for m in send.sent if m["type"] == "final"]
    assert finals and finals[-1]["text"], send.sent
    assert not [m for m in send.sent if m["type"] == "letter"], "en2zh 不得走逐格判定"
    assert not [m for m in send.sent if m["type"] == "verdict"], "en2zh 网关不判分"
    # 判定仍走 HTTP answer（compare_service）：「猫」命中
    r = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": cat_id,
              "mode": "spell_en2zh", "answer": "猫", "elapsed_ms": 900},
    )
    assert r.json()["data"]["correct"] is True


def test_ws_spell_start_rejects_unknown_mode(client: TestClient):
    """mode 非法 → PARAM_INVALID(10001) error 帧（白名单外一律拒）。"""
    session, send = _make_session()

    async def flow() -> None:
        await session.handle_text(json.dumps(
            {"type": "start", "mode": "spell_bogus", "item_id": 1}
        ))
        await session.flush()

    run(flow())
    errs = [m for m in send.sent if m["type"] == "error"]
    assert errs and errs[-1]["code"] == 10001
