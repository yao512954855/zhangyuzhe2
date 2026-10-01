"""WP-BE-05 背诵引擎与结算 —— 验收测试（10b U7 ~ U16 + 边界）。

覆盖：提示机制（U7~U10）、结算评分（U11/U12/U15）、**服务端重算防刷分**（U13）、
事件落库（U14）、`total_chars` 口径一致性（U16），以及服务层的权限与幂等。

---
### 本文件的三个坑（与 BE-03/BE-04 相同，必须自己处理）

**① `os.environ` 是进程级的。** 多个测试模块会被同一次 pytest 收集，
fixture 必须重新声明环境变量并断言库路径确实是自己的。

**② `SettingsService._cache` 是类变量。** 换库不清缓存 → `asr_align_window`、
`silence_hint_ms` 会串到上一个测试，症状是「断言偶尔飘」。

**③ `ReciteService` 的引擎缓存也是类变量。** 不清会让上一个测试的背诵
在新库里「还活着」，越权断言随之失真。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_be05e_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "be05-engine-secret-0123456789ab"
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "be05-Admin-Pass"
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx  # noqa: E402
from app.main import app  # noqa: E402
from app.services import pinyin_service  # noqa: E402
from app.services.recite import ReciteEngine, ReciteService  # noqa: E402
from app.services.recite.aligner import to_pinyin_seq  # noqa: E402
from app.services.recite.scoring import compute_score, grade_of  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402


# ---------------------------------------------------------------------------
# fixture 与工具
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """每个测试一个**全新的空库** + 干净的限流桶 + 干净的 settings / 引擎缓存。"""
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = "be05-engine-secret-0123456789ab"
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = "admin"
    os.environ["ADMIN_INIT_PASSWORD"] = "be05-Admin-Pass"
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE, "DB_PATH 被同进程里别的测试模块改掉了"

    for suffix in ("", "-wal", "-shm"):
        path = Path(str(_DB_FILE) + suffix)
        if path.exists():
            path.unlink()

    SettingsService._cache = None
    rate_limit.clear_all()
    ReciteService.clear_cache()

    with TestClient(app) as c:
        yield c


def _sql(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    with read_conn() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, username: str = "20260001") -> tuple[str, int]:
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "password": "Student-Pass-1", "real_name": f"学生{username}"},
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["access_token"], body["data"]["user"]["id"]


def _insert_text(
    unit_id: int,
    paragraphs: list[str],
    *,
    title: str = "背影",
    author: str = "朱自清",
    scope: list[int] | None = None,
    status: str = "active",
    order_no: int = 0,
) -> int:
    """往已有单元里插一篇课文，返回 `text_id`。

    按 `import_service.texts_commit` 的同口径生成派生列
    （`content_plain` / `content_pinyin` / `paragraphs` / `total_chars`），
    保证与真实导入链路一致。
    """
    picked = list(range(len(paragraphs))) if scope is None else list(scope)
    build = pinyin_service.build_scoped(paragraphs, picked)
    assert build.ok, "测试数据的拼音生成失败"

    metas = [
        {
            "idx": index,
            "text": text,
            "chars": pinyin_service.count_han(text),
            "recite": index in set(picked),
        }
        for index, text in enumerate(paragraphs)
    ]

    with write_tx() as conn:
        cursor = conn.execute(
            """
            INSERT INTO texts (unit_id, title, author, content_raw,
                               content_plain, content_pinyin, paragraphs,
                               recite_scope, total_chars, order_no, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(unit_id),
                title,
                author,
                "\n".join(paragraphs),
                build.plain,
                build.pinyin,
                json.dumps(metas, ensure_ascii=False),
                json.dumps(picked, ensure_ascii=False),
                build.length,
                order_no,
                status,
            ),
        )
        return int(cursor.lastrowid)


def _seed_text_unit(
    *,
    paragraphs: list[str],
    scope: list[int] | None = None,
    title: str = "背影",
    author: str = "朱自清",
    status: str = "active",
) -> tuple[int, int]:
    """造一个语文单元 + 一篇课文，返回 `(unit_id, text_id)`。"""
    with write_tx() as conn:
        cursor = conn.execute(
            "INSERT INTO units (subject, grade, semester, name, status) VALUES ('chinese', 7, 'first', ?, 'active')",
            (f"第1单元-{title}",),
        )
        unit_id = int(cursor.lastrowid)
    text_id = _insert_text(
        unit_id, paragraphs, title=title, author=author, scope=scope, status=status
    )
    return unit_id, text_id


