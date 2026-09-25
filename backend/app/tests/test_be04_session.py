"""WP-BE-04 练习流程与掌握度 —— 验收测试（10a U16 ~ U28 + 补充）。

覆盖：组卷（F8/F9）、答题判定与落库（F10/F11）、评分（F12）、结算（F13/F14）、
限时行为（F15）、我的单元/进度（F16/F17）、错题本（F18）、申诉（F19）、
幂等（F20）。

---
### 本文件的两个坑（与 BE-03 相同，必须自己处理）

**① `os.environ` 是进程级的。** BE-01/02/03/04 四个测试模块会被同一次 pytest 收集，
每个 fixture 都要重新声明环境变量并断言库路径确实是自己的。

**② `SettingsService._cache` 是类变量。** 换库不清缓存 → `time_limit_behavior`、
`mastery_streak` 会串到上一个测试，且症状是「断言偶尔飘」，很难查。

### 为什么 U25 用 `require_spell=0` 的词条

F14 规定「一个词条算一项，**两步都对**才算该项正确」。若种子词条要求拼读，
那么「18 题答对」就得为每题提交 step1+step2 两条答案，徒增噪音。
正确率分母的口径与是否要拼读无关，所以这里用只走一步的词条把用例聚焦在分母上；
「两步都对才算对」另有专门用例（`test_two_steps_both_required`）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_be04_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "be04-test-secret-0123456789abcdef"
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "be04-Admin-Pass"
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx  # noqa: E402
from app.main import app  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402


# ---------------------------------------------------------------------------
# fixture 与工具
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """每个测试一个**全新的空库** + 干净的限流桶 + 干净的 settings 缓存。"""
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = "be04-test-secret-0123456789abcdef"
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = "admin"
    os.environ["ADMIN_INIT_PASSWORD"] = "be04-Admin-Pass"
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE, "DB_PATH 被同进程里别的测试模块改掉了"

    for suffix in ("", "-wal", "-shm"):
        path = Path(str(_DB_FILE) + suffix)
        if path.exists():
            path.unlink()

    SettingsService._cache = None
    rate_limit.clear_all()

    with TestClient(app) as c:
        yield c


def _sql(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    with read_conn() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def _scalar(query: str, params: tuple = ()) -> Any:
    rows = _sql(query, params)
    return list(rows[0].values())[0] if rows else None


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _new_student(client: TestClient, username: str = "20260001") -> tuple[str, int]:
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": "Student-Pass-1", "real_name": f"学生{username}"},
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["access_token"], body["data"]["user"]["id"]


def _seed_unit(
    *,
    subject: str = "english",
    grade: int = 3,
    semester: str = "first",
    name: str = "Unit 1",
    status: str = "active",
) -> int:
    with write_tx() as conn:
        cursor = conn.execute(
            """
            INSERT INTO units (subject, grade, semester, name, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (subject, grade, semester, name, status),
        )
        return int(cursor.lastrowid)


