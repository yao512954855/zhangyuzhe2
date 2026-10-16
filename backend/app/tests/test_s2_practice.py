"""WP-A 验收 —— 练习链路（U6 start→settle / U10 compare_service / WS verdict）。

对应《10-后端工作包规格》WP-A 验收用例 U6 / U10，外加契约 §5 v1.3
WS verdict 整词判定行为测试（spell_zh2en，用 MockProvider 不起真服务，
验收用例 U13-1 ~ U13-6）。
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
from app.services.asr.base import BYTES_PER_MS  # noqa: E402
from app.services.asr.session_manager import ASRSession  # noqa: E402
from app.core.deps import CurrentUser  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402

# 幅度 2000 的直流块（≈6% 满量程）：模拟「真实说话电平」——语音活跃度门控
# （session_manager._spell_verdict）会把全零/极低电平音频当「没有听到声音」拦截。
CHUNK = b"\xd0\x07" * (150 * BYTES_PER_MS // 2)      # 150ms @16k/16bit/mono


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

    # settle → practice_logs 落一行，correct+wrong=total（v1.3：unit_ids/types JSON 数组）
    r = client.post("/api/practice/settle", json={"session_id": sid, "duration_ms": 12345})
    assert r.json()["code"] == 0 and r.json()["data"] == {}
    with read_conn() as conn:
        rows = conn.execute("SELECT * FROM practice_logs ORDER BY id DESC LIMIT 1").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert json.loads(row["unit_ids"]) == [unit_with_items]
    assert json.loads(row["types"]) == ["word"]
    assert row["mode"] == "spell_en2zh"
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
    """spell_zh2en 上报留痕（v1.3 §4.1：判定已在服务端 WS verdict 完成，
    App 收 verdict 后走本接口上报，`correct` 必填）。"""
    r = client.post(
        "/api/practice/start",
        json={"unit_id": unit_with_items, "type": "word", "mode": "spell_zh2en"},
    )
    sid = r.json()["data"]["session_id"]
    cat = next(i for i in r.json()["data"]["items"] if i["en"] == "cat")
    ok_resp = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": cat["id"], "mode": "spell_zh2en",
              "answer": "C A T", "elapsed_ms": 800, "correct": True},
    )
    assert ok_resp.json()["data"]["correct"] is True
    bad_resp = client.post(
        "/api/practice/answer",
        json={"session_id": sid, "item_id": cat["id"], "mode": "spell_zh2en",
              "answer": "cap", "elapsed_ms": 800, "correct": False},
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
# 契约 §5 v1.3：WS verdict 整词判定（spell_zh2en，U13-1 ~ U13-6）
# ===========================================================================

def _write_asr_yaml(spell_final: str = "see aye tee", rehear: str = "") -> None:
    body = {
        "provider": "mock",
        "lang_map": {"word": "en", "spell": "zh", "zh_answer": "zh", "recite": "zh"},
        "mock": {
            "final_text": "see",
            "by_task": {"spell": {"final_text": spell_final,
                                  "rehear_text": rehear}},
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


def test_ws_verdict_u13_1_correct_flow(client: TestClient, unit_with_items: int):
    """U13-1：start → 推音频（mock "see aye tee"）→ end → 恰好一条
    verdict{correct:true, text:"cat", session_id, item_id}，全程无任何 letter。

    v1.3 §5：松开（end）后服务端整词判定（谐音映射还原字母序列，与
    item.en 完全一致 = 对），letter 逐格判定已废除。
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
        await session.handle_bytes(CHUNK)
        await session.handle_bytes(CHUNK)
        await session.handle_text(json.dumps({"type": "end"}))
        await session.flush()

    run(flow())
    assert not [m for m in send.sent if m["type"] == "letter"], "letter 已废除"
    verdicts = [m for m in send.sent if m["type"] == "verdict"]
    assert len(verdicts) == 1, send.sent
    v = verdicts[0]
    assert set(v.keys()) == {"type", "correct", "text", "session_id", "item_id"}, \
        "verdict 载荷冻结于契约 §5"
    assert v["correct"] is True
    assert v["text"] == "cat"
    assert v["session_id"] == "practice-sess-1", "session_id 原样透传"
    assert v["item_id"] == cat_id
    # 回显顺序：utterance（按住中透传）→ final → verdict（终态）
    types = [m["type"] for m in send.sent]
    assert types.index("utterance") < types.index("final") < types.index("verdict")


