"""WP-2E 背诵事件回放 —— `GET /api/me/recitations/{id}/events`（验收测试）。

数据来源是 `recitation_events`（一期只写不读，本包首次开读）。

覆盖：响应结构 / 字段映射 / `seq` 升序 / **只回自己的数据** /
**越权与不存在同码 `10004`**（不暴露存在性） / 真链路结算后也能读到。

---

### 反向验证（契约 §9 / 50-文档 §9 的 DoD 硬要求）

`test_only_returns_own_records` 就是反向验证用例：学生 B 用**自己**的 token 去查
学生 A 的背诵事件，必须拿到 `10004`。

> 把 `me_recitations.py` 里 `int(row["student_id"]) != int(user.id)` 这段
> 「student_id 过滤」去掉后，B 会直接读到 A 的事件（`code == 0` 且非空），
> 本用例随即**转红** —— 这正是「撤掉 student_id 过滤后用例必须转红」的护栏。
>
> 真跑验证方法：临时注释掉该行 → `pytest` 该用例 FAIL → 还原 → PASS。
> （已在本地演示，见交付报告。）

另：`test_not_found_for_missing_recitation` 钉住「不存在」也返回 `10004`，
与越权同码 —— 防止实现把"记录不存在"写成空列表（`code 0`），那样会暴露
「它存在过 / 你查了个合法的空记录」。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_wp2e_"))
_DB_FILE = _TMP_DIR / "test.db"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["JWT_SECRET"] = "wp2e-replay-secret-0123456789ab"
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "wp2e-Admin-Pass"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn, write_tx  # noqa: E402
from app.main import app  # noqa: E402
from app.services import pinyin_service  # noqa: E402
from app.services.recite import ReciteEngine, ReciteService  # noqa: E402


# ---------------------------------------------------------------------------
# fixture 与工具
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """每个测试一个全新空库 + 干净限流桶 + 干净 settings / 引擎缓存。"""
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["JWT_SECRET"] = "wp2e-replay-secret-0123456789ab"
    os.environ["JWT_TTL_HOURS"] = "12"
    os.environ["ADMIN_INIT_USERNAME"] = "admin"
    os.environ["ADMIN_INIT_PASSWORD"] = "wp2e-Admin-Pass"
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE, "DB_PATH 被同进程里别的测试模块改掉了"

    for suffix in ("", "-wal", "-shm"):
        path = Path(str(_DB_FILE) + suffix)
        if path.exists():
            path.unlink()

    rate_limit.clear_all()
    ReciteService.clear_cache()

    with TestClient(app) as c:
        yield c


def _sql(query: str, params: tuple = ()) -> list[dict]:
    with read_conn() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


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


def _seed_text_unit(
    *, paragraphs: list[str], title: str = "背影", author: str = "朱自清"
) -> tuple[int, int]:
    """造一个语文单元 + 一篇课文，返回 `(unit_id, text_id)`。"""
    with write_tx() as conn:
        cursor = conn.execute(
            "INSERT INTO units (subject, grade, semester, name, status) "
            "VALUES ('chinese', 7, 'first', ?, 'active')",
            (f"第1单元-{title}",),
        )
        unit_id = int(cursor.lastrowid)
        build = pinyin_service.build_scoped(paragraphs, list(range(len(paragraphs))))
        assert build.ok, "测试数据的拼音生成失败"
        metas = [
            {
                "idx": index,
                "text": text,
                "chars": pinyin_service.count_han(text),
                "recite": True,
            }
            for index, text in enumerate(paragraphs)
        ]
        cursor = conn.execute(
            """
            INSERT INTO texts (unit_id, title, author, content_raw, content_plain,
                              content_pinyin, paragraphs, recite_scope, total_chars,
                              order_no, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'active')
            """,
            (
                unit_id,
                title,
                author,
                "\n".join(paragraphs),
                build.plain,
                build.pinyin,
                __import__("json").dumps(metas, ensure_ascii=False),
                __import__("json").dumps(list(range(len(paragraphs))), ensure_ascii=False),
                build.length,
            ),
        )
        return unit_id, int(cursor.lastrowid)


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
    client: TestClient, token: str, session_id: int, text_id: int
) -> int:
    resp = client.post(
        "/api/recitations",
        json={"session_id": session_id, "text_id": text_id, "scope": []},
        headers=_auth(token),
    )
    body = resp.json()
    assert body["code"] == 0, body
    return int(body["data"]["recitation_id"])


def _insert_recitation(
    *, student_id: int, text_id: int, session_id: int, unit_id: int
) -> int:
    """直接插一条 `recitations`（测试读写分离用，绕过实时引擎）。"""
    with write_tx() as conn:
        cursor = conn.execute(
            """
            INSERT INTO recitations
                (session_id, student_id, text_id, total_chars, matched_chars,
                 covered_ratio, restart_count, duration_ms, created_at)
            VALUES (?, ?, ?, 7, 5, 0.7, 0, 30000,
                    strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (session_id, student_id, text_id),
        )
        return int(cursor.lastrowid)


