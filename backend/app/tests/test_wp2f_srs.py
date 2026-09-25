"""WP-2F 间隔重复 SRS —— 验收测试（含反向验证）。

覆盖（契约《50-二期工作包拆解》§3 / §9 + §15.2）：
- U1 到期筛选：`due` 只回 `due_at <= 现在` 的计划，按 `due_at` 升序；
- U2 无到期 → `{items: []}`，**不报错**（code=0）；
- U3 `limit` 生效（`due` 截断）；
- U4 SM-2 推进：连续 grade=5 → 间隔 1 → 6 → 16，reps 递增，ease 不低于下限；
- U5 grade 影响：grade<3（遗忘）重置 reps/lapses、间隔回 1；grade>=3 推进；
- U6 找不到计划 → `70004`（HTTP 200）；
- U7 **越权/隔离**：B 复习 A 的计划 → `70004`，且 A 的计划不被改动；
- U8 **due 学生隔离反向验证**：摘掉 `student_id` 过滤必须转红；
- U9 `grade` 越界 → `10001`；`limit` 越界 → `10001`；缺 word_id → `10001`；
- U10 未带 token → `10002`。

反向验证说明：
- `test_review_other_student_isolated`：B 复习 A 的词 → 70004，且断言 A 的 `reps` 仍是
  测试开头写好的已知值。若有人把 `review` 里的 `student_id = ?` 去掉，B 会改到 A 的计划，
  断言转红。
- `test_due_student_isolation_reverse`：A 的到期数严格小于「全局到期数」。
  若 `due` 漏掉 `student_id = ?`，A 会看到 B 的到期项，断言 `a_count < global_count` 转红。

每个测试用**全新空库**（function-scoped），避免 review 把 `due_at` 推到未来而互相污染。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_wp2f_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "wp2f-srs-secret-0123456789abcdef"
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "wp2f-Admin-Pass"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx, utc_now_iso  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402


# ---------------------------------------------------------------------------
# fixture：每个测试一个全新空库
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_env():
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = "wp2f-srs-secret-0123456789abcdef"
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = "admin"
    os.environ["ADMIN_INIT_PASSWORD"] = "wp2f-Admin-Pass"
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE, "DB_PATH 被同进程里别的测试模块改掉了"
    yield


@pytest.fixture()
def client():
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(_DB_FILE) + suffix)
        if path.exists():
            path.unlink()
    rate_limit.clear_all()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# 造数工具
# ---------------------------------------------------------------------------


def _iso_offset(days: int) -> str:
    """相对于**现在**偏移 days 天的 UTC ISO 串（负数=过去=已到期）。"""
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class Seed:
    student_a: int = 0
    student_b: int = 0
    unit: int = 0
    word_a1: int = 0
    word_a2: int = 0
    word_a_future: int = 0
    word_b1: int = 0


def _seed(client: TestClient) -> Seed:
    """造两名学生 + 一个单元 + 4 个词 + 复习计划。

    A：word_a1(-3d 到期)、word_a2(-1d 到期)、word_a_future(+3d 未到期)
    B：word_b1(-5d 到期)
    """
    s = Seed()
    with write_tx() as conn:
        cur = conn.execute(
            "INSERT INTO users (role, username, real_name, password_hash) VALUES (?,?,?,?)",
            ("student", "wp2f_a", "学生A", "x"),
        )
        s.student_a = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO users (role, username, real_name, password_hash) VALUES (?,?,?,?)",
            ("student", "wp2f_b", "学生B", "x"),
        )
        s.student_b = int(cur.lastrowid)

        cur = conn.execute(
            "INSERT INTO units (subject, grade, semester, name, order_no) "
            "VALUES ('english', 7, 'first', 'WP2F单元', 0)",
        )
        s.unit = int(cur.lastrowid)

        def add_word(en: str, cn: str) -> int:
            c = conn.execute(
                "INSERT INTO words (unit_id, type, en, cn) VALUES (?, 'word', ?, ?)",
                (s.unit, en, cn),
            )
            return int(c.lastrowid)

        s.word_a1 = add_word("apple", "苹果")
        s.word_a2 = add_word("banana", "香蕉")
        s.word_a_future = add_word("cat", "猫")
        s.word_b1 = add_word("dog", "狗")

        def add_plan(student_id: int, word_id: int, due_at: str,
                     interval_days: int = 1, ease: float = 2.5,
                     reps: int = 0, lapses: int = 0) -> None:
            conn.execute(
                """
                INSERT INTO srs_review_plan
                    (student_id, unit_id, word_id, due_at, interval_days, ease, reps, lapses)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (student_id, s.unit, word_id, due_at, interval_days, ease, reps, lapses),
            )

        add_plan(s.student_a, s.word_a1, _iso_offset(-3), interval_days=1, reps=0)
        add_plan(s.student_a, s.word_a2, _iso_offset(-1), interval_days=1, reps=0)
        add_plan(s.student_a, s.word_a_future, _iso_offset(3), interval_days=1, reps=0)
        add_plan(s.student_b, s.word_b1, _iso_offset(-5), interval_days=1, reps=0)
    return s