def _seed_word(
    unit_id: int,
    en: str,
    cn: str,
    *,
    accepted: list[str] | None = None,
    require_spell: int = 0,
    order_no: int = 0,
    status: str = "active",
    type_: str = "word",
) -> int:
    with write_tx() as conn:
        cursor = conn.execute(
            """
            INSERT INTO words (unit_id, type, en, cn, accepted, require_spell, order_no, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                unit_id,
                type_,
                en,
                cn,
                json.dumps(accepted, ensure_ascii=False) if accepted else None,
                require_spell,
                order_no,
                status,
            ),
        )
        return int(cursor.lastrowid)


def _seed_words(unit_id: int, count: int = 20, **kwargs: Any) -> list[int]:
    """批量造词条：`word01/单词01` 这种即可，判定只关心 `en`/`cn`。"""
    return [
        _seed_word(
            unit_id,
            f"word{i:02d}",
            f"单词{i:02d}",
            order_no=i,
            **kwargs,
        )
        for i in range(1, count + 1)
    ]


def _start(client: TestClient, token: str, unit_id: int, **strategy: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"unit_id": unit_id, "content_type": "english"}
    if strategy:
        payload["strategy"] = strategy
    resp = client.post("/api/sessions", json=payload, headers=_auth(token))
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


def _answer(
    client: TestClient,
    token: str,
    session_id: int,
    *,
    word_id: int,
    mode: str = "cn2en",
    step: int = 1,
    asr_text: str | None = None,
    answer_text: str | None = None,
    **extra: Any,
):
    payload = {
        "word_id": word_id,
        "mode": mode,
        "step": step,
        "asr_text": asr_text,
        "answer_text": answer_text,
        **extra,
    }
    return client.post(
        f"/api/sessions/{session_id}/answers", json=payload, headers=_auth(token)
    )


def _answer_ok(client: TestClient, token: str, session_id: int, **kwargs: Any) -> dict[str, Any]:
    resp = _answer(client, token, session_id, **kwargs)
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


def _mastery_row(student_id: int, word_id: int) -> dict[str, Any] | None:
    rows = _sql(
        "SELECT * FROM word_mastery WHERE student_id = ? AND word_id = ?",
        (student_id, word_id),
    )
    return rows[0] if rows else None


# ===========================================================================
# F8 / F9 组卷
# ===========================================================================


class TestCreateSession:
    def test_u16_random_n(self, client):
        """U16：`random_n` + `random` 顺序；全部 `status='active'`。"""
        unit = _seed_unit()
        _seed_words(unit, 30)

        token, _ = _new_student(client)
        data = _start(client, token, unit, count=20, count_mode="random_n", order="random")

        assert len(data["items"]) == 20
        seqs = [item["seq"] for item in data["items"]]
        assert seqs == list(range(1, 21)), "seq 必须是 1..N 连续"

        word_ids = [item["word_id"] for item in data["items"]]
        assert len(set(word_ids)) == 20, "不能重复出同一词条"
        assert _scalar(
            "SELECT COUNT(*) FROM words WHERE id IN (%s) AND status = 'active'"
            % ",".join("?" * len(word_ids)),
            tuple(word_ids),
        ) == 20

    def test_u17_no_answer_in_response(self, client):
        """U17：**任何 item 都不含答案侧字段**（禁止清单 4）。"""
        unit = _seed_unit()
        _seed_words(unit, 10)
        token, _ = _new_student(client)

        allowed = {"seq", "word_id", "type", "mode", "prompt", "example_cn",
                   "require_spell", "target_len"}

        # cn2en：prompt 是中文，不能出现 en
        data = _start(client, token, unit, mode="cn2en")
        for item in data["items"]:
            assert set(item) <= allowed, item
            assert item["prompt"].startswith("单词")
            assert "en" not in item

        # en2cn：prompt 是英文，不能出现 cn；且不要求拼读（需求 §5.5）
        data = _start(client, token, unit, mode="en2cn")
        for item in data["items"]:
            assert set(item) <= allowed, item
            assert item["prompt"].startswith("word")
            assert "cn" not in item
            assert item["require_spell"] is False

    def test_target_len_is_english_length(self, client):
        """`target_len` = 英文去空格后长度，用于画占位框（不泄露内容）。"""
        unit = _seed_unit()
        _seed_word(unit, "classroom", "教室")
        token, _ = _new_student(client)
        data = _start(client, token, unit)
        assert data["items"][0]["target_len"] == len("classroom")

    def test_inactive_words_excluded(self, client):
        """停用词条不参与出题（F8 明文）。"""
        unit = _seed_unit()
        active = _seed_word(unit, "apple", "苹果", status="active")
        _seed_word(unit, "banana", "香蕉", status="inactive")
        token, _ = _new_student(client)

        data = _start(client, token, unit)
        assert [item["word_id"] for item in data["items"]] == [active]

    def test_order_sequential_and_reverse(self, client):
        unit = _seed_unit()
        ids = _seed_words(unit, 5)
        token, _ = _new_student(client)

        forward = _start(client, token, unit, order="sequential")
        assert [i["word_id"] for i in forward["items"]] == ids

        backward = _start(client, token, unit, order="reverse")
        assert [i["word_id"] for i in backward["items"]] == list(reversed(ids))

    def test_count_mode_first_n(self, client):
        unit = _seed_unit()
        ids = _seed_words(unit, 10)
        token, _ = _new_student(client)
        data = _start(client, token, unit, count=3, count_mode="first_n")
        assert [i["word_id"] for i in data["items"]] == ids[:3]

    def test_scope_range_by_position(self, client):
        """自定义区间按「第 N 个」的序号切片（需求 §5.3）。"""
        unit = _seed_unit()
        ids = _seed_words(unit, 10)
        token, _ = _new_student(client)
        data = _start(
            client, token, unit, scope="range", scope_detail={"start": 5, "end": 8}
        )
        assert [i["word_id"] for i in data["items"]] == ids[4:8]

    def test_count_mode_alias_random(self, client):
        """接口示例里的 `random` 旧写法要能接住（字段表写 `random_n`）。"""
        unit = _seed_unit()
        _seed_words(unit, 10)
        token, _ = _new_student(client)
        data = _start(client, token, unit, count=4, count_mode="random")
        assert len(data["items"]) == 4

    def test_strategy_snapshot_stored(self, client):
        """`sessions.strategy` 必须原样存快照。"""
        unit = _seed_unit()
        _seed_words(unit, 5)
        token, _ = _new_student(client)
        data = _start(client, token, unit, count=3, count_mode="first_n", order="reverse")

        raw = _scalar("SELECT strategy FROM sessions WHERE id = ?", (data["session_id"],))
        snapshot = json.loads(raw)
        assert snapshot["count_mode"] == "first_n"
        assert snapshot["order"] == "reverse"
        assert snapshot["scope"] == "unit"

    def test_empty_scope_rejected(self, client):
        """没有候选词条 → 参数错误，不是空场次。"""
        unit = _seed_unit()
        token, _ = _new_student(client)
        resp = client.post(
            "/api/sessions",
            json={"unit_id": unit, "content_type": "english"},
            headers=_auth(token),
        )
        assert resp.json()["code"] == 10001

    def test_inactive_unit_still_blocks_nothing(self, client):
        """停用单元仍可取词（一期不拦，但词条必须是 active）—— 记录当前行为。"""
        unit = _seed_unit(status="inactive")
        _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        data = _start(client, token, unit)
        assert len(data["items"]) == 1


# ===========================================================================
# F10 / F11 答题
# ===========================================================================


class TestSubmitAnswer:
    def test_u18_two_steps_two_records(self, client):
        """U18：说词 + 拼读各一条，`step` 分别 1/2。"""
        unit = _seed_unit()
        word = _seed_word(unit, "classroom", "教室", require_spell=1)
        token, student = _new_student(client)
        session = _start(client, token, unit)

        _answer_ok(client, token, session["session_id"], word_id=word, step=1, asr_text="classroom")
        _answer_ok(client, token, session["session_id"], word_id=word, step=2, asr_text="c l a s s r o o m")

        rows = _sql(
            "SELECT step, match_type, letter_seq, target_seq FROM answers "
            "WHERE session_id = ? ORDER BY step",
            (session["session_id"],),
        )
        assert [r["step"] for r in rows] == [1, 2]
        assert rows[1]["letter_seq"] == "classroom"
        assert rows[1]["target_seq"] == "classroom"

    def test_expected_returned_after_judging(self, client):
        """`expected` 在判定后才出现，且是词条的标准答案。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果", accepted=["Apple 苹果"])
        token, _ = _new_student(client)
        session = _start(client, token, unit)

        data = _answer_ok(
            client, token, session["session_id"], word_id=word, step=1, asr_text="apple"
        )
        assert data["expected"] == "apple"
        assert data["is_correct"] is True
        assert data["match_type"] == "exact"

    def test_spell_returns_diff(self, client):
        """拼读响应必须带逐字母 diff（需求 §5.4 硬需求）。"""
        unit = _seed_unit()
        word = _seed_word(unit, "class", "班级", require_spell=1)
        token, _ = _new_student(client)
        session = _start(client, token, unit)

        data = _answer_ok(
            client, token, session["session_id"], word_id=word, step=2, asr_text="c l e s s"
        )
        assert data["match_type"] == "wrong"
        assert data["diff"][2] == {"pos": 2, "expected": "a", "actual": "e", "state": "wrong"}

    def test_loose_can_appeal(self, client):
        unit = _seed_unit()
        word = _seed_word(unit, "classroom", "教室")
        token, _ = _new_student(client)
        session = _start(client, token, unit)

        data = _answer_ok(
            client, token, session["session_id"], word_id=word, step=1, asr_text="a classroom"
        )
        assert data["match_type"] == "loose"
        assert data["can_appeal"] is True

    def test_used_keyboard_penalty(self, client):
        """键盘扣 15 分（需求 §6.4）。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit)

        spoken = _answer_ok(
            client, token, session["session_id"], word_id=word, step=1, asr_text="apple"
        )
        typed = _answer_ok(
            client, token, session["session_id"], word_id=word, step=1, answer_text="apple",
            used_keyboard=True,
        )
        assert spoken["score"] == 100
        assert typed["score"] == 85

    def test_retry_and_hint_penalty(self, client):
        """重试 −10/次；提示一/二/三档 −20/−40/−60；下限 0（需求 §6.4）。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        assert _answer_ok(client, token, sid, word_id=word, asr_text="apple",
                          retry_count=3)["score"] == 70
        assert _answer_ok(client, token, sid, word_id=word, asr_text="apple",
                          hint_level=2)["score"] == 60
        assert _answer_ok(client, token, session["session_id"], word_id=word,
                          asr_text="apple", retry_count=1, used_keyboard=True,
                          hint_level=1)["score"] == 55
        # 下限 0：重试 20 次 + 键盘 + 三档提示
        assert _answer_ok(client, token, sid, word_id=word, asr_text="apple",
                          retry_count=20, used_keyboard=True, hint_level=3)["score"] == 0

    def test_wrong_answer_scores_zero(self, client):
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        data = _answer_ok(client, token, session["session_id"], word_id=word,
                          asr_text="banana")
        assert data["is_correct"] is False
        assert data["score"] == 0

    def test_empty_not_counted_as_error(self, client):
        """F3：识别为空不计错，也**不更新掌握度**（契约 §7.2）。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, student = _new_student(client)
        session = _start(client, token, unit)

        data = _answer_ok(client, token, session["session_id"], word_id=word, step=1, asr_text="")
        assert data["match_type"] == "empty"
        assert data["is_correct"] is False
        assert data["mastery"]["status"] == "not_started"
        assert _mastery_row(student, word) is None, "empty 不该写 word_mastery"

    def test_other_students_session_is_404(self, client):
        """越权：别人的场次当作不存在。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token_a, _ = _new_student(client, "20260001")
        token_b, _ = _new_student(client, "20260002")
        session = _start(client, token_a, unit)

        resp = _answer(client, token_b, session["session_id"], word_id=word, asr_text="apple")
        assert resp.json()["code"] == 10004

    def test_finished_session_rejects_answer(self, client):
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        client.post(
            f"/api/sessions/{session['session_id']}/finish",
            json={"reason": "completed"},
            headers=_auth(token),
        )
        resp = _answer(client, token, session["session_id"], word_id=word, asr_text="apple")
        assert resp.json()["code"] == 10001

    def test_missing_word_is_404(self, client):
        unit = _seed_unit()
        _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        resp = _answer(client, token, session["session_id"], word_id=999999, asr_text="apple")
        assert resp.json()["code"] == 30002

    def test_u24_idempotency_key(self, client):
        """U24：同一 `Idempotency-Key` 二次提交 → 回放首次结果，不新增记录。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        headers = {**_auth(token), "Idempotency-Key": "be04-idem-1"}

        payload = {"word_id": word, "mode": "cn2en", "step": 1, "asr_text": "apple"}
        first = client.post(
            f"/api/sessions/{session['session_id']}/answers", json=payload, headers=headers
        ).json()
        second = client.post(
            f"/api/sessions/{session['session_id']}/answers", json=payload, headers=headers
        ).json()

        assert first["data"]["answer_id"] == second["data"]["answer_id"]
        assert _scalar(
            "SELECT COUNT(*) FROM answers WHERE session_id = ?", (session["session_id"],)
        ) == 1

    def test_u26_timeout_behavior_a(self, client):
        """U26：行为 A 下超时 → `match_type='timeout'` 且计错。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit, time_limit_ms=1000)

        data = _answer_ok(
            client, token, session["session_id"], word_id=word, asr_text="apple",
            duration_ms=1500,
        )
        assert data["match_type"] == "timeout"
        assert data["is_correct"] is False
        assert data["score"] == 0
        assert data["timed_out"] is True

    def test_timeout_behavior_b_skips(self, client):
        """行为 B：跳过，不计对错（连答题记录都不落）。"""
        SettingsService.set("time_limit_behavior", "B")
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit, time_limit_ms=1000)

        data = _answer_ok(
            client, token, session["session_id"], word_id=word, asr_text="apple",
            duration_ms=1500,
        )
        assert data["skipped"] is True
        assert data["answer_id"] is None
        assert _scalar(
            "SELECT COUNT(*) FROM answers WHERE session_id = ?", (session["session_id"],)
        ) == 0

    def test_timeout_behavior_c_keeps_verdict(self, client):
        """行为 C：继续答题，判定按实际结果，只额外标记超时。"""
        SettingsService.set("time_limit_behavior", "C")
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit, time_limit_ms=1000)

        data = _answer_ok(
            client, token, session["session_id"], word_id=word, asr_text="apple",
            duration_ms=1500,
        )
        assert data["match_type"] == "exact"
        assert data["is_correct"] is True
        assert data["timed_out"] is True


