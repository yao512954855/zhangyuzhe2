"""WP-BE-01 验收用例 —— 对应《10a-后端工作包规格（上）》WP-BE-01「验收用例」U1~U13。

运行：
    cd backend
    .venv/Scripts/python.exe -m pytest app/tests/test_be01_bootstrap.py -v

设计要点：
- 用**独立临时数据库**，环境变量在 `import app.main` 之前注入（pydantic-settings 优先级：
  环境变量 > .env），因此不会碰开发库。
- 测试路由（require_admin 保护 / 抛异常 / 参数校验）在 fixture 中动态挂载，
  不污染 `app/api/` 目录。
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

TEST_JWT_SECRET = "unit-test-secret-0123456789abcdef0123456789ab"
TEST_ADMIN_PASSWORD = "unit-test-admin-pass"


class EchoIn(BaseModel):
    """U7 用：故意漏传 `age` 以触发字段级校验错误。

    ⚠️ 必须定义在**模块级**。本文件启用了 `from __future__ import annotations`，
    注解会变成字符串，FastAPI 解析类型时依赖全局命名空间 ——
    定义在 fixture 函数内部会导致解析失败，退化成「整个 body 一个参数」，
    拿不到具体字段名（这正是 U7 最初报 `参数不正确：payload` 的原因）。
    """

    name: str
    age: int


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app_env(tmp_path_factory):
    """隔离环境变量。必须在 import app.main 之前生效。"""
    tmp = tmp_path_factory.mktemp("bjsys_be01")
    os.environ["DB_PATH"] = str(tmp / "test.db")
    os.environ["BACKUP_DIR"] = str(tmp / "backups")
    os.environ["JWT_SECRET"] = TEST_JWT_SECRET
    os.environ["ADMIN_INIT_USERNAME"] = "admin"
    os.environ["ADMIN_INIT_PASSWORD"] = TEST_ADMIN_PASSWORD
    os.environ["LOG_LEVEL"] = "WARNING"
    return tmp


@pytest.fixture(scope="module")
def client(app_env):
    """启动应用（触发 lifespan → 迁移 + 建 admin），并挂载测试路由。"""
    from fastapi import APIRouter, Depends
    from fastapi.testclient import TestClient

    from app.core.config import reset_config_cache
    from app.core.deps import require_admin
    from app.core.response import ok

    reset_config_cache()

    from app.main import app  # noqa: PLC0415 - 必须在环境变量就绪后导入

    _router = APIRouter(prefix="/api/_test", tags=["_test"])

    @_router.get("/admin-only")
    def _admin_only(user=Depends(require_admin)):  # noqa: ANN001
        return ok({"admin": user.username})

    @_router.get("/boom")
    def _boom():
        raise RuntimeError("intentional error for U6")

    @_router.post("/echo")
    def _echo(payload: EchoIn):
        return ok(payload.model_dump())

    app.include_router(_router)

    # U12 需要一条路径为 /api/auth/login 的写接口来触发「登录档」限流。
    #
    # ⚠️ 跨包集成说明（2026-09-23，WP-BE-02 落地后）：BE-02 的真实登录接口是
    #    在 `app.main` 导入时由自动路由发现注册的，**早于本桩路由**；
    #    Starlette 按注册顺序匹配，所以真实路由会抢先命中，本桩实际上是死代码。
    #    因此 U12 的断言已改为「只验证限流桶是否生效」，不再假设该路径的业务 code。
    _login_router = APIRouter(tags=["_test"])

    @_login_router.post("/api/auth/login")
    def _login_mock():
        return ok({"mock": True})

    app.include_router(_login_router)

    # raise_server_exceptions=False：U6 要验证「未捕获异常被全局 handler 兜住」，
    # 若保持默认 True，TestClient 会把异常直接抛到测试代码里，绕过了 handler。
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _scalar(sql: str, params: tuple = ()):
    conn = _db_conn()
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _make_student(username: str, *, status: str = "active") -> int:
    """直接插一个学生账号，返回 id。"""
    from app.core.db import utc_now_iso, write_tx
    from app.core.security import hash_password

    with write_tx() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (role, username, real_name, password_hash,
                               register_source, status, created_at)
            VALUES ('student', ?, ?, ?, 'self', ?, ?)
            """,
            (username, f"学生{username}", hash_password("pass123"), status, utc_now_iso()),
        )
        return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# U1 / U2：迁移与首次初始化
# ---------------------------------------------------------------------------

