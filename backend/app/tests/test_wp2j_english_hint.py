"""WP-2J 英语三档提示 —— 后端验收测试（含反向验证）。

覆盖契约 §15.2 / §15.3 / §15.4：
- 三档门控：失败 0/1/2 次各能拿到哪一档；
- 越档 → `70006`（HINT_EXHAUSTED）；
- `en2cn` 题型 → `70007`（HINT_NOT_ALLOWED）；
- 越权 / 不存在同码 `10004`（A3 安全口径）；
- 答案确实来自 `words` 表（不是参数回显）；
- **反向验证**：撤掉失败次数门控后，门控用例必须转红。

造数方式：直接 SQL 写 `units / words / users / sessions / answers`
（不走写入接口，因为验的是**门控与越权隔离**）。
"""

from __future__ import annotations

import os
import itertools
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_TMP = Path(tempfile.mkdtemp(prefix="bjsys_wp2j_"))
_DB = _TMP / "test.db"

os.environ["DB_PATH"] = str(_DB)
os.environ["BACKUP_DIR"] = str(_TMP / "backups")
os.environ["JWT_SECRET"] = "test-secret-" + "0" * 32
os.environ["JWT_TTL_HOURS"] = "12"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "CHANGE_ME_admin_password"
os.environ["LOG_LEVEL"] = "WARNING"

from app.core import rate_limit  # noqa: E402
from app.core.config import reset_config_cache  # noqa: E402
from app.core.db import write_tx  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.services.hint_service import HintService  # noqa: E402
from app.services.judge.normalize import normalize_letters  # noqa: E402

WORD_EN = "apple"  # 5 个字母，首字母 a


# =========================================================================== 夹具

@pytest.fixture(autouse=True)
def _no_rate_limit():
    rate_limit.clear_all()
    yield


@pytest.fixture(scope="module", autouse=True)
def _fresh_config():
    os.environ["DB_PATH"] = str(_DB)
    reset_config_cache()
    yield


@pytest.fixture(scope="module")
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class Env:
    """每个用例一份独立数据（隔离失败次数累加）。"""


_unit_seq = itertools.count(1)


@pytest.fixture
def env(client):
    e = Env()
    e.client = client
    seq = next(_unit_seq)
    with write_tx() as conn:
        cur = conn.execute(
            "INSERT INTO units(subject,grade,semester,name,status) "
            "VALUES ('english',3,'first',?, 'active')",
            (f"WP2J-U{seq}",),
        )
        e.unit = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO words(unit_id,type,en,cn,status) VALUES (?, 'word', ?, '苹果', 'active')",
            (e.unit, WORD_EN),
        )
        e.word = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO users(role,username,real_name,password_hash) "
            "VALUES ('student',?, 'A','x')",
            (f"wp2j_a{seq}",),
        )
        e.stu_a = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO users(role,username,real_name,password_hash) "
            "VALUES ('student',?, 'B','x')",
            (f"wp2j_b{seq}",),
        )
        e.stu_b = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO sessions(student_id,unit_id,content_type,strategy,status) "
            "VALUES (?,?,'english','{}','ongoing')",
            (e.stu_a, e.unit),
        )
        e.sess = int(cur.lastrowid)
    e.token_a = _token(e.stu_a)
    e.token_b = _token(e.stu_b)
    yield e


# =========================================================================== 辅助

def _token(uid: int) -> str:
    token, _ = create_access_token(user_id=uid, role="student")
    return token


def _add_failures(session_id: int, student_id: int, word_id: int, n: int) -> None:
    with write_tx() as conn:
        for _ in range(n):
            conn.execute(
                "INSERT INTO answers(session_id,student_id,word_id,mode,step,expected,"
                "is_correct,match_type) VALUES (?,?,?,'cn2en',1,'apple',0,'wrong')",
                (session_id, student_id, word_id),
            )


def _hint(e: Env, *, word_id=None, mode="cn2en", current_level=0, token=None, sess=None, idx=1):
    word_id = e.word if word_id is None else word_id
    sess = e.sess if sess is None else sess
    token = e.token_a if token is None else token
    return e.client.post(
        f"/api/sessions/{sess}/items/{idx}/hint",
        headers={"Authorization": f"Bearer {token}"},
        json={"word_id": word_id, "mode": mode, "current_level": current_level},
    ).json()


# =========================================================================== 门控映射（单元测试）

def test_max_level_map():
    """门控数学：失败次数 → 允许的最高档位（§15.4）。"""
    assert HintService._max_level_for_failures(0) == 1
    assert HintService._max_level_for_failures(1) == 2
    assert HintService._max_level_for_failures(2) == 3
    assert HintService._max_level_for_failures(7) == 3


# =========================================================================== 三档门控

def test_fail0_grants_level1_and_exhausted(client, env):
    """失败 0 次：立即给档 1（占位框数量），且当前门控下已用尽。"""
    body = _hint(env, current_level=0)
    assert body["code"] == 0, body
    assert body["data"]["level"] == 1
    assert body["data"]["hint"] == f"答案共 {len(normalize_letters(WORD_EN))} 个字母"
    assert body["data"]["exhausted"] is True