# ===========================================================================
# F10 / F16 掌握度联动
# ===========================================================================


class TestMastery:
    def test_u19_first_correct(self, client):
        """U19：答对一次 → `streak=1`、`correct_count=1`、`last_result=1`。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, student = _new_student(client)
        session = _start(client, token, unit)

        data = _answer_ok(client, token, session["session_id"], word_id=word, asr_text="apple")
        assert data["mastery"] == {"status": "practiced_ok", "streak": 1, "mastered": False}

        row = _mastery_row(student, word)
        assert row["streak"] == 1
        assert row["correct_count"] == 1
        assert row["wrong_count"] == 0
        assert row["last_result"] == 1
        assert row["mastered_at"] is None

    def test_u20_mastered_after_three(self, client):
        """U20：连续三次答对 → `mastered_at` 非空、`status='mastered'`。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, student = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        for _ in range(3):
            data = _answer_ok(client, token, sid, word_id=word, asr_text="apple")

        assert data["mastery"]["streak"] == 3
        assert data["mastery"]["mastered"] is True
        assert data["mastery"]["status"] == "mastered"
        assert _mastery_row(student, word)["mastered_at"] is not None

    def test_u21_wrong_resets_mastery(self, client):
        """U21：答错已掌握的词 → `mastered_at` 置 NULL、`streak=0`、`wrong_count+1`。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, student = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        for _ in range(3):
            _answer_ok(client, token, sid, word_id=word, asr_text="apple")
        assert _mastery_row(student, word)["mastered_at"] is not None

        data = _answer_ok(client, token, sid, word_id=word, asr_text="banana")

        assert data["mastery"]["status"] == "practiced_with_error"
        assert data["mastery"]["streak"] == 0
        assert data["mastery"]["mastered"] is False

        row = _mastery_row(student, word)
        assert row["mastered_at"] is None
        assert row["streak"] == 0
        assert row["wrong_count"] == 1

    def test_mastery_streak_from_settings(self, client):
        """`mastery_streak` 走 settings（默认 3），改配置立刻生效。"""
        SettingsService.set("mastery_streak", 2)
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        _answer_ok(client, token, sid, word_id=word, asr_text="apple")
        data = _answer_ok(client, token, sid, word_id=word, asr_text="apple")
        assert data["mastery"]["mastered"] is True

    def test_hint_total_accumulates(self, client):
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, student = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        _answer_ok(client, token, sid, word_id=word, asr_text="apple", hint_level=1)
        _answer_ok(client, token, sid, word_id=word, asr_text="apple", hint_level=2)
        assert _mastery_row(student, word)["hint_total"] == 3

    def test_avg_duration_incremental(self, client):
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, student = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        _answer_ok(client, token, sid, word_id=word, asr_text="apple", duration_ms=1000)
        _answer_ok(client, token, sid, word_id=word, asr_text="apple", duration_ms=3000)
        assert _mastery_row(student, word)["avg_duration_ms"] == 2000

    def test_service_batch_is_consistent_with_sql(self, client):
        """`MasteryService.get_status` 批量结果必须与物化表一致（BE-03 依赖它）。"""
        from app.services.mastery_service import MasteryService

        unit = _seed_unit()
        ids = _seed_words(unit, 3)
        token, student = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        _answer_ok(client, token, sid, word_id=ids[0], asr_text="word01")   # 对
        _answer_ok(client, token, sid, word_id=ids[1], asr_text="nope")     # 错

        status = MasteryService.get_status(student, ids + [999999])
        assert status[ids[0]] == "practiced_ok"
        assert status[ids[1]] == "practiced_with_error"
        assert status[ids[2]] == "not_started"
        assert status[999999] == "not_started"

    def test_unit_status_enum(self, client):
        """单元级派生用同一枚举，且「无练习」优先于「全部掌握」。"""
        from app.services.mastery_service import MasteryService

        unit = _seed_unit()
        _seed_words(unit, 2, require_spell=0)
        token, student = _new_student(client)

        counts = MasteryService.unit_counts(student, unit)
        assert counts["status"] == "not_started"

        assert MasteryService.derive_unit_status(
            word_total=2, practiced=2, mastered=2, with_error=0
        ) == "mastered"
        assert MasteryService.derive_unit_status(
            word_total=0, practiced=0, mastered=0, with_error=0
        ) == "not_started"

    def test_u27_scope_unmastered(self, client):
        """U27：`scope='unmastered'` 排除 `mastered_at` 非空的词条。"""
        unit = _seed_unit()
        ids = _seed_words(unit, 3)
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]
        for _ in range(3):
            _answer_ok(client, token, sid, word_id=ids[0], asr_text="word01")

        data = _start(client, token, unit, scope="unmastered")
        got = {i["word_id"] for i in data["items"]}
        assert ids[0] not in got
        assert got == {ids[1], ids[2]}


# ===========================================================================
# F13 / F14 结算
# ===========================================================================


class TestFinish:
    def test_u25_accuracy_excludes_empty(self, client):
        """U25：20 题、2 题全 empty → 分母为 18；`status='finished'`。"""
        unit = _seed_unit()
        ids = _seed_words(unit, 20)
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        for word_id in ids[:18]:
            _answer_ok(client, token, sid, word_id=word_id, asr_text=str(
                _sql("SELECT en FROM words WHERE id = ?", (word_id,))[0]["en"]
            ))
        for word_id in ids[18:]:
            _answer_ok(client, token, sid, word_id=word_id, asr_text="")

        resp = client.post(f"/api/sessions/{sid}/finish", json={"reason": "completed"},
                           headers=_auth(token))
        data = resp.json()["data"]

        assert data["total_items"] == 20
        assert data["empty_items"] == 2
        assert data["counted_items"] == 18
        assert data["correct_items"] == 18
        assert data["accuracy"] == 1.0
        assert _scalar("SELECT status FROM sessions WHERE id = ?", (sid,)) == "finished"
        assert _scalar("SELECT ended_at FROM sessions WHERE id = ?", (sid,)) is not None

    def test_two_steps_both_required(self, client):
        """F14：`cn2en` 且要求拼读时，**两步都对**才算该项正确。"""
        unit = _seed_unit()
        word = _seed_word(unit, "class", "班级", require_spell=1)
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        _answer_ok(client, token, sid, word_id=word, step=1, asr_text="class")
        _answer_ok(client, token, sid, word_id=word, step=2, asr_text="c l e s s")  # 错

        data = client.post(f"/api/sessions/{sid}/finish", json={"reason": "completed"},
                           headers=_auth(token)).json()["data"]

        assert data["total_items"] == 1
        assert data["correct_items"] == 0
        assert data["accuracy"] == 0.0
        assert len(data["wrong_items"]) == 1

    def test_two_steps_missing_second_is_wrong(self, client):
        """只提交第一步 → 该项不算正确（不能靠「少提交」拿分）。"""
        unit = _seed_unit()
        word = _seed_word(unit, "class", "班级", require_spell=1)
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        _answer_ok(client, token, sid, word_id=word, step=1, asr_text="class")
        data = client.post(f"/api/sessions/{sid}/finish", json={"reason": "completed"},
                           headers=_auth(token)).json()["data"]
        assert data["correct_items"] == 0

    def test_wrong_items_detail(self, client):
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        _answer_ok(client, token, sid, word_id=word, asr_text="banana")
        data = client.post(f"/api/sessions/{sid}/finish", json={"reason": "completed"},
                           headers=_auth(token)).json()["data"]

        assert data["wrong_items"] == [
            {"word_id": word, "en": "apple", "cn": "苹果",
             "your_answer": "banana", "match_type": "wrong"}
        ]

    def test_wrong_items_prefers_wrong_record(self, client):
        """错题清单的 `match_type` 必须取**判错的那条**，不能机械取最后一条。

        真实场景：学生先说错、再（用宽松命中）补答对，或说词对了但拼读没做。
        若取最后一条，前端会看到「match_type=loose（判对）」却出现在错题清单里，
        自相矛盾。此用例钉住这个行为。
        """
        unit = _seed_unit()
        word = _seed_word(unit, "class", "班级", require_spell=1)
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        _answer_ok(client, token, sid, word_id=word, step=1, asr_text="nope")      # 判错
        _answer_ok(client, token, sid, word_id=word, step=1, asr_text="a class")   # loose（补答对）

        data = client.post(f"/api/sessions/{sid}/finish", json={},
                           headers=_auth(token)).json()["data"]
        assert len(data["wrong_items"]) == 1
        assert data["wrong_items"][0]["match_type"] == "wrong"
        assert data["wrong_items"][0]["your_answer"] == "nope"

    def test_finish_twice_is_stable(self, client):
        """重复结算不报错，结果一致（客户端重试友好）。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]
        _answer_ok(client, token, sid, word_id=word, asr_text="apple")

        first = client.post(f"/api/sessions/{sid}/finish", json={}, headers=_auth(token)).json()
        second = client.post(f"/api/sessions/{sid}/finish", json={}, headers=_auth(token)).json()
        assert first["code"] == 0
        assert second["data"]["correct_items"] == first["data"]["correct_items"]

    def test_other_student_cannot_finish(self, client):
        unit = _seed_unit()
        _seed_word(unit, "apple", "苹果")
        token_a, _ = _new_student(client, "20260001")
        token_b, _ = _new_student(client, "20260002")
        session = _start(client, token_a, unit)

        resp = client.post(f"/api/sessions/{session['session_id']}/finish",
                           json={}, headers=_auth(token_b))
        assert resp.json()["code"] == 10004


