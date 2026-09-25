"""WP-BE-08 服务发现与运维 —— 数据备份部分（F6 / F7 / F8）。

覆盖 10b 的验收用例：U6（手动备份 + 校验 + 审计）、U7（保留 30 份）、U8（损坏库校验失败），
外加备份命名、保留策略、`verify_integrity` 边界、以及 F7 接口的鉴权与失败语义。

### 为什么每个用例都换一个 BACKUP_DIR
备份是**按目录里全部 `app_*.db` 做保留清理**的。若共用目录，一个用例造出来的
31 个文件会把另一个用例刚备份出来的文件清掉，用例之间互相影响、失败信息极难读懂。
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_be08_backup_"))
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
from app.core.db import read_conn  # noqa: E402
from app.main import app  # noqa: E402
from app.services import backup_service  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """清掉进程级限流计数（登录限 5 次/分钟，会跨测试模块累积 → 假 `10005`）。

    见 `test_be08_discovery.py` 同名 fixture 的说明。
    """
    rate_limit.clear_all()
    yield


@pytest.fixture(scope="module", autouse=True)
def _fresh_config():
    """把配置重新指到本模块的临时库（理由见 `test_be08_discovery.py` 同名 fixture）。

    同一个 pytest 进程里先跑的模块会把 `DB_PATH` 定死在 `lru_cache` 里，
    后面的模块必须显式重设 + 清缓存，否则备份会因为「数据库文件不存在」而全红。
    """
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


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把备份目录与数据库路径都指到本用例私有目录。"""
    directory = tmp_path / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        backup_service,
        "get_settings",
        lambda: SimpleNamespace(db_file=_DB_FILE, backup_dir=directory),
    )
    return directory


def _audits(action: str) -> list[dict]:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT detail FROM audit_logs WHERE action = ? ORDER BY id DESC", (action,)
        ).fetchall()
    return [json.loads(r["detail"]) for r in rows]


def _admin_headers(client) -> dict:
    """登录管理者并返回鉴权头。

    单独抽出来是为了**失败信息可读**：直接 `["data"]["access_token"]` 在
    登录被拒时会抛 `TypeError: 'NoneType' object is not subscriptable`，
    完全看不出是 20002（密码错）还是 10005（触发锁定）还是别的。
    """
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "CHANGE_ME_admin_password", "role": "admin"},
    ).json()
    assert resp.get("code") == 0, f"admin 登录失败：{resp}"
    return {"Authorization": f"Bearer {resp['data']['access_token']}"}


def _make_history(directory: Path, count: int, *, start_day: int = 1) -> list[str]:
    """造 `count` 份 2026-08-xx 的历史备份文件，返回文件名（升序）。

    选 8 月是因为它有 31 天 —— U7 需要 31 份。
    """
    names: list[str] = []
    for i in range(count):
        day = start_day + i
        assert day <= 31, "8 月只有 31 天，造数超出范围"
        name = f"app_202608{day:02d}.db"
        (directory / name).write_bytes(b"placeholder")
        names.append(name)
    assert len(names) == count
    return names


# =========================================================================== 命名


def test_backup_file_naming():
    """F6：定时备份固定名 `app_YYYYMMDD.db`；手动备份带秒，**不覆盖当天定时备份**。"""
    moment = datetime(2026, 9, 24, 10, 15, 30, tzinfo=timezone.utc)
    assert backup_service.backup_file_name(daily=True, now=moment) == "app_20260924.db"
    assert (
        backup_service.backup_file_name(daily=False, now=moment) == "app_20260924_101530.db"
    )
    assert backup_service.backup_file_name(daily=True, now=moment) != backup_service.backup_file_name(
        daily=False, now=moment
    )


# =========================================================================== U6


def test_u6_manual_backup_creates_file_and_audits(client, sandbox):
    """U6：手动备份 → 目录里生成文件、`integrity_check` 通过、`audit_logs` 有记录。"""
    outcome = backup_service.create_backup(daily=False, operator_id=1, ip="192.168.1.9")
    assert outcome.ok is True, outcome.error

    target = Path(outcome.path)
    assert target.is_file()
    assert target.parent == sandbox
    assert target.name.startswith("app_") and target.name.endswith(".db")
    assert outcome.size_bytes > 0
    assert outcome.integrity_ok is True
    assert outcome.integrity_detail == "ok"
    assert outcome.kept >= 1

    # 副本必须能独立打开且表结构完整（这才是「备份可用」的真正含义）
    conn = sqlite3.connect(str(target))
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()
    assert {"users", "units", "answers", "word_mastery"} <= tables

    records = _audits("backup.create")
    assert records, "备份成功必须写审计"
    assert records[0]["ok"] is True
    assert records[0]["file"] == target.name
    assert records[0]["integrity_ok"] is True