def test_ws_verdict_u13_2_wrong_then_suppressed(client: TestClient, unit_with_items: int):
    """U13-2：转写错（"bee aye tee" → "bat" ≠ "cat"）→ verdict{correct:false}；
    之后再推音频 → 无 partial/utterance/verdict（F13-3 判错抑制生效）。"""
    _write_asr_yaml(spell_final="bee aye tee")
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
        await session.handle_bytes(CHUNK)
        await session.handle_text(json.dumps({"type": "end"}))
        await session.flush()

    run(flow())
    verdicts = [m for m in send.sent if m["type"] == "verdict"]
    assert len(verdicts) == 1, send.sent
    assert verdicts[0]["correct"] is False
    assert verdicts[0]["text"] == "bat"
    assert session._spell_muted is True

    # 判错后再推音频：全丢（无 partial/utterance/verdict），直到下一次 start
    mark = len(send.sent)

    async def more_audio() -> None:
        for _ in range(3):
            await session.handle_bytes(CHUNK)
        await session.flush()

    run(more_audio())
    tail = send.sent[mark:]
    assert not [m for m in tail if m["type"] in ("partial", "utterance", "verdict")], tail


def test_ws_utterance_relay_u13_3_no_verdict_before_end(
    client: TestClient, unit_with_items: int
):
    """U13-3：按住中 utterance 照常透传（text 非空）但不产生 letter/verdict。

    v1.3 §5（决策 #11）：utterance 只透传回显、识别字母实时上屏；
    判定收敛到松开（end）后的整词 verdict —— 改写自旧「utterance 即判格」
    用例（契约修订 v1.0.3 语义已废）。
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
        # 按住中：每个语句边界一次 utterance（**全程没有 end**）
        session._on_utterance("see", 100)
        session._on_utterance("aye", 200)
        session._on_utterance("tee", 300)
        await session.flush()

    run(flow())
    utts = [m for m in send.sent if m["type"] == "utterance"]
    assert [m["text"] for m in utts] == ["see", "aye", "tee"], send.sent
    assert all(m["text"] for m in utts)
    assert utts[0]["item_id"] == cat_id
    assert not [m for m in send.sent if m["type"] == "letter"], "letter 已废除"
    assert not [m for m in send.sent if m["type"] == "verdict"], "未松开不得判定"


def test_ws_verdict_u13_4_close_without_end(client: TestClient, unit_with_items: int):
    """U13-4：不发 end 直接 close（按住中断线）→ 无 verdict。"""
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
        await session.handle_bytes(CHUNK)
        await session.close()          # 未松开就断开

    run(flow())
    assert not [m for m in send.sent if m["type"] == "verdict"], send.sent
    assert not [m for m in send.sent if m["type"] == "letter"]
    assert not [m for m in send.sent if m["type"] == "final"], "close 不产 final"


def _rehear_flow(client: TestClient, unit_with_items: int, spell_final: str,
                 rehear: str):
    """重听式判分公共流程：推 2 块响度音频 → end → 返回 (send, cat_id)。"""
    _write_asr_yaml(spell_final=spell_final, rehear=rehear)
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
        await session.handle_bytes(CHUNK)
        await session.handle_bytes(CHUNK)
        await session.handle_text(json.dumps({"type": "end"}))
        await session.flush()

    run(flow())
    return send, cat_id


def test_ws_verdict_u13_7_rehear_beats_stream(client: TestClient,
                                              unit_with_items: int):
    """U13-7a 重听式判分（2026-10-16）：流式猜错 'a p x'、重听对 'c a t'
    → 判对，verdict.text 按重听文本（= target）。"""
    send, cat_id = _rehear_flow(client, unit_with_items,
                                spell_final="a p x", rehear="c a t")
    verdicts = [m for m in send.sent if m["type"] == "verdict"]
    assert len(verdicts) == 1, send.sent
    assert verdicts[0]["correct"] is True
    assert verdicts[0]["text"] == "cat"
    assert verdicts[0]["item_id"] == cat_id


def test_ws_verdict_u13_8_stream_backstop(client: TestClient,
                                          unit_with_items: int):
    """U13-7b 两边择优的另一半：重听错 'a p x'、流式对 'c a t'
    → 仍判对，verdict.text 回退流式文本。"""
    send, _ = _rehear_flow(client, unit_with_items,
                           spell_final="c a t", rehear="a p x")
    verdicts = [m for m in send.sent if m["type"] == "verdict"]
    assert len(verdicts) == 1, send.sent
    assert verdicts[0]["correct"] is True
    assert verdicts[0]["text"] == "cat"


def test_ws_verdict_u13_9_both_wrong_prefers_rehear(client: TestClient,
                                                    unit_with_items: int):
    """U13-7c 两遍皆错才判错：verdict.text 按重听文本上屏（整体更可靠）。"""
    send, _ = _rehear_flow(client, unit_with_items,
                           spell_final="a p x", rehear="a p z")
    verdicts = [m for m in send.sent if m["type"] == "verdict"]
    assert len(verdicts) == 1, send.sent
    assert verdicts[0]["correct"] is False
    assert verdicts[0]["text"] == "apz"


def test_ws_verdict_u13_5_terminal_no_rejudge(client: TestClient, unit_with_items: int):
    """U13-5：verdict 是本轮终态；final 照常下发但**只判一次** ——
    verdict 后再推音频 / 再发 end 都不得触发第二次判定或第二条 verdict。"""
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
        await session.handle_bytes(CHUNK)
        await session.handle_text(json.dumps({"type": "end"}))
        await session.flush()
        # 异常客户端时序：verdict 后仍推帧 + 再发 end
        await session.handle_bytes(CHUNK)
        await session.handle_text(json.dumps({"type": "end"}))
        await session.flush()

    run(flow())
    finals = [m for m in send.sent if m["type"] == "final"]
    verdicts = [m for m in send.sent if m["type"] == "verdict"]
    assert len(finals) == 1, send.sent
    assert len(verdicts) == 1, "verdict 只允许一条（不得二次判定）"


def test_ws_verdict_u13_6_second_start_lifts_suppression(
    client: TestClient, unit_with_items: int
):
    """U13-6：判错抑制后，二次 start（「再来一次」/ 下一题）→ 抑制解除，
    链路正常（新一轮照常出 utterance/final/verdict）。"""
    _write_asr_yaml(spell_final="bee aye tee")     # 对 cat 判错
    reset_config_cache()
    from app.api import asr_ws as asr_ws_api
    asr_ws_api.reset_gate()

    r = client.get("/api/items", params={"keyword": "cat"})
    cat_id = r.json()["data"]["items"][0]["id"]

    session, send = _make_session()
    start_msg = json.dumps(
        {"type": "start", "mode": "spell_zh2en", "item_id": cat_id,
         "session_id": "practice-sess-1"}
    )
    end_msg = json.dumps({"type": "end"})

    async def one_round() -> None:
        await session.handle_text(start_msg)
        await session.flush()
        await session.handle_bytes(CHUNK)
        await session.handle_text(end_msg)
        await session.flush()

    async def flow() -> None:
        await one_round()                          # 第一轮：判错 → 抑制
        assert session._spell_muted is True
        await one_round()                          # 第二轮：抑制解除

    run(flow())
    verdicts = [m for m in send.sent if m["type"] == "verdict"]
    assert len(verdicts) == 2, send.sent
    assert all(v["correct"] is False for v in verdicts)
    assert session._spell_muted is True            # 第二轮仍判错 → 抑制再次生效
    # 抑制解除的直接证据：第二轮照常透传 utterance（抑制期会被丢弃）
    utts = [m for m in send.sent if m["type"] == "utterance"]
    assert len(utts) == 2, send.sent


def test_ws_utterance_ignored_outside_spell_zh2en(client: TestClient):
    """utterance 只在 zh2en 模式透传；其他模式忽略（final 仍走 end 收口），
    不破坏 en2zh / 听写 / 背诵既有链路。"""
    session, send = _make_session()
    session._mode = "spell_en2zh"
    session._spell_target = ""

    session._on_utterance("你好", 0)
    run(session.flush())
    assert not [m for m in send.sent if m["type"] == "letter"]
    assert not [m for m in send.sent if m["type"] == "utterance"]
    assert not [m for m in send.sent if m["type"] == "verdict"]


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