def _token(student_id: int) -> str:
    token, _ = create_access_token(user_id=student_id, role="student")
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# U1 + U2 + U3：到期筛选 / 空 / limit
# ---------------------------------------------------------------------------


def test_due_returns_only_overdue_ordered(client):
    """U1：只回已到期计划，按 due_at 升序。A 有 2 条到期，1 条未到期不出现。"""
    s = _seed(client)
    resp = client.get("/api/me/srs/due?limit=20", headers=_auth(_token(s.student_a)))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0, body
    items = body["data"]["items"]
    ids = [it["word_id"] for it in items]
    assert ids == [s.word_a1, s.word_a2], ids
    assert items[0]["due_at"] <= items[1]["due_at"]


def test_due_empty_when_none(client):
    """U2：没有任何计划的学生 due → 空列表，不报错。"""
    s = _seed(client)
    token = _token(s.student_a)
    # 顺手造一个干净学生
    with write_tx() as conn:
        cur = conn.execute(
            "INSERT INTO users (role, username, real_name, password_hash) VALUES (?,?,?,?)",
            ("student", "wp2f_clean", "干净生", "x"),
        )
        clean_id = int(cur.lastrowid)
    resp = client.get("/api/me/srs/due", headers=_auth(_token(clean_id)))
    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["items"] == []


def test_due_respects_limit(client):
    """U3：limit=1 只回 1 条。"""
    s = _seed(client)
    resp = client.get("/api/me/srs/due?limit=1", headers=_auth(_token(s.student_a)))
    assert resp.json()["code"] == 0
    assert len(resp.json()["data"]["items"]) == 1


def test_due_includes_word_display_fields(client):
    """到期项带展示字段（JOIN words/units），学生端无需二次请求。"""
    s = _seed(client)
    resp = client.get("/api/me/srs/due?limit=20", headers=_auth(_token(s.student_a)))
    item = resp.json()["data"]["items"][0]
    assert item["en"] == "apple"
    assert item["cn"] == "苹果"
    assert item["unit_name"] == "WP2F单元"
    assert item["type"] == "word"


# ---------------------------------------------------------------------------
# U4 + U5：SM-2 推进 / grade 影响
# ---------------------------------------------------------------------------