def _start_chinese_session(client: TestClient, token: str, unit_id: int) -> int:
    resp = client.post(
        "/api/sessions",
        json={"unit_id": unit_id, "content_type": "chinese"},
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    return int(body["data"]["session_id"])


def _create_recitation(
    client: TestClient, token: str, session_id: int, text_id: int, scope: list[int] | None = None
) -> dict[str, Any]:
    resp = client.post(
        "/api/recitations",
        json={"session_id": session_id, "text_id": text_id, "scope": scope or []},
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


def _engine_of(text: str, **kwargs: Any) -> ReciteEngine:
    return ReciteEngine(
        content_plain=text,
        content_pinyin=" ".join(to_pinyin_seq(text)),
        scope=[0],
        **kwargs,
    )


# ===========================================================================
# U7 ~ U10：提示机制（引擎层，纯计算）
# ===========================================================================


class TestHintMechanism:
    def test_u7_stuck_by_consecutive_miss(self):
        """U7｜连续漏 5 字 → 判定卡壳，返回 1 档。"""
        engine = _engine_of("我最不能忘记的是他的背影")
        engine.feed("我最不能忘记的呀呀呀呀呀")

        assert engine.miss_streak == 5
        assert engine.check_auto_hint(0) == 1

    def test_u7_no_hint_when_not_stuck(self):
        """反向：只漏 4 个字不该触发（阈值是 5）。"""
        engine = _engine_of("我最不能忘记的是他的背影")
        engine.feed("我最不能忘记的呀呀呀呀")

        assert engine.miss_streak == 4
        assert engine.check_auto_hint(0) is None

    def test_u8_silence_escalates_and_caps(self):
        """U8｜静音 8s→1 档、16s→2 档、24s→3 档、32s 封顶不再触发。"""
        engine = _engine_of("我与父亲不相见")

        assert engine.check_auto_hint(8000) == 1
        assert engine.check_auto_hint(16000) == 2
        assert engine.check_auto_hint(24000) == 3
        assert engine.check_auto_hint(32000) is None

    def test_u8_no_hint_before_threshold(self):
        engine = _engine_of("我与父亲不相见")
        assert engine.check_auto_hint(7999) is None

    def test_u9_same_position_fires_once(self):
        """U9｜同一位置反复超时 → 只触发一次（`last_hint_pos` 生效）。"""
        engine = _engine_of("我与父亲不相见")

        assert engine.check_auto_hint(8000) == 1
        assert engine.check_auto_hint(8000) is None
        assert engine.check_auto_hint(8000) is None

    def test_u9_position_change_rearms(self):
        """位置一旦推进，提示额度重置（否则学生背到后面就再也要不到提示）。"""
        engine = _engine_of("我与父亲不相见")
        assert engine.check_auto_hint(8000) == 1
        engine.feed("我与父")
        assert engine.check_auto_hint(8000) == 1

    def test_u10_manual_hint_two(self):
        """U10｜手动 2 档：返回下一个字，并记一条 `hint` 事件。"""
        engine = _engine_of("我与父亲不相见")
        engine.feed("我与父")

        content = engine.apply_hint(2, "manual")
        assert content == "亲"

        hits = [e for e in engine.events if e.event_type == "hint"]
        assert len(hits) == 1
        assert hits[0].hint_level == 2
        assert hits[0].trigger_by == "manual"
        assert engine.hint_count == {"1": 0, "2": 1, "3": 0}

    def test_hint_levels_content(self):
        """三档内容递进：空框 → 下一个字 → 下一个字 + 上下文。"""
        engine = _engine_of("我与父亲不相见")

        assert engine.hint_text(1) == "_" * 7
        assert engine.hint_text(2) == "我"
        assert engine.hint_text(3) == "我与父亲不"

    def test_hint_invalid_level(self):
        engine = _engine_of("我与父亲不相见")
        with pytest.raises(ValueError):
            engine.apply_hint(4, "manual")

    def test_hint_at_end_returns_empty(self):
        engine = _engine_of("我与父亲")
        engine.feed("我与父亲")
        assert engine.hint_text(2) == ""
        assert engine.hint_text(1) == ""


# ===========================================================================
# U11/U12/U15：结算评分
# ===========================================================================


class TestScoring:
    def test_u11_full_cover_no_hint(self):
        """U11｜背完整篇、零提示 → 覆盖 1.0、满分、优秀。"""
        engine = _engine_of("我与父亲不相见")
        engine.feed("我与父亲不相见")
        result = engine.finish("completed")

        assert result.covered_ratio == 1.0
        assert result.matched_chars == 7
        assert result.missed_chars == []
        assert result.score == 100.0
        assert result.grade == "优秀"
        assert result.finish_reason == "completed"

    def test_u12_manual_finish_counts_rest_as_missed(self):
        """U12｜背到一半收工 → 未背部分计入漏字，覆盖比例反映实际。"""
        engine = _engine_of("我与父亲不相见")
        engine.feed("我与父亲")
        result = engine.finish("manual")

        assert result.matched_chars == 4
        assert result.missed_chars == [4, 5, 6]
        assert result.covered_ratio == pytest.approx(4 / 7, abs=1e-4)
        assert result.grade == "合格"

    def test_u15_three_hints_full_cover(self):
        """U15｜提示 3 次 + 全覆盖 + 零漏字 → `60 + 25 + (15 − 3×5)` = 85 → 良好。

        这条同时钉住 10b 正文那个写错的公式（它算出来是 97.75）。
        """
        engine = _engine_of("我与父亲不相见")
        engine.feed("我与父亲不相见")
        for level in (1, 2, 3):
            engine.apply_hint(level, "auto")
        result = engine.finish("completed")

        assert result.covered_ratio == 1.0
        assert result.hint_count == {"1": 1, "2": 1, "3": 1}
        assert result.score == 85.0
        assert result.grade == "良好"

    def test_hint_penalty_capacity(self):
        """提示惩罚额度只有 15 分，4 次以上不再多扣（扣完为止）。"""
        engine = _engine_of("我与父亲不相见")
        engine.feed("我与父亲不相见")
        for _ in range(5):
            engine.apply_hint(1, "manual")
        result = engine.finish("completed")

        assert result.score == 85.0

    @pytest.mark.parametrize(
        ("score", "grade"),
        [
            (100.0, "优秀"),
            (90.0, "优秀"),
            (89.9, "良好"),
            (75.0, "良好"),
            (74.9, "合格"),
            (60.0, "合格"),
            (59.9, "待加强"),
            (0.0, "待加强"),
        ],
    )
    def test_grade_boundaries(self, score: float, grade: str):
        assert grade_of(score) == grade

    def test_compute_score_empty_text(self):
        """空课文兜底：不抛异常，全 0。"""
        score, accuracy, grade = compute_score(
            covered_ratio=0.0, missed_count=0, total_chars=0, hints=0
        )
        assert score == 0.0
        assert accuracy == 0.0
        assert grade == "待加强"

    def test_finish_rejects_bad_reason(self):
        engine = _engine_of("我与父亲")
        with pytest.raises(ValueError):
            engine.finish("whatever")


# ===========================================================================
# 服务层：创建背诵 / 范围裁剪 / 权限
# ===========================================================================


class TestServiceCreate:
    def test_create_and_scope(self, client):
        """F1：创建背诵返回 `recitation_id` / `total_chars` / 每段字数，**不含拼音与正文**。"""
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见", "那年冬天"])
        session_id = _start_chinese_session(client, token, unit_id)

        data = _create_recitation(client, token, session_id, text_id)

        assert data["recitation_id"] > 0
        assert data["total_chars"] == pinyin_service.count_han("我与父亲不相见那年冬天")
        assert data["char_count_per_paragraph"] == [7, 4]
        # 安全线：绝不能下发拼音或正文
        assert "content_pinyin" not in data
        assert "content_plain" not in data
        assert "content_raw" not in data

    def test_partial_scope(self, client):
        """按范围裁剪：只选第 1 段时 `total_chars` 只算那一段。"""
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见", "那年冬天"])
        session_id = _start_chinese_session(client, token, unit_id)

        data = _create_recitation(client, token, session_id, text_id, scope=[0])
        assert data["total_chars"] == 7
        assert data["char_count_per_paragraph"] == [7]
        assert data["scope"] == [0]

    def test_u16_total_chars_matches_text(self, client):
        """U16｜`total_chars` 与 `scope_snapshot` 一致（同 scope 下等于 `texts.total_chars`）。"""
        token, _ = _register(client)
        paragraphs = ["我与父亲不相见", "那年冬天"]
        unit_id, text_id = _seed_text_unit(paragraphs=paragraphs)
        session_id = _start_chinese_session(client, token, unit_id)

        data = _create_recitation(client, token, session_id, text_id)
        stored = _sql("SELECT total_chars FROM texts WHERE id = ?", (text_id,))[0]["total_chars"]
        snapshot = _sql("SELECT scope_snapshot FROM recitations WHERE id = ?", (data["recitation_id"],))[
            0
        ]["scope_snapshot"]

        assert data["total_chars"] == int(stored)
        assert json.loads(snapshot)["end"] - json.loads(snapshot)["start"] == int(stored)

    def test_reject_unknown_scope(self, client):
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        session_id = _start_chinese_session(client, token, unit_id)

        resp = client.post(
            "/api/recitations",
            json={"session_id": session_id, "text_id": text_id, "scope": [9]},
            headers=_auth(token),
        )
        assert resp.json()["code"] == 10001

    def test_reject_inactive_text(self, client):
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        session_id = _start_chinese_session(client, token, unit_id)
        # 组卷之后才停用（组卷时课文必须是 active，否则单元里没有可背课文）
        with write_tx() as conn:
            conn.execute("UPDATE texts SET status = 'inactive' WHERE id = ?", (text_id,))

        resp = client.post(
            "/api/recitations",
            json={"session_id": session_id, "text_id": text_id, "scope": []},
            headers=_auth(token),
        )
        assert resp.json()["code"] == 30004

    def test_reject_other_students_session(self, client):
        """越权：拿别人的场次创建背诵 → 按「不存在」处理。"""
        token_a, _ = _register(client, "20260001")
        token_b, _ = _register(client, "20260002")
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        session_id = _start_chinese_session(client, token_a, unit_id)

        resp = client.post(
            "/api/recitations",
            json={"session_id": session_id, "text_id": text_id, "scope": []},
            headers=_auth(token_b),
        )
        assert resp.json()["code"] == 10004

    def test_reject_english_session(self, client):
        """英语场次不能用来背课文。"""
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        with write_tx() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions (student_id, unit_id, content_type, strategy,
                                      total_items, correct_items, hint_count, status)
                VALUES ((SELECT id FROM users WHERE username = '20260001'), ?, 'english', '{}', 0, 0, 0, 'ongoing')
                """,
                (unit_id,),
            )
            session_id = int(cursor.lastrowid)

        resp = client.post(
            "/api/recitations",
            json={"session_id": session_id, "text_id": text_id, "scope": []},
            headers=_auth(token),
        )
        assert resp.json()["code"] == 10001

    def test_need_auth(self, client):
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        resp = client.post(
            "/api/recitations", json={"session_id": 1, "text_id": text_id, "scope": []}
        )
        assert resp.json()["code"] == 10002


# ===========================================================================
# 服务层：feed / hint / restart
# ===========================================================================


class TestServiceRuntime:
    def _bootstrap(self, client: TestClient, text: str = "我与父亲不相见"):
        token, user_id = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=[text])
        session_id = _start_chinese_session(client, token, unit_id)
        data = _create_recitation(client, token, session_id, text_id)
        return token, user_id, session_id, data["recitation_id"], text

    def test_feed_advances(self, client):
        token, _, _, rid, _ = self._bootstrap(client)
        resp = client.post(
            f"/api/recitations/{rid}/feed",
            json={"partial_text": "我与父亲"},
            headers=_auth(token),
        )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["pos"] == 4
        assert body["data"]["newly_matched"] == [0, 1, 2, 3]
        assert body["data"]["total_chars"] == 7

    def test_feed_suggests_hint_without_consuming(self, client):
        """`feed` 里的 `hint_suggestion` 只是建议，**不消耗档位**。"""
        token, _, _, rid, _ = self._bootstrap(client)
        body = client.post(
            f"/api/recitations/{rid}/feed",
            json={"partial_text": "我与父亲", "silence_ms": 8000},
            headers=_auth(token),
        ).json()
        assert body["data"]["hint_suggestion"] == 1

        hint = client.post(
            f"/api/recitations/{rid}/hint",
            json={"level": None, "trigger_by": "auto", "silence_ms": 8000},
            headers=_auth(token),
        ).json()
        assert hint["data"]["level"] == 1
        assert hint["data"]["content"] == "_" * 3

    def test_feed_suggests_hint_without_silence(self, client):
        """silence_ms=0 也出建议（2026-10-01 修「卡壳死锁」）：客户端 partial
        到达即重置静音计时、恒传 0 —— 原先那行 `if silence_ms > 0` 把噪声/
        漏字分支的建议也一起挡死，念叨型卡壳永远等不到提示。"""
        token, _, _, rid, _ = self._bootstrap(client)
        # ⚠️ 累计文本语义：feed 收「到目前为止的全文」，新增片段才被消费。
        #    每轮追加一段英文 → 新增片段抽不出汉字 → 走噪声分支。
        partial = ""
        for chunk in ("ok", "yes", "sir", "well", "done"):
            partial += chunk
            body = client.post(
                f"/api/recitations/{rid}/feed",
                json={"partial_text": partial, "silence_ms": 0},
                headers=_auth(token),
            ).json()
            assert body["code"] == 0
        assert body["data"]["is_noise"] is True
        assert body["data"]["hint_suggestion"] == 1

    def test_manual_hint(self, client):
        token, _, _, rid, _ = self._bootstrap(client)
        client.post(
            f"/api/recitations/{rid}/feed", json={"partial_text": "我与父"}, headers=_auth(token)
        )
        body = client.post(
            f"/api/recitations/{rid}/hint", json={"level": 2}, headers=_auth(token)
        ).json()
        assert body["data"]["content"] == "亲"
        assert body["data"]["hint_count"]["2"] == 1

    def test_auto_hint_none(self, client):
        token, _, _, rid, _ = self._bootstrap(client)
        body = client.post(
            f"/api/recitations/{rid}/hint",
            json={"level": None, "silence_ms": 0},
            headers=_auth(token),
        ).json()
        assert body["data"]["level"] is None
        assert body["data"]["content"] is None

    def test_restart_resets(self, client):
        token, _, _, rid, _ = self._bootstrap(client)
        client.post(
            f"/api/recitations/{rid}/feed", json={"partial_text": "我与父"}, headers=_auth(token)
        )
        body = client.post(f"/api/recitations/{rid}/restart", headers=_auth(token)).json()
        assert body["data"]["pos"] == 0
        assert body["data"]["matched_chars"] == 0
        assert body["data"]["restart_count"] == 1

    def test_feed_other_student_denied(self, client):
        token_a, _, _, rid, _ = self._bootstrap(client)
        token_b, _ = _register(client, "20260009")
        resp = client.post(
            f"/api/recitations/{rid}/feed",
            json={"partial_text": "我与父亲"},
            headers=_auth(token_b),
        )
        assert resp.json()["code"] == 10004

    def test_feed_expired_engine(self, client):
        """引擎缓存被清（模拟进程重启）→ 提示重新开始，而不是 500。"""
        token, _, _, rid, _ = self._bootstrap(client)
        ReciteService.clear_cache()
        resp = client.post(
            f"/api/recitations/{rid}/feed",
            json={"partial_text": "我与父亲"},
            headers=_auth(token),
        )
        assert resp.json()["code"] == 10004


# ===========================================================================
# U13/U14：结算、防刷分、事件落库
# ===========================================================================


class TestSettle:
    def _bootstrap(self, client: TestClient, text: str = "我与父亲不相见"):
        token, user_id = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=[text])
        session_id = _start_chinese_session(client, token, unit_id)
        data = _create_recitation(client, token, session_id, text_id)
        return token, user_id, session_id, data["recitation_id"], text_id

    def test_u11_settle_full_cover(self, client):
        token, _, session_id, rid, _ = self._bootstrap(client)
        client.post(
            f"/api/recitations/{rid}/feed",
            json={"partial_text": "我与父亲不相见"},
            headers=_auth(token),
        )
        body = client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": "我与父亲不相见", "duration_ms": 30000, "first_voice_ms": 1200,
                  "finish_reason": "completed"},
            headers=_auth(token),
        ).json()

        data = body["data"]
        assert data["covered_ratio"] == 1.0
        assert data["score"] == 100.0
        assert data["grade"] == "优秀"
        assert data["missed_detail"] == []

        row = _sql("SELECT * FROM recitations WHERE id = ?", (rid,))[0]
        assert row["finish_reason"] == "completed"
        assert row["matched_chars"] == 7
        assert row["duration_ms"] == 30000
        assert row["first_voice_ms"] == 1200

        session = _sql("SELECT * FROM sessions WHERE id = ?", (session_id,))[0]
        # 背完一篇**不关场次**（同一节课还要背别的课文），只同步统计
        assert session["status"] == "ongoing"
        assert session["total_items"] == 7
        assert session["correct_items"] == 7
        assert session["score"] == 100.0

    def test_u13_client_cannot_cheat(self, client):
        """U13｜客户端谎报 `matched_chars = total`，服务端重算后返回真实值。"""
        token, _, _, rid, _ = self._bootstrap(client)
        # 实际只背了 4 个字
        client.post(
            f"/api/recitations/{rid}/feed", json={"partial_text": "我与父亲"}, headers=_auth(token)
        )

        body = client.post(
            f"/api/recitations/{rid}/settle",
            json={
                "transcript": "我与父亲",
                "matched_chars": 7,               # 谎报
                "missed_chars": [],               # 谎报
                "hint_count": {"1": 0, "2": 0, "3": 0},
                "finish_reason": "completed",
            },
            headers=_auth(token),
        ).json()

        assert body["data"]["matched_chars"] == 4
        assert body["data"]["missed_count"] == 3
        assert body["data"]["covered_ratio"] == pytest.approx(4 / 7, abs=1e-4)

        row = _sql("SELECT matched_chars, covered_ratio FROM recitations WHERE id = ?", (rid,))[0]
        assert row["matched_chars"] == 4

    def test_settle_after_process_restart(self, client):
        """引擎不在内存（进程重启）→ 用 `transcript` 重建后照常算出结果。"""
        token, _, _, rid, _ = self._bootstrap(client)
        ReciteService.clear_cache()

        body = client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": "我与父亲不相见", "finish_reason": "completed"},
            headers=_auth(token),
        ).json()

        assert body["data"]["covered_ratio"] == 1.0
        assert body["data"]["score"] == 100.0

    def test_settle_partial_with_transcript(self, client):
        """断线补报：`transcript` 比引擎见过的更长时补喂增量。"""
        token, _, _, rid, _ = self._bootstrap(client)
        client.post(
            f"/api/recitations/{rid}/feed", json={"partial_text": "我与"}, headers=_auth(token)
        )
        body = client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": "我与父亲不相见", "finish_reason": "completed"},
            headers=_auth(token),
        ).json()
        assert body["data"]["covered_ratio"] == 1.0

    def test_settle_is_idempotent(self, client):
        """重复结算返回原结果，不重复写库。"""
        token, _, _, rid, _ = self._bootstrap(client)
        payload = {"transcript": "我与父亲", "finish_reason": "manual"}
        first = client.post(
            f"/api/recitations/{rid}/settle", json=payload, headers=_auth(token)
        ).json()["data"]
        second = client.post(
            f"/api/recitations/{rid}/settle", json=payload, headers=_auth(token)
        ).json()["data"]

        assert second["score"] == first["score"]
        assert second["matched_chars"] == first["matched_chars"]
        assert second["replay"] is True
        assert first["replay"] is False
        assert len(_sql("SELECT id FROM recitations WHERE id = ?", (rid,))) == 1

    def test_settle_returns_server_duration(self, client):
        """契约 §9.4：结算响应**回带服务端已落库**的用时 / 首声延迟。

        这条是「结算页用时同源」的护栏。之前 `/settle` 不回带，客户端只能用
        自己那份本地计时 —— 切后台 / 杀进程就失真，而且与学生报告、管理端统计
        不是同一个来源。

        **幂等重放也必须回带**：二次进入结算页（或重复提交）时若变回 0，
        页面就会闪出一个「0 秒」。
        """
        token, _, _, rid, _ = self._bootstrap(client)
        client.post(
            f"/api/recitations/{rid}/feed",
            json={"partial_text": "我与父亲不相见"},
            headers=_auth(token),
        )

        first = client.post(
            f"/api/recitations/{rid}/settle",
            json={
                "transcript": "我与父亲不相见",
                "duration_ms": 42000,
                "first_voice_ms": 1800,
                "finish_reason": "completed",
            },
            headers=_auth(token),
        ).json()["data"]

        assert first["duration_ms"] == 42000
        assert first["first_voice_ms"] == 1800

        # 库里也确实是这一对值（响应不能是另算的）
        row = _sql("SELECT duration_ms, first_voice_ms FROM recitations WHERE id = ?", (rid,))[0]
        assert row["duration_ms"] == 42000
        assert row["first_voice_ms"] == 1800

        # 幂等重放：从库里读回同一对值
        again = client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": "我与父亲不相见", "finish_reason": "completed"},
            headers=_auth(token),
        ).json()["data"]
        assert again["replay"] is True
        assert again["duration_ms"] == 42000, "重放要回带库里的值，不能变 0"
        assert again["first_voice_ms"] == 1800

    def test_settle_first_voice_null_when_never_spoke(self, client):
        """没发过声 → `first_voice_ms` 回 `null`（而不是 0）。

        `0` 会被结算页画成「首次发声 0 秒」，那是假的；`null` 才能渲染成「—」。
        """
        token, _, _, rid, _ = self._bootstrap(client)
        data = client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": "", "duration_ms": 9000, "finish_reason": "manual"},
            headers=_auth(token),
        ).json()["data"]

        assert data["first_voice_ms"] is None
        assert data["duration_ms"] == 9000

    def test_settle_bad_reason(self, client):
        token, _, _, rid, _ = self._bootstrap(client)
        resp = client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": "我与父亲", "finish_reason": "exploded"},
            headers=_auth(token),
        )
        assert resp.json()["code"] == 10001

    def test_settle_other_student_denied(self, client):
        token_a, _, _, rid, _ = self._bootstrap(client)
        token_b, _ = _register(client, "20260008")
        resp = client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": "我与父亲不相见", "finish_reason": "completed"},
            headers=_auth(token_b),
        )
        assert resp.json()["code"] == 10004

    def test_u14_events_persisted_ordered(self, client):
        """U14｜结算后 `recitation_events` 事件齐全（matched/missed/hint）且按 `seq` 有序。"""
        token, _, _, rid, _ = self._bootstrap(client)
        client.post(
            f"/api/recitations/{rid}/feed", json={"partial_text": "我与父"}, headers=_auth(token)
        )
        client.post(
            f"/api/recitations/{rid}/hint",
            json={"level": 1, "trigger_by": "manual"},
            headers=_auth(token),
        )
        client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": "我与父", "finish_reason": "manual"},
            headers=_auth(token),
        )

        events = _sql(
            "SELECT seq, pos, char, event_type, hint_level, trigger_by FROM recitation_events "
            "WHERE recitation_id = ? ORDER BY seq",
            (rid,),
        )
        types = {e["event_type"] for e in events}
        assert {"matched", "hint"} <= types
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

        # matched 事件带出正确的字
        matched = [e for e in events if e["event_type"] == "matched"]
        assert [e["char"] for e in matched] == ["我", "与", "父"]
        assert [e["pos"] for e in matched] == [0, 1, 2]

        hint = [e for e in events if e["event_type"] == "hint"][0]
        assert hint["hint_level"] == 1
        assert hint["trigger_by"] == "manual"

    def test_events_report_appends_after_server(self, client):
        """F2：App 断线补报的事件接在服务端事件之后（服务端才是权威）。"""
        token, _, _, rid, _ = self._bootstrap(client)
        client.post(
            f"/api/recitations/{rid}/feed", json={"partial_text": "我与父"}, headers=_auth(token)
        )
        report = client.post(
            f"/api/recitations/{rid}/events",
            json={"events": [{"pos": 3, "event_type": "noise"}, {"pos": 4, "event_type": "matched", "char": "亲"}]},
            headers=_auth(token),
        ).json()
        assert report["data"]["accepted"] == 2
        assert report["data"]["authoritative"] == "server"

        client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": "我与父", "finish_reason": "manual"},
            headers=_auth(token),
        )
        events = _sql(
            "SELECT seq, event_type FROM recitation_events WHERE recitation_id = ? ORDER BY seq",
            (rid,),
        )
        assert events[-1]["event_type"] == "matched"          # 补报的两条在最后
        assert events[-2]["event_type"] == "noise"
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    def test_events_report_rejects_junk(self, client):
        token, _, _, rid, _ = self._bootstrap(client)
        resp = client.post(
            f"/api/recitations/{rid}/events",
            json={"events": [{"pos": 0, "event_type": "nonsense"}]},
            headers=_auth(token),
        )
        assert resp.json()["code"] == 10001

    def test_settle_missed_detail_has_chars(self, client):
        """结算回显漏字的**具体字**（需求 §5.6「结算页支持回看」）。"""
        token, _, _, rid, _ = self._bootstrap(client)
        body = client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": "我与父亲", "finish_reason": "manual"},
            headers=_auth(token),
        ).json()
        assert body["data"]["missed_detail"] == [
            {"pos": 4, "char": "不"},
            {"pos": 5, "char": "相"},
            {"pos": 6, "char": "见"},
        ]

    def test_multiple_recitations_in_one_session(self, client):
        """一节课连着背多篇：第一次结算后场次仍是 `ongoing`，能接着开第二次。"""
        token, _ = _register(client)
        unit_id, first = _seed_text_unit(paragraphs=["我与父亲不相见"])
        second = _insert_text(unit_id, ["那年冬天"], title="冬天", order_no=1)
        session_id = _start_chinese_session(client, token, unit_id)

        for text_id in (first, second):
            created = client.post(
                "/api/recitations",
                json={"session_id": session_id, "text_id": text_id, "scope": []},
                headers=_auth(token),
            ).json()
            assert created["code"] == 0, created
            rid = created["data"]["recitation_id"]
            client.post(
                f"/api/recitations/{rid}/settle",
                json={"transcript": "", "finish_reason": "manual"},
                headers=_auth(token),
            )

        session = _sql("SELECT * FROM sessions WHERE id = ?", (session_id,))[0]
        assert session["status"] == "ongoing"
        assert len(_sql("SELECT id FROM recitations WHERE session_id = ?", (session_id,))) == 2

    def test_finish_chinese_session_by_hand(self, client):
        """显式收口语文场次：按 `recitations` 汇总（没有 `answers` 可数）。"""
        token, _, session_id, rid, _ = self._bootstrap(client)
        client.post(
            f"/api/recitations/{rid}/feed",
            json={"partial_text": "我与父亲不相见"},
            headers=_auth(token),
        )
        client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": "我与父亲不相见", "finish_reason": "completed"},
            headers=_auth(token),
        )

        body = client.post(
            f"/api/sessions/{session_id}/finish",
            json={"reason": "completed"},
            headers=_auth(token),
        ).json()

        assert body["code"] == 0, body
        data = body["data"]
        assert data["content_type"] == "chinese"
        assert data["status"] == "finished"
        assert data["recitations"] == 1
        assert data["covered_ratio"] == 1.0
        assert data["score"] == 100.0

        session = _sql("SELECT status, score FROM sessions WHERE id = ?", (session_id,))[0]
        assert session["status"] == "finished"
        assert session["score"] == 100.0

    def test_finish_chinese_session_without_recitations(self, client):
        """一次都没背就收口：汇总全 0，不抛异常。"""
        token, _ = _register(client)
        unit_id, _ = _seed_text_unit(paragraphs=["我与父亲不相见"])
        session_id = _start_chinese_session(client, token, unit_id)

        body = client.post(
            f"/api/sessions/{session_id}/finish",
            json={"reason": "manual"},
            headers=_auth(token),
        ).json()
        assert body["code"] == 0, body
        assert body["data"]["recitations"] == 0
        assert body["data"]["covered_ratio"] == 0.0


# ===========================================================================
# 语文场次（BE-05 补齐，接口设计 §9.1 未定义）
# ===========================================================================


class TestChineseSession:
    def test_create_chinese_session(self, client):
        """语文场次的 `items` = **课文清单**（一篇一项），每项带背诵范围与总字数。"""
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见", "那年冬天"])

        body = client.post(
            "/api/sessions",
            json={"unit_id": unit_id, "content_type": "chinese"},
            headers=_auth(token),
        ).json()

        assert body["code"] == 0
        data = body["data"]
        assert data["content_type"] == "chinese"
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["text_id"] == text_id
        assert item["title"] == "背影"
        assert item["total_chars"] == 11
        assert item["recite_scope"] == [0, 1]
        # 组卷响应不含正文（正文由 §8.7 单独拉）
        assert "content_raw" not in item

    def test_chinese_session_multiple_texts(self, client):
        """一个单元多篇课文 → 多题，按 `order_no` 排序。"""
        token, _ = _register(client)
        unit_id, first = _seed_text_unit(paragraphs=["我与父亲不相见"])
        second = _insert_text(unit_id, ["那年冬天"], title="冬天", order_no=1)

        data = client.post(
            "/api/sessions",
            json={"unit_id": unit_id, "content_type": "chinese"},
            headers=_auth(token),
        ).json()["data"]

        assert [i["text_id"] for i in data["items"]] == [first, second]
        assert [i["total_chars"] for i in data["items"]] == [7, 4]

    def test_chinese_session_range(self, client):
        """`scope=range` 按**课文序号**切片（1 起闭区间），不是按段落。"""
        token, _ = _register(client)
        unit_id, first = _seed_text_unit(paragraphs=["我与父亲不相见"])
        _insert_text(unit_id, ["那年冬天"], title="冬天", order_no=1)

        data = client.post(
            "/api/sessions",
            json={
                "unit_id": unit_id,
                "content_type": "chinese",
                "strategy": {"scope": "range", "scope_detail": {"start": 1, "end": 1}},
            },
            headers=_auth(token),
        ).json()["data"]

        assert len(data["items"]) == 1
        assert data["items"][0]["text_id"] == first
        assert data["items"][0]["total_chars"] == 7

    def test_chinese_session_by_text_ids(self, client):
        """`scope_detail.text_ids` 精确指定课文。"""
        token, _ = _register(client)
        unit_id, first = _seed_text_unit(paragraphs=["我与父亲不相见"])
        second = _insert_text(unit_id, ["那年冬天"], title="冬天", order_no=1)

        data = client.post(
            "/api/sessions",
            json={
                "unit_id": unit_id,
                "content_type": "chinese",
                "strategy": {"scope_detail": {"text_ids": [second]}},
            },
            headers=_auth(token),
        ).json()["data"]

        assert [i["text_id"] for i in data["items"]] == [second]
        assert first not in [i["text_id"] for i in data["items"]]

    def test_chinese_session_no_texts(self, client):
        token, _ = _register(client)
        with write_tx() as conn:
            cursor = conn.execute(
                "INSERT INTO units (subject, grade, semester, name, status) VALUES ('chinese', 7, 'first', '空单元', 'active')"
            )
            unit_id = int(cursor.lastrowid)

        body = client.post(
            "/api/sessions",
            json={"unit_id": unit_id, "content_type": "chinese"},
            headers=_auth(token),
        ).json()
        assert body["code"] == 10001


# ===========================================================================
# 回归：窗口内对齐不得「向后跳」（2026-09-29 真机缺陷）
# ===========================================================================

# 与 `texts.content_plain` 同口径的《背影（节选）》全文（50 字，无标点）。
_BEIYING = (
    "我与父亲不相见已二年余了我最不能忘记的是他的背影"
    "那年冬天祖母死了父亲的差使也交卸了正是祸不单行的日子"
)


class TestAlignerWindowBias:
    """`Alignment.distance` 只能在**认领区间**内累计。

    真机现象（2026-09-29）：学生把两句全背完，
    `asr_logs` 里 transcript 与课文逐字吻合，但界面只点亮 **13/50**、
    得分 32、评级「待加强」，`recitation_events` 里的 matched 位置
    呈**孤立散布**（0~7、10、18、44、47、49）——典型「指针被推着往后跑」。

    根因在 `best_alignment`：片段是
    `target[start : start + len(actual) + window]`，**会在 `target` 末尾截断**。
    于是「起点越靠后 → 片段越短 → 尾部待删的 missing 越少 → `distance` 越小」，
    起点之间不可比，离末尾近的错误起点反而赢。

    这批用例把 `distance` 的口径钉死：**未认领的尾部是「还没背到」，
    既不算漏字、也不算错误**（与 `claimed_end` 的口径保持一致）。
    """

    def test_short_delta_after_long_match_does_not_jump(self):
        """`…是他的` 之后的 3 字增量「的背影」不得把指针推到 13 个字之后。"""
        engine = _engine_of(_BEIYING)
        engine.feed("我与父亲不相见已二年余了我最不能忘记的是他的")
        assert engine.pos == 22, f"前置推进就错了：pos={engine.pos}"

        result = engine.feed("我与父亲不相见已二年余了我最不能忘记的是他的背影")

        assert result.newly_matched == [22, 23], (
            f"应点亮「背影」两位，实际 {result.newly_matched}"
        )
        assert engine.pos == 24, (
            f"指针跳到了 {engine.pos}（应为 24）—— distance 又被尾部未认领的 "
            f"missing 带偏了"
        )

    def test_full_recitation_matches_every_char(self):
        """按流式 partial 的真实节奏逐段喂全文 → 必须 50/50。"""
        engine = _engine_of(_BEIYING)
        partials = [
            "我",
            "我与父亲",
            "我与父亲不相见",
            "我与父亲不相见已二年",
            "我与父亲不相见已二年余了",
            "我与父亲不相见已二年余了我最不能忘",
            "我与父亲不相见已二年余了我最不能忘记的是他的",
            "我与父亲不相见已二年余了我最不能忘记的是他的背影",
            "我与父亲不相见已二年余了我最不能忘记的是他的背影那年",
            "我与父亲不相见已二年余了我最不能忘记的是他的背影那年冬天",
            "我与父亲不相见已二年余了我最不能忘记的是他的背影那年冬天祖母死了",
            "我与父亲不相见已二年余了我最不能忘记的是他的背影那年冬天祖母死了父亲",
            "我与父亲不相见已二年余了我最不能忘记的是他的背影那年冬天祖母死了父亲的差使也",
            "我与父亲不相见已二年余了我最不能忘记的是他的背影那年冬天祖母死了父亲的差使也交卸了",
            "我与父亲不相见已二年余了我最不能忘记的是他的背影那年冬天祖母死了父亲的差使也交卸了正是祸不单行的",
            "我与父亲不相见已二年余了我最不能忘记的是他的背影那年冬天祖母死了父亲的差使也交卸了正是祸不单行的日子",
        ]
        for text in partials:
            engine.feed(text)

        assert engine.matched_count == 50, (
            f"只点亮 {engine.matched_count}/50 —— 对齐被推着往后跳"
        )
        assert engine.pos == 50

    def test_realign_rescues_char_missed_by_partial_correction(self):
        """结算重对齐：ASR 中间 partial 把「那年冬天」岔成「那冬天」→ 增量
        对齐把「年」判 missed 且不回头（2026-10-01 真机《猴子》实锤，64/65）；
        结算用最终转写 realign 后必须救回 65/65。"""
        text = "我与李同学不相见已二年余了我最不能忘记的是他的大脑袋那年冬天他去深圳了正是他开始奋斗的日子他有一个爱好就是打英雄联盟但是他玩的很菜"
        engine = _engine_of(text)

        seg1 = "我与李同学不相见已二年余了。我最不能忘记的是他的大脑袋。那冬天，"
        seg2 = "他去深圳了，正是他开始奋斗的日子。"
        seg3 = "他有一个爱好，就是打《英雄联盟》，但是他玩得很菜。"
        for s in (seg1, seg1 + seg2, seg1 + seg2 + seg3):
            engine.feed(s)
        assert sorted(engine._missed) == [27], (
            f"前置：增量对齐应把「年」(pos 27) 判 missed，实际 {sorted(engine._missed)}"
        )

        # ASR 自我纠正后的最终转写（含「那年冬天」）
        final = "我与李同学不相见已二年余了。我最不能忘记的是他的大脑袋。那年冬天，他去深圳了，正是他开始奋斗的日子。他有一个爱好，就是打《英雄联盟》，但是他玩得很菜。"
        assert engine.realign(final) is True
        assert engine.matched_count == 65, f"重对齐后应 65/65，实际 {engine.matched_count}"
        assert engine._missed == set()

        # 不碰提示/重开计数：realign 前后保持原值
        assert engine.hint_count == {"1": 0, "2": 0, "3": 0}
        assert engine.restart_count == 0

    def test_realign_noop_when_transcript_empty_or_not_better(self):
        """空转写 / 无新增匹配时 realign 不动状态、返回 False。"""
        text = "我与李同学不相见已二年余了"
        engine = _engine_of(text)
        engine.feed(text)
        before = engine.matched_count

        assert engine.realign("") is False
        assert engine.realign("   ") is False
        assert engine.realign(text) is False  # 无新增匹配
        assert engine.matched_count == before

    def test_realign_union_keeps_incremental_matches(self):
        """并集口径：增量过程匹配过、但全量重对齐判噪声的内容仍算背出。"""
        text = "我与李同学不相见已二年余了我最不能忘记的是他的大脑袋那年冬天"
        engine = _engine_of(text)
        # 增量喂前半段（后半段学生念了但 ASR 全岔成英文数字）
        engine.feed("我与李同学不相见已二年余了我最不能忘记的是他的大脑袋那年冬天")
        assert engine.matched_count == len(text)
        # 最终转写后半段全是英文数字（噪声）——重对齐不得把增量已点亮的丢掉
        final = "我与李同学不相见已二年余了我最不能忘记的是他的大脑袋那年冬天 hehe 123"
        assert engine.realign(final) is False
        assert engine.matched_count == len(text)

    def test_alignment_distance_ignores_window_size(self):
        """`distance` 只累计认领区间 —— **窗口变大不得让 distance 变大**。

        未认领的尾部（片段比识别文本多出来的那截）语义是「学生还没背到」，
        不是错误。旧实现把它算进 `distance` 后，各候选起点的片段长度不同
        （越靠后越短、尾部 missing 越少）→ distance 不可比 → 引擎被推着往后跳。
        """
        from app.services.recite.aligner import (
            align_sequences,
            build_candidates,
            compile_target,
            summarize,
            to_pinyin_seq,
        )

        chars, pinyin = compile_target(_BEIYING, " ".join(to_pinyin_seq(_BEIYING)))
        candidates = build_candidates(chars, pinyin)
        actual = to_pinyin_seq("的背影")

        small = summarize(
            align_sequences(list(candidates[22 : 22 + len(actual) + 20]), actual), 22
        )
        large = summarize(
            align_sequences(list(candidates[22 : 22 + len(actual) + 40]), actual), 22
        )

        assert len(small.matched) == 2, f"应点亮「背影」两位，实际 {small.matched}"
        assert small.claimed_end == 24
        assert large.distance == small.distance, (
            f"窗口 20 → distance={small.distance}，窗口 40 → distance={large.distance}；"
            f"尾部未认领的字被当成错误了"
        )

    def test_alignment_prefers_the_true_start_over_a_later_one(self):
        """择优必须选中真起点 22，而不是末尾那个「的」（起点更靠后≠更优）。"""
        from app.services.recite.aligner import (
            align_sequences,
            build_candidates,
            compile_target,
            summarize,
            to_pinyin_seq,
        )

        chars, pinyin = compile_target(_BEIYING, " ".join(to_pinyin_seq(_BEIYING)))
        candidates = build_candidates(chars, pinyin)
        actual = to_pinyin_seq("的背影")

        right = summarize(
            align_sequences(list(candidates[22 : 22 + len(actual) + 20]), actual), 22
        )
        wrong = summarize(
            align_sequences(list(candidates[34 : 34 + len(actual) + 20]), actual), 34
        )

        assert len(right.matched) == 2, "真起点要能点亮「背影」两位"
        assert len(wrong.matched) == 1, "末尾那个「的」只该中 1 位"
        assert (right.distance, -len(right.matched)) < (
            wrong.distance,
            -len(wrong.matched),
        ), (
            f"择优键 (distance, -matched)：真起点 {(right.distance, -len(right.matched))} "
            f"输给了末尾起点 {(wrong.distance, -len(wrong.matched))}"
        )


# ===========================================================================
# 跨包回归：不能因为 BE-05 的改动碰坏 BE-03 / BE-04
# ===========================================================================


class TestCrossPackageRegression:
    def test_english_session_still_works(self, client):
        """英语组卷与判分链路不受影响（`content_type='english'` 走原分支）。"""
        token, _ = _register(client)
        with write_tx() as conn:
            cursor = conn.execute(
                "INSERT INTO units (subject, grade, semester, name, status) VALUES ('english', 3, 'first', 'Unit 1', 'active')"
            )
            unit_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO words (unit_id, type, en, cn, require_spell, order_no, status) "
                "VALUES (?, 'word', 'classroom', '教室', 1, 0, 'active')",
                (unit_id,),
            )

        created = client.post(
            "/api/sessions",
            json={"unit_id": unit_id, "content_type": "english"},
            headers=_auth(token),
        ).json()
        assert created["code"] == 0
        assert created["data"]["items"][0]["prompt"] == "教室"

        session_id = created["data"]["session_id"]
        word_id = created["data"]["items"][0]["word_id"]
        answer = client.post(
            f"/api/sessions/{session_id}/answers",
            json={"word_id": word_id, "mode": "cn2en", "step": 1, "asr_text": "classroom"},
            headers=_auth(token),
        ).json()
        assert answer["code"] == 0, answer
        assert answer["data"]["is_correct"] is True
        assert answer["data"]["match_type"] == "exact"

    def test_text_detail_still_hides_pinyin(self, client):
        """§8.7 学生端取课文详情仍不下发拼音。"""
        token, _ = _register(client)
        _, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])

        body = client.get(f"/api/texts/{text_id}", headers=_auth(token)).json()
        assert body["code"] == 0
        assert "content_pinyin" not in body["data"]
        assert "content_plain" not in body["data"]
        assert body["data"]["scope_snapshot"] == {"start": 0, "end": 7}


# ===========================================================================
# §8.10 语文错题本（`GET /api/me/wrong-chars`，A5）
# ===========================================================================


class TestChineseWrongBook:
    """语文错题本。**放在 BE-05 的测试文件里是有意的**：它的数据源是
    `recitation_events`（由 BE-05 的引擎写），所以每个用例都**真跑一遍背诵**
    —— 在测试里手插 `recitation_events` 会绕开真实口径，测的就不是生产行为。

    两条最容易混的分界线各有一个用例钉死：
    ① 「**背到了那儿却没对上**」才算漏；中途结束、剩下没背的字
       会进 `settle.missed_detail`（§9.4，结算页要回看）但**不进错题本**
       —— `engine.finish()` 刻意不补 `missed` 事件；
    ② `noise`（整段背错，需求 §6.3「等学生自我纠正」）**不算漏**。
    """

    #: 目标课文「我爱北京天安门」；喂「我爱天门」会漏 北(2) / 京(3) / 安(5)。
    TEXT = "我爱北京天安门"
    FEED = "我爱天门"

    @staticmethod
    def _recite(client: TestClient, token: str, unit_id: int, text_id: int, fed: str) -> int:
        """开一场 → 建一次背诵 → 喂 `fed` → 结算（事件随结算落库）。"""
        session_id = _start_chinese_session(client, token, unit_id)
        rec = client.post(
            "/api/recitations",
            json={"session_id": session_id, "text_id": text_id, "scope": []},
            headers=_auth(token),
        ).json()
        assert rec["code"] == 0, rec
        rid = int(rec["data"]["recitation_id"])

        feed = client.post(
            f"/api/recitations/{rid}/feed", json={"partial_text": fed}, headers=_auth(token)
        ).json()
        assert feed["code"] == 0, feed

        settle = client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": fed, "finish_reason": "manual"},
            headers=_auth(token),
        ).json()
        assert settle["code"] == 0, settle
        return rid

    @staticmethod
    def _wrong_chars(client: TestClient, token: str, **params: Any) -> dict[str, Any]:
        resp = client.get("/api/me/wrong-chars", params=params, headers=_auth(token))
        assert resp.json()["code"] == 0, resp.json()
        return resp.json()["data"]

    def test_missed_chars_listed_with_context(self, client):
        """真链路：漏过的字按课文列出，带次数与上下文，且 `first_pos` 指得准。"""
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=[self.TEXT], title="天安门")
        self._recite(client, token, unit_id, text_id, self.FEED)

        data = self._wrong_chars(client, token)
        assert data["total"] == 3
        assert {(i["char"], i["wrong_count"]) for i in data["items"]} == {
            ("北", 1),
            ("京", 1),
            ("安", 1),
        }

        by_char = {i["char"]: i for i in data["items"]}
        assert by_char["北"]["text_title"] == "天安门"
        assert by_char["北"]["unit_name"] == "第1单元-天安门"
        assert by_char["北"]["context_before"] == "我爱"
        assert by_char["北"]["context_after"] == "京天安门"
        assert by_char["安"]["context_before"] == "爱北京天"
        assert by_char["安"]["context_after"] == "门"

        # 口径自检：`first_pos` 必须真的指向那个字（下标与正文错位是这类接口最常见的静默错）
        plain = _sql("SELECT content_plain FROM texts WHERE id = ?", (text_id,))[0][
            "content_plain"
        ]
        assert plain == self.TEXT
        for item in data["items"]:
            assert plain[int(item["first_pos"])] == item["char"]

    def test_only_returns_own_records(self, client):
        """错题本**只回自己**。

        反向验证：把 `MasteryService.wrong_chars` 的 `r.student_id = ?` 去掉后，
        本用例必须转红（B 会看到 A 的 3 个漏字）。
        """
        token_a, _ = _register(client, "20260001")
        token_b, _ = _register(client, "20260002")
        unit_id, text_id = _seed_text_unit(paragraphs=[self.TEXT], title="天安门")
        self._recite(client, token_a, unit_id, text_id, self.FEED)

        assert self._wrong_chars(client, token_a)["total"] == 3

        b = self._wrong_chars(client, token_b)
        assert b["total"] == 0
        assert b["items"] == []

    def test_unreached_chars_not_in_book(self, client):
        """⚠️ 分界线①：中途结束、**剩下没背的**字进结算明细，但不进错题本。"""
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=[self.TEXT], title="天安门")
        rid = self._recite(client, token, unit_id, text_id, "我爱")  # 只背到第 2 个字

        detail = json.loads(
            _sql("SELECT missed_chars FROM recitations WHERE id = ?", (rid,))[0]["missed_chars"]
        )
        assert detail == [2, 3, 4, 5, 6], "结算页应该看到这 5 个字"
        assert self._wrong_chars(client, token)["total"] == 0, "但它们不是「漏背」"

    def test_noise_not_counted(self, client):
        """⚠️ 分界线②：整段背错记 `noise`，不算漏字。"""
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=[self.TEXT], title="天安门")
        self._recite(client, token, unit_id, text_id, "哈哈哈哈哈哈")

        types = [e["event_type"] for e in _sql("SELECT event_type FROM recitation_events")]
        assert types == ["noise"]
        assert self._wrong_chars(client, token)["total"] == 0

    def test_wrong_count_accumulates_and_orders(self, client):
        """同一个字漏两次要**合并成一条** `wrong_count=2`，并按次数降序排出。"""
        token, _ = _register(client)
        unit_id, text_a = _seed_text_unit(paragraphs=[self.TEXT], title="天安门")
        text_b = _insert_text(unit_id, ["春眠不觉晓"], title="春晓", order_no=1)

        # ① 天安门背两次，两次都漏 北/京/安 → 各 2 次
        self._recite(client, token, unit_id, text_a, self.FEED)
        self._recite(client, token, unit_id, text_a, self.FEED)
        # ② 春晓背一次，漏 不/觉 → 各 1 次
        self._recite(client, token, unit_id, text_b, "春眠晓")

        data = self._wrong_chars(client, token)
        assert data["total"] == 5, "同一个字要合并，不是 8 条"
        counts = [i["wrong_count"] for i in data["items"]]
        assert counts == [2, 2, 2, 1, 1]
        assert {i["char"] for i in data["items"][:3]} == {"北", "京", "安"}
        assert {i["char"] for i in data["items"][3:]} == {"不", "觉"}

    def test_inactive_text_excluded(self, client):
        """课文停用后不该再出现在错题本（点进去也背不了）。"""
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=[self.TEXT], title="天安门")
        self._recite(client, token, unit_id, text_id, self.FEED)
        assert self._wrong_chars(client, token)["total"] == 3

        with write_tx() as conn:
            conn.execute("UPDATE texts SET status = 'inactive' WHERE id = ?", (text_id,))

        assert self._wrong_chars(client, token)["total"] == 0

    def test_filters_and_paging(self, client):
        """`unit_id` / `grade`+`semester` 过滤，以及分页（`total` 是全量）。"""
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=[self.TEXT], title="天安门")
        other_unit, _ = _seed_text_unit(paragraphs=[self.TEXT], title="别的单元")
        self._recite(client, token, unit_id, text_id, self.FEED)

        assert self._wrong_chars(client, token, unit_id=unit_id)["total"] == 3
        assert self._wrong_chars(client, token, unit_id=other_unit)["total"] == 0
        # `_seed_text_unit` 固定造 chinese / 7 / first
        assert self._wrong_chars(client, token, grade=7, semester="first")["total"] == 3
        assert self._wrong_chars(client, token, grade=3, semester="first")["total"] == 0

        p1 = self._wrong_chars(client, token, page=1, page_size=2)
        p2 = self._wrong_chars(client, token, page=2, page_size=2)
        assert p1["total"] == p2["total"] == 3
        assert len(p1["items"]) == 2 and len(p2["items"]) == 1
        assert not (
            {i["char"] for i in p1["items"]} & {i["char"] for i in p2["items"]}
        ), "两页不能重复"

    def test_empty_when_nothing_missed(self, client):
        """一次都没背过 → 空列表 + `total=0`（不是报错、也不是 404）。"""
        token, _ = _register(client)
        assert self._wrong_chars(client, token) == {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
        }

    def test_wrong_words_still_english_only(self, client):
        """交叉回归：新增 §8.10 **没有**把英语错题本（§8.8）牵连成两种形状。"""
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=[self.TEXT], title="天安门")
        self._recite(client, token, unit_id, text_id, self.FEED)

        resp = client.get("/api/me/wrong-words", headers=_auth(token)).json()
        assert resp["code"] == 0
        assert resp["data"]["total"] == 0
        assert resp["data"]["items"] == []