def test_u6_daily_backup_uses_fixed_name(client, sandbox):
    """F6：定时备份用固定名，当日重跑覆盖同名文件而不是堆积。"""
    first = backup_service.create_backup(daily=True)
    assert first.ok is True
    assert first.file == f"app_{datetime.now(timezone.utc):%Y%m%d}.db"

    second = backup_service.create_backup(daily=True)
    assert second.ok is True
    assert second.file == first.file
    assert len(list(sandbox.glob("app_*.db"))) == 1, "同日定时备份应覆盖，不是新增"


# =========================================================================== U7


def test_u7_prune_keeps_thirty(client, sandbox):
    """U7：预置 31 份后触发清理 → 最旧被删，保留 30 份。"""
    names = _make_history(sandbox, 31)
    assert len(backup_service.list_backups()) == 31

    removed = backup_service.prune_backups(keep=30)

    assert removed == [names[0]], f"应只删最旧的一份，实际 {removed}"
    remaining = [p.name for p in backup_service.list_backups()]
    assert len(remaining) == 30
    assert names[0] not in remaining


def test_u7_prune_is_noop_below_limit(client, sandbox):
    """不到上限时**不能**误删（保留策略最容易写成「总是删最旧」）。"""
    _make_history(sandbox, 3)
    assert backup_service.prune_backups(keep=30) == []
    assert len(backup_service.list_backups()) == 3


def test_u7_backup_triggers_prune(client, sandbox):
    """备份完成后会顺带做保留清理，最终稳定维持在上限以内。"""
    _make_history(sandbox, 31)
    outcome = backup_service.create_backup(daily=False)
    assert outcome.ok is True
    assert len(backup_service.list_backups()) == 30
    assert len(outcome.removed) == 2, "31 份历史 + 1 份新的 = 32 → 应删掉 2 份最旧的"
    assert outcome.kept == 30


def test_prune_ignores_foreign_files(client, sandbox):
    """清理只认 `app_*.db`，别把用户放进备份目录的其他文件删了。"""
    (sandbox / "说明.txt").write_text("不要删我", encoding="utf-8")
    (sandbox / "other.db").write_bytes(b"x")
    _make_history(sandbox, 31)
    backup_service.prune_backups(keep=30)
    assert (sandbox / "说明.txt").is_file()
    assert (sandbox / "other.db").is_file()


# =========================================================================== U8


def test_u8_corrupt_backup_fails_integrity(client, sandbox):
    """U8：用损坏的 db 触发校验 → `integrity_check` 失败，日志与审计都明确报错。"""
    broken = sandbox / "app_19990101.db"
    broken.write_bytes(b"this is definitely not a sqlite database" * 10)

    ok, detail = backup_service.verify_integrity(broken)
    assert ok is False
    assert detail, "失败必须给出可读原因"


def test_u8_backup_from_corrupt_source_records_failure(client, sandbox, monkeypatch):
    """源库本身损坏时，`VACUUM INTO` 会失败 —— 必须**不抛异常**且留下失败审计。"""
    corrupt_db = sandbox / "corrupt_source.db"
    corrupt_db.write_bytes(b"not a database" * 200)
    monkeypatch.setattr(
        backup_service,
        "get_settings",
        lambda: SimpleNamespace(db_file=corrupt_db, backup_dir=sandbox),
    )

    outcome = backup_service.create_backup(daily=False, operator_id=1)

    assert outcome.ok is False
    assert outcome.error
    assert outcome.integrity_ok is None or outcome.integrity_ok is False
    assert not list(sandbox.glob("app_*.db")), "失败时不能留下半成品副本"

    records = _audits("backup.failed")
    assert records, "失败必须写审计（否则运维无从追查）"
    assert records[0]["ok"] is False
    assert records[0]["error"]


