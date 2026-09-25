"""WP-2K 游戏化激励 —— 验收测试（含反向验证）。

直接 SQL 造数（不走写入接口）：本包验的是**积分/徽章的推导口径**、**读时惰性刷新
的幂等 upsert**、**学生隔离**，不是写入链路。

覆盖（契约 §9 验收）：
- U1 响应形状：`{points, streak_days, badges:[{code,name,earned,earned_at?}]}`；
- U2 积分推导 = `compute_points`（正确题/掌握词/背诵/完成场次/连续天数各自的建议值）；
- U3 连续天数（北京时间切天，复用 MasteryService.streak_days）；
- U4 徽章达成/未达成（阈值命中 / 不命中）；
- U5 **学生隔离 + 反向验证**：撤掉 `WHERE student_id = ?` 必须转红；
- U6 幂等 upsert：`GET` 两次，`user_points` 不出现第二行，`user_badges` 不重复；
- U7 未登录 → 10002（HTTP 200）；
- U8 无数据学生：points=0 / streak=0 / 全部徽章未达成，不报错。

数据底座（真实用例数字）：
  学生 A：12 题正确、掌握 2 词、完成 1 场、完成 1 次背诵、连续 3 天
          -> 积分 = 12*10 + 2*50 + 1*30 + 1*20 + 3*5 = 285
          -> 已达成：first_practice / ten_correct / first_mastered / first_recite /
                     first_session / streak_3（共 6 枚）
  学生 B：100 题正确、掌握 10 词、完成 1 场、完成 1 次背诵、连续 7 天
          -> 积分 = 100*10 + 10*50 + 1*30 + 1*20 + 7*5 = 1585
          -> 额外达成：hundred_correct / ten_mastered / streak_7
  （B 的徽章 A 不应拥有 —— 反向验证的锚点）
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_wp2k_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "test-secret-" + "0" * 32
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "CHANGE_ME_admin_password"
os.environ["LOG_LEVEL"] = "WARNING"

from app.core import rate_limit  # noqa: E402
from app.core.config import reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.services.gamify_service import (  # noqa: E402
    BADGE_CATALOG,
    _Stats,
    compute_points,
    snapshot,
)
from app.services.stats_service import _BJ, beijing_day as _system_today  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


# =========================================================================== 夹具


@pytest.fixture(autouse=True)
def _no_rate_limit():
    rate_limit.clear_all()
    yield


@pytest.fixture(scope="module", autouse=True)
def _fresh_config():
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = "test-secret-" + "0" * 32
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = "admin"
    os.environ["ADMIN_INIT_PASSWORD"] = "CHANGE_ME_admin_password"
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    return _DB_FILE


@pytest.fixture(scope="module")
def client(_fresh_config):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# =========================================================================== 造数


def _utc(bj_day: str, hour: int = 12, minute: int = 0) -> str:
    """北京时间某天某时 -> 入库 UTC ISO 串。"""
    dt = datetime.fromisoformat(f"{bj_day}T{hour:02d}:{minute:02d}:00+08:00")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def beijing_day(offset_days: int = 0) -> str:
    base = _system_today(0)
    return (date.fromisoformat(base) + timedelta(days=offset_days)).isoformat()


class Seed:
    student_a: int = 0
    student_b: int = 0
    sess_a: int = 0
    sess_b: int = 0
    recit_a: int = 0
    recit_b: int = 0


SEED = Seed()


@pytest.fixture(scope="module")
def seed(client):
    """造两名学生的可控数据（详见文件头"数据底座"）。"""
    today = _system_today(0)

    with write_tx() as conn:
        cur = conn.execute(
            "INSERT INTO users (role, username, real_name, password_hash) VALUES ('student','wp2k_a','学生A','x')"
        )
        SEED.student_a = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO users (role, username, real_name, password_hash) VALUES ('student','wp2k_b','学生B','x')"
        )
        SEED.student_b = int(cur.lastrowid)
        # 给 student_c 一个"无数据"学生，验空态。
        cur = conn.execute(
            "INSERT INTO users (role, username, real_name, password_hash) VALUES ('student','wp2k_c','学生C','x')"
        )

        # unit + 12 词（供 word_mastery 引用）。
        cur = conn.execute(
            "INSERT INTO units (grade, semester, subject, name, status) VALUES (3,'first','english','U','active')"
        )
        unit = int(cur.lastrowid)
        word_ids: list[int] = []
        for i in range(12):
            cur = conn.execute(
                "INSERT INTO words (unit_id, en, cn, type, status) VALUES (?,?,?,?,?)",
                (unit, f"w{i}", f"中{i}", "word", "active"),
            )
            word_ids.append(int(cur.lastrowid))

        # 场次。
        cur = conn.execute(
            "INSERT INTO sessions (student_id, content_type, strategy, total_items, correct_items, status) "
            "VALUES (?, 'english', 'review', 0, 0, 'finished')",
            (SEED.student_a,),
        )
        SEED.sess_a = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO sessions (student_id, content_type, strategy, total_items, correct_items, status) "
            "VALUES (?, 'english', 'review', 0, 0, 'finished')",
            (SEED.student_b,),
        )
        SEED.sess_b = int(cur.lastrowid)

        def _answer(sid, sess, bj_day, match_type, wid, hour=12):
            conn.execute(
                "INSERT INTO answers (session_id, student_id, word_id, mode, step, expected, match_type, created_at) "
                "VALUES (?,?,?, 'cn2en', 1, 'x', ?, ?)",
                (sess, sid, wid, match_type, _utc(bj_day, hour)),
            )

        # 学生 A：连续 3 天（D-2,D-1,D0），12 题正确（exact）。
        for off in (-2, -1, 0):
            for k in range(4):
                _answer(SEED.student_a, SEED.sess_a, beijing_day(off), "exact", word_ids[k % 12])

        # 学生 B：连续 7 天（D-6..D0），100 题正确（exact）。
        for off in range(-6, 1):
            for k in range(15):
                _answer(SEED.student_b, SEED.sess_b, beijing_day(off), "exact", word_ids[k % 12])
        # 多补 10 题确保 >= 100（7*15=105）。

        # word_mastery（直接物化，模拟 BE-04 已写入）。
        # A 掌握 2 词；B 掌握 10 词。
        for i in range(2):
            conn.execute(
                "INSERT INTO word_mastery (student_id, word_id, total_count, correct_count, mastered_at) "
                "VALUES (?,?,1,1,?)",
                (SEED.student_a, word_ids[i], beijing_day(0) + "T00:00:00Z"),
            )
        for i in range(10):
            conn.execute(
                "INSERT INTO word_mastery (student_id, word_id, total_count, correct_count, mastered_at) "
                "VALUES (?,?,1,1,?)",
                (SEED.student_b, word_ids[i], beijing_day(0) + "T00:00:00Z"),
            )

        # recitations（完成背诵）。
        cur = conn.execute(
            "INSERT INTO recitations (session_id, student_id, text_id, finish_reason, total_chars, matched_chars) "
            "VALUES (?,?,NULL,'completed',10,10)",
            (SEED.sess_a, SEED.student_a),
        )
        SEED.recit_a = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO recitations (session_id, student_id, text_id, finish_reason, total_chars, matched_chars) "
            "VALUES (?,?,NULL,'completed',10,10)",
            (SEED.sess_b, SEED.student_b),
        )
        SEED.recit_b = int(cur.lastrowid)

    return SEED


def _token(student_id: int) -> str:
    token, _ = create_access_token(user_id=student_id, role="student")
    return token


def _earned_codes(snap) -> set[str]:
    return {b.code for b in snap.badges if b.earned}


# =========================================================================== 用例


def test_happy_shape(client, seed):
    """U1：带 token 访问返回 200/code=0，badges 形状正确。"""
    resp = client.get(
        "/api/me/gamify",
        headers={"Authorization": f"Bearer {_token(SEED.student_a)}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert isinstance(data["points"], int)
    assert isinstance(data["streak_days"], int)
    assert isinstance(data["badges"], list)

    keys = {"code", "name", "earned"}
    for b in data["badges"]:
        assert set(b.keys()) == keys or set(b.keys()) == keys | {"earned_at"}, b
        assert isinstance(b["code"], str)
        assert isinstance(b["name"], str)
        assert isinstance(b["earned"], bool)
        # earned=true 必有 earned_at；earned=false 必无 earned_at
        if b["earned"]:
            assert "earned_at" in b and b["earned_at"]
        else:
            assert "earned_at" not in b


def test_points_derivation(client, seed):
    """U2 + U4：积分按建议值公式推导，且 A 的徽章达成情况与数据底座一致。"""
    token_a = _token(SEED.student_a)
    resp = client.get("/api/me/gamify", headers={"Authorization": f"Bearer {token_a}"})
    data = resp.json()["data"]

    # 积分 = 正确题*10 + 掌握词*50 + 背诵*30 + 完成场次*20 + 连续天数*5
    # A: 12*10 + 2*50 + 1*30 + 1*20 + 3*5 = 285
    assert data["points"] == 285, data
    assert data["streak_days"] == 3, data

    earned = {b["code"] for b in data["badges"] if b["earned"]}
    expected_earned = {
        "first_practice", "ten_correct", "first_mastered",
        "first_recite", "first_session", "streak_3",
    }
    assert earned == expected_earned, earned
    # 未达成项不应出现
    assert "ten_mastered" not in earned
    assert "hundred_correct" not in earned
    assert "streak_7" not in earned

    # 反向对账：直接按常量重算，确保接口值与公式一致（不依赖手算常量）。
    with read_conn() as conn:
        snap = snapshot(conn, SEED.student_a)
    assert snap.points == compute_points(
        _Stats(
            answer_count=12, correct_count=12, mastered_words=2,
            recitation_count=1, finished_session_count=1, streak_days=3, practice_days=3,
        )
    )


def test_streak_days(client, seed):
    """U3：B 连续 7 天练习 -> streak_days=7，且达成 streak_7。"""
    resp = client.get(
        "/api/me/gamify",
        headers={"Authorization": f"Bearer {_token(SEED.student_b)}"},
    )
    data = resp.json()["data"]
    assert data["streak_days"] == 7, data
    earned = {b["code"] for b in data["badges"] if b["earned"]}
    assert "streak_7" in earned
    # B 正确题 = 105，积分 = 105*10 + 10*50 + 1*30 + 1*20 + 7*5 = 1635
    assert data["points"] == 1635, data


def test_badge_unearned_and_catalog_complete(client, seed):
    """U4：徽章墙含全部目录项（未达成也展示），且数量与 BADGE_CATALOG 一致。"""
    resp = client.get(
        "/api/me/gamify",
        headers={"Authorization": f"Bearer {_token(SEED.student_a)}"},
    )
    data = resp.json()["data"]
    codes = {b["code"] for b in data["badges"]}
    assert codes == {d.code for d in BADGE_CATALOG}
    # 未达成的 fifty_sessions / hundred_correct / ten_mastered / streak_7 必须出现且 earned=false
    for code in ("fifty_sessions", "hundred_correct", "ten_mastered", "streak_7"):
        item = next(b for b in data["badges"] if b["code"] == code)
        assert item["earned"] is False
        assert "earned_at" not in item


def test_student_isolation_and_reverse_validation(client, seed):
    """U5（反向验证）：A 绝不拿到 B 的数据；撤掉 student_id 过滤必须转红。

    机制：直接调用 `snapshot(conn, A)` 计算 A 的积分。若 `_compute_stats`
    里漏掉 `WHERE student_id = ?`，A 的积分会变成"全库合计"，既不等于 A 专属的
    285，也会让 A 凭空"达成" `ten_mastered` / `hundred_correct` —— 下面两个断言
    任一不成立即说明过滤失效（转红）。
    """
    with read_conn() as conn:
        snap_a = snapshot(conn, SEED.student_a)

    # 1) A 的积分严格等于"仅 A 数据"推导值（=285），不能等于全库合计。
    assert snap_a.points == 285, snap_a.points

    # 2) A 不应拥有 B 专属的徽章。
    earned_a = _earned_codes(snap_a)
    assert "ten_mastered" not in earned_a
    assert "hundred_correct" not in earned_a
    assert "streak_7" not in earned_a

    # 3) 反向验证核心：无 student_id 过滤的全库积分必须 > A 看到的值。
    #    若有人删掉 `WHERE student_id = ?`，snap_a.points 会等于 global_points，
    #    下面 `assert snap_a.points < global_points` 将失败 -> 转红。
    with read_conn() as conn:
        global_points = int(
            conn.execute(
                """
                SELECT COALESCE(SUM(CASE WHEN match_type IN ('exact','loose','fuzzy')
                                         THEN 10 ELSE 0 END), 0)
                     + (SELECT COALESCE(SUM(CASE WHEN mastered_at IS NOT NULL THEN 50 ELSE 0 END),0)
                        FROM word_mastery)
                     + (SELECT COALESCE(COUNT(*) ,0) FROM recitations WHERE finish_reason='completed') * 30
                     + (SELECT COALESCE(COUNT(*) ,0) FROM sessions WHERE status='finished') * 20
                FROM answers
                """
            ).fetchone()[0]
        )
    assert global_points > snap_a.points, (
        f"A 的积分 {snap_a.points} 不应等于全库合计 {global_points}：student_id 过滤疑似失效"
    )
    # 同时确认全局积分确实包含 B 的贡献（B 单独就有 1635 分，且不含连续天数奖励的全局口径
    # = 105*10 + 10*50 + 1*30 + 1*20 = 1800，必 > A 的 285）。
    assert global_points >= 1800


def test_idempotent_upsert(client, seed):
    """U6：GET 两次，user_points 不出现第二行，user_badges 不重复（幂等）。"""
    token_a = _token(SEED.student_a)
    for _ in range(2):
        resp = client.get("/api/me/gamify", headers={"Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 200 and resp.json()["code"] == 0

    with read_conn() as conn:
        points_rows = int(
            conn.execute(
                "SELECT COUNT(*) FROM user_points WHERE student_id = ?", (SEED.student_a,)
            ).fetchone()[0]
        )
        badge_rows = int(
            conn.execute(
                "SELECT COUNT(*) FROM user_badges WHERE student_id = ?", (SEED.student_a,)
            ).fetchone()[0]
        )
        # 再读一次接口，确认 badge 数量稳定（徽章墙里 earned 的项 == 库里行数）。
        earned_api = sum(1 for b in client.get(
            "/api/me/gamify", headers={"Authorization": f"Bearer {token_a}"}
        ).json()["data"]["badges"] if b["earned"])

    # user_points 唯一（PK = student_id），不应因多次刷新产生多行。
    assert points_rows == 1, f"user_points 应为 1 行，实际 {points_rows}"
    # A 已达成 6 枚徽章，库里 6 行；幂等 INSERT OR IGNORE 不会重复。
    assert badge_rows == 6, f"user_badges 应为 6 行，实际 {badge_rows}"
    assert earned_api == 6


def test_student_isolation_api(client, seed):
    """U5（接口层）：A 的 /me/gamify 不会暴露 B 的 ten_mastered。"""
    resp_a = client.get(
        "/api/me/gamify", headers={"Authorization": f"Bearer {_token(SEED.student_a)}"}
    )
    resp_b = client.get(
        "/api/me/gamify", headers={"Authorization": f"Bearer {_token(SEED.student_b)}"}
    )
    a_badges = {b["code"]: b["earned"] for b in resp_a.json()["data"]["badges"]}
    b_badges = {b["code"]: b["earned"] for b in resp_b.json()["data"]["badges"]}

    assert a_badges["ten_mastered"] is False
    assert b_badges["ten_mastered"] is True
    assert resp_a.json()["data"]["points"] == 285
    assert resp_b.json()["data"]["points"] == 1635


def test_auth_required(client, seed):
    """U7：未带 token 访问受保护路由 -> 10002，且 HTTP 200。"""
    resp = client.get("/api/me/gamify")
    assert resp.status_code == 200
    assert resp.json()["code"] == 10002, resp.json()


def test_empty_student(client, seed):
    """U8：无任何数据的 student_c -> points=0 / streak=0 / 全部徽章未达成，不报错。"""
    resp = client.get(
        "/api/me/gamify", headers={"Authorization": f"Bearer {_token(_student_c_id(client))}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["points"] == 0
    assert data["streak_days"] == 0
    assert all(not b["earned"] for b in data["badges"])


def _student_c_id(client) -> int:
    """从库里取 student_c 的 id（造数时未保存，这里查一下）。"""
    with read_conn() as conn:
        return int(
            conn.execute(
                "SELECT id FROM users WHERE username = 'wp2k_c'"
            ).fetchone()[0]
        )