# ===========================================================================
# F16 / F17 / F18 / F19
# ===========================================================================


class TestMyLearning:
    def test_u28_my_units_status_enum(self, client):
        """U28：英语单元 `status` 取契约枚举之一；无练习的单元为 `not_started`。"""
        english = _seed_unit(subject="english", name="Unit 1")
        _seed_words(english, 3, require_spell=0)
        untouched = _seed_unit(subject="english", name="Unit 2", grade=3)
        _seed_words(untouched, 2, require_spell=0)

        token, _ = _new_student(client)
        client.put("/api/me/profile", json={"preferred_grade": 3, "preferred_semester": "first"},
                   headers=_auth(token))

        session = _start(client, token, english)
        ids = [i["word_id"] for i in session["items"]]
        _answer_ok(client, token, session["session_id"], word_id=ids[0], asr_text="word01")

        resp = client.get("/api/me/units", headers=_auth(token))
        body = resp.json()
        assert body["code"] == 0, body
        items = {item["name"]: item for item in body["data"]["items"]}

        assert items["Unit 1"]["status"] in (
            "not_started", "practiced_with_error", "practiced_ok", "mastered"
        )
        assert items["Unit 1"]["word_total"] == 3
        assert items["Unit 1"]["practiced"] == 1
        assert items["Unit 2"]["status"] == "not_started"

    def test_my_units_empty_without_preference(self, client):
        """没有年级偏好时返回空列表而不是报错（新注册学生首页要能渲染）。"""
        _seed_unit()
        token, _ = _new_student(client)
        body = client.get("/api/me/units", headers=_auth(token)).json()
        assert body["code"] == 0
        assert body["data"]["items"] == []

    def test_my_progress(self, client):
        unit = _seed_unit()
        ids = _seed_words(unit, 3)
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]
        _answer_ok(client, token, sid, word_id=ids[0], asr_text="word01")
        _answer_ok(client, token, sid, word_id=ids[1], asr_text="nope")

        body = client.get("/api/me/progress", headers=_auth(token)).json()
        assert body["code"] == 0
        data = body["data"]
        assert data["total_words"] == 3
        assert data["practiced_words"] == 2
        assert data["mastered_words"] == 0
        assert data["accuracy"] == 0.5
        assert data["streak_days"] == 1
        assert data["today"]["items"] == 2
        assert data["today"]["correct"] == 1

    def test_u22_wrong_words_sorted(self, client):
        """U22：错题本只含答错词条，按错误次数降序。"""
        unit = _seed_unit()
        ids = _seed_words(unit, 3)
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]

        _answer_ok(client, token, sid, word_id=ids[0], asr_text="word01")   # 对，不进错题本
        _answer_ok(client, token, sid, word_id=ids[1], asr_text="nope")
        for _ in range(3):
            _answer_ok(client, token, sid, word_id=ids[2], asr_text="nope")

        body = client.get(f"/api/me/wrong-words?unit_id={unit}", headers=_auth(token)).json()
        assert body["code"] == 0
        items = body["data"]["items"]
        assert [i["word_id"] for i in items] == [ids[2], ids[1]]
        assert items[0]["wrong_count"] == 3
        assert items[0]["last_your_answer"] == "nope"
        assert items[0]["last_wrong_at"] is not None
        assert items[0]["mastery_status"] == "practiced_with_error"
        assert body["data"]["total"] == 2

    def test_wrong_words_is_paged(self, client):
        unit = _seed_unit()
        ids = _seed_words(unit, 5)
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        sid = session["session_id"]
        for word_id in ids:
            _answer_ok(client, token, sid, word_id=word_id, asr_text="nope")

        body = client.get(
            f"/api/me/wrong-words?unit_id={unit}&page=1&page_size=2", headers=_auth(token)
        ).json()
        assert body["data"]["total"] == 5
        assert len(body["data"]["items"]) == 2
        assert body["data"]["page"] == 1
        assert body["data"]["page_size"] == 2

    def test_wrong_words_scope_drives_session(self, client):
        """「只练错题」复用组卷接口，不新增接口（§8.8 明文）。"""
        unit = _seed_unit()
        ids = _seed_words(unit, 3)
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        _answer_ok(client, token, session["session_id"], word_id=ids[1], asr_text="nope")

        data = _start(client, token, unit, scope="wrong")
        assert [i["word_id"] for i in data["items"]] == [ids[1]]

    def test_u23_appeal_does_not_change_verdict(self, client):
        """U23：申诉只置 `appealed=1`，`is_correct`/`match_type` 一律不动。"""
        unit = _seed_unit()
        word = _seed_word(unit, "classroom", "教室")
        token, _ = _new_student(client)
        session = _start(client, token, unit)

        answered = _answer_ok(
            client, token, session["session_id"], word_id=word, asr_text="a classroom"
        )
        answer_id = answered["answer_id"]
        assert answered["can_appeal"] is True

        body = client.post(
            f"/api/answers/{answer_id}/appeal", json={"reason": "我读对了"}, headers=_auth(token)
        ).json()
        assert body["code"] == 0
        assert body["data"] == {"answer_id": answer_id, "appealed": True}

        row = _sql("SELECT appealed, is_correct, match_type FROM answers WHERE id = ?", (answer_id,))[0]
        assert row["appealed"] == 1
        assert row["is_correct"] == 1        # 未改变
        assert row["match_type"] == "loose"  # 未改变

    def test_appeal_is_idempotent(self, client):
        unit = _seed_unit()
        word = _seed_word(unit, "classroom", "教室")
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        answered = _answer_ok(client, token, session["session_id"], word_id=word,
                              asr_text="a classroom")

        first = client.post(f"/api/answers/{answered['answer_id']}/appeal", json={},
                            headers=_auth(token)).json()
        second = client.post(f"/api/answers/{answered['answer_id']}/appeal", json={},
                             headers=_auth(token)).json()
        assert first["code"] == 0 and second["code"] == 0
        assert _scalar(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'answer.appeal'"
        ) == 1, "重复申诉不该重复写审计"

    def test_appeal_rejected_for_exact(self, client):
        """只有 `loose`/`fuzzy` 可申诉，`exact` → 10003。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        session = _start(client, token, unit)
        answered = _answer_ok(client, token, session["session_id"], word_id=word,
                              asr_text="apple")
        resp = client.post(f"/api/answers/{answered['answer_id']}/appeal", json={},
                           headers=_auth(token))
        assert resp.json()["code"] == 10003

    def test_appeal_foreign_answer_is_404(self, client):
        """越权：别人的答题记录 → 10004（§8.9 明文要求校验归属）。"""
        unit = _seed_unit()
        word = _seed_word(unit, "classroom", "教室")
        token_a, _ = _new_student(client, "20260001")
        token_b, _ = _new_student(client, "20260002")
        session = _start(client, token_a, unit)
        answered = _answer_ok(client, token_a, session["session_id"], word_id=word,
                              asr_text="a classroom")

        resp = client.post(f"/api/answers/{answered['answer_id']}/appeal", json={},
                           headers=_auth(token_b))
        assert resp.json()["code"] == 10004
        assert _sql("SELECT appealed FROM answers WHERE id = ?",
                    (answered["answer_id"],))[0]["appealed"] == 0

    def test_appeal_unknown_answer_is_404(self, client):
        token, _ = _new_student(client)
        resp = client.post("/api/answers/999999/appeal", json={}, headers=_auth(token))
        assert resp.json()["code"] == 10004

    def test_learning_endpoints_require_auth(self, client):
        for method, path in (
            ("get", "/api/me/units"),
            ("get", "/api/me/progress"),
            ("get", "/api/me/wrong-words"),
        ):
            resp = getattr(client, method)(path)
            assert resp.json()["code"] == 10002, path


# ===========================================================================
# §9.5 我的记录：场次列表 / 单次详情（契约 §14.1 缺口 7，2026-09-25 补）
# ===========================================================================


def _finish(
    client: TestClient, token: str, session_id: int, reason: str = "completed"
) -> dict[str, Any]:
    body = client.post(
        f"/api/sessions/{session_id}/finish",
        json={"reason": reason},
        headers=_auth(token),
    ).json()
    assert body["code"] == 0, body
    return body["data"]


def _seed_finished_chinese_session(
    *, student_id: int, missed_pos: int
) -> tuple[int, str | None]:
    """直接造一场**已结算**的语文场次（一篇课文 + 一条 `recitations`）。

    本组只验证**读取侧**，所以不走「WS 背一遍再 settle」那条重链路；
    但字段与真实落库保持一致：`content_plain` 是**范围内**的汉字序列，
    `scope_snapshot` 给出 `start`/`end`，`missed_chars` 是**范围下标**数组。

    返回 `(session_id, 期望漏掉的那个字)`；下标越界时期望值为 `None`。
    """
    plain = "静夜思明月光"  # 6 个字，`content_plain[0:6]`
    with write_tx() as conn:
        unit_id = int(
            conn.execute(
                "INSERT INTO units (subject, grade, semester, name, status)"
                " VALUES ('chinese', 2, 'first', '第1单元-古诗', 'active')"
            ).lastrowid
        )
        text_id = int(
            conn.execute(
                """
                INSERT INTO texts (unit_id, title, author, content_raw, content_plain,
                                   content_pinyin, paragraphs, recite_scope, total_chars,
                                   order_no, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'active')
                """,
                (
                    unit_id,
                    "静夜思",
                    "李白",
                    plain,
                    plain,
                    "jing ye si ming yue guang",
                    json.dumps(
                        [{"idx": 0, "text": plain, "chars": 6, "recite": True}]
                    ),
                    "[0]",
                    6,
                ),
            ).lastrowid
        )
        session_id = int(
            conn.execute(
                """
                INSERT INTO sessions (student_id, unit_id, content_type, strategy,
                                      total_items, correct_items, hint_count, score,
                                      status, started_at, ended_at)
                VALUES (?, ?, 'chinese', '{}', 6, 5, 1, 86.0, 'finished',
                        '2026-09-25T10:02:00Z', '2026-09-25T10:09:40Z')
                """,
                (student_id, unit_id),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO recitations (session_id, student_id, text_id, scope_snapshot,
                                     transcript, total_chars, matched_chars, missed_chars,
                                     hint_count, restart_count, duration_ms, covered_ratio,
                                     score, grade, finish_reason, created_at)
            VALUES (?, ?, ?, ?, ?, 6, 5, ?, ?, 0, 45000, 0.8333, 86.0, '良好',
                    'completed', '2026-09-25T10:09:40Z')
            """,
            (
                session_id,
                student_id,
                text_id,
                json.dumps({"scope": [0], "start": 0, "end": 6}),
                "静夜思明月",
                json.dumps([missed_pos]),
                json.dumps({"1": 1, "2": 0, "3": 0}),
            ),
        )
    return session_id, (plain[missed_pos] if 0 <= missed_pos < len(plain) else None)


