"""WP-2D 课堂实时大屏 —— 验收测试。

跑法：
    cd backend && env -u PYTHONPATH .venv/Scripts/python.exe -m pytest app/tests/test_be2d_live_board.py -q

设计说明：
- 数据库直接 SQL 造（与 test_be07_stats 同思路）：大屏验的是**聚合口径与鉴权**，
  写入链路由 BE-03/BE-04 覆盖。
- **正确率反向验证**：用同一个 `UserService._accuracy` 在测试里算期望值，
  断言接口返回完全一致 —— 证明后端是「转发」而非「重写」（契约 §15.5 硬约束）。
- 鉴权：admin 通过 / 学生(非 admin) 被拒 `10003` / 缺 token 被拒 `10002`。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_2d_"))
_DB_FILE = _TMP_DIR / "test.db"

TEST_JWT_SECRET = "unit-test-secret-0123456789abcdef0123456789ab"
TEST_ADMIN_USERNAME = "admin"
TEST_ADMIN_PASSWORD = "unit-test-admin-pass"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = TEST_JWT_SECRET
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = TEST_ADMIN_USERNAME
os.environ["ADMIN_INIT_PASSWORD"] = TEST_ADMIN_PASSWORD
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import write_tx  # noqa: E402
from app.core.errors import ErrorCode  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.services.stats_service import beijing_day  # noqa: E402
from app.services.user_service import UserService  # noqa: E402


@pytest.fixture(autouse=True)
def _no_rate_limit():
    rate_limit.clear_all()
    yield


@pytest.fixture()
def client():
    """干净库 + 已启动应用（lifespan 跑迁移 + 建 admin）。"""
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = TEST_JWT_SECRET
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = TEST_ADMIN_USERNAME
    os.environ["ADMIN_INIT_PASSWORD"] = TEST_ADMIN_PASSWORD
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE, "DB_PATH 未生效，测试会写到真实数据库！"
    if _DB_FILE.exists():
        _DB_FILE.unlink()
    with TestClient(app) as c:
        yield c


# =========================================================================== 造数


class _Seed:
    class_id: int = 0
    s1: int = 0
    s2: int = 0


SEED = _Seed()


def _bj_utc_iso(day: str, hour: int = 12) -> str:
    """北京时间某天某时 → 入库用的 UTC ISO 串（保证 `date(datetime(created_at,'+8h'))` = day）。"""
    from datetime import datetime, timezone, timedelta

    dt = datetime.fromisoformat(f"{day}T{hour:02d}:00:00+08:00")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed():
    today = beijing_day(0)
    ts = _bj_utc_iso(today)
    with write_tx() as conn:
        cur = conn.execute(
            "INSERT INTO classes (name, grade, semester, school_year) VALUES (?,?,?,?)",
            ("三(1)班", 3, "first", "2026-2027"),
        )
        SEED.class_id = int(cur.lastrowid)

        cur = conn.execute(
            """
            INSERT INTO users (role, username, real_name, password_hash, class_id, register_source, status)
            VALUES ('student', ?, ?, 'x', ?, 'import', 'active')
            """,
            ("s001", "张三", SEED.class_id),
        )
        SEED.s1 = int(cur.lastrowid)

        cur = conn.execute(
            """
            INSERT INTO users (role, username, real_name, password_hash, class_id, register_source, status)
            VALUES ('student', ?, ?, 'x', ?, 'import', 'active')
            """,
            ("s002", "李四", SEED.class_id),
        )
        SEED.s2 = int(cur.lastrowid)

        # 每个学生的场次（answers.session_id 是 NOT NULL 外键）
        conn.execute(
            """
            INSERT INTO sessions (student_id, content_type, strategy, status, started_at)
            VALUES (?, 'english', 'normal', 'finished', ?)
            """,
            (SEED.s1, ts),
        )
        conn.execute(
            """
            INSERT INTO sessions (student_id, content_type, strategy, status, started_at)
            VALUES (?, 'english', 'normal', 'finished', ?)
            """,
            (SEED.s2, ts),
        )

        # s1 今日：3 exact + 1 wrong + 1 empty → counted=4, correct=3 → 0.75
        for mt in ("exact", "exact", "exact", "wrong", "empty"):
            conn.execute(
                """
                INSERT INTO answers (student_id, session_id, mode, step, expected, match_type, created_at)
                VALUES (?, 1, 'en2cn', 1, 'x', ?, ?)
                """,
                (SEED.s1, mt, ts),
            )
        # s2 今日：2 exact + 1 empty → counted=2, correct=2 → 1.0
        for mt in ("exact", "exact", "empty"):
            conn.execute(
                """
                INSERT INTO answers (student_id, session_id, mode, step, expected, match_type, created_at)
                VALUES (?, 2, 'en2cn', 1, 'x', ?, ?)
                """,
                (SEED.s2, mt, ts),
            )


# =========================================================================== 鉴权辅助


def _admin_headers(c: TestClient) -> dict:
    resp = c.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD, "role": "admin"},
    ).json()
    assert resp["code"] == 0, f"admin 登录失败：{resp}"
    return {"Authorization": f"Bearer {resp['data']['access_token']}"}


def _student_headers(c: TestClient) -> dict:
    token, _ = create_access_token(user_id=SEED.s1, role="student")
    return {"Authorization": f"Bearer {token}"}


# =========================================================================== 用例


def test_u1_admin_ok_shape(client):
    """admin 访问通过，响应形状为 {updated_at, classes, students}。"""
    _seed()
    body = client.get("/api/stats/live-board", headers=_admin_headers(client)).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert set(data.keys()) == {"updated_at", "classes", "students"}
    assert isinstance(data["updated_at"], str) and data["updated_at"].endswith("Z")
    assert isinstance(data["classes"], list) and isinstance(data["students"], list)


def test_u2_student_rejected(client):
    """非 admin（学生）→ 10003。"""
    _seed()
    body = client.get("/api/stats/live-board", headers=_student_headers(client)).json()
    assert body["code"] == ErrorCode.FORBIDDEN, body
    assert body["code"] == 10003


def test_u3_no_token_rejected(client):
    """无 token → 10002（UNAUTHORIZED）。"""
    _seed()
    body = client.get("/api/stats/live-board").json()
    assert body["code"] == 10002


def test_u4_class_not_found(client):
    """class_id 不存在 → 20004（CLASS_NOT_FOUND）。"""
    _seed()
    body = client.get(
        "/api/stats/live-board", params={"class_id": 999999}, headers=_admin_headers(client)
    ).json()
    assert body["code"] == 20004


def test_u5_accuracy_forwards_user_service(client):
    """正确率**转发** UserService._accuracy —— 用同一函数算期望并对齐（反向验证）。

    s1: correct=3, counted=4 → 0.75；s2: correct=2, counted=2 → 1.0。
    班级 today_accuracy = (3+2)/(4+2) = 5/6。
    """
    _seed()
    body = client.get("/api/stats/live-board", headers=_admin_headers(client)).json()["data"]

    by_student = {s["student_id"]: s for s in body["students"]}
    assert by_student[SEED.s1]["today_accuracy"] == UserService._accuracy(3, 4)
    assert by_student[SEED.s1]["accuracy"] == UserService._accuracy(3, 4)
    assert by_student[SEED.s2]["today_accuracy"] == UserService._accuracy(2, 2)
    assert by_student[SEED.s2]["accuracy"] == UserService._accuracy(2, 2)

    # 班级 rollup 同样走转发，不是重新平均
    klass = next(c for c in body["classes"] if c["class_id"] == SEED.class_id)
    assert klass["today_accuracy"] == UserService._accuracy(5, 6)
    assert klass["today_item_count"] == 8  # 5 + 3
    assert klass["today_active_students"] == 2


def test_u6_class_filter_scopes_data(client):
    """class_id 过滤：只返回该班级及其学生。"""
    _seed()
    # 再建一个空班级，验证过滤不会混入
    with write_tx() as conn:
        conn.execute(
            "INSERT INTO classes (name, grade, semester, school_year) VALUES (?,?,?,?)",
            ("空班级", 3, "first", "2026-2027"),
        )
    body = client.get(
        "/api/stats/live-board",
        params={"class_id": SEED.class_id},
        headers=_admin_headers(client),
    ).json()["data"]
    assert len(body["classes"]) == 1
    assert body["classes"][0]["class_id"] == SEED.class_id
    assert {s["student_id"] for s in body["students"]} == {SEED.s1, SEED.s2}