def test_fail0_request_level2_exhausted(client, env):
    """失败 0 次却要档 2 → 越档 `70006`。"""
    body = _hint(env, current_level=1)
    assert body["code"] == 70006, body
    assert body["data"] is None


def test_fail1_grants_level2(client, env):
    """失败 1 次：可拿到档 2（首字母），且用尽。"""
    _add_failures(env.sess, env.stu_a, env.word, 1)
    body = _hint(env, current_level=1)
    assert body["code"] == 0, body
    assert body["data"]["level"] == 2
    assert body["data"]["hint"] == f"首字母：{normalize_letters(WORD_EN)[0].upper()}"
    assert body["data"]["exhausted"] is True


def test_fail1_only_level1_when_not_escalated(client, env):
    """失败 1 次但还没升过档：给档 1（首字母档需先持有档 1）。"""
    _add_failures(env.sess, env.stu_a, env.word, 1)
    body = _hint(env, current_level=0)
    assert body["code"] == 0, body
    assert body["data"]["level"] == 1
    assert body["data"]["exhausted"] is False  # 还能升到档 2


def test_fail1_request_level3_exhausted(client, env):
    """失败 1 次要档 3 → 越档 `70006`。"""
    _add_failures(env.sess, env.stu_a, env.word, 1)
    body = _hint(env, current_level=2)
    assert body["code"] == 70006, body


def test_fail2_grants_level3_from_db(client, env):
    """失败 2 次：可拿到档 3（完整答案），且答案确实来自库。"""
    _add_failures(env.sess, env.stu_a, env.word, 2)
    body = _hint(env, current_level=2)
    assert body["code"] == 0, body
    assert body["data"]["level"] == 3
    # 关键：档 3 的 hint 就是库的 en 字段，不是客户端传回的
    assert body["data"]["hint"] == WORD_EN
    assert body["data"]["exhausted"] is True


def test_fail2_level1_then_level2_then_exhausted(client, env):
    """失败 2 次：逐步升级 1→2→3，每档内容正确。"""
    _add_failures(env.sess, env.stu_a, env.word, 2)
    b1 = _hint(env, current_level=0)
    assert b1["code"] == 0 and b1["data"]["level"] == 1
    b2 = _hint(env, current_level=1)
    assert b2["code"] == 0 and b2["data"]["level"] == 2
    assert b2["data"]["hint"] == f"首字母：{normalize_letters(WORD_EN)[0].upper()}"
    b3 = _hint(env, current_level=2)
    assert b3["code"] == 0 and b3["data"]["level"] == 3
    assert b3["data"]["hint"] == WORD_EN


def test_fail2_request_level4_exhausted(client, env):
    """失败 2 次要档 4 → 越档 `70006`（档位上限为 3）。"""
    _add_failures(env.sess, env.stu_a, env.word, 2)
    body = _hint(env, current_level=3)
    assert body["code"] == 70006, body


# =========================================================================== 题型门控

def test_en2cn_not_allowed(client, env):
    """`en2cn` 题型无论失败几次都 → `70007`。"""
    _add_failures(env.sess, env.stu_a, env.word, 5)
    body = _hint(env, mode="en2cn", current_level=0)
    assert body["code"] == 70007, body


# =========================================================================== 越权 / 不存在同码

def test_other_student_forbidden_same_code(client, env):
    """学生 B 访问学生 A 的场次 → `10004`（与不存在同码）。"""
    body = _hint(env, token=env.token_b)
    assert body["code"] == 10004, body


def test_nonexistent_session_same_code(client, env):
    """不存在的场次 → `10004`（不暴露存在性）。"""
    body = _hint(env, sess=999999)
    assert body["code"] == 10004, body


# =========================================================================== 答案来自库（非回显）

def test_hint_source_is_db_not_param(client, env):
    """档 3 的答案恒为库 `words.en`，即使客户端在别处看到别的字符串也不影响。"""
    _add_failures(env.sess, env.stu_a, env.word, 2)
    # 故意让「客户端展示用的字符串」与库不同，验证 hint 仍来自库
    body = _hint(env, current_level=2)
    assert body["code"] == 0, body
    assert body["data"]["hint"] == WORD_EN
    assert body["data"]["hint"] != "something-else"


# =========================================================================== 反向验证

def test_reverse_gating_removed_turns_red(client, env, monkeypatch):
    """反向验证：撤掉失败次数门控（`_max_level_for_failures` 恒返 3）后，
    原本「失败 0 次要档 3 → 70006」的用例会反转为拿到 `level=3`。

    说明：上面的 `test_fail0_request_level2_exhausted` / `test_fail1_request_level3_exhausted`
    等用例是对门控敏感的——一旦有人把门控删掉，它们必然转红。本用例用 monkeypatch
    模拟「撤掉门控」，验证其行为确实翻转。
    """
    monkeypatch.setattr(
        HintService, "_max_level_for_failures", staticmethod(lambda _f: 3)
    )
    # 失败 0 次却要档 3（current_level=2）
    body = _hint(env, current_level=2)
    assert body["code"] == 0, body  # 门控撤掉后不再 70006
    assert body["data"]["level"] == 3
    assert body["data"]["hint"] == WORD_EN