def test_u1_first_boot_creates_schema_and_admin(client):
    """U1 空目录首次启动 → 建库建表；schema_version = 全部迁移脚本；settings 20 条；生成 admin。"""
    from app.core.migrate import discover_migrations

    # ⚠️ 版本条数/最大值由 `app/migrations/` 推导，**不写死** ——
    #    新增 003/004 时本用例无需再改（2026-09-25 加 002_wave2.sql 时即因此处写死 1 而假红）
    migrations = discover_migrations()
    assert _scalar("SELECT COUNT(*) FROM schema_version") == len(migrations)
    assert _scalar("SELECT MAX(version) FROM schema_version") == max(
        m.version for m in migrations
    )

    # settings 种子值：与《数据库设计》§4 逐条一致，共 20 条
    assert _scalar("SELECT COUNT(*) FROM settings") == 20

    # 一期 15 张核心表 + 3 个视图都在（二期 7 张表见 002_wave2.sql，此处只核一期基线）
    tables = {
        r[0]
        for r in _db_conn().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    expected_tables = {
        "classes", "users", "units", "words", "texts", "sessions", "answers",
        "recitations", "recitation_events", "word_mastery", "asr_logs",
        "asr_daily_stats", "settings", "audit_logs", "schema_version",
    }
    assert expected_tables <= tables

    views = {
        r[0]
        for r in _db_conn().execute(
            "SELECT name FROM sqlite_master WHERE type = 'view'"
        ).fetchall()
    }
    assert {"v_word_error_ranking", "v_student_unit_progress", "v_class_today_activity"} <= views

    # admin 已生成
    assert _scalar("SELECT COUNT(*) FROM users WHERE role = 'admin'") == 1


def test_u2_second_migration_is_idempotent(client):
    """U2 二次启动 → 迁移不重复执行。"""
    from app.core.migrate import discover_migrations, run_migrations

    applied = run_migrations()
    assert applied == [], f"二次迁移不应再执行任何脚本，实际：{applied}"
    # 同样按目录推导，不写死（见 U1 注释）
    assert _scalar("SELECT COUNT(*) FROM schema_version") == len(discover_migrations())


# ---------------------------------------------------------------------------
# U3：健康检查
# ---------------------------------------------------------------------------

def test_u3_health_check(client):
    """U3 GET /api/health → code=0，含 db:true、schema_version=最新迁移版本。"""
    from app.core.migrate import discover_migrations

    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["db"] is True
    assert data["schema_version"] == max(m.version for m in discover_migrations())
    assert "asr_ready" in data
    assert body["trace_id"]


# ---------------------------------------------------------------------------
# U4 / U5：鉴权与角色
# ---------------------------------------------------------------------------

def test_u4_no_token_gives_10002(client):
    """U4 不带 token 访问 require_admin 路由 → code=10002，HTTP 200。"""
    r = client.get("/api/_test/admin-only")
    assert r.status_code == 200
    assert r.json()["code"] == 10002


def test_u5_student_token_gives_10003(client):
    """U5 student 角色 token 访问 admin 路由 → code=10003。"""
    from app.core.security import create_access_token

    sid = _make_student("u5_student")
    token, _ = create_access_token(user_id=sid, role="student")

    r = client.get(
        "/api/_test/admin-only",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["code"] == 10003


# ---------------------------------------------------------------------------
# U6：未捕获异常
# ---------------------------------------------------------------------------

def test_u6_uncaught_exception_returns_10006_with_trace_id(client):
    """U6 未捕获异常 → code=10006 且带 trace_id。"""
    r = client.get("/api/_test/boom")
    body = r.json()
    assert body["code"] == 10006
    assert body["trace_id"], "响应必须携带 trace_id"


# ---------------------------------------------------------------------------
# U7：参数校验
# ---------------------------------------------------------------------------

def test_u7_invalid_payload_gives_10001_with_field_name(client):
    """U7 非法 JSON → code=10001，message 含具体字段名。"""
    r = client.post("/api/_test/echo", json={"name": "x"})  # 缺 age
    body = r.json()
    assert body["code"] == 10001
    assert "age" in body["message"], f"message 应含字段名，实际：{body['message']}"


# ---------------------------------------------------------------------------
# U8：并发写串行化
# ---------------------------------------------------------------------------

def test_u8_concurrent_writes_no_lock_error(client):
    """U8 10 线程各写 100 条 → 全部成功，无 database is locked。"""
    from app.core.db import write_tx

    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(100):
                with write_tx() as conn:
                    conn.execute(
                        "INSERT INTO audit_logs (action, target_type, detail) VALUES (?, ?, ?)",
                        (f"u8_{n}", "test", str(i)),
                    )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发写入报错：{errors[:3]}"
    assert _scalar("SELECT COUNT(*) FROM audit_logs WHERE action LIKE 'u8_%'") == 1000


# ---------------------------------------------------------------------------
# U9：自动路由发现
# ---------------------------------------------------------------------------

def test_u9_router_autodiscovery(app_env):
    """U9 在 app/api/ 新建含 router 的模块 → 自动生效，未改任何注册文件。"""
    from fastapi import FastAPI

    from app.core.router_loader import load_routers

    api_dir = Path(__file__).resolve().parent.parent / "api"
    tmp_module = api_dir / "_tmp_u9_route.py"

    tmp_module.write_text(
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter(prefix='/api/_u9', tags=['_u9'])\n"
        "\n"
        "\n"
        "@router.get('/ping')\n"
        "def _ping():\n"
        "    return {'code': 0}\n",
        encoding="utf-8",
    )

    try:
        importlib.invalidate_caches()
        fresh_app = FastAPI()
        registered = load_routers(fresh_app)
        assert any("_tmp_u9_route" in name for name in registered), registered

        # ⚠️ 不能用 `fresh_app.routes` 的 path 做断言：FastAPI 0.141 起
        # `include_router()` 往 app.routes 里塞的是 `_IncludedRouter` 包装对象
        # （没有 `.path` 属性，路径是懒解析的），会比出一堆 None。
        # 改用 openapi 路径表 + 真实请求来验证「路由确实生效了」。
        assert "/api/_u9/ping" in fresh_app.openapi().get("paths", {}), (
            "openapi 路径表中没有 /api/_u9/ping"
        )

        from fastapi.testclient import TestClient

        with TestClient(fresh_app, raise_server_exceptions=False) as c:
            r = c.get("/api/_u9/ping")
            assert r.status_code == 200, r.text
            assert r.json()["code"] == 0
    finally:
        tmp_module.unlink(missing_ok=True)
        importlib.invalidate_caches()
        # 清掉缓存，避免同进程内二次调用拿到指向已删文件的模块对象
        import sys

        sys.modules.pop("app.api._tmp_u9_route", None)


# ---------------------------------------------------------------------------
# U10：SettingsService
# ---------------------------------------------------------------------------

def test_u10_settings_service_read_and_write(client):
    """U10 get_int('silence_hint_ms') == 8000；set 后立即读到新值且库中已更新。"""
    from app.services.settings_service import SettingsService
    from app.core.db import read_conn

    assert SettingsService.get_int("silence_hint_ms") == 8000

    SettingsService.set("silence_hint_ms", 9000)
    assert SettingsService.get_int("silence_hint_ms") == 9000

    with read_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'silence_hint_ms'"
        ).fetchone()
    assert row["value"] == "9000"

    # 还原，避免影响其它用例
    SettingsService.set("silence_hint_ms", 8000)


# ---------------------------------------------------------------------------
# U11：preview_store TTL
# ---------------------------------------------------------------------------

def test_u11_preview_store_expires(client):
    """U11 put 后等 TTL 过期再 get → None。"""
    from app.core import preview_store

    token = preview_store.put({"hello": "world"}, ttl=1)
    assert preview_store.get(token) == {"hello": "world"}

    time.sleep(1.2)
    assert preview_store.get(token) is None


# ---------------------------------------------------------------------------
# U12：登录限流
# ---------------------------------------------------------------------------

def test_u12_login_rate_limit(client):
    """U12 连续 6 次登录请求 → 第 6 次返回 10005。

    本用例只验证「登录档」限流桶生效，**不假设**该路径的业务 code：
    BE-02 落地真实登录接口后，前 5 次会由真实接口处理（缺参数时是 `10001`），
    而不再是桩路由的 `0`。见 `client` fixture 里的跨包集成说明。
    """
    from app.core import rate_limit

    # 清掉可能存在的计数，保证用例可重复执行
    rate_limit.clear_all()

    codes = []
    for _ in range(6):
        r = client.post("/api/auth/login", json={"username": "x", "password": "y"})
        codes.append(r.json()["code"])

    assert len(codes) == 6
    assert all(c != 10005 for c in codes[:5]), f"前 5 次不应被限流，实际：{codes}"
    assert codes[5] == 10005, f"第 6 次应被限流，实际：{codes}"

    rate_limit.clear_all()


# ---------------------------------------------------------------------------
# U13：停用账号旧 token 立即失效
# ---------------------------------------------------------------------------

def test_u13_disabled_account_token_rejected(client):
    """U13 停用某账号后用其旧 token 访问 → code=10002。"""
    from app.core.db import utc_now_iso, write_tx
    from app.core.security import create_access_token
    from app.services.settings_service import SettingsService

    sid = _make_student("u13_student")
    token, _ = create_access_token(user_id=sid, role="admin")  # 即便角色是 admin

    with write_tx() as conn:
        conn.execute(
            "UPDATE users SET status = 'disabled', updated_at = ? WHERE id = ?",
            (utc_now_iso(), sid),
        )

    r = client.get(
        "/api/_test/admin-only",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.json()["code"] == 10002

    # 顺带确认 SettingsService 的缓存没被上面的写事务搞坏
    assert SettingsService.get_int("silence_hint_ms") == 8000
