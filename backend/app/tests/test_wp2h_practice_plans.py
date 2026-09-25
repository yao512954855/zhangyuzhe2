"""WP-2H 保存「我的练习方案」 —— `practice_plans` CRUD（验收测试）。

覆盖：列表 / 存 / 改 / 删 / **只回自己的数据** / **越权与不存在同码 `70001`** /
**payload 往返无损** / 参数校验。

---

### 反向验证（契约 §9 / 50-文档 §9 的 DoD 硬要求）

`test_only_returns_own_plans` 与 `test_other_student_cannot_delete` 是反向验证用例：
把 `plan_service.py`（或 `me_plans.py`）里所有 `WHERE ... AND student_id = ?`
的归属过滤去掉后，学生 B 会**读到 / 删掉**学生 A 的方案（code 变成 0），
下面断言 `code == 70001` / 「列表为空」会立即转红 —— 这正是
「撤掉 student_id 过滤后用例必须转红」的护栏。

真跑验证方法：把 `me_plans.py` 的 `get_own_plan` / `delete_plan` 的 `student_id`
过滤注释掉 → 跑本文件相关用例 FAIL → 还原 → PASS（已在本地演示）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_wp2h_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "wp2h-plan-secret-0123456789ab"
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "wp2h-Admin-Pass"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx  # noqa: E402
from app.core.errors import ErrorCode  # noqa: E402
from app.main import app  # noqa: E402


# ---------------------------------------------------------------------------
# fixture 与工具
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """每个测试一个全新空库 + 干净限流桶 + 干净配置缓存。"""
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = "wp2h-plan-secret-0123456789ab"
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = "admin"
    os.environ["ADMIN_INIT_PASSWORD"] = "wp2h-Admin-Pass"
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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, username: str = "20260001") -> tuple[str, int]:
    resp = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "Student-Pass-1",
            "real_name": f"学生{username}",
        },
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["access_token"], body["data"]["user"]["id"]


def _create_plan(
    client: TestClient,
    token: str,
    *,
    name: str = "周末复习",
    subject: str = "english",
    payload: dict | None = None,
) -> int:
    if payload is None:
        payload = {
            "scope": "unit",
            "count_mode": "random_n",
            "count": 10,
            "order": "sequential",
            "mode": "cn2en",
            "time_limit": {"kind": "perQuestion", "seconds": 30},
        }
    resp = client.post(
        "/api/me/practice-plans",
        json={"name": name, "subject": subject, "payload": payload},
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    return int(body["data"]["id"])


def _list(client: TestClient, token: str) -> dict:
    resp = client.get("/api/me/practice-plans", headers=_auth(token))
    assert resp.json()["code"] == 0, resp.json()
    return resp.json()["data"]


# ===========================================================================
# 存（POST）
# ===========================================================================


class TestCreate:
    def test_create_returns_id(self, client):
        token, _ = _register(client)
        pid = _create_plan(client, token)
        # 列表里能看到刚存的
        items = _list(client, token)["items"]
        assert any(p["id"] == pid for p in items)

    def test_name_subject_payload_fields(self, client):
        token, _ = _register(client)
        payload = {
            "scope": "wrong",
            "count_mode": "all",
            "order": "wrong_first",
            "mode": "en2cn",
            "time_limit": {"kind": "none", "seconds": 0},
        }
        pid = _create_plan(client, token, name="只练错题", subject="english", payload=payload)
        plan = next(p for p in _list(client, token)["items"] if p["id"] == pid)
        assert plan["name"] == "只练错题"
        assert plan["subject"] == "english"
        assert plan["payload"] == payload
        # 字段名与契约一致
        assert set(plan.keys()) == {"id", "name", "subject", "payload", "created_at", "updated_at"}

    def test_empty_name_rejected(self, client):
        token, _ = _register(client)
        resp = client.post(
            "/api/me/practice-plans",
            json={"name": "   ", "subject": "english", "payload": {"scope": "unit"}},
            headers=_auth(token),
        )
        # 空/纯空白 name → 10001（Pydantic min_length + strip_whitespace）
        assert resp.json()["code"] == ErrorCode.PARAM_INVALID

    def test_invalid_subject_rejected(self, client):
        token, _ = _register(client)
        resp = client.post(
            "/api/me/practice-plans",
            json={"name": "x", "subject": "math", "payload": {"scope": "unit"}},
            headers=_auth(token),
        )
        assert resp.json()["code"] == ErrorCode.PARAM_INVALID

    def test_payload_must_be_object(self, client):
        token, _ = _register(client)
        # 裸标量不是合法快照
        resp = client.post(
            "/api/me/practice-plans",
            json={"name": "x", "subject": "english", "payload": "hello"},
            headers=_auth(token),
        )
        assert resp.json()["code"] == ErrorCode.PARAM_INVALID

    def test_need_auth(self, client):
        resp = client.post(
            "/api/me/practice-plans",
            json={"name": "x", "subject": "english", "payload": {}},
        )
        assert resp.json()["code"] == ErrorCode.UNAUTHORIZED


# ===========================================================================
# 列表 + 学生隔离（反向验证）
# ===========================================================================


class TestListAndIsolation:
    def test_only_returns_own_plans(self, client):
        """**只回自己的数据** + 反向验证。

        学生 A 存了 2 条；学生 B 列表必须为空。
        把 `plan_service.list_plans` 的 `WHERE student_id = ?` 去掉 → B 会看到
        A 的 2 条 → 下面 `assert items == []` 立即转红。
        """
        token_a, _ = _register(client, "20260001")
        token_b, _ = _register(client, "20260002")
        _create_plan(client, token_a, name="A-1")
        _create_plan(client, token_a, name="A-2")

        items_b = _list(client, token_b)["items"]
        assert items_b == []

        items_a = _list(client, token_a)["items"]
        assert {p["name"] for p in items_a} == {"A-1", "A-2"}


# ===========================================================================
# 改（PUT）/ 删（DELETE）+ 越权同码 70001
# ===========================================================================


class TestUpdateDeleteAndOwnership:
    def test_update_own_plan(self, client):
        token, _ = _register(client)
        pid = _create_plan(client, token, name="旧名")
        resp = client.put(
            f"/api/me/practice-plans/{pid}",
            json={"name": "新名", "payload": {"scope": "range"}},
            headers=_auth(token),
        )
        assert resp.json()["code"] == 0, resp.json()
        plan = next(p for p in _list(client, token)["items"] if p["id"] == pid)
        assert plan["name"] == "新名"
        assert plan["payload"] == {"scope": "range"}
        assert plan["updated_at"] is not None

    def test_update_empty_body_rejected(self, client):
        token, _ = _register(client)
        pid = _create_plan(client, token)
        resp = client.put(
            f"/api/me/practice-plans/{pid}",
            json={},
            headers=_auth(token),
        )
        assert resp.json()["code"] == ErrorCode.PARAM_INVALID

    def test_delete_own_plan(self, client):
        token, _ = _register(client)
        pid = _create_plan(client, token)
        resp = client.delete(f"/api/me/practice-plans/{pid}", headers=_auth(token))
        assert resp.json()["code"] == 0, resp.json()
        # 删完列表里没了
        assert all(p["id"] != pid for p in _list(client, token)["items"])

    def test_delete_missing_returns_70001(self, client):
        """「不存在」也返回 `70001`（与越权同码，不暴露存在性）。"""
        token, _ = _register(client)
        resp = client.delete("/api/me/practice-plans/999999", headers=_auth(token))
        assert resp.json()["code"] == ErrorCode.PLAN_NOT_FOUND
        assert resp.json()["data"] is None

    def test_other_student_cannot_delete(self, client):
        """**越权 → 70001** + 反向验证。

        学生 A 存方案；学生 B 用自己 token 去 DELETE A 的方案 → `70001`，
        且 A 的方案**原封不动**。

        把 `plan_service.delete_plan` 的 `WHERE ... AND student_id = ?` 去掉
        → B 的删除会成功（code 0，A 的方案被删）→ 下面断言转红。
        """
        token_a, _ = _register(client, "20260001")
        token_b, _ = _register(client, "20260002")
        pid = _create_plan(client, token_a, name="A-方案")

        resp_b = client.delete(f"/api/me/practice-plans/{pid}", headers=_auth(token_b))
        assert resp_b.json()["code"] == ErrorCode.PLAN_NOT_FOUND

        # A 的方案还在
        items_a = _list(client, token_a)["items"]
        assert any(p["id"] == pid and p["name"] == "A-方案" for p in items_a)

    def test_other_student_cannot_update(self, client):
        """B 改 A 的方案 → `70001`，且 A 的方案内容不变。"""
        token_a, _ = _register(client, "20260001")
        token_b, _ = _register(client, "20260002")
        pid = _create_plan(client, token_a, name="A-原")

        resp_b = client.put(
            f"/api/me/practice-plans/{pid}",
            json={"name": "被B改了"},
            headers=_auth(token_b),
        )
        assert resp_b.json()["code"] == ErrorCode.PLAN_NOT_FOUND

        plan_a = next(p for p in _list(client, token_a)["items"] if p["id"] == pid)
        assert plan_a["name"] == "A-原"

    def test_other_student_cannot_see_via_list(self, client):
        """B 列表里不能出现 A 的方案（即使 B 知道 id 也查不到 GET-by-id 端点，
        但列表这道关必须守住）。"""
        token_a, _ = _register(client, "20260001")
        token_b, _ = _register(client, "20260002")
        _create_plan(client, token_a, name="A-secret")
        # B 列表空
        assert _list(client, token_b)["items"] == []


# ===========================================================================
# payload 往返无损
# ===========================================================================


class TestPayloadRoundTrip:
    def test_nested_and_list_preserved(self, client):
        token, _ = _register(client)
        payload = {
            "scope": "lesson",
            "selected_text_ids": [301, 303, 302],
            "range_start": 1,
            "range_end": 12,
            "count_mode": "first_n",
            "count": 5,
            "order": "random",
            "mode": "mixed",
            "time_limit": {"kind": "wholeGroup", "seconds": 600},
            "nested": {"a": [1, 2, 3], "b": {"c": "文本"}, "flag": True},
            "ratio": 0.88,
            "empty": None,
        }
        pid = _create_plan(client, token, name="复杂快照", payload=payload)
        plan = next(p for p in _list(client, token)["items"] if p["id"] == pid)
        # 完全相等：嵌套、列表、浮点、null、unicode 都无损
        assert plan["payload"] == payload
        assert plan["payload"]["selected_text_ids"] == [301, 303, 302]
        assert plan["payload"]["ratio"] == 0.88
        assert plan["payload"]["nested"]["b"]["c"] == "文本"

    def test_chinese_payload_without_mode(self, client):
        """语文方案如实**不发 mode**（契约口径：语文没有 mode 维度）。"""
        token, _ = _register(client)
        payload = {
            "scope": "lesson",
            "selected_text_ids": [12, 13],
            "time_limit": {"kind": "wholeGroup", "seconds": 300},
        }
        pid = _create_plan(client, token, name="语文背诵", subject="chinese", payload=payload)
        plan = next(p for p in _list(client, token)["items"] if p["id"] == pid)
        assert plan["subject"] == "chinese"
        assert plan["payload"] == payload
        assert "mode" not in plan["payload"]

    def test_update_payload_roundtrip(self, client):
        token, _ = _register(client)
        pid = _create_plan(client, token, payload={"scope": "unit"})
        new_payload = {
            "scope": "unmastered",
            "count_mode": "all",
            "order": "reverse",
            "mode": "cn2en",
            "time_limit": {"kind": "none", "seconds": 0},
        }
        resp = client.put(
            f"/api/me/practice-plans/{pid}",
            json={"payload": new_payload},
            headers=_auth(token),
        )
        assert resp.json()["code"] == 0, resp.json()
        plan = next(p for p in _list(client, token)["items"] if p["id"] == pid)
        assert plan["payload"] == new_payload


# ===========================================================================
# 数据库层直接核对
# ===========================================================================


class TestDbLayer:
    def test_stored_payload_is_json_text(self, client):
        """直接查库确认 payload 以合法 JSON 文本落库（供反向验证/排障）。"""
        token, _ = _register(client)
        payload = {"scope": "unit", "count": 7}
        pid = _create_plan(client, token, payload=payload)
        with read_conn() as conn:
            row = conn.execute(
                "SELECT payload FROM practice_plans WHERE id = ?", (pid,)
            ).fetchone()
        import json

        assert json.loads(row["payload"]) == payload

    def test_cascade_on_user_delete(self, client):
        """用户被删 → 其方案级联删除（ON DELETE CASCADE，契约 §15.1）。"""
        token_a, user_a = _register(client, "20260001")
        _create_plan(client, token_a, name="将随账号消失")

        # 直接删 users（绕过业务逻辑，模拟级联）
        with write_tx() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_a,))

        with read_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM practice_plans WHERE student_id = ?",
                (user_a,),
            ).fetchone()["n"]
        assert n == 0
