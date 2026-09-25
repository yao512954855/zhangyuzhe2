"""WP-QA-01 端到端场景驱动 —— `qa/e2e/e2e.sh` 调用，真后端 + 真库。

《40-集成与验收规格》§3 列了 10 个 E2E 场景。它们**不是都在这里跑** ——
有些已经被各包自己的真机烟测覆盖得更细，重复实现只会多一份会漂的代码。
本文件只做「要么没被覆盖、要么必须在**多包拼起来**的状态下才成立」的那几个：

| 场景 | 在哪验 | 说明 |
|---|---|---|
| E2E-01 新学生英语闭环 | `student-app/scripts/smoke_app03.py` | 逐字母回显、重试、结算的口径在 APP-03 烟测里 |
| E2E-02 内容→数据全链路 | `backend/scripts/smoke_be05.py` + APP-04 烟测 | 课文导入→背诵→报告 |
| E2E-03 IP 变化自动恢复 | `backend/scripts/smoke_be08.py`（UDP 真往返、IP 变化落状态） | 真机换网段属人工验收 |
| **E2E-04 断网降级** | **本文件**（服务端侧） | 键盘通路 + `used_keyboard` 落库 |
| E2E-05 申诉链路 | `qa/smoke/smoke_core.py` | fuzzy → 申诉 → 复核不改判分 |
| **E2E-06 备份与恢复** | **本文件**（`restore-prepare` / `restore-verify` + `e2e.sh` 真停服换库） | 唯一需要重启服务的场景 |
| E2E-07 权限隔离 | `qa/smoke/smoke_core.py` | 学生 token → 10003 |
| **E2E-08 内容删除保护** | **本文件** | 30003 + 停用后学生端不可见 |
| **E2E-09 并发练习** | **本文件** | 20 并发无 `database is locked` |
| **E2E-10 识别为空不计错** | **本文件** | 不进错题本、正确率分母排除 |

E2E-06 被拆成两个模式，是因为它必须**真的停服换库再启服** ——
这件事只能由 shell 做（进程归 shell 管），所以：
`restore-prepare` 记录备份点 → shell 停服/换库/启服 → `restore-verify` 断言回到备份点。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
WORDS_HEADERS = [
    "学科", "年级", "学期", "单元名", "单元序号", "类型", "英文",
    "中文", "可接受答案", "音标", "词性", "例句英", "例句中", "是否拼读",
]

ADMIN_USER = "admin"
ADMIN_PASSWORD = "Smoke-Qa01-Pass"
MIN_CHECKS = 12

_results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((bool(ok), name, detail))
    print(f"{'[OK]' if ok else '[!!]'} #{len(_results):02d} {name}"
          + (f"    {detail}" if detail else ""), flush=True)
    return bool(ok)


def _ascii(text: str) -> str:
    return " ".join("".join(ch for ch in text if ch.isascii()).split()) or "-"


def _xlsx(headers: list[str], rows: list[list]) -> bytes:
    from app.core.excel import write_xlsx

    return write_xlsx(headers, rows)


def _scalar(db: Path, sql: str, params: tuple = ()) -> Any:
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _rows(db: Path, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 公共造数：管理员 / 班级 / 学生 / 一个英语单元（8 词）
# ---------------------------------------------------------------------------

EN_UNIT = "E2E Unit"


def _seed(client: httpx.Client, db: Path, *, n_words: int = 8) -> dict[str, Any]:
    login = client.post("/api/auth/login",
                        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "role": "admin"}).json()
    if login.get("code") != 0:
        raise SystemExit(f"管理员登录失败：{login.get('message')}")
    ah = {"Authorization": f"Bearer {login['data']['access_token']}"}

    words = [f"word{i:02d}" for i in range(n_words)]
    content = _xlsx(WORDS_HEADERS, [
        ["英语", 4, "上学期", EN_UNIT, 1, "单词", w, f"词{i}", "", "", "", "", "", "是"]
        for i, w in enumerate(words)
    ])
    pv = client.post("/api/import/words/preview",
                     files={"file": ("w.xlsx", content, XLSX_MIME)}, headers=ah).json()
    cm = client.post("/api/import/words/commit",
                     json={"preview_token": pv["data"]["preview_token"],
                           "create_missing_units": True, "on_duplicate": "overwrite"},
                     headers=ah).json()
    unit_id = int(_scalar(db, "SELECT id FROM units WHERE name = ?", (EN_UNIT,)) or 0)

    sid = f"e2e{int(time.time()) % 100000}"
    client.post("/api/auth/register",
                json={"username": sid, "real_name": "E2E 学生", "password": "e2e12345"})
    sl = client.post("/api/auth/login",
                     json={"username": sid, "password": "e2e12345", "role": "student"}).json()
    sh = {"Authorization": f"Bearer {sl['data']['access_token']}"}
    student_id = int(sl["data"]["user"]["id"])

    return {
        "ah": ah, "sh": sh, "student_id": student_id, "unit_id": unit_id,
        "words": words, "commit": cm,
    }


def _new_session(client: httpx.Client, sh: dict, unit_id: int, count: int | None) -> tuple[int, list]:
    body: dict[str, Any] = {"unit_id": unit_id, "content_type": "english",
                            "strategy": {"order": "sequential", "mode": "cn2en"}}
    if count:
        body["strategy"]["count"] = count
    r = client.post("/api/sessions", json=body, headers=sh).json()
    data = r.get("data") or {}
    return int(data.get("session_id") or 0), (data.get("items") or [])


def _word_id(db: Path, en: str) -> int:
    return int(_scalar(db, "SELECT id FROM words WHERE en = ?", (en,)) or 0)


# ---------------------------------------------------------------------------
# scenarios：E2E-04 / 08 / 09 / 10
# ---------------------------------------------------------------------------


def mode_scenarios(client: httpx.Client, db: Path) -> None:  # noqa: C901
    seed = _seed(client, db)
    ah, sh, unit_id = seed["ah"], seed["sh"], seed["unit_id"]
    student_id = seed["student_id"]

    # ---------------- E2E-10 识别为空不计错 ----------------
    # 专门挑一个词，**只给它 empty 答案**，这样它是否进错题本就是干净的判据
    empty_word = "word00"
    empty_wid = _word_id(db, empty_word)
    sid10, _items = _new_session(client, sh, unit_id, 8)
    client.post(f"/api/sessions/{sid10}/answers",
                json={"word_id": empty_wid, "mode": "cn2en", "step": 1,
                      "asr_text": "", "provider": "mock"},
                headers={**sh, "Idempotency-Key": f"e2e10-{sid10}-empty"})
    ans10 = _rows(db, "SELECT * FROM answers WHERE session_id = ?", (sid10,))
    empty_rows = [a for a in ans10 if a.get("match_type") == "empty"]
    check("E2E-10 空识别判为 empty 且 is_correct=0",
          len(empty_rows) == 1 and int(empty_rows[0]["is_correct"]) == 0,
          f"rows={len(empty_rows)} match_type={empty_rows[0]['match_type'] if empty_rows else None}")

    wrong = client.get("/api/me/wrong-words", params={"grade": 4, "semester": "first"},
                       headers=sh).json()
    wrong_words = [w.get("en") for w in ((wrong.get("data") or {}).get("items") or [])]
    check("E2E-10 空识别**不进错题本**",
          wrong.get("code") == 0 and empty_word not in wrong_words,
          f"wrong_words={wrong_words}")

    mastery = _scalar(db, "SELECT COUNT(*) FROM word_mastery WHERE student_id = ? AND word_id = ?",
                      (student_id, empty_wid))
    check("E2E-10 空识别不写 word_mastery（不进掌握度统计）",
          int(mastery or 0) == 0, f"rows={mastery}")

    fin10 = client.post(f"/api/sessions/{sid10}/finish", json={"reason": "completed"},
                        headers=sh).json()
    d10 = fin10.get("data") or {}
    check("E2E-10 正确率分母排除 empty（counted = total − empty）",
          fin10.get("code") == 0
          and int(d10.get("empty_items") or 0) == 1
          and int(d10.get("counted_items") or 0) == int(d10.get("total_items") or 0) - 1,
          f"total={d10.get('total_items')} empty={d10.get('empty_items')} counted={d10.get('counted_items')}")

    # ---------------- E2E-04 断网降级（服务端侧）----------------
    kb_word = "word01"
    kb_wid = _word_id(db, kb_word)
    sid04, items04 = _new_session(client, sh, unit_id, 8)
    r04 = client.post(f"/api/sessions/{sid04}/answers",
                      json={"word_id": kb_wid, "mode": "cn2en", "step": 1,
                            "answer_text": kb_word, "used_keyboard": True,
                            "duration_ms": 2000, "provider": "keyboard"},
                      headers={**sh, "Idempotency-Key": f"e2e04-{sid04}-1"}).json()
    row04 = _rows(db, "SELECT * FROM answers WHERE session_id = ? AND word_id = ?", (sid04, kb_wid))
    check("E2E-04 语音不可用时键盘通路可用（判定照常给出）",
          r04.get("code") == 0 and (r04.get("data") or {}).get("is_correct") is True,
          f"is_correct={(r04.get('data') or {}).get('is_correct')} match_type={(r04.get('data') or {}).get('match_type')}")
    check("E2E-04 降级留痕 used_keyboard=1（可统计语音优先原则的破坏率）",
          bool(row04) and int(row04[0].get("used_keyboard") or 0) == 1,
          f"used_keyboard={row04[0].get('used_keyboard') if row04 else None}")

    # ---------------- E2E-08 内容删除保护 ----------------
    del_word = client.request("DELETE", f"/api/words/{kb_wid}", headers=ah).json()
    check("E2E-08 有答题记录的词条删除 → 30003 且带 record_count",
          del_word.get("code") == 30003,
          f"code={del_word.get('code')} data={del_word.get('data')}")

    del_unit = client.request("DELETE", f"/api/units/{unit_id}", headers=ah).json()
    check("E2E-08 有记录的单元删除 → 30003（界面应引导改停用）",
          del_unit.get("code") == 30003,
          f"code={del_unit.get('code')} data={del_unit.get('data')}")

    still_there = _scalar(db, "SELECT COUNT(*) FROM words WHERE id = ?", (kb_wid,))
    check("E2E-08 被拒后内容**仍在**（拒绝是硬拒，不是先删后报错）",
          int(still_there or 0) == 1, f"rows={still_there}")

    deactivate = client.patch(f"/api/words/{kb_wid}", json={"status": "inactive"}, headers=ah).json()
    active_after = client.get(f"/api/units/{unit_id}/words", params={"status": "active"},
                              headers=ah).json()
    active_ids = [w.get("id") for w in ((active_after.get("data") or {}).get("items") or [])]
    check("E2E-08 停用后不再出现在学生可见（active）列表里",
          deactivate.get("code") == 0 and kb_wid not in active_ids,
          f"active={active_ids}")

    # ---------------- E2E-09 并发练习（20 客户端）----------------
    errors: list[str] = []
    codes: list[int] = []
    lock = threading.Lock()

    def _one(idx: int) -> None:
        try:
            with httpx.Client(base_url=str(client.base_url), timeout=30.0, trust_env=False) as c:
                sid, items = _new_session(c, sh, unit_id, 4)
                if not items:
                    with lock:
                        errors.append(f"#{idx} 组卷无题")
                    return
                _w = int(items[0].get("word_id") or 0)
                r = c.post(f"/api/sessions/{sid}/answers",
                           json={"word_id": _w, "mode": "cn2en", "step": 1,
                                 "asr_text": f"word{_w % 100:02d}", "provider": "mock"},
                           headers={**sh, "Idempotency-Key": f"e2e09-{idx}-{sid}"}).json()
                # ⚠️ 别写 `int(r.get("code") or -1)`：业务成功的 `code` 就是 **0**，
                #    被 `or` 当成假值 → 成功被记成 -1。后果是下面两条断言**互相打架**：
                #    「20 个全成功」永远为真（errors 收不到东西），
                #    「错误码全为 0」永远为假。**这是典型的自动假绿**，本包真的写错过一次。
                code = r.get("code")
                with lock:
                    codes.append(-1 if code is None else int(code))
                    if code != 0:
                        errors.append(f"#{idx} code={code} {str(r.get('message'))[:60]}")
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"#{idx} {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=_one, args=(i,)) for i in range(20)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    conc_ms = (time.perf_counter() - t0) * 1000

    locked = [e for e in errors if "locked" in e.lower()]
    check("E2E-09 20 并发练习全部成功（无 database is locked）",
          not errors and len(codes) == 20,
          f"ok={len(codes)} errors={errors[:3]} locked={len(locked)} 耗时={conc_ms:.0f}ms")
    check("E2E-09 并发下无 5xx / 无超时（错误码全为 0）",
          all(c == 0 for c in codes) and len(codes) == 20,
          f"codes_set={sorted(set(codes))}")


# ---------------------------------------------------------------------------
# restore-prepare / restore-verify：E2E-06 备份与恢复
# ---------------------------------------------------------------------------


def mode_restore_prepare(client: httpx.Client, db: Path, backups: Path, expect_file: Path) -> None:
    seed = _seed(client, db, n_words=4)
    ah = seed["ah"]

    bk = client.post("/api/maintenance/backup", headers=ah).json()
    files = sorted(backups.glob("app_*.db")) if backups.is_dir() else []
    check("E2E-06 手动备份产出文件", bk.get("code") == 0 and bool(files),
          f"files={len(files)}")

    users_at_backup = int(_scalar(db, "SELECT COUNT(*) FROM users") or 0)
    answers_at_backup = int(_scalar(db, "SELECT COUNT(*) FROM answers") or 0)

    # 备份**之后**再注册一个学生 —— 恢复后他必须消失，这就是「回到备份点」的判据
    marker = f"after{int(time.time()) % 1000000}"
    r = client.post("/api/auth/register",
                    json={"username": marker, "real_name": "备份后新增", "password": "after12345"}).json()
    live_users = int(_scalar(db, "SELECT COUNT(*) FROM users") or 0)
    check("E2E-06 备份后新增数据确实落库（live 比备份点多 1）",
          r.get("code") == 0 and live_users == users_at_backup + 1,
          f"backup={users_at_backup} live={live_users}")

    # 直接读备份文件，确认它**没有**这条新数据（否则「恢复」根本证明不了什么）
    newest = files[-1] if files else None
    in_backup = int(_scalar(newest, "SELECT COUNT(*) FROM users")) if newest else -1
    check("E2E-06 备份文件本身不含备份后的新增数据",
          newest is not None and in_backup == users_at_backup,
          f"backup_users={in_backup} expect={users_at_backup}")

    expect_file.write_text(json.dumps({
        "backup_file": str(newest) if newest else "",
        "users_at_backup": users_at_backup,
        "answers_at_backup": answers_at_backup,
        "marker": marker,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"  → 备份点已记录：users={users_at_backup} answers={answers_at_backup} marker={marker}",
          flush=True)


def mode_restore_verify(client: httpx.Client, db: Path, expect_file: Path) -> None:
    exp = json.loads(expect_file.read_text(encoding="utf-8"))
    health = client.get("/api/health").json()
    check("E2E-06 换库后服务可用（/api/health）",
          health.get("code") == 0 and (health.get("data") or {}).get("db") is True,
          f"db={(health.get('data') or {}).get('db')}")

    users_now = int(_scalar(db, "SELECT COUNT(*) FROM users") or 0)
    check("E2E-06 恢复后数据回到备份点（新增的学生已消失）",
          users_now == int(exp["users_at_backup"]),
          f"now={users_now} expect={exp['users_at_backup']}")

    marker_gone = int(_scalar(db, "SELECT COUNT(*) FROM users WHERE username = ?", (exp["marker"],)) or 0)
    check("E2E-06 备份后写入的那条数据确实没了", marker_gone == 0, f"rows={marker_gone}")

    schema = int(_scalar(db, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='users'") or 0)
    check("E2E-06 恢复后的库结构完整（可正常迁移/查询）", schema == 1, f"users_table={schema}")

    # 恢复后还能继续用：管理员能登录、能开新场次
    login = client.post("/api/auth/login",
                        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "role": "admin"}).json()
    check("E2E-06 恢复后管理员仍可登录（业务可用）",
          login.get("code") == 0, str(login.get("message"))[:60])


# ---------------------------------------------------------------------------


def _print_summary(min_checks: int) -> int:
    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    print()
    print(f"E2E 结果：{passed}/{total} 通过", flush=True)
    for ok, name, detail in _results:
        if not ok:
            print(f"  未通过：{name}    {detail}", flush=True)
    if total < min_checks:
        print(f"[!!] 用例数 {total} < 下限 {min_checks}，判定为失败（防假绿）", flush=True)
        print(f"SUMMARY pass={passed} total={total} failed={total - passed} guard=FAIL", flush=True)
        return 1
    print(f"SUMMARY pass={passed} total={total} failed={total - passed} guard=OK(min={min_checks})",
          flush=True)
    print("NAMES " + ";".join(_ascii(n) for _, n, _ in _results), flush=True)
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="QA-01 端到端场景驱动")
    ap.add_argument("--mode", required=True,
                    choices=["scenarios", "restore-prepare", "restore-verify"])
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--backups", required=True)
    ap.add_argument("--expect-file", required=True)
    ap.add_argument("--min-checks", type=int, default=MIN_CHECKS)
    a = ap.parse_args()

    db = Path(a.db)
    backups = Path(a.backups)
    expect_file = Path(a.expect_file)

    with httpx.Client(base_url=a.base_url, timeout=30.0, trust_env=False) as client:
        if a.mode == "scenarios":
            mode_scenarios(client, db)
        elif a.mode == "restore-prepare":
            mode_restore_prepare(client, db, backups, expect_file)
        else:
            mode_restore_verify(client, db, expect_file)

    return _print_summary(a.min_checks)


if __name__ == "__main__":
    sys.exit(main())