def test_u8_verify_integrity_edge_cases(client, sandbox):
    """校验的边界：不存在 / 空文件 / 正常文件。"""
    assert backup_service.verify_integrity(sandbox / "nope.db") == (False, "备份文件不存在")

    empty = sandbox / "app_empty.db"
    empty.write_bytes(b"")
    ok, detail = backup_service.verify_integrity(empty)
    assert ok is False and "空" in detail

    good = backup_service.create_backup(daily=False)
    assert backup_service.verify_integrity(Path(good.path)) == (True, "ok")


def test_failed_backup_does_not_prune(client, sandbox, monkeypatch):
    """校验失败时**不做保留清理** —— 否则坏副本会把好备份挤掉。"""
    names = _make_history(sandbox, 31)
    corrupt_db = sandbox / "corrupt_source.db"
    corrupt_db.write_bytes(b"nope" * 100)
    monkeypatch.setattr(
        backup_service,
        "get_settings",
        lambda: SimpleNamespace(db_file=corrupt_db, backup_dir=sandbox),
    )
    outcome = backup_service.create_backup(daily=False)
    assert outcome.ok is False
    assert outcome.removed == []
    remaining = [p.name for p in backup_service.list_backups()]
    assert remaining == names, "失败时不该动历史备份"


# =========================================================================== F7 接口


def test_f7_manual_backup_endpoint(client, sandbox):
    """F7：`POST /api/maintenance/backup`（admin）→ 返回路径与校验结果。"""
    resp = client.post("/api/maintenance/backup", headers=_admin_headers(client))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["ok"] is True
    assert data["integrity_ok"] is True
    assert Path(data["path"]).is_file()
    assert data["size_bytes"] > 0
    assert data["size_human"]


def test_f7_backup_requires_admin(client):
    """非管理者调备份接口 → `10003`（运维接口不能给学生开）。

    ⚠️ 用户名规则是**4–20 位字母或数字**（`clean_username`），
    带下划线会被 `10001` 拒掉 —— 用 `be08student` 这种纯字母数字。
    """
    registered = client.post(
        "/api/auth/register",
        json={
            "username": "be08student",
            "password": "student-pass-9911",
            "real_name": "学生甲",
        },
    ).json()
    if registered.get("code") == 0:
        token = registered["data"]["access_token"]
    else:
        # 已注册过（同一模块被重跑）→ 直接登录
        logged_in = client.post(
            "/api/auth/login",
            json={
                "username": "be08student",
                "password": "student-pass-9911",
                "role": "student",
            },
        ).json()
        assert logged_in.get("code") == 0, f"学生登录失败：{logged_in}"
        token = logged_in["data"]["access_token"]

    resp = client.post(
        "/api/maintenance/backup", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.json()["code"] == 10003, resp.json()
    assert client.post("/api/maintenance/backup").json()["code"] == 10002


def test_f7_backup_failure_returns_nonzero_code(client, sandbox, monkeypatch):
    """备份失败是真实故障 —— 接口必须报非零 code，不能用 code=0 糊过去。"""
    corrupt_db = sandbox / "corrupt_source.db"
    corrupt_db.write_bytes(b"nope" * 100)
    monkeypatch.setattr(
        backup_service,
        "get_settings",
        lambda: SimpleNamespace(db_file=corrupt_db, backup_dir=sandbox),
    )
    headers = _admin_headers(client)

    body = client.post("/api/maintenance/backup", headers=headers).json()
    assert body["code"] != 0
    assert "备份失败" in body["message"]
    assert body["data"]["ok"] is False


# =========================================================================== 概览


def test_summary_and_latest(client, sandbox):
    """`summary()` 给管理端看的字段要齐全。"""
    empty = backup_service.summary()
    assert empty["count"] >= 0
    assert empty["keep"] == backup_service.DEFAULT_KEEP
    assert empty["dir"]

    outcome = backup_service.create_backup(daily=False)
    assert outcome.ok

    summary = backup_service.summary()
    assert summary["count"] >= 1
    assert summary["total_bytes"] > 0
    latest = summary["latest"]
    assert latest is not None
    assert latest["file"] == outcome.file
    assert latest["modified_at"].endswith("Z")


def test_human_size():
    assert backup_service.human_size(0) == "0 B"
    assert backup_service.human_size(999) == "999 B"
    assert backup_service.human_size(2048) == "2.0 KB"
    assert backup_service.human_size(5 * 1024 * 1024) == "5.0 MB"