class TestSessionRecords:
    """§9.5 读取侧：列表 / 详情的口径与**两条安全线**。"""

    # ------------------------------------------------------------------ 列表

    def test_list_only_mine_and_desc(self, client):
        """只回自己的场次；按 `started_at DESC, id DESC`（同秒时用 id 兜底）。"""
        unit = _seed_unit()
        _seed_words(unit, 3)
        token_a, _ = _new_student(client, "20260001")
        token_b, _ = _new_student(client, "20260002")

        other = _start(client, token_b, unit)
        _finish(client, token_b, other["session_id"])

        first = _start(client, token_a, unit)
        _finish(client, token_a, first["session_id"])
        second = _start(client, token_a, unit)
        _finish(client, token_a, second["session_id"])

        body = client.get("/api/sessions", headers=_auth(token_a)).json()
        assert body["code"] == 0
        ids = [it["session_id"] for it in body["data"]["items"]]
        assert ids == [second["session_id"], first["session_id"]], ids
        assert other["session_id"] not in ids, "不能看到别人的场次"

    def test_list_pagination_and_total(self, client):
        unit = _seed_unit()
        _seed_words(unit, 1)
        token, _ = _new_student(client)
        for _ in range(3):
            _finish(client, token, _start(client, token, unit)["session_id"])

        page1 = client.get(
            "/api/sessions?page=1&page_size=2", headers=_auth(token)
        ).json()["data"]
        assert (page1["page"], page1["page_size"], page1["total"]) == (1, 2, 3)
        assert len(page1["items"]) == 2

        page2 = client.get(
            "/api/sessions?page=2&page_size=2", headers=_auth(token)
        ).json()["data"]
        assert len(page2["items"]) == 1

    def test_list_filters_and_invalid_values(self, client):
        """过滤生效；**非法取值必须报错**而不是静默当「不过滤」。"""
        unit = _seed_unit()
        _seed_words(unit, 1)
        token, _ = _new_student(client)
        done = _start(client, token, unit)
        _finish(client, token, done["session_id"])
        running = _start(client, token, unit)  # 留着，不结算

        ongoing = client.get(
            "/api/sessions?status=ongoing", headers=_auth(token)
        ).json()["data"]
        assert [i["session_id"] for i in ongoing["items"]] == [running["session_id"]]
        # `ongoing` 还没成绩：如实给 null / 0，不编一个数出来
        assert ongoing["items"][0]["score"] is None
        assert ongoing["items"][0]["duration_ms"] == 0

        finished = client.get(
            "/api/sessions?status=finished", headers=_auth(token)
        ).json()["data"]
        assert [i["session_id"] for i in finished["items"]] == [done["session_id"]]

        cn = client.get(
            "/api/sessions?content_type=chinese", headers=_auth(token)
        ).json()["data"]
        assert cn["total"] == 0

        bad_type = client.get(
            "/api/sessions?content_type=eng", headers=_auth(token)
        ).json()
        assert bad_type["code"] == 10001, bad_type
        bad_status = client.get(
            "/api/sessions?status=nope", headers=_auth(token)
        ).json()
        assert bad_status["code"] == 10001, bad_status

    def test_list_accuracy_matches_sessions_columns(self, client):
        """`accuracy` 必须等于 `sessions` 落库的两个计数之比（与结算同源）。"""
        unit = _seed_unit()
        ids = _seed_words(unit, 4)
        token, _ = _new_student(client)
        sid = _start(client, token, unit)["session_id"]

        for word_id in ids[:3]:  # 3 题答对
            en = _sql("SELECT en FROM words WHERE id = ?", (word_id,))[0]["en"]
            _answer_ok(client, token, sid, word_id=word_id, asr_text=en)
        _answer_ok(client, token, sid, word_id=ids[3], asr_text="完全不对")

        _finish(client, token, sid)

        item = client.get("/api/sessions", headers=_auth(token)).json()["data"]["items"][0]
        total = _scalar("SELECT total_items FROM sessions WHERE id = ?", (sid,))
        correct = _scalar("SELECT correct_items FROM sessions WHERE id = ?", (sid,))
        assert item["total_items"] == total
        assert item["correct_items"] == correct
        assert item["accuracy"] == round(correct / total, 4)
        assert 0 < item["accuracy"] < 1, "造数没生效（全对或全错都不该出现）"

    def test_list_duration_from_timestamps_not_client(self, client):
        """`duration_ms` 由服务端按 `ended_at − started_at` 算，不采信客户端。"""
        unit = _seed_unit()
        _seed_words(unit, 1)
        token, _ = _new_student(client)
        sid = _start(client, token, unit)["session_id"]
        _finish(client, token, sid)

        with write_tx() as conn:
            conn.execute(
                "UPDATE sessions SET started_at = ?, ended_at = ? WHERE id = ?",
                ("2026-09-25T09:00:00Z", "2026-09-25T09:02:40Z", sid),
            )

        item = client.get("/api/sessions", headers=_auth(token)).json()["data"]["items"][0]
        assert item["duration_ms"] == 160_000

    # ------------------------------------------------------------------ 详情

    def test_detail_english_items(self, client):
        """英语场次：一个词条一行，`state ∈ {correct, wrong, empty}`。"""
        unit = _seed_unit()
        ids = _seed_words(unit, 4)
        token, _ = _new_student(client)
        sid = _start(client, token, unit)["session_id"]

        for word_id in ids[:2]:
            en = _sql("SELECT en FROM words WHERE id = ?", (word_id,))[0]["en"]
            _answer_ok(client, token, sid, word_id=word_id, asr_text=en)
        _answer_ok(client, token, sid, word_id=ids[2], asr_text="完全不对")
        _answer_ok(client, token, sid, word_id=ids[3], asr_text="")  # 识别为空

        _finish(client, token, sid)

        data = client.get(f"/api/sessions/{sid}", headers=_auth(token)).json()["data"]
        assert data["content_type"] == "english"
        assert data["recitations"] is None
        assert len(data["items"]) == 4
        assert {i["state"] for i in data["items"]} == {"correct", "wrong", "empty"}

        wrong = [i for i in data["items"] if i["state"] == "wrong"][0]
        assert wrong["your_answer"] == "完全不对"
        assert wrong["expected"] == _sql(
            "SELECT en FROM words WHERE id = ?", (ids[2],)
        )[0]["en"]

        empty = [i for i in data["items"] if i["state"] == "empty"][0]
        assert empty["expected"] is None, "空项没有答案可复盘，不给编造"

    def test_detail_ongoing_hides_expected(self, client):
        """**安全线 2**：练习还没结束，绝不能把 `expected` 下发下去（等于泄题）。"""
        unit = _seed_unit()
        word = _seed_word(unit, "apple", "苹果")
        token, _ = _new_student(client)
        sid = _start(client, token, unit)["session_id"]
        _answer_ok(client, token, sid, word_id=word, asr_text="apple")

        resp = client.get(f"/api/sessions/{sid}", headers=_auth(token))
        body = resp.json()
        assert body["code"] == 10003, body
        assert "items" not in (body.get("data") or {})
        assert "apple" not in resp.text, "拒绝时也不能顺手把答案带出来"

    def test_detail_foreign_and_missing_same_code(self, client):
        """**安全线 1**：别人的场次与不存在的场次**同码同文案**，不给探测信号。"""
        unit = _seed_unit()
        _seed_words(unit, 1)
        token_a, _ = _new_student(client, "20260001")
        token_b, _ = _new_student(client, "20260002")
        sid = _start(client, token_a, unit)["session_id"]

        foreign = client.get(f"/api/sessions/{sid}", headers=_auth(token_b)).json()
        missing = client.get("/api/sessions/999999", headers=_auth(token_b)).json()
        assert foreign["code"] == 10004
        assert missing["code"] == 10004
        assert foreign["message"] == missing["message"]

    def test_detail_chinese_missed_detail(self, client):
        """语文场次：按篇给明细，漏字 `char` 由 `content_plain[start:end]` 重建。"""
        token, student = _new_student(client)
        sid, expected_char = _seed_finished_chinese_session(
            student_id=student, missed_pos=2
        )
        assert expected_char == "思"

        data = client.get(f"/api/sessions/{sid}", headers=_auth(token)).json()["data"]
        assert data["content_type"] == "chinese"
        assert data["items"] is None
        assert len(data["recitations"]) == 1
        recitation = data["recitations"][0]
        assert recitation["title"] == "静夜思"
        assert recitation["missed_detail"] == [{"pos": 2, "char": "思"}]

    def test_detail_chinese_out_of_range_pos_is_null(self, client):
        """脏数据兜底：漏字下标越界只让 `char` 为 `null`，不许 500。"""
        token, student = _new_student(client)
        sid, _ = _seed_finished_chinese_session(student_id=student, missed_pos=99)

        body = client.get(f"/api/sessions/{sid}", headers=_auth(token)).json()
        assert body["code"] == 0, body
        assert body["data"]["recitations"][0]["missed_detail"] == [
            {"pos": 99, "char": None}
        ]

    def test_records_endpoints_require_auth(self, client):
        for path in ("/api/sessions", "/api/sessions/1"):
            assert client.get(path).json()["code"] == 10002, path
