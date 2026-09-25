"""WP-2G 作业布置与截止提醒 —— 验收测试（含反向验证）。

覆盖：
- U1 管理端创建 → 列表可见（分页，items 含派生字段）；
- U2 管理端 PATCH 修改标题/状态/截止时间；
- U3 管理端 DELETE 后不存在；
- U4 `20004` 班级不存在（POST）；
- U5 `70003` 截止时间早于现在（POST / PATCH）；
- U6 `70002` 作业不存在（PATCH / DELETE）；
- U7 `10003` 非 admin 调管理端接口被拒（HTTP 200 + 码 10003）；
- U8 **学生只看自己班级** 作业（契约硬约束）；
- U9 **反向验证**：撤掉 class_id 过滤会让用例转红；
- U10 未分班学生返回空列表，不报错；
- U11 学生提交状态：已生成的 submission(done) 正确回显；
- U12 未带 token → `10002`（HTTP 200）。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_wp2g_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "wp2g-homework-secret-0123456789ab"
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "wp2g-Admin-Pass"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """每个测试一个全新空库 + 干净限流桶 + 干净配置缓存。"""
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = "wp2g-homework-secret-0123456789ab"
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = "admin"
    os.environ["ADMIN_INIT_PASSWORD"] = "wp2g-Admin-Pass"
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE, "DB_PATH 被同进程里别的测试模块改掉了"

    for suffix in ("", "-wal", "-shm"):
        path = Path(str(_DB_FILE) + suffix)
        if path.exists():
            path.unlink()

    rate_limit.clear_all()

    with TestClient(app) as c:
        yield c


class Seed:
    admin_id = 0
    class_1 = 0
    class_2 = 0
    student_a = 0  # 在 class_1
    student_b = 0  # 在 class_2
    student_c = 0  # 未分班


SEED = Seed()


def _insert(conn, sql: str, params: tuple = ()) -> int:
    return int(conn.execute(sql, params).lastrowid)


@pytest.fixture()
def seed(client):
    """建管理员、两个班级、三名学生（A 在 1 班、B 在 2 班、C 未分班）。

    每测试一个全新库（client 为 function 作用域），故这里也用 function 作用域，
    避免跨测试数据累积、也避免 module 作用域依赖 function 作用域 client 的 scope 冲突。
    """
    with write_tx() as conn:
        SEED.admin_id = _insert(
            conn,
            "INSERT INTO users (role, username, real_name, password_hash, status) "
            "VALUES ('admin','admin_wp2g','管理者','x','active')",
        )
        SEED.class_1 = _insert(
            conn,
            "INSERT INTO classes (name, grade, semester, status) VALUES (?,?,?,?)",
            ("一班", 7, "first", "active"),
        )
        SEED.class_2 = _insert(
            conn,
            "INSERT INTO classes (name, grade, semester, status) VALUES (?,?,?,?)",
            ("二班", 7, "first", "active"),
        )
        SEED.student_a = _insert(
            conn,
            "INSERT INTO users (role, username, real_name, password_hash, class_id, status) "
            "VALUES ('student','a','学生A','x',?, 'active')",
            (SEED.class_1,),
        )
        SEED.student_b = _insert(
            conn,
            "INSERT INTO users (role, username, real_name, password_hash, class_id, status) "
            "VALUES ('student','b','学生B','x',?, 'active')",
            (SEED.class_2,),
        )
        SEED.student_c = _insert(
            conn,
            "INSERT INTO users (role, username, real_name, password_hash, status) "
            "VALUES ('student','c','学生C','x','active')",
        )
    return SEED


def _admin_token() -> str:
    token, _ = create_access_token(user_id=SEED.admin_id, role="admin")
    return token


def _student_token(user_id: int) -> str:
    token, _ = create_access_token(user_id=user_id, role="student")
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _count_active_homework_all_classes() -> int:
    with read_conn() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM homework WHERE status = 'active'"
            ).fetchone()["n"]
        )


# ---------------------------------------------------------------------------
# U1 创建 + 列表
# ---------------------------------------------------------------------------


def test_admin_create_and_list(client, seed):
    """U1：admin 创建作业后，列表能查到，且含派生字段。"""
    resp = client.post(
        "/api/homework",
        headers=_auth(_admin_token()),
        json={
            "class_id": SEED.class_1,
            "title": "背诵第一课",
            "subject": "chinese",
            "unit_ids": [11, 12],
            "due_at": "2099-01-01T00:00:00Z",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0, body
    hw_id = body["data"]["id"]
    assert isinstance(hw_id, int) and hw_id > 0

    # 列表
    resp = client.get("/api/homework", headers=_auth(_admin_token()))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0, body
    items = body["data"]["items"]
    assert body["data"]["total"] >= 1

    item = next(i for i in items if i["id"] == hw_id)
    assert item["title"] == "背诵第一课"
    assert item["subject"] == "chinese"
    assert item["unit_ids"] == [11, 12]
    assert item["class_id"] == SEED.class_1
    assert item["class_name"] == "一班"
    assert item["status"] == "active"
    # 派生字段存在且为 int
    assert isinstance(item["submitted_count"], int)
    assert isinstance(item["student_count"], int)
    # 一班只有 A 一名学生在读 -> student_count == 1
    assert item["student_count"] == 1


# ---------------------------------------------------------------------------
# U4 / U5 失败分支
# ---------------------------------------------------------------------------


def test_create_class_not_found(client, seed):
    """U4：班级不存在 → 20004（HTTP 200）。"""
    resp = client.post(
        "/api/homework",
        headers=_auth(_admin_token()),
        json={"class_id": 99999, "title": "x", "subject": "chinese", "unit_ids": []},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == ErrorCodeWrap.CLASS_NOT_FOUND, resp.json()


def test_create_due_in_past(client, seed):
    """U5：截止时间早于现在 → 70003（HTTP 200）。"""
    resp = client.post(
        "/api/homework",
        headers=_auth(_admin_token()),
        json={
            "class_id": SEED.class_1,
            "title": "过期作业",
            "subject": "chinese",
            "unit_ids": [],
            "due_at": "2000-01-01T00:00:00Z",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == ErrorCodeWrap.HOMEWORK_EXPIRED, resp.json()


def test_create_invalid_subject(client, seed):
    """subject 非法 → 10001（由 service 校验）。"""
    resp = client.post(
        "/api/homework",
        headers=_auth(_admin_token()),
        json={"class_id": SEED.class_1, "title": "x", "subject": "math", "unit_ids": []},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == ErrorCodeWrap.PARAM_INVALID, resp.json()


# ---------------------------------------------------------------------------
# U2 PATCH
# ---------------------------------------------------------------------------


def test_patch_update(client, seed):
    """U2：PATCH 能改标题 / 状态 / 截止时间。"""
    created = client.post(
        "/api/homework",
        headers=_auth(_admin_token()),
        json={"class_id": SEED.class_1, "title": "旧标题", "subject": "english", "unit_ids": []},
    ).json()["data"]["id"]

    resp = client.patch(
        f"/api/homework/{created}",
        headers=_auth(_admin_token()),
        json={"title": "新标题", "status": "closed", "due_at": "2099-05-05T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == 0, resp.json()
    d = resp.json()["data"]
    assert d["title"] == "新标题"
    assert d["status"] == "closed"
    assert d["due_at"] == "2099-05-05T00:00:00Z"


def test_patch_not_found(client, seed):
    """U6：PATCH 不存在的作业 → 70002。"""
    resp = client.patch(
        "/api/homework/99999",
        headers=_auth(_admin_token()),
        json={"title": "x"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == ErrorCodeWrap.HOMEWORK_NOT_FOUND, resp.json()


def test_patch_due_in_past(client, seed):
    """U5：PATCH 把截止时间改到过去 → 70003。"""
    created = client.post(
        "/api/homework",
        headers=_auth(_admin_token()),
        json={"class_id": SEED.class_1, "title": "p", "subject": "english", "unit_ids": []},
    ).json()["data"]["id"]
    resp = client.patch(
        f"/api/homework/{created}",
        headers=_auth(_admin_token()),
        json={"due_at": "2000-01-01T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == ErrorCodeWrap.HOMEWORK_EXPIRED, resp.json()


# ---------------------------------------------------------------------------
# U3 DELETE
# ---------------------------------------------------------------------------


def test_delete_and_not_found(client, seed):
    """U3：DELETE 后能删掉；删不存在 → 70002。"""
    created = client.post(
        "/api/homework",
        headers=_auth(_admin_token()),
        json={"class_id": SEED.class_1, "title": "待删", "subject": "english", "unit_ids": []},
    ).json()["data"]["id"]

    resp = client.delete(f"/api/homework/{created}", headers=_auth(_admin_token()))
    assert resp.status_code == 200
    assert resp.json()["code"] == 0, resp.json()

    # 再查列表应已不在
    resp = client.get("/api/homework", headers=_auth(_admin_token()))
    ids = [i["id"] for i in resp.json()["data"]["items"]]
    assert created not in ids

    # 删不存在
    resp = client.delete("/api/homework/99999", headers=_auth(_admin_token()))
    assert resp.status_code == 200
    assert resp.json()["code"] == ErrorCodeWrap.HOMEWORK_NOT_FOUND, resp.json()


# ---------------------------------------------------------------------------
# U7 非 admin 鉴权
# ---------------------------------------------------------------------------


def test_non_admin_forbidden(client, seed):
    """U7：学生 token 调管理端接口 → 10003（HTTP 200）。"""
    resp = client.get("/api/homework", headers=_auth(_student_token(SEED.student_a)))
    assert resp.status_code == 200
    assert resp.json()["code"] == ErrorCodeWrap.FORBIDDEN, resp.json()

    resp = client.post(
        "/api/homework",
        headers=_auth(_student_token(SEED.student_a)),
        json={"class_id": SEED.class_1, "title": "x", "subject": "english", "unit_ids": []},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == ErrorCodeWrap.FORBIDDEN, resp.json()


def test_no_token(client, seed):
    """U12：未带 token 调管理端接口 → 10002（HTTP 200）。"""
    resp = client.get("/api/homework")
    assert resp.status_code == 200
    assert resp.json()["code"] == ErrorCodeWrap.UNAUTHORIZED, resp.json()


# ---------------------------------------------------------------------------
# U8 / U9 / U10 / U11 学生端
# ---------------------------------------------------------------------------


def test_student_only_own_class(client, seed):
    """U8 + U9（反向验证）：学生 A（1 班）只能看到 1 班的作业，看不到 2 班的。

    反向验证核心：直接 SQL 统计「全部 active 作业数」必然 > A 看到的数量
    （因为 2 班也有 active 作业存在）。若 `student_homework` 里删掉
    `class_id = ?` 过滤，A 会看到全部，断言 `len(a_items) < total_active` 将失败 → 转红。
    """
    # 通过管理端为 1 班、2 班各布置一份 active 作业
    client.post(
        "/api/homework",
        headers=_auth(_admin_token()),
        json={"class_id": SEED.class_1, "title": "一班作业", "subject": "chinese", "unit_ids": []},
    )
    client.post(
        "/api/homework",
        headers=_auth(_admin_token()),
        json={"class_id": SEED.class_2, "title": "二班作业", "subject": "english", "unit_ids": []},
    )

    resp = client.get("/api/me/homework", headers=_auth(_student_token(SEED.student_a)))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0, body
    items = body["data"]["items"]

    # A 只能看到 1 班的「一班作业」
    titles = [i["title"] for i in items]
    assert "一班作业" in titles
    assert "二班作业" not in titles
    # A 在 1 班，1 班只有这一份作业
    assert len(items) == 1, items

    # 反向验证：全部 active 作业数必然 > A 看到的（因为存在 2 班作业）
    total_active = _count_active_homework_all_classes()
    assert total_active >= 2, f"应至少 2 份 active 作业，实际 {total_active}"
    assert len(items) < total_active, "学生疑似越权看到了其他班级的作业！"


def test_student_unassigned_empty(client, seed):
    """U10：未分班学生 GET /api/me/homework → 空列表，不报错。"""
    resp = client.get("/api/me/homework", headers=_auth(_student_token(SEED.student_c)))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["items"] == []


def test_student_submission_status_done(client, seed):
    """U11：作业布置后默认 pending；若 submission 已是 done，则回显 done。

    验证 `COALESCE(s.status, 'pending')` 的两条分支。
    """
    created = client.post(
        "/api/homework",
        headers=_auth(_admin_token()),
        json={"class_id": SEED.class_1, "title": "完成状态", "subject": "chinese", "unit_ids": []},
    ).json()["data"]["id"]

    # 默认：A 应拿到 pending
    resp = client.get("/api/me/homework", headers=_auth(_student_token(SEED.student_a)))
    items = resp.json()["data"]["items"]
    mine = next(i for i in items if i["id"] == created)
    assert mine["status"] == "pending"
    # 契约字段齐备
    assert set(mine.keys()) == {"id", "title", "subject", "due_at", "status"}, mine

    # 把 A 的 submission 标成 done，再查应回显 done
    with write_tx() as conn:
        conn.execute(
            "UPDATE homework_submissions SET status = 'done', finished_at = ? "
            "WHERE homework_id = ? AND student_id = ?",
            ("2099-01-01T00:00:00Z", created, SEED.student_a),
        )
    resp = client.get("/api/me/homework", headers=_auth(_student_token(SEED.student_a)))
    mine = next(i for i in resp.json()["data"]["items"] if i["id"] == created)
    assert mine["status"] == "done"


# 把错误码常量内联，避免额外 import 路径漂移（与 app.core.errors.ErrorCode 取值一致）。
class ErrorCodeWrap:
    PARAM_INVALID = 10001
    UNAUTHORIZED = 10002
    FORBIDDEN = 10003
    CLASS_NOT_FOUND = 20004
    HOMEWORK_NOT_FOUND = 70002
    HOMEWORK_EXPIRED = 70003
