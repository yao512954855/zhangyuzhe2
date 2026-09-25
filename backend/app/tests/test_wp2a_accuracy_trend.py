"""WP-2A 个人正确率趋势 —— 验收测试（含反向验证）。

直接 SQL 造数（不走写入接口）：本包验的是**聚合口径与越权隔离**，不是写入链路。

覆盖：
- U1 响应形状 / 默认天数 30；
- U2 逐日正确率公式 = `UserService._accuracy()`（分母是「剔除 empty 的有效题数」，不是总答题数）；
- U3 北京时间切天：北京时间 07:30（UTC 前一天 23:30）必须落进北京当天，而非 UTC 当天；
- U4 缺失天补零（accuracy=0.0, answered=0, correct=0）；
- U5 **学生隔离反向验证**：摘掉 `WHERE student_id = ?` 必须让用例转红；
- U6 未带 token → 10002（HTTP 200）；
- U7 `days` 校验：<=0 / 非整数 / 超上限 → 10001。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_wp2a_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "test-secret-" + "0" * 32
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "CHANGE_ME_admin_password"
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402
from app.services.stats_service import _BJ, beijing_day as _system_today  # noqa: E402
from app.services.stats.trend_service import MAX_TREND_DAYS  # noqa: E402
from app.services.stats.trend_service import accuracy_trend  # noqa: E402
from app.services.user_service import UserService  # noqa: E402


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
        SettingsService.refresh()
        yield c


# =========================================================================== 造数


def _utc(bj_day: str, hour: int = 12, minute: int = 0) -> str:
    """给定**北京时间**某天某时，返回入库用的 UTC ISO 串（U3 靠它制造跨日样本）。"""
    dt = datetime.fromisoformat(f"{bj_day}T{hour:02d}:{minute:02d}:00+08:00")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Seed:
    today: str = ""
    student_a: int = 0
    student_b: int = 0
    sess_a: int = 0
    sess_b: int = 0


SEED = Seed()


def beijing_day(offset_days: int = 0) -> str:
    """锚定到造数时刻的北京今天，不随运行时刻漂移。"""
    base = SEED.today or _system_today(0)
    return (date.fromisoformat(base) + timedelta(days=offset_days)).isoformat()


def _insert_answer(conn, student_id, session_id, bj_day, match_type, hour=12, minute=0):
    conn.execute(
        """
        INSERT INTO answers (session_id, student_id, mode, step, expected, match_type, created_at)
        VALUES (?, ?, 'cn2en', 1, 'cat', ?, ?)
        """,
        (session_id, student_id, match_type, _utc(bj_day, hour, minute)),
    )


@pytest.fixture(scope="module")
def seed(client):
    """造两名学生的可控答题记录。

    学生 A：
      D-3：[exact, exact]                  -> answered=2 counted=2 correct=2 acc=1.0
      D-2：(无)                             -> 补零
      D-1：[exact, fuzzy, wrong, wrong, empty] -> answered=5 counted=4 correct=2 acc=0.5
      D0 ：[exact, exact, loose, empty]      + 北京时间 07:30 的 [exact]（UTC 前一天 23:30）
            -> answered=5 counted=4 correct=4 acc=1.0（07:30 必须算进 D0，不能漏进 D-1）
    学生 B（隔离验证用，只 D0 有 3 条 exact）。
    """
    SEED.today = _system_today(0)
    today = SEED.today

    with write_tx() as conn:
        cur = conn.execute(
            "INSERT INTO users (role, username, real_name, password_hash) VALUES (?,?,?,?)",
            ("student", "wp2a_a", "学生A", "x"),
        )
        SEED.student_a = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO users (role, username, real_name, password_hash) VALUES (?,?,?,?)",
            ("student", "wp2a_b", "学生B", "x"),
        )
        SEED.student_b = int(cur.lastrowid)

        cur = conn.execute(
            "INSERT INTO sessions (student_id, content_type, strategy, total_items, correct_items) "
            "VALUES (?, 'english', 'review', 0, 0)",
            (SEED.student_a,),
        )
        SEED.sess_a = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO sessions (student_id, content_type, strategy, total_items, correct_items) "
            "VALUES (?, 'english', 'review', 0, 0)",
            (SEED.student_b,),
        )
        SEED.sess_b = int(cur.lastrowid)

        # 学生 A
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(-3), "exact")
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(-3), "exact")
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(-1), "exact")
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(-1), "fuzzy")
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(-1), "wrong")
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(-1), "wrong")
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(-1), "empty")
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(0), "exact")
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(0), "exact")
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(0), "loose")
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(0), "empty")
        # U3 跨日边界：北京时间 07:30 -> UTC 前一天 23:30
        _insert_answer(conn, SEED.student_a, SEED.sess_a, beijing_day(0), "exact", hour=7, minute=30)

        # 学生 B（仅 D0）
        _insert_answer(conn, SEED.student_b, SEED.sess_b, beijing_day(0), "exact")
        _insert_answer(conn, SEED.student_b, SEED.sess_b, beijing_day(0), "exact")
        _insert_answer(conn, SEED.student_b, SEED.sess_b, beijing_day(0), "exact")

    return SEED


def _token(student_id: int) -> str:
    token, _ = create_access_token(user_id=student_id, role="student")
    return token


def _point_map(points):
    return {p["date"]: p for p in points}


# =========================================================================== 用例


def test_happy_shape_and_default_days(client, seed):
    """U1：带 token 访问返回 200/code=0，points 形状正确，默认 30 天。"""
    token = _token(SEED.student_a)
    resp = client.get(
        "/api/me/accuracy-trend",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0, body
    points = body["data"]["points"]
    assert isinstance(points, list)
    assert len(points) == 30, f"默认应为 30 天，实际 {len(points)}"

    keys = {"date", "accuracy", "answered", "correct"}
    for p in points:
        assert set(p.keys()) == keys, p
        assert isinstance(p["date"], str) and p["date"]
        assert isinstance(p["answered"], int) and isinstance(p["correct"], int)
        assert isinstance(p["accuracy"], float)
        assert 0.0 <= p["accuracy"] <= 1.0


def test_accuracy_formula_and_zero_fill(client, seed):
    """U2 + U4：逐日正确率 = UserService._accuracy(correct, counted)，缺失天补零。"""
    token = _token(SEED.student_a)
    resp = client.get(
        "/api/me/accuracy-trend?days=4",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0, body
    points = body["data"]["points"]

    # days=4 -> [D-3, D-2, D-1, D0]，升序
    assert [p["date"] for p in points] == [
        beijing_day(-3),
        beijing_day(-2),
        beijing_day(-1),
        beijing_day(0),
    ]

    m = _point_map(points)

    # D-3: 2 exact
    d3 = m[beijing_day(-3)]
    assert d3["answered"] == 2 and d3["correct"] == 2
    assert d3["accuracy"] == UserService._accuracy(2, 2) == 1.0

    # D-2: 无数据 -> 补零
    d2 = m[beijing_day(-2)]
    assert d2["answered"] == 0 and d2["correct"] == 0
    assert d2["accuracy"] == 0.0

    # D-1: exact, fuzzy, wrong, wrong, empty -> 分母剔除 empty
    d1 = m[beijing_day(-1)]
    assert d1["answered"] == 5 and d1["correct"] == 2
    assert d1["accuracy"] == UserService._accuracy(2, 4) == 0.5

    # D0: 4 条 + 07:30 跨日 1 条 exact
    d0 = m[beijing_day(0)]
    assert d0["answered"] == 5 and d0["correct"] == 4
    assert d0["accuracy"] == UserService._accuracy(4, 4) == 1.0


def test_beijing_day_boundary(client, seed):
    """U3：北京时间 07:30（UTC 前一天 23:30）的题必须计入 D0，而非 D-1。

    若改用 UTC 日期分桶，这条会被算进 D-1，使 D-1 answered 变成 6。
    """
    token = _token(SEED.student_a)
    resp = client.get(
        "/api/me/accuracy-trend?days=4",
        headers={"Authorization": f"Bearer {token}"},
    )
    points = resp.json()["data"]["points"]
    m = _point_map(points)
    # D-1 不应包含那条 07:30 的题
    assert m[beijing_day(-1)]["answered"] == 5
    # D0 包含它
    assert m[beijing_day(0)]["answered"] == 5


def test_student_isolation_reverse_validation(client, seed):
    """U5（反向验证）：摘掉 student_id 过滤必须让本用例转红。

    学生 B 在 D0 有 3 条答案。若服务漏掉 `WHERE student_id = ?`，学生 A 的 D0
    answered 会变成 5(A) + 3(B) = 8；这里断言 A 只拿到自己的 5 条，且与「无过滤全局合计」
    不相等 —— 二者任一不成立即说明过滤失效。
    """
    token_a = _token(SEED.student_a)
    token_b = _token(SEED.student_b)

    resp_a = client.get(
        "/api/me/accuracy-trend?days=4",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    resp_b = client.get(
        "/api/me/accuracy-trend?days=4",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_a.json()["code"] == 0 and resp_b.json()["code"] == 0
    a_d0 = _point_map(resp_a.json()["data"]["points"])[beijing_day(0)]
    b_d0 = _point_map(resp_b.json()["data"]["points"])[beijing_day(0)]

    # 各自只看到自己的数据
    assert a_d0["answered"] == 5, a_d0
    assert b_d0["answered"] == 3, b_d0

    # 跨用户不串：A 的 D0 不等于 B 的 D0
    assert a_d0["answered"] != b_d0["answered"]

    # 反向验证核心：直接跑「无 student_id 过滤」的全局合计，必须 > A 看到的值。
    # 若有人删掉 accuracy_trend 里的 WHERE student_id，下面这个全局合计会等于
    # a_d0 看到的值，断言 `a_d0 < global_total` 将失败 -> 转红。
    with read_conn() as conn:
        global_d0 = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM answers
                WHERE date(datetime(created_at, ?)) = ?
                """,
                (_BJ, beijing_day(0)),
            ).fetchone()[0]
        )
    assert global_d0 == 8, f"全局 D0 应为 8（A5+B3），实际 {global_d0}"
    assert a_d0["answered"] < global_d0, "A 看到了别人的数据！student_id 过滤疑似失效"