def test_sm2_progression_grade5(client):
    """U4：连续 grade=5 → 间隔 1 → 6 → 16，reps 0→1→2→3，ease 不低于下限。"""
    s = _seed(client)
    token = _token(s.student_a)
    word = s.word_a1

    r1 = client.post(
        "/api/me/srs/review", json={"word_id": word, "grade": 5}, headers=_auth(token)
    ).json()
    assert r1["code"] == 0, r1
    assert r1["data"]["interval_days"] == 1
    assert r1["data"]["reps"] == 1
    assert r1["data"]["ease"] >= 1.3

    r2 = client.post(
        "/api/me/srs/review", json={"word_id": word, "grade": 5}, headers=_auth(token)
    ).json()
    assert r2["data"]["interval_days"] == 6
    assert r2["data"]["reps"] == 2

    r3 = client.post(
        "/api/me/srs/review", json={"word_id": word, "grade": 5}, headers=_auth(token)
    ).json()
    assert r3["data"]["interval_days"] == 16
    assert r3["data"]["reps"] == 3

    ivals = [r1["data"]["interval_days"], r2["data"]["interval_days"], r3["data"]["interval_days"]]
    assert ivals == sorted(ivals)
    assert r3["data"]["ease"] >= 1.3

    next_due = datetime.strptime(r3["data"]["next_due"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    expected = datetime.now(timezone.utc) + timedelta(days=16)
    assert abs((next_due - expected).total_seconds()) < 60


def test_grade_impact_lapse_vs_pass(client):
    """U5：相同起点下，grade=0（遗忘）与 grade=5（通过）产生不同结果。"""
    s = _seed(client)
    token = _token(s.student_a)
    word = s.word_a2
    # 起点：reps=2, interval=6, ease=2.5, lapses=0
    with write_tx() as conn:
        conn.execute(
            "UPDATE srs_review_plan SET interval_days=6, ease=2.5, reps=2, lapses=0, due_at=? "
            "WHERE student_id=? AND word_id=?",
            (_iso_offset(-1), s.student_a, word),
        )

    # 遗忘（grade=0）：reps 归零、lapses+1、间隔回 1、ease 下降（>=下限）
    r_lapse = client.post(
        "/api/me/srs/review", json={"word_id": word, "grade": 0}, headers=_auth(token)
    ).json()
    assert r_lapse["code"] == 0, r_lapse
    assert r_lapse["data"]["reps"] == 0
    assert r_lapse["data"]["lapses"] == 1
    assert r_lapse["data"]["interval_days"] == 1
    assert r_lapse["data"]["ease"] >= 1.3

    # 重新摆回起点再测通过
    with write_tx() as conn:
        conn.execute(
            "UPDATE srs_review_plan SET interval_days=6, ease=2.5, reps=2, lapses=0, due_at=? "
            "WHERE student_id=? AND word_id=?",
            (_iso_offset(-1), s.student_a, word),
        )
    r_pass = client.post(
        "/api/me/srs/review", json={"word_id": word, "grade": 5}, headers=_auth(token)
    ).json()
    assert r_pass["data"]["reps"] == 3
    assert r_pass["data"]["lapses"] == 0
    assert r_pass["data"]["interval_days"] == 15  # round(6 * 2.5)
    # grade 确实影响调度
    assert r_lapse["data"]["reps"] != r_pass["data"]["reps"]
    assert r_lapse["data"]["interval_days"] != r_pass["data"]["interval_days"]


# ---------------------------------------------------------------------------
# U6 + U7：找不到 / 越权 → 70004
# ---------------------------------------------------------------------------


def test_review_not_found_returns_70004(client):
    """U6：复习一个对当前学生**不存在**的计划 → 70004，HTTP 仍 200。"""
    s = _seed(client)
    # 用 A 的 token 去复习 B 独占的 word_b1 → 对 A 而言「不存在」→ 70004
    resp = client.post(
        "/api/me/srs/review",
        json={"word_id": s.word_b1, "grade": 5},
        headers=_auth(_token(s.student_a)),
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == 70004, resp.json()
    assert resp.json()["data"] is None


def test_review_other_student_isolated(client):
    """U7（越权 + 反向验证）：B 复习 A 的词 → 70004，且 A 的计划不被改动。

    反向验证：把 `srs.py` 的 `review` 查询里的 `student_id = ?` 去掉后，
    B 会改到 A 的计划（A 的 `reps` 从 0 变 1），下面 `assert reps == 0` 立即转红。
    """
    s = _seed(client)
    # 把 A 的 word_a1 明确摆成 reps=0 的已知状态
    with write_tx() as conn:
        conn.execute(
            "UPDATE srs_review_plan SET interval_days=1, ease=2.5, reps=0, lapses=0, due_at=? "
            "WHERE student_id=? AND word_id=?",
            (_iso_offset(-3), s.student_a, s.word_a1),
        )

    resp = client.post(
        "/api/me/srs/review",
        json={"word_id": s.word_a1, "grade": 5},
        headers=_auth(_token(s.student_b)),
    )
    assert resp.json()["code"] == 70004, resp.json()
    assert resp.json()["data"] is None

    # A 的计划应保持原样（reps 仍是 0，未被 B 的越权请求改变）
    with read_conn() as conn:
        row = conn.execute(
            "SELECT reps FROM srs_review_plan WHERE student_id=? AND word_id=?",
            (s.student_a, s.word_a1),
        ).fetchone()
    assert int(row["reps"]) == 0, "B 的越权复习竟然改动了 A 的计划！student_id 过滤疑似失效"


# ---------------------------------------------------------------------------
# U8：due 学生隔离反向验证
# ---------------------------------------------------------------------------


def test_due_student_isolation_reverse(client):
    """U8（反向验证）：A 的到期数严格小于「全局到期数」。

    若 `due_items` 漏掉 `WHERE student_id = ?`，A 会看到 B 的到期项，
    `a_count < global_count` 将不成立 → 转红。
    """
    s = _seed(client)
    a_items = client.get(
        "/api/me/srs/due?limit=200", headers=_auth(_token(s.student_a))
    ).json()["data"]["items"]
    b_items = client.get(
        "/api/me/srs/due?limit=200", headers=_auth(_token(s.student_b))
    ).json()["data"]["items"]

    assert {it["word_id"] for it in a_items} == {s.word_a1, s.word_a2}
    assert {it["word_id"] for it in b_items} == {s.word_b1}

    a_count = len(a_items)
    with read_conn() as conn:
        global_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM srs_review_plan WHERE due_at <= ?",
                (utc_now_iso(),),
            ).fetchone()[0]
        )
    # 全局 = A 的 2 条 + B 的 1 条 = 3；A 只应看到自己的 2 条
    assert global_count == 3, f"全局到期数应为 3，实际 {global_count}"
    assert a_count == 2, f"A 的到期数应为 2，实际 {a_count}"
    assert a_count < global_count, "A 看到了别人的到期计划！student_id 过滤疑似失效"


# ---------------------------------------------------------------------------
# U9：参数校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grade", [-1, 6, 100, "x"])
def test_grade_validation(client, grade):
    """U9：grade 越界（<0 / >5 / 非整数）→ 10001。"""
    s = _seed(client)
    resp = client.post(
        "/api/me/srs/review",
        json={"word_id": s.word_a1, "grade": grade},
        headers=_auth(_token(s.student_a)),
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == 10001, resp.json()


@pytest.mark.parametrize("limit", ["0", "-1", "201", "abc"])
def test_limit_validation(client, limit):
    """U9：due 的 limit 越界 → 10001。"""
    s = _seed(client)
    resp = client.get(f"/api/me/srs/due?limit={limit}", headers=_auth(_token(s.student_a)))
    assert resp.status_code == 200
    assert resp.json()["code"] == 10001, resp.json()


def test_review_missing_word_id(client):
    """U9 补充：缺 word_id → 10001。"""
    s = _seed(client)
    resp = client.post(
        "/api/me/srs/review", json={"grade": 5}, headers=_auth(_token(s.student_a))
    )
    assert resp.json()["code"] == 10001, resp.json()


# ---------------------------------------------------------------------------
# U10：未带 token
# ---------------------------------------------------------------------------


def test_auth_required(client):
    """U10：未带 token → 10002，HTTP 200。"""
    _seed(client)
    resp = client.get("/api/me/srs/due")
    assert resp.status_code == 200
    assert resp.json()["code"] == 10002, resp.json()
    resp2 = client.post("/api/me/srs/review", json={"word_id": 1, "grade": 5})
    assert resp2.json()["code"] == 10002, resp2.json()