def _insert_events(recitation_id: int, events: list[dict]) -> None:
    """直接插 `recitation_events`，`events=[{seq,pos,event_type,elapsed_ms}]`。"""
    with write_tx() as conn:
        conn.executemany(
            """
            INSERT INTO recitation_events
                (recitation_id, seq, pos, char, event_type, elapsed_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    recitation_id,
                    int(e["seq"]),
                    int(e["pos"]),
                    e.get("char"),
                    e["event_type"],
                    e.get("elapsed_ms"),
                )
                for e in events
            ],
        )


def _insert_session_for(student_id: int, unit_id: int) -> int:
    """给某个学生插一条 `sessions`（供直接插 recitations 时外键引用）。"""
    with write_tx() as conn:
        cursor = conn.execute(
            """
            INSERT INTO sessions (student_id, unit_id, content_type, strategy,
                                 total_items, correct_items, status)
            VALUES (?, ?, 'chinese', '{}', 7, 5, 'finished')
            """,
            (student_id, unit_id),
        )
        return int(cursor.lastrowid)


def _events_json(client: TestClient, token: str, recitation_id: int) -> dict:
    resp = client.get(
        f"/api/me/recitations/{recitation_id}/events", headers=_auth(token)
    )
    assert resp.json()["code"] == 0, resp.json()
    return resp.json()["data"]


# ===========================================================================
# 正常读取：结构 / 字段映射 / seq 升序
# ===========================================================================


class TestReadEvents:
    def test_shape_and_fields(self, client):
        """响应 `{events:[{seq,pos,event_type,at_ms}]}`，字段齐全且类型正确。"""
        token, user_id = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        session_id = _insert_session_for(user_id, unit_id)
        rid = _insert_recitation(
            student_id=user_id, text_id=text_id, session_id=session_id, unit_id=unit_id
        )
        _insert_events(
            rid,
            [
                {"seq": 1, "pos": 0, "event_type": "matched", "elapsed_ms": 1200, "char": "我"},
                {"seq": 2, "pos": 1, "event_type": "matched", "elapsed_ms": 2600, "char": "与"},
                {"seq": 3, "pos": 4, "event_type": "hint", "elapsed_ms": 9000, "char": None},
            ],
        )

        data = _events_json(client, token, rid)
        events = data["events"]
        assert len(events) == 3

        # 字段名与契约 §15.2 完全一致（不能多不能少）
        assert set(events[0].keys()) == {"seq", "pos", "event_type", "at_ms"}
        assert events[0]["seq"] == 1
        assert events[0]["pos"] == 0
        assert events[0]["event_type"] == "matched"
        assert events[0]["at_ms"] == 1200
        assert events[2]["event_type"] == "hint"
        assert events[2]["at_ms"] == 9000

    def test_ordered_by_seq_asc(self, client):
        """乱序插入必须按 `seq` 升序返回。"""
        token, user_id = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        session_id = _insert_session_for(user_id, unit_id)
        rid = _insert_recitation(
            student_id=user_id, text_id=text_id, session_id=session_id, unit_id=unit_id
        )
        _insert_events(
            rid,
            [
                {"seq": 3, "pos": 4, "event_type": "missed", "elapsed_ms": 9000},
                {"seq": 1, "pos": 0, "event_type": "matched", "elapsed_ms": 800},
                {"seq": 2, "pos": 2, "event_type": "matched", "elapsed_ms": 2000},
            ],
        )

        data = _events_json(client, token, rid)
        assert [e["seq"] for e in data["events"]] == [1, 2, 3]
        assert [e["pos"] for e in data["events"]] == [0, 2, 4]

    def test_at_ms_is_null_when_absent(self, client):
        """一期引擎落库的 `elapsed_ms` 多为 NULL（一期只写不读未填），须如实训 `null`。"""
        token, user_id = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        session_id = _insert_session_for(user_id, unit_id)
        rid = _insert_recitation(
            student_id=user_id, text_id=text_id, session_id=session_id, unit_id=unit_id
        )
        _insert_events(
            rid,
            [{"seq": 1, "pos": 0, "event_type": "matched", "elapsed_ms": None}],
        )

        data = _events_json(client, token, rid)
        assert data["events"][0]["at_ms"] is None

    def test_empty_when_no_events(self, client):
        """背诵记录存在但一条事件都没有 → `{events: []}`，不是报错也不是 404。"""
        token, user_id = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        session_id = _insert_session_for(user_id, unit_id)
        rid = _insert_recitation(
            student_id=user_id, text_id=text_id, session_id=session_id, unit_id=unit_id
        )

        data = _events_json(client, token, rid)
        assert data == {"events": []}


# ===========================================================================
# 鉴权 / 归属 / 不存在同码
# ===========================================================================


class TestOwnershipAndExistence:
    def test_only_returns_own_records(self, client):
        """**只回自己的数据** + 反向验证。

        学生 A 的背诵里塞了 3 条事件；学生 B 拿自己的 token 去查 A 的背诵 → `10004`。

        ⚠️ 反向验证：把 `me_recitations.py` 的 `int(row["student_id"]) != int(user.id)`
        这段 student_id 过滤去掉后，B 会读到 A 的 3 条事件（`code == 0`），
        下面的 `assert code == 10004` 立即转红。
        """
        token_a, user_a = _register(client, "20260001")
        token_b, _ = _register(client, "20260002")
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        session_a = _insert_session_for(user_a, unit_id)
        rid_a = _insert_recitation(
            student_id=user_a, text_id=text_id, session_id=session_a, unit_id=unit_id
        )
        _insert_events(
            rid_a,
            [
                {"seq": 1, "pos": 0, "event_type": "matched", "elapsed_ms": 1000},
                {"seq": 2, "pos": 1, "event_type": "matched", "elapsed_ms": 2000},
                {"seq": 3, "pos": 2, "event_type": "matched", "elapsed_ms": 3000},
            ],
        )

        # A 自己看得到
        assert len(_events_json(client, token_a, rid_a)["events"]) == 3

        # B 看 A 的 → 必须是 10004（不暴露「A 有这么一条背诵」）
        resp_b = client.get(
            f"/api/me/recitations/{rid_a}/events", headers=_auth(token_b)
        )
        assert resp_b.json()["code"] == 10004
        assert resp_b.json()["data"] is None

    def test_not_found_for_missing_recitation(self, client):
        """「不存在」也返回 `10004`（与越权同码，不暴露存在性）。"""
        token, _ = _register(client)
        resp = client.get(
            "/api/me/recitations/999999/events", headers=_auth(token)
        )
        assert resp.json()["code"] == 10004
        assert resp.json()["data"] is None

    def test_need_auth(self, client):
        resp = client.get("/api/me/recitations/1/events")
        assert resp.json()["code"] == 10002

    def test_cannot_see_other_session_recitation(self, client):
        """跨人：A 的 session 下开的背诵，B 来查 → 10004（复用归属校验）。"""
        token_a, user_a = _register(client, "20260001")
        token_b, user_b = _register(client, "20260002")
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        session_a = _insert_session_for(user_a, unit_id)
        rid_a = _insert_recitation(
            student_id=user_a, text_id=text_id, session_id=session_a, unit_id=unit_id
        )
        _insert_events(
            rid_a, [{"seq": 1, "pos": 0, "event_type": "matched", "elapsed_ms": 500}]
        )

        # 确保 B 也有一个 session（避免"因为 session 不存在"而误判）
        _insert_session_for(user_b, unit_id)
        resp_b = client.get(
            f"/api/me/recitations/{rid_a}/events", headers=_auth(token_b)
        )
        assert resp_b.json()["code"] == 10004


# ===========================================================================
# 真链路：开背 → feed → settle → 事件可读
# ===========================================================================


class TestRealFlow:
    def _recite(self, client: TestClient, token: str, unit_id: int, text_id: int, fed: str) -> int:
        session_id = _start_chinese_session(client, token, unit_id)
        rid = _create_recitation(client, token, session_id, text_id)
        feed = client.post(
            f"/api/recitations/{rid}/feed",
            json={"partial_text": fed},
            headers=_auth(token),
        )
        assert feed.json()["code"] == 0, feed.json()
        settle = client.post(
            f"/api/recitations/{rid}/settle",
            json={"transcript": fed, "finish_reason": "manual"},
            headers=_auth(token),
        )
        assert settle.json()["code"] == 0, settle.json()
        return rid

    def test_events_readable_after_settle(self, client):
        """真链路：背完结算后，回放接口能读到落库的事件，且按 seq 升序。"""
        token, _ = _register(client)
        unit_id, text_id = _seed_text_unit(paragraphs=["我与父亲不相见"])
        rid = self._recite(client, token, unit_id, text_id, "我与父")

        events = _events_json(client, token, rid)["events"]
        # 喂了「我与父」→ 至少 3 条 matched
        matched = [e for e in events if e["event_type"] == "matched"]
        assert len(matched) == 3
        assert [e["pos"] for e in matched] == [0, 1, 2]
        # 所有事件 seq 连续递增
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        # 一期引擎未填 elapsed_ms → at_ms 全 null
        assert all(e["at_ms"] is None for e in events)