def test_auth_required(client, seed):
    """U6：未带 token 访问受保护路由 -> 10002，且 HTTP 200。"""
    resp = client.get("/api/me/accuracy-trend")
    assert resp.status_code == 200
    assert resp.json()["code"] == 10002, resp.json()


@pytest.mark.parametrize(
    "bad_days, expect_code",
    [
        ("0", 10001),
        ("-3", 10001),
        ("abc", 10001),
        (str(MAX_TREND_DAYS + 1), 10001),
    ],
)
def test_days_validation(client, seed, bad_days, expect_code):
    """U7：days 非法（<=0 / 非整数 / 超上限）-> 10001。"""
    token = _token(SEED.student_a)
    resp = client.get(
        f"/api/me/accuracy-trend?days={bad_days}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == expect_code, resp.json()


def test_accuracy_forwards_user_service_formula(client, seed):
    """U2 补充：服务层 accuracy_trend 与 UserService._accuracy 逐日完全一致。

    用一份**独立**的逐日聚合（同样的 student_id 过滤 + 同样的 _BJ 切天）重新算
    answered/counted/correct，再与接口返回的 point 逐项对账 —— 不依赖 seed 注释里的手算值。
    """
    token = _token(SEED.student_a)
    resp = client.get(
        "/api/me/accuracy-trend?days=4",
        headers={"Authorization": f"Bearer {token}"},
    )
    points = resp.json()["data"]["points"]
    start, end = beijing_day(-3), beijing_day(0)

    with read_conn() as conn:
        rows = conn.execute(
            """
            SELECT date(datetime(created_at, ?)) AS d,
                   COUNT(*)                                              AS answered,
                   SUM(CASE WHEN match_type <> 'empty' THEN 1 ELSE 0 END) AS counted,
                   SUM(CASE WHEN match_type IN ('exact','loose','fuzzy')
                            THEN 1 ELSE 0 END)                           AS correct
            FROM answers
            WHERE student_id = ?
              AND date(datetime(created_at, ?)) BETWEEN ? AND ?
            GROUP BY d
            """,
            (_BJ, SEED.student_a, _BJ, start, end),
        ).fetchall()
    raw = {
        str(r["d"]): {
            "answered": int(r["answered"] or 0),
            "counted": int(r["counted"] or 0),
            "correct": int(r["correct"] or 0),
        }
        for r in rows
    }

    for p in points:
        exp = raw.get(p["date"], {"answered": 0, "counted": 0, "correct": 0})
        assert p["answered"] == exp["answered"], p
        assert p["correct"] == exp["correct"], p
        assert p["accuracy"] == UserService._accuracy(exp["correct"], exp["counted"]), p
