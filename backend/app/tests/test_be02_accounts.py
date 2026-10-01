"""WP-BE-02 验收测试 —— 账号与班级管理（10a「验收用例」U1 ~ U14）。

跑法：
    cd backend && ./.venv/Scripts/python.exe -m pytest app/tests/test_be02_accounts.py -v

设计说明：
- **每个用例一个干净的数据库**（function 级 fixture 删库重建 + 重跑迁移），
  避免用例之间互相污染 —— 导入类用例（U7/U8）尤其经不起串场。
- 环境变量必须在 `import app.main` **之前**写好，因为 `app.main` 在导入时
  就调用 `get_settings()`（`core/config.py` 有 `lru_cache`）。
- 登录限流是**进程级内存计数**，所有用例共用同一个 `testclient` 身份，
  所以 fixture 里必须 `rate_limit.clear_all()`。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 环境准备：必须在导入 app.main 之前
# ---------------------------------------------------------------------------

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_be02_"))
_DB_FILE = _TMP_DIR / "test.db"

TEST_JWT_SECRET = "unit-test-secret-0123456789abcdef0123456789ab"
TEST_ADMIN_USERNAME = "admin"
TEST_ADMIN_PASSWORD = "unit-test-admin-pass"
TEST_DEFAULT_PWD = "123456"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = TEST_JWT_SECRET
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = TEST_ADMIN_USERNAME
os.environ["ADMIN_INIT_PASSWORD"] = TEST_ADMIN_PASSWORD
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import preview_store, rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.excel import write_xlsx  # noqa: E402
from app.main import app  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402
from app.services.user_service import UserService  # noqa: E402

reset_config_cache()
assert get_settings().db_file == _DB_FILE, "DB_PATH 未生效，测试会写到真实数据库上！"


# ---------------------------------------------------------------------------
# fixtures / 工具
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """干净数据库 + 已启动的应用（lifespan 里跑迁移与建 admin）。

    ⚠️ **must** 在 fixture 里重新声明一次环境变量：`os.environ` 是进程级的，
    同一个 pytest 进程里若同时收集了别的测试模块（如 `test_be01_bootstrap.py`），
    对方的 fixture 会把 `DB_PATH` 改成它自己的临时库。谁在跑，谁负责声明。
    """
    # ⚠️ 这几个 env 必须**整套**重设，不能只重设 DB_PATH。
    #
    # 踩过的坑：原先只重设 DB_PATH，与 be03 / be05 等模块在同一次 pytest 里混跑时，
    # `JWT_SECRET` 与 `ADMIN_INIT_PASSWORD` 还是**上一个被 import 的模块**留下的值
    # → 本模块的 admin 登录直接 20002 → 所有需要 token 的用例连锁失败（实测 20 failed）。
    # 而单独跑 `pytest app/tests/test_be02_accounts.py` 是全绿的，这类问题极难定位。
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = TEST_JWT_SECRET
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = TEST_ADMIN_USERNAME
    os.environ["ADMIN_INIT_PASSWORD"] = TEST_ADMIN_PASSWORD
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE, "DB_PATH 被子进程里别的测试模块改掉了"

    for suffix in ("", "-wal", "-shm"):
        Path(str(_DB_FILE) + suffix).unlink(missing_ok=True)

    # 三个进程级缓存的隔离：否则上一个用例的配置/限流/登录锁定会带过来
    SettingsService._cache = None  # noqa: SLF001 - 测试专用，BE-01 未提供 reset 接口
    preview_store.clear_all()
    rate_limit.clear_all()
    UserService.reset_login_state()

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _auth(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _login(client: TestClient, username: str, password: str, role: str = "admin") -> dict:
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password, "role": role},
    )
    return resp.json()


def _register(
    client: TestClient,
    username: str,
    real_name: str,
    password: str = "abc123456",
    class_id: int | None = None,
) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "real_name": real_name,
            "password": password,
            "class_id": class_id,
        },
    )
    return resp.json()


def _admin_token(client: TestClient) -> str:
    body = _login(client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD, "admin")
    assert body["code"] == 0, body
    return body["data"]["access_token"]


def _make_class(
    client: TestClient,
    token: str,
    name: str = "三年级二班",
    grade: int = 3,
    semester: str = "first",
) -> int:
    resp = client.post(
        "/api/classes",
        json={"name": name, "grade": grade, "semester": semester},
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["id"]


def _students_xlsx(rows: list[list]) -> bytes:
    return write_xlsx(
        ["学号", "姓名", "班级", "初始密码"], rows, sheet_name="学生名单"
    )


def _upload(client: TestClient, token: str, content: bytes) -> dict:
    resp = client.post(
        "/api/students/import",
        files={
            "file": (
                "名单.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=_auth(token),
    )
    return resp.json()


def _query(sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(_DB_FILE))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _count_students() -> int:
    return int(_query("SELECT COUNT(*) FROM users WHERE role = 'student'")[0][0])


def _seed_answers(student_id: int, match_types: list[str]) -> None:
    """直接造答题记录（`answers` 属 BE-04，此处仅为验证正确率口径）。"""
    conn = sqlite3.connect(str(_DB_FILE))
    try:
        sid = conn.execute(
            """
            INSERT INTO sessions (student_id, content_type, strategy, status)
            VALUES (?, 'english', 'sequential', 'finished')
            """,
            (student_id,),
        ).lastrowid
        for mt in match_types:
            conn.execute(
                """
                INSERT INTO answers
                    (session_id, student_id, mode, step, expected, match_type, is_correct)
                VALUES (?, ?, 'cn2en', 1, 'apple', ?, ?)
                """,
                (sid, student_id, mt, 1 if mt in ("exact", "loose", "fuzzy") else 0),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# U1 ~ U5：登录与注册
# ---------------------------------------------------------------------------


def test_u1_admin_login(client):
    """U1 用 admin 账号登录 → 返回 token，role='admin'。"""
    body = _login(client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD, "admin")
    assert body["code"] == 0
    data = body["data"]
    # 契约 §5.2 字段名（接口设计 §2.1 示例里的 "token" 以契约优先）
    assert data["access_token"]
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] == 12 * 3600
    assert data["user"]["role"] == "admin"
    assert data["user"]["username"] == TEST_ADMIN_USERNAME
    assert body["trace_id"]


def test_u2_wrong_password_no_enumeration(client):
    """U2 错误密码 → 20002，且**不透露账号是否存在**。"""
    wrong = _login(client, TEST_ADMIN_USERNAME, "definitely-wrong", "admin")
    missing = _login(client, "no-such-user-9999", "definitely-wrong", "admin")

    assert wrong["code"] == 20002
    assert missing["code"] == 20002
    # 两条路径的 message 必须一致，否则可直接枚举账号
    assert wrong["message"] == missing["message"]


def test_u2b_role_isolation(client):
    """补充：学生账号走管理端入口登录 → 同样 20002（不泄露账号存在）。"""
    _register(client, "20260001", "张小明")
    body = _login(client, "20260001", "abc123456", "admin")
    assert body["code"] == 20002


def test_u3_disabled_account_login(client):
    """U3 停用账号登录 → 20003。"""
    token = _admin_token(client)
    reg = _register(client, "20260002", "李小红")
    sid = reg["data"]["user"]["id"]

    resp = client.patch(
        f"/api/students/{sid}", json={"status": "disabled"}, headers=_auth(token)
    )
    assert resp.json()["code"] == 0

    body = _login(client, "20260002", "abc123456", "student")
    assert body["code"] == 20003


def test_u4_student_self_register(client):
    """U4 学生注册新学号 → 成功；`register_source='self'`、`class_id=null`。"""
    body = _register(client, "20260003", "王二小")
    assert body["code"] == 0
    user = body["data"]["user"]
    assert user["register_source"] == "self"
    assert user["class_id"] is None
    assert user["role"] == "student"
    assert body["data"]["access_token"]

    rows = _query(
        "SELECT register_source, class_id, status FROM users WHERE username = '20260003'"
    )
    assert rows[0] == ("self", None, "active")


def test_u5_register_existing_username_not_overwritten(client):
    """U5 用已存在学号注册 → 20001，且**原账号姓名与密码未被改动**。"""
    first = _register(client, "20260004", "张小明", password="oldpwd123")
    assert first["code"] == 0

    again = _register(client, "20260004", "冒名顶替", password="newpwd456")
    assert again["code"] == 20001

    # 原密码仍然可用
    assert _login(client, "20260004", "oldpwd123", "student")["code"] == 0
    # 新密码不可用
    assert _login(client, "20260004", "newpwd456", "student")["code"] == 20002
    # 姓名没被覆盖
    assert _query("SELECT real_name FROM users WHERE username = '20260004'")[0][0] == "张小明"


def test_u6_register_then_use_immediately(client):
    """U6 注册后立即调 `/api/me/profile` → 成功，无需审核。"""
    reg = _register(client, "20260005", "赵小亮", class_id=None)
    token = reg["data"]["access_token"]

    resp = client.get("/api/me/profile", headers=_auth(token))
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["username"] == "20260005"
    assert body["data"]["real_name"] == "赵小亮"
    # 护眼设置来自 settings 表，应当是布尔值而不是 0/1（契约 §4.6）
    assert body["data"]["eye_rest_enabled"] is True
    assert body["data"]["eye_warm_mode_auto"] is True


def test_login_lock_after_five_failures(client):
    """接口设计 §2.1：连续 5 次密码错误 → 锁定 10 分钟，返回 `10005`。

    ⚠️ HTTP 层限流（`RateLimitMiddleware` 单账号/单 IP 5 次/分钟）与账号锁定
    都会返回 `10005`，在真实客户端眼里无法区分。这里每次请求前清一次限流计数，
    以便单独验证**账号锁定**这条逻辑。
    """
    for _ in range(5):
        rate_limit.clear_all()
        assert _login(client, TEST_ADMIN_USERNAME, "bad-pwd", "admin")["code"] == 20002

    rate_limit.clear_all()
    locked = _login(client, TEST_ADMIN_USERNAME, "bad-pwd", "admin")
    assert locked["code"] == 10005

    # 锁定期内即使密码正确也拒绝
    rate_limit.clear_all()
    assert _login(client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD, "admin")["code"] == 10005


# ---------------------------------------------------------------------------
# U7 ~ U9：名单导入
# ---------------------------------------------------------------------------


def _build_45_rows() -> list[list]:
    """45 行：43 行合法 + 2 行错误；其中第 2 行（学号 20260001）在库中已存在。

    行号约定（含表头）：第 2 行 → 20260001，第 46 行 → 20260045。
    - 第 8 行（20260008）姓名留空          → invalid
    - 第 14 行（20260014）班级不存在        → invalid
    - 第 2 行（20260001）库里已有该学号     → duplicated（走**更新**，不算 invalid）

    ⚠️ 关于 `duplicated` 的语义，《接口设计》§4.4 的示例自相矛盾（`summary.duplicated=1`
    却又给出 `created=43`，并把「学号重复」那行画进了 `invalid_rows`）。本实现按
    **需求 §4.2 + 10a F8**（「重复导入同一学号时为更新而非新增」）落地：
    `duplicated` = 学号已在库中、本次将走更新的行数，**不计入 `invalid`**。
    """
    rows: list[list] = []
    for i in range(1, 46):
        rows.append([f"2026{i:04d}", f"学生{i:02d}", "三年级二班", ""])

    # 错误行 1：第 8 行（学号 20260008）姓名留空
    rows[7][1] = ""
    # 错误行 2：第 14 行（学号 20260014）班级不存在
    rows[13][2] = "不存在的班"
    return rows


def test_u7_import_preview_counts(client):
    """U7 导入 45 行（2 行错误、1 行重复）→ valid=43 / invalid=2 / duplicated=1。"""
    token = _admin_token(client)
    _make_class(client, token, "三年级二班", 3, "first")

    # 先造一个「已存在」的学号，让第 2 行成为 duplicated（而不是 invalid）
    pre = _register(client, "20260001", "已存在同学")
    assert pre["code"] == 0

    rows = _build_45_rows()
    body = _upload(client, token, _students_xlsx(rows))
    assert body["code"] == 0, body

    summary = body["data"]["summary"]
    assert summary["total"] == 45
    assert summary["invalid"] == 2, summary
    assert summary["duplicated"] == 1, summary
    assert summary["valid"] == 43, summary

    # 错误行必须带原因与原始数据
    assert len(body["data"]["invalid_rows"]) == 2
    for item in body["data"]["invalid_rows"]:
        assert item["row"] >= 2
        assert item["reason"]
        assert isinstance(item["raw"], list) and len(item["raw"]) == 4
    reasons = " / ".join(i["reason"] for i in body["data"]["invalid_rows"])
    assert "姓名不能为空" in reasons
    assert "不存在" in reasons

    assert body["data"]["error_report_url"].startswith("/api/import/report/")
    # preview 阶段**不落库**
    assert _count_students() == 1


def test_u7b_import_handles_excel_numeric_and_error_report(client):
    """补充：学号被 Excel 存成数字（`20260001.0`）也能正确解析；错误报告可下载。"""
    token = _admin_token(client)
    _make_class(client, token, "三年级二班", 3, "first")

    # 直接写数字类型（模拟 Excel 数字单元格），并故意留一行姓名为空
    content = write_xlsx(
        ["学号", "姓名", "班级", "初始密码"],
        [[20260011, "数字学号同学", "三年级二班", 123456], [20260012, "", "三年级二班", ""]],
        sheet_name="学生名单",
    )
    body = _upload(client, token, content)
    assert body["code"] == 0, body
    assert body["data"]["summary"]["valid"] == 1
    assert body["data"]["summary"]["invalid"] == 1

    report_url = body["data"]["error_report_url"]
    report = client.get(report_url, headers=_auth(token))
    assert report.status_code == 200
    assert report.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(report.content) > 1000

    # 提交后学号应当是纯数字串，而不是 "20260011.0"
    commit = client.post(
        "/api/students/import/commit",
        json={"preview_token": body["data"]["preview_token"], "default_password": "abc123456"},
        headers=_auth(token),
    ).json()
    assert commit["code"] == 0, commit
    assert _query("SELECT username FROM users WHERE real_name = '数字学号同学'")[0][0] == "20260011"


def test_u8_second_import_updates_not_creates(client):
    """U8 同一份文件二次 import + commit → created=0，updated=43，学生总数不变。

    第一次：43 行合法里 1 行（20260001）库里已有 → 更新；其余 42 行新建。
    第二次：43 行全部已存在 → 全部更新，**一个都不新建**，总数不变。
    """
    token = _admin_token(client)
    _make_class(client, token, "三年级二班", 3, "first")
    _register(client, "20260001", "已存在同学")

    content = _students_xlsx(_build_45_rows())

    first_preview = _upload(client, token, content)
    assert first_preview["code"] == 0
    assert first_preview["data"]["summary"]["duplicated"] == 1
    first = client.post(
        "/api/students/import/commit",
        json={
            "preview_token": first_preview["data"]["preview_token"],
            "default_password": "abc123456",
        },
        headers=_auth(token),
    ).json()
    assert first["code"] == 0, first
    assert first["data"]["created"] == 42
    assert first["data"]["updated"] == 1
    after_first = _count_students()
    assert after_first == 43  # 1 个已存在 + 42 个新建

    second_preview = _upload(client, token, content)
    assert second_preview["code"] == 0
    assert second_preview["data"]["summary"]["valid"] == 43
    assert second_preview["data"]["summary"]["duplicated"] == 43
    second = client.post(
        "/api/students/import/commit",
        json={
            "preview_token": second_preview["data"]["preview_token"],
            "default_password": "abc123456",
        },
        headers=_auth(token),
    ).json()
    assert second["code"] == 0, second
    assert second["data"]["created"] == 0
    assert second["data"]["updated"] == 43
    assert _count_students() == after_first  # 总数不变


def test_u8b_reimport_does_not_reset_password(client):
    """补充（安全）：重复导入**不得**把已有账号的密码刷回默认弱口令。"""
    token = _admin_token(client)
    _make_class(client, token, "三年级二班", 3, "first")

    content = _students_xlsx([["20260021", "同学甲", "三年级二班", ""]])
    p1 = _upload(client, token, content)
    client.post(
        "/api/students/import/commit",
        json={"preview_token": p1["data"]["preview_token"], "default_password": "abc123456"},
        headers=_auth(token),
    )
    # 学生自己改了密码
    student_id = _query("SELECT id FROM users WHERE username = '20260021'")[0][0]
    client.post(
        f"/api/students/{student_id}/reset-password",
        json={"new_password": "mypwd88888"},
        headers=_auth(token),
    )
    assert _login(client, "20260021", "mypwd88888", "student")["code"] == 0

    # 管理者把同一份名单再导一次（default_password 依然是弱口令）
    p2 = _upload(client, token, content)
    client.post(
        "/api/students/import/commit",
        json={"preview_token": p2["data"]["preview_token"], "default_password": "123456"},
        headers=_auth(token),
    )
    # 密码没被刷回
    assert _login(client, "20260021", "mypwd88888", "student")["code"] == 0
    assert _login(client, "20260021", "123456", "student")["code"] == 20002


def test_u9_expired_preview_token(client):
    """U9 preview 后不 commit，30 分钟后 commit → token 失效，返回明确提示。"""
    token = _admin_token(client)
    _make_class(client, token, "三年级二班", 3, "first")

    preview = _upload(client, token, _students_xlsx([["20260031", "同学乙", "三年级二班", ""]]))
    pv = preview["data"]["preview_token"]

    # 把该条目的过期时间拨回过去（真实等 30 分钟不现实）
    with preview_store._lock:  # noqa: SLF001 - 测试专用
        preview_store._cache[pv]["expire_at"] = time.time() - 1

    resp = client.post(
        "/api/students/import/commit",
        json={"preview_token": pv, "default_password": "abc123456"},
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 40003
    assert "失效" in body["message"]
    assert body["data"]["reason"] == "preview_token_expired"
    assert _count_students() == 0

    # 不存在的 token 走同一条路径
    bogus = client.post(
        "/api/students/import/commit",
        json={"preview_token": "pv_not_exists", "default_password": "abc123456"},
        headers=_auth(token),
    ).json()
    assert bogus["code"] == 40003


# ---------------------------------------------------------------------------
# U10 ~ U14：归班 / 重置 / 停用 / 偏好 / 审计
# ---------------------------------------------------------------------------


def _three_students(client: TestClient) -> list[int]:
    ids = []
    for i in range(3):
        body = _register(client, f"2026004{i}", f"待归班{i}")
        ids.append(body["data"]["user"]["id"])
    return ids


def test_u10_batch_assign_class(client):
    """U10 批量归班 3 人 → class_id 正确更新；unassigned_count 减少。"""
    token = _admin_token(client)
    class_id = _make_class(client, token, "三年级二班", 3, "first")
    ids = _three_students(client)

    before = client.get("/api/students", headers=_auth(token)).json()
    assert before["data"]["unassigned_count"] == 3

    resp = client.post(
        "/api/students/batch-assign-class",
        json={"student_ids": ids, "class_id": class_id},
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["success_count"] == 3
    assert body["data"]["failed_count"] == 0
    assert body["data"]["unassigned_count"] == 0

    rows = _query(
        "SELECT class_id FROM users WHERE id IN (?, ?, ?) ORDER BY id", tuple(ids)
    )
    assert [r[0] for r in rows] == [class_id, class_id, class_id]

    # 归班顺带回填了年级学期偏好（10a 关键实现要点）
    prefs = _query(
        "SELECT preferred_grade, preferred_semester FROM users WHERE id = ?", (ids[0],)
    )[0]
    assert prefs == (3, "first")

    # 未分班筛选能查空
    unassigned = client.get(
        "/api/students", params={"class_id": "null"}, headers=_auth(token)
    ).json()
    assert unassigned["data"]["total"] == 0


def test_u10b_single_assign_and_remove_from_class(client):
    """补充：单个归班 / 移出班级（`class_id=null` 的语义不能被当成「不修改」）。"""
    token = _admin_token(client)
    class_id = _make_class(client, token, "三年级二班", 3, "first")
    sid = _register(client, "20260051", "小同学")["data"]["user"]["id"]

    assigned = client.post(
        f"/api/students/{sid}/assign-class",
        json={"class_id": class_id},
        headers=_auth(token),
    ).json()
    assert assigned["code"] == 0
    assert assigned["data"]["class_id"] == class_id

    removed = client.post(
        f"/api/students/{sid}/assign-class",
        json={"class_id": None},
        headers=_auth(token),
    ).json()
    assert removed["code"] == 0
    assert removed["data"]["class_id"] is None

    # 只改姓名时不能顺手把班级清掉（三态判定的关键）
    rename = client.patch(
        f"/api/students/{sid}", json={"real_name": "改名了"}, headers=_auth(token)
    ).json()
    assert rename["code"] == 0
    assert rename["data"]["real_name"] == "改名了"
    assert _query("SELECT real_name FROM users WHERE id = ?", (sid,))[0][0] == "改名了"


def test_u11_reset_password(client):
    """U11 重置某生密码后用旧密码登录失败，用新密码成功。"""
    token = _admin_token(client)
    sid = _register(client, "20260061", "小同学", password="oldpwd123")["data"]["user"]["id"]
    assert _login(client, "20260061", "oldpwd123", "student")["code"] == 0

    resp = client.post(
        f"/api/students/{sid}/reset-password",
        json={"new_password": "newpwd456"},
        headers=_auth(token),
    )
    assert resp.json()["code"] == 0

    rate_limit.clear_all()
    # 锁定状态无关，这里密码是错的
    assert _login(client, "20260061", "oldpwd123", "student")["code"] == 20002
    rate_limit.clear_all()
    assert _login(client, "20260061", "newpwd456", "student")["code"] == 0


def test_u12_disabled_student_token_invalidated(client):
    """U12 停用某生后，用其 token 调接口 → 10002。"""
    token = _admin_token(client)
    reg = _register(client, "20260071", "小同学")
    sid = reg["data"]["user"]["id"]
    stu_token = reg["data"]["access_token"]

    assert client.get("/api/me/profile", headers=_auth(stu_token)).json()["code"] == 0

    client.patch(f"/api/students/{sid}", json={"status": "disabled"}, headers=_auth(token))

    body = client.get("/api/me/profile", headers=_auth(stu_token)).json()
    assert body["code"] == 10002


def test_u13_update_profile(client):
    """U13 `PUT /api/me/profile` 改年级学期后重查 → 返回新值。"""
    reg = _register(client, "20260081", "小同学")
    token = reg["data"]["access_token"]

    resp = client.put(
        "/api/me/profile",
        json={"preferred_grade": 4, "preferred_semester": "second"},
        headers=_auth(token),
    )
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["preferred_grade"] == 4
    assert resp.json()["data"]["preferred_semester"] == "second"

    again = client.get("/api/me/profile", headers=_auth(token)).json()
    assert again["data"]["preferred_grade"] == 4
    assert again["data"]["preferred_semester"] == "second"

    # 只传一个字段时另一个保持不变
    partial = client.put(
        "/api/me/profile", json={"preferred_grade": 5}, headers=_auth(token)
    ).json()
    assert partial["data"]["preferred_grade"] == 5
    assert partial["data"]["preferred_semester"] == "second"

    # 非法值被参数校验拦住
    bad = client.put(
        "/api/me/profile", json={"preferred_grade": 10}, headers=_auth(token)
    ).json()
    assert bad["code"] == 10001


def test_u14_audit_logs(client):
    """U14 导入 / 归班 / 重置密码后查 `audit_logs` → 记录齐全，含操作人与目标。"""
    token = _admin_token(client)
    admin_id = _login(client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD, "admin")["data"][
        "user"
    ]["id"]
    class_id = _make_class(client, token, "三年级二班", 3, "first")

    # ① 导入
    preview = _upload(client, token, _students_xlsx([["20260091", "审计同学", "三年级二班", ""]]))
    client.post(
        "/api/students/import/commit",
        json={"preview_token": preview["data"]["preview_token"], "default_password": "abc123456"},
        headers=_auth(token),
    )
    sid = _query("SELECT id FROM users WHERE username = '20260091'")[0][0]

    # ② 归班
    client.post(
        f"/api/students/{sid}/assign-class",
        json={"class_id": class_id},
        headers=_auth(token),
    )
    # ③ 重置密码
    client.post(
        f"/api/students/{sid}/reset-password",
        json={"new_password": "anotherpwd"},
        headers=_auth(token),
    )

    rows = _query(
        "SELECT action, user_id, target_type, target_id FROM audit_logs ORDER BY id"
    )
    by_action = {r[0]: r for r in rows}

    assert "student.import" in by_action, by_action
    assert "student.assign_class" in by_action, by_action
    assert "student.reset_password" in by_action, by_action

    for action in ("student.import", "student.assign_class", "student.reset_password"):
        _, user_id, target_type, target_id = by_action[action]
        assert user_id == admin_id, f"{action} 的操作人不对"
        assert target_type is not None and target_id is not None, f"{action} 缺目标"

    assert by_action["student.assign_class"][2:] == ("student", sid)
    assert by_action["student.reset_password"][2:] == ("student", sid)


# ---------------------------------------------------------------------------
# 补充：口径 / 边界 / 权限
# ---------------------------------------------------------------------------


def test_accuracy_follows_contract_8_1(client):
    """正确率必须走契约 §8.1：`(exact+loose+fuzzy) / (总数 − empty)`。"""
    token = _admin_token(client)
    sid = _register(client, "20260101", "口径同学")["data"]["user"]["id"]
    _seed_answers(sid, ["exact", "exact", "fuzzy", "wrong", "empty"])

    body = client.get(
        "/api/students", params={"keyword": "20260101"}, headers=_auth(token)
    ).json()
    assert body["code"] == 0
    item = body["data"]["items"][0]
    assert item["practice_count"] == 5  # 含 empty
    assert item["accuracy"] == 0.75  # 3 / (5 - 1)

    # 只有 empty 时不得除零
    sid2 = _register(client, "20260102", "全空同学")["data"]["user"]["id"]
    _seed_answers(sid2, ["empty"])
    body2 = client.get(
        "/api/students", params={"keyword": "20260102"}, headers=_auth(token)
    ).json()
    assert body2["data"]["items"][0]["accuracy"] == 0.0

    # 没有任何答题记录
    _register(client, "20260103", "没练过")
    body3 = client.get(
        "/api/students", params={"keyword": "20260103"}, headers=_auth(token)
    ).json()
    assert body3["data"]["items"][0]["practice_count"] == 0
    assert body3["data"]["items"][0]["accuracy"] == 0.0


def test_class_duplicate_name_rejected(client):
    """F5：创建同名班级 → 10001 且提示写清是哪个班重名。"""
    token = _admin_token(client)
    _make_class(client, token, "三年级二班", 3, "first")

    dup = client.post(
        "/api/classes",
        json={"name": "三年级二班", "grade": 3, "semester": "first"},
        headers=_auth(token),
    ).json()
    assert dup["code"] == 10001
    assert "三年级二班" in dup["message"]

    # 归档后名字释放，允许重建同名班级
    rows = client.get("/api/classes", headers=_auth(token)).json()["data"]["items"]
    cls_id = rows[0]["id"]
    archived = client.patch(
        f"/api/classes/{cls_id}", json={"status": "archived"}, headers=_auth(token)
    ).json()
    assert archived["code"] == 0
    assert archived["data"]["status"] == "archived"

    again = client.post(
        "/api/classes",
        json={"name": "三年级二班", "grade": 3, "semester": "first"},
        headers=_auth(token),
    ).json()
    assert again["code"] == 0

    # 默认列表只返回活跃班级
    active = client.get("/api/classes", headers=_auth(token)).json()
    assert active["data"]["total"] == 1
    all_of = client.get(
        "/api/classes", params={"status": "all"}, headers=_auth(token)
    ).json()
    assert all_of["data"]["total"] == 2


def test_class_options_public_and_minimal(client):
    """契约 §4.1 补充：注册页班级选项**免鉴权**、字段最小、只给活跃班级。

    三条都是硬要求，缺一条这接口就白开：

    1. **免鉴权** —— 注册发生在登录之前；要 token 的话注册页永远拉不到，
       UI 规范 §7.3「班级选择用卡片列表」就只能永远显示「先不选」；
    2. **字段最小** —— 别把 `student_count` / `unassigned_count` 漏给未登录者；
    3. **只要活跃班级** —— 已归档的班不再接收新学生。
    """
    token = _admin_token(client)
    kept = _make_class(client, token, "三年级二班", 3, "first")
    gone = _make_class(client, token, "四年级一班", 4, "first")
    assert (
        client.patch(
            f"/api/classes/{gone}", json={"status": "archived"}, headers=_auth(token)
        ).json()["code"]
        == 0
    )

    # ① 不带 Authorization 头也能拿到（注册页就是这个状态）
    body = client.get("/api/classes/options").json()
    assert body["code"] == 0, body
    items = body["data"]["items"]
    assert [i["id"] for i in items] == [kept], "归档班级不应出现在选项里"
    assert body["data"]["total"] == 1

    # ② 只有最小字段
    assert set(items[0]) == {"id", "name", "grade", "semester"}, "字段收窄是这接口的意义"
    assert items[0]["name"] == "三年级二班"

    # ③ 对照：§4.1 那个管理端接口仍然要鉴权（未带 token → 10002）
    assert client.get("/api/classes").json()["code"] == 10002


def test_class_options_grade_filter(client):
    """`grade` 筛选在选项接口上同样生效（注册页可能让学生先选年级再选班）。"""
    token = _admin_token(client)
    g3 = _make_class(client, token, "三年级二班", 3, "first")
    _make_class(client, token, "四年级一班", 4, "first")

    body = client.get("/api/classes/options", params={"grade": 3}).json()
    assert body["code"] == 0, body
    assert [i["id"] for i in body["data"]["items"]] == [g3]
    assert body["data"]["total"] == 1


def test_class_options_status_param(client):
    """`status` 参数（2026-09-29 新增）：缺省只看活跃；`all` 连归档一起给。

    加它的原因：管理端「班级筛选」下拉要能筛到**已归档班里的学生**
    （归档只归档班级，学生不会自动移出），而 `GET /api/classes` 受契约 §5.1
    `page_size ≤ 100` 约束、没法一次拿全。扩选项接口比放宽分页上限合规。
    ⚠️ 默认行为必须**完全不变** —— 注册页看到的仍是「只有活跃班」。
    """
    token = _admin_token(client)
    kept = _make_class(client, token, "三年级二班", 3, "first")
    gone = _make_class(client, token, "四年级一班", 4, "first")
    assert (
        client.patch(
            f"/api/classes/{gone}", json={"status": "archived"}, headers=_auth(token)
        ).json()["code"]
        == 0
    )

    # ① 缺省：只看活跃（与加参数之前逐字一致）
    body = client.get("/api/classes/options").json()
    assert [i["id"] for i in body["data"]["items"]] == [kept]
    # ② 显式 status=active：同上
    body = client.get("/api/classes/options", params={"status": "active"}).json()
    assert [i["id"] for i in body["data"]["items"]] == [kept]
    # ③ status=all：归档班也出来
    body = client.get("/api/classes/options", params={"status": "all"}).json()
    assert sorted(i["id"] for i in body["data"]["items"]) == sorted([kept, gone])
    # ④ 字段仍是收窄的 4 个 —— 加参数不能把管理字段（student_count 等）带出来
    assert set(body["data"]["items"][0]) == {"id", "name", "grade", "semester"}


def test_import_template_download(client):
    """补充：名单模板可下载，且含数据有效性下拉（班级名）。"""
    token = _admin_token(client)
    _make_class(client, token, "三年级二班", 3, "first")

    resp = client.get("/api/students/import/template", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(resp.content) > 1000


def test_credentials_export(client):
    """补充：`credentials_url` 能把「学号-初始密码」对照表导出来。"""
    token = _admin_token(client)
    _make_class(client, token, "三年级二班", 3, "first")

    preview = _upload(
        client,
        token,
        _students_xlsx(
            [["20260111", "有密码同学", "三年级二班", "custom123"], ["20260112", "默认密码同学", "三年级二班", ""]]
        ),
    )
    commit = client.post(
        "/api/students/import/commit",
        json={"preview_token": preview["data"]["preview_token"], "default_password": "abc123456"},
        headers=_auth(token),
    ).json()
    assert commit["code"] == 0

    url = commit["data"]["credentials_url"]
    export = client.get(url, headers=_auth(token))
    assert export.status_code == 200
    assert len(export.content) > 1000

    # 显式密码与默认密码都真的生效了
    assert _login(client, "20260111", "custom123", "student")["code"] == 0
    rate_limit.clear_all()
    assert _login(client, "20260112", "abc123456", "student")["code"] == 0

    # class_id=0 → 不限班级的名单导出（无密码列）
    roster = client.get("/api/classes/0/students/credentials", headers=_auth(token))
    assert roster.status_code == 200


def test_auth_and_param_validation(client):
    """权限与参数校验：未登录 10002；学生调管理接口 10003；弱密码 10001。"""
    assert client.get("/api/students").json()["code"] == 10002
    assert client.get("/api/classes").json()["code"] == 10002

    stu_token = _register(client, "20260121", "小同学")["data"]["access_token"]
    assert client.get("/api/students", headers=_auth(stu_token)).json()["code"] == 10003
    assert client.post(
        "/api/classes",
        json={"name": "X班", "grade": 1, "semester": "first"},
        headers=_auth(stu_token),
    ).json()["code"] == 10003

    # 密码 5 位 → 10001 且 message 带字段名
    weak = client.post(
        "/api/auth/register",
        json={"username": "20260122", "real_name": "小同学", "password": "12345"},
    ).json()
    assert weak["code"] == 10001
    assert "password" in weak["message"]

    # 学号含非字母数字 → 10001
    bad_name = client.post(
        "/api/auth/register",
        json={"username": "2026_001", "real_name": "小同学", "password": "abc123456"},
    ).json()
    assert bad_name["code"] == 10001

    # 班级不存在 → 20004
    not_found = client.post(
        "/api/auth/register",
        json={
            "username": "20260123",
            "real_name": "小同学",
            "password": "abc123456",
            "class_id": 9999,
        },
    ).json()
    assert not_found["code"] == 20004


def test_file_too_large(client):
    """契约 §5.4：上传超过 10 MB → HTTP 413 + `40004`。"""
    token = _admin_token(client)
    resp = client.post(
        "/api/students/import",
        files={"file": ("big.xlsx", b"x" * (10 * 1024 * 1024 + 16), "application/octet-stream")},
        headers=_auth(token),
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == 40004


def test_invalid_xlsx_rejected(client):
    """非 xlsx 文件 → 40001。"""
    token = _admin_token(client)
    resp = client.post(
        "/api/students/import",
        files={"file": ("not.xlsx", b"this is not a spreadsheet", "application/octet-stream")},
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 40001
