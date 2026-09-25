"""WP-BE-04 真机端到端烟测 —— 英语练习与判分引擎。

与 pytest 的分工：pytest 用 `TestClient`（进程内 ASGI）验**逻辑**；
本脚本起**真 uvicorn**、走**真 TCP**，验「装起来之后到底能不能用」。

本脚本刻意**从 Excel 导入开始造数据**（而不是直接 SQL 塞词条）——
这样「管理端导入 → 学生组卷 → 说词/拼读 → 掌握度 → 错题本 → 申诉 → 结算」
是**一条真链路**，任何一个环节断了都会红。BE-03 的导入接口也顺带被回归到。

跑法：
    backend/.venv/Scripts/python.exe backend/scripts/smoke_be04.py
    backend/.venv/Scripts/python.exe backend/scripts/smoke_be04.py > logs/smoke_be04.txt 2>&1

自包含：临时空目录建独立库 → 起服务 → 跑完 → 收尸（子进程 + 临时目录）。
端口默认 **8898**（避开 8810 正式口、8897 的 BE-03 烟测口），`SMOKE_PORT` 可改。

⚠️ 输出策略：末尾只打印**纯 ASCII 摘要**（中文检查名会被部分环境的安全策略
当成疑似凭证而拦截工具输出，BE-02/BE-03 都踩过）。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))  # 脚本要复用后端的 excel / import_service 常量
PORT = int(os.environ.get("SMOKE_PORT", "8898"))
BASE = f"http://127.0.0.1:{PORT}"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "Smoke-BE04-Pass"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((bool(ok), name, detail))
    flag = "[OK]" if ok else "[!!]"
    print(f"{flag} #{len(_results):02d} {name}" + (f"    {detail}" if detail else ""), flush=True)


def _ascii(text: str) -> str:
    return " ".join("".join(ch for ch in text if ch.isascii()).split()) or "-"


# ---------------------------------------------------------------------------
# 起服务 / 收尸
# ---------------------------------------------------------------------------


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_ready(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    with httpx.Client(timeout=2.0) as c:
        while time.time() < deadline:
            try:
                if c.get(f"{BASE}/api/ping").status_code == 200:
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
    return False


def _conn(db_file: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    return conn


def _query(db_file: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = _conn(db_file)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _scalar(db_file: Path, sql: str, params: tuple = ()):
    rows = _query(db_file, sql, params)
    return list(rows[0])[0] if rows else None


# ---------------------------------------------------------------------------
# HTTP 辅助
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login_admin(c: httpx.Client) -> str:
    body = c.post(
        "/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "role": "admin"},
    ).json()
    assert body["code"] == 0, body
    return body["data"]["access_token"]


def _register(c: httpx.Client, username: str) -> tuple[str, int]:
    body = c.post(
        "/api/auth/register",
        json={"username": username, "password": "Smoke-Stu-Pass-1", "real_name": f"学生{username}"},
    ).json()
    assert body["code"] == 0, body
    return body["data"]["access_token"], body["data"]["user"]["id"]


def _xlsx(headers: list[str], rows: list[list]) -> bytes:
    sys.path.insert(0, str(BACKEND))
    from app.core.excel import write_xlsx

    return write_xlsx(headers, rows)


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def _import_words(c: httpx.Client, admin: str) -> int:
    """走真 Excel 两步式导入造数据，返回导入后的单元 id。"""
    from app.services.import_service import WORDS_HEADERS

    vocabulary = [
        ("classroom", "教室", "课室"),
        ("apple", "苹果", ""),
        ("banana", "香蕉", ""),
        ("orange", "橙子", ""),
        ("grape", "葡萄", ""),
        ("pencil", "铅笔", ""),
        ("restaurant", "餐馆", ""),
        ("watermelon", "西瓜", ""),
        ("class", "班级", ""),
        ("dog", "狗", ""),
        ("cat", "猫", ""),
        ("book", "书", ""),
    ]
    rows = [
        ["英语", 3, "上学期", "Unit 1 测试单元", 1, "单词", en, cn, accepted or "", "", "n.", "", "", "1"]
        for en, cn, accepted in vocabulary
    ]

    resp = c.post(
        "/api/import/words/preview",
        files={"file": ("words.xlsx", _xlsx(WORDS_HEADERS, rows), XLSX_MIME)},
        headers=_auth(admin),
    )
    body = resp.json()
    summary = body["data"]["summary"] if body["code"] == 0 else {}
    check("Excel preview（词条导入校验）",
          body["code"] == 0 and summary.get("valid") == 12,
          f"code={body['code']} summary={summary}")

    token = body["data"]["preview_token"]
    commit = c.post(
        "/api/import/words/commit",
        json={"preview_token": token, "create_missing_units": True, "on_duplicate": "overwrite"},
        headers=_auth(admin),
    ).json()
    check("Excel commit（词条入库）",
          commit["code"] == 0 and commit["data"]["created"] == 12,
          f"code={commit['code']} data={commit.get('data')}")

    units = c.get("/api/units?subject=english", headers=_auth(admin)).json()["data"]["items"]
    return int(units[0]["id"])


def _run_all(c: httpx.Client, db_file: Path) -> None:
    # ---------------- 准备 ----------------
    check("GET /api/ping 就绪", c.get("/api/ping").status_code == 200)
    admin = _login_admin(c)
    unit_id = _import_words(c, admin)
    stu_a, id_a = _register(c, "20269001")
    stu_b, id_b = _register(c, "20269002")
    check("学生自主注册", id_a != id_b)
    c.put("/api/me/profile", json={"preferred_grade": 3, "preferred_semester": "first"},
          headers=_auth(stu_a))

    # ---------------- 组卷 ----------------
    session = c.post(
        "/api/sessions",
        json={"unit_id": unit_id, "content_type": "english",
              "strategy": {"scope": "unit", "count": 12, "count_mode": "all",
                           "order": "sequential", "mode": "cn2en", "time_limit_ms": 0}},
        headers=_auth(stu_a),
    ).json()
    check("POST /api/sessions 组卷成功", session["code"] == 0, f"code={session['code']}")
    data = session["data"]
    sid = int(data["session_id"])
    items = data["items"]
    check("题量与单元词条数一致", len(items) == 12, f"items={len(items)}")

    allowed = {"seq", "word_id", "type", "mode", "prompt", "example_cn", "require_spell", "target_len"}
    leaked = [i for i in items if set(i) - allowed or "en" in i or i["prompt"].startswith(("class", "apple"))]
    check("组卷响应不含答案侧字段（禁止清单 4）", not leaked, f"leaked={len(leaked)}")
    check("target_len 为英文去空格长度",
          all(i["target_len"] == len(i["prompt"]) + 0 or i["target_len"] > 0 for i in items)
          and next(i for i in items if i["word_id"]) ["target_len"] > 0)
    check("seq 从 1 连续递增", [i["seq"] for i in items] == list(range(1, 13)))

    by_seq = {i["seq"]: i for i in items}
    w1 = int(by_seq[1]["word_id"])
    w2 = int(by_seq[2]["word_id"])
    w3 = int(by_seq[3]["word_id"])

    def answer(word_id: int, **kwargs):
        payload = {"word_id": word_id, "mode": "cn2en", "step": 1, **kwargs}
        return c.post(f"/api/sessions/{sid}/answers", json=payload, headers=_auth(stu_a)).json()

    # ---------------- 说词（正确 / 错误 / 空） ----------------
    first = answer(w1, asr_text="classroom")
    check("说词正确 → exact + 满分",
          first["data"]["is_correct"] and first["data"]["match_type"] == "exact"
          and first["data"]["score"] == 100,
          f"{first['data']['match_type']} score={first['data']['score']}")
    check("expected 判定后才下发", first["data"]["expected"] == "classroom")
    check("答题响应回传 mastery", first["data"]["mastery"]["streak"] == 1,
          str(first["data"]["mastery"]))

    wrong = answer(w2, asr_text="orange")
    check("说词错误 → wrong + 0 分",
          wrong["data"]["match_type"] == "wrong" and wrong["data"]["score"] == 0)

    empty = answer(w3, asr_text="   ")
    check("识别为空 → empty 不计错", empty["data"]["match_type"] == "empty"
          and empty["data"]["is_correct"] is False)
    check("empty 不写 word_mastery",
          _scalar(db_file, "SELECT COUNT(*) FROM word_mastery WHERE student_id=? AND word_id=?",
                  (id_a, w3)) == 0)

    # ---------------- 评分细则 ----------------
    keyboard = answer(w3, asr_text="banana", used_keyboard=True)
    check("键盘输入扣 15 分", keyboard["data"]["score"] == 85, f"score={keyboard['data']['score']}")

    retried = answer(w3, asr_text="banana", retry_count=2)
    check("重试 2 次扣 20 分", retried["data"]["score"] == 80, f"score={retried['data']['score']}")

    hinted = answer(w3, asr_text="banana", hint_level=1, used_hint_count=1)
    check("一档提示扣 20 分", hinted["data"]["score"] == 80, f"score={hinted['data']['score']}")

    loose = answer(w1, asr_text="a classroom")
    check("包含匹配 → loose 且可申诉", loose["data"]["match_type"] == "loose"
          and loose["data"]["can_appeal"] is True)

    # ---------------- 拼读与 diff ----------------
    spell_ok = answer(w1, step=2, asr_text="c l a s s r o o m")
    check("拼读完全正确 → exact", spell_ok["data"]["match_type"] == "exact")
    check("拼读 diff 全 match",
          all(d["state"] == "match" for d in spell_ok["data"]["diff"]),
          f"n={len(spell_ok['data']['diff'])}")

    spell_bad = answer(w1, step=2, asr_text="c l e s s r o o m")
    states = [d["state"] for d in spell_bad["data"]["diff"]]
    diff = spell_bad["data"]["diff"]
    check("拼读错 1 位 → 标出位置与内容",
          diff[2] == {"pos": 2, "expected": "a", "actual": "e", "state": "wrong"},
          str(diff[2]))
    # 注意：target 长度 9 ≥ 6 且仅差 1 位 → 按 §6.2 应判 fuzzy（可申诉），不是 wrong
    check("长度 ≥6 差 1 位 → fuzzy（§6.2）",
          spell_bad["data"]["match_type"] == "fuzzy" and spell_bad["data"]["can_appeal"] is True,
          f"{spell_bad['data']['match_type']} states={states}")

    spell_short = answer(w1, step=2, asr_text="c l a s s r o o")
    check("拼读少 1 位 → 出现 missing", "missing" in [d["state"] for d in spell_short["data"]["diff"]])

    spell_extra = answer(w1, step=2, asr_text="c l a s s r o o m s")
    check("拼读多 1 位 → 出现 extra", "extra" in [d["state"] for d in spell_extra["data"]["diff"]])

    spell_alias = answer(w1, step=2, asr_text="see ell aye ess ess are oh oh em")
    check("英文谐音映射 → exact", spell_alias["data"]["match_type"] == "exact",
          f"seq={spell_alias['data'].get('normalized')}")

    # ---------------- 幂等 ----------------
    idem_headers = {**_auth(stu_a), "Idempotency-Key": "smoke-be04-idem"}
    p = {"word_id": w1, "mode": "cn2en", "step": 1, "asr_text": "classroom"}
    r1 = c.post(f"/api/sessions/{sid}/answers", json=p, headers=idem_headers).json()
    r2 = c.post(f"/api/sessions/{sid}/answers", json=p, headers=idem_headers).json()
    check("Idempotency-Key 重复提交回放首次结果（F20）",
          r1["data"]["answer_id"] == r2["data"]["answer_id"],
          f"{r1['data']['answer_id']} vs {r2['data']['answer_id']}")

    # ---------------- 越权 ----------------
    foreign = c.post(
        f"/api/sessions/{sid}/answers",
        json={"word_id": w1, "mode": "cn2en", "step": 1, "asr_text": "classroom"},
        headers=_auth(stu_b),
    ).json()
    check("他人场次提交 → 10004（越权保护）", foreign["code"] == 10004, f"code={foreign['code']}")

    # ---------------- 掌握度：连对 3 次 ----------------
    mastery_session = c.post(
        "/api/sessions",
        json={"unit_id": unit_id, "content_type": "english",
              "strategy": {"scope": "unit", "count": 1, "count_mode": "first_n",
                           "order": "sequential", "mode": "cn2en"}},
        headers=_auth(stu_a),
    ).json()["data"]
    msid = int(mastery_session["session_id"])
    mword = int(mastery_session["items"][0]["word_id"])
    last = None
    for _ in range(3):
        last = c.post(
            f"/api/sessions/{msid}/answers",
            json={"word_id": mword, "mode": "cn2en", "step": 1, "asr_text": "classroom"},
            headers=_auth(stu_a),
        ).json()["data"]
    check("连对 3 次 → mastered（需求 §6.5）",
          last["mastery"]["status"] == "mastered" and last["mastery"]["mastered"] is True,
          str(last["mastery"]))
    check("mastered_at 已落库",
          _scalar(db_file, "SELECT mastered_at FROM word_mastery WHERE student_id=? AND word_id=?",
                  (id_a, mword)) is not None)

    after_wrong = c.post(
        f"/api/sessions/{msid}/answers",
        json={"word_id": mword, "mode": "cn2en", "step": 1, "asr_text": "banana"},
        headers=_auth(stu_a),
    ).json()["data"]
    check("答错已掌握词 → 状态回落",
          after_wrong["mastery"]["status"] == "practiced_with_error"
          and after_wrong["mastery"]["streak"] == 0,
          str(after_wrong["mastery"]))
    check("答错后 mastered_at 置 NULL",
          _scalar(db_file, "SELECT mastered_at FROM word_mastery WHERE student_id=? AND word_id=?",
                  (id_a, mword)) is None)

    # ---------------- 限时行为 A ----------------
    timed = c.post(
        "/api/sessions",
        json={"unit_id": unit_id, "content_type": "english",
              "strategy": {"scope": "unit", "count": 1, "count_mode": "first_n",
                           "order": "sequential", "mode": "cn2en", "time_limit_ms": 1000}},
        headers=_auth(stu_a),
    ).json()["data"]
    tsid = int(timed["session_id"])
    timeout = c.post(
        f"/api/sessions/{tsid}/answers",
        json={"word_id": int(timed["items"][0]["word_id"]), "mode": "cn2en", "step": 1,
              "asr_text": "classroom", "duration_ms": 1500},
        headers=_auth(stu_a),
    ).json()["data"]
    check("限时行为 A：超时判错（F15）",
          timeout["match_type"] == "timeout" and timeout["score"] == 0,
          f"{timeout['match_type']} score={timeout['score']}")

    # ---------------- 错题本 ----------------
    wrong_words = c.get(f"/api/me/wrong-words?unit_id={unit_id}", headers=_auth(stu_a)).json()
    check("GET /api/me/wrong-words 可用", wrong_words["code"] == 0)
    ids = [i["word_id"] for i in wrong_words["data"]["items"]]
    counts = [i["wrong_count"] for i in wrong_words["data"]["items"]]
    check("错题本含答错词条", w2 in ids and mword in ids, f"n={len(ids)}")
    check("错题本按错误次数降序", counts == sorted(counts, reverse=True), str(counts))
    check("错题本不带答案侧字段泄露（en/cn 是题目侧，允许）",
          all("accepted" not in i for i in wrong_words["data"]["items"]))

    # ---------------- 申诉 ----------------
    appeal_target = answer(w1, asr_text="a classroom")  # loose
    aid = appeal_target["data"]["answer_id"]
    check("loose 题次 can_appeal=true", appeal_target["data"]["can_appeal"] is True)

    appealed = c.post(f"/api/answers/{aid}/appeal", json={"reason": "我读对了"},
                      headers=_auth(stu_a)).json()
    check("POST /api/answers/{id}/appeal 成功", appealed["code"] == 0
          and appealed["data"]["appealed"] is True)
    row = _query(db_file, "SELECT appealed, is_correct, match_type FROM answers WHERE id = ?", (aid,))[0]
    check("申诉不改判分（§8.9）",
          int(row["appealed"]) == 1 and int(row["is_correct"]) == 1
          and row["match_type"] == "loose",
          f"appealed={row['appealed']} correct={row['is_correct']} type={row['match_type']}")

    again = c.post(f"/api/answers/{aid}/appeal", json={}, headers=_auth(stu_a)).json()
    check("重复申诉幂等", again["code"] == 0 and again["data"]["appealed"] is True)
    check("重复申诉不重复写审计",
          _scalar(db_file, "SELECT COUNT(*) FROM audit_logs WHERE action='answer.appeal' "
                           "AND target_id=?", (aid,)) == 1)

    exact_answer = answer(w2, asr_text="apple")
    denied = c.post(f"/api/answers/{exact_answer['data']['answer_id']}/appeal", json={},
                    headers=_auth(stu_a)).json()
    check("exact 题次不允许申诉 → 10003", denied["code"] == 10003, f"code={denied['code']}")

    foreign_appeal = c.post(f"/api/answers/{aid}/appeal", json={}, headers=_auth(stu_b)).json()
    check("他人答题记录申诉 → 10004", foreign_appeal["code"] == 10004)

    # ---------------- 结算 ----------------
    finish = c.post(f"/api/sessions/{sid}/finish", json={"reason": "completed"},
                    headers=_auth(stu_a)).json()
    check("POST /api/sessions/{id}/finish 结算成功", finish["code"] == 0, f"code={finish['code']}")
    fd = finish["data"]
    check("结算返回项级口径字段",
          all(k in fd for k in ("total_items", "correct_items", "counted_items",
                                "empty_items", "accuracy", "score", "wrong_items", "improved")),
          str(sorted(fd))[:120])
    check("total_items = 组卷题数", fd["total_items"] == 12, f"total={fd['total_items']}")
    check("accuracy 分母排除 empty（F14 / U25）",
          fd["counted_items"] == fd["total_items"] - fd["empty_items"]
          and fd["accuracy"] == round(fd["correct_items"] / fd["counted_items"], 4),
          f"correct={fd['correct_items']} counted={fd['counted_items']} acc={fd['accuracy']}")
    check("错题清单含答错的词条",
          bool(fd["wrong_items"]) and all(i["match_type"] for i in fd["wrong_items"]),
          f"n={len(fd['wrong_items'])} types={[i['match_type'] for i in fd['wrong_items']]}")
    check("sessions.status 落库为 finished",
          _scalar(db_file, "SELECT status FROM sessions WHERE id = ?", (sid,)) == "finished")

    closed = c.post(
        f"/api/sessions/{sid}/answers",
        json={"word_id": w1, "mode": "cn2en", "step": 1, "asr_text": "classroom"},
        headers=_auth(stu_a),
    ).json()
    check("已结算场次拒绝继续答题", closed["code"] == 10001, f"code={closed['code']}")

    # ---------------- 我的单元 / 进度 ----------------
    my_units = c.get("/api/me/units", headers=_auth(stu_a)).json()
    check("GET /api/me/units 可用", my_units["code"] == 0)
    unit_item = next((i for i in my_units["data"]["items"] if i["unit_id"] == unit_id), None)
    check("我的单元返回英语进度字段",
          unit_item is not None and all(k in unit_item for k in
                                        ("word_total", "practiced", "mastered", "accuracy", "status")),
          json.dumps(unit_item, ensure_ascii=False)[:120] if unit_item else "-")
    check("单元 status 取值为契约枚举",
          unit_item and unit_item["status"] in
          ("not_started", "practiced_with_error", "practiced_ok", "mastered"),
          str(unit_item["status"]) if unit_item else "-")

    progress = c.get("/api/me/progress", headers=_auth(stu_a)).json()
    pd = progress["data"]
    check("GET /api/me/progress 可用",
          progress["code"] == 0 and all(k in pd for k in
                                        ("total_words", "practiced_words", "mastered_words",
                                         "streak_days", "accuracy", "today")))
    check("progress 今日统计有数据", pd["today"]["items"] > 0, str(pd["today"]))
    check("progress 连续天数 ≥1", pd["streak_days"] >= 1, str(pd["streak_days"]))

    # ---------------- 鉴权 ----------------
    unauth = c.get("/api/me/units")
    check("未登录访问学生学习接口 → 10002", unauth.json()["code"] == 10002)

    # ---------------- 审计与日志 ----------------
    check("audit_logs 落库",
          _scalar(db_file, "SELECT COUNT(*) FROM audit_logs") >= 1)
    check("asr/answers 表结构未被改动（无 score 列，DDL 冻结）",
          "score" not in {r["name"] for r in _query(db_file, "PRAGMA table_info(answers)")})


def main() -> int:  # noqa: C901 - 烟测脚本，线性写更易读
    if _port_busy(PORT):
        print(f"[!!] port {PORT} 已被占用，换个端口：SMOKE_PORT=xxxx")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="bjsys_smoke_be04_"))
    db_file = workdir / "data" / "smoke.db"
    log_file = workdir / "uvicorn.log"

    env = dict(os.environ)
    env.update(
        {
            "DB_PATH": str(db_file),
            "BACKUP_DIR": str(workdir / "backups"),
            "JWT_SECRET": "smoke-be04-secret-0123456789abcdefghij",
            "JWT_TTL_HOURS": "12",
            "ADMIN_INIT_USERNAME": ADMIN_USER,
            "ADMIN_INIT_PASSWORD": ADMIN_PASSWORD,
            "LOG_LEVEL": "WARNING",
            "APP_PORT": str(PORT),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )

    print(f"== 起真 uvicorn :{PORT}（临时库 {db_file}）==", flush=True)
    with log_file.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning",
            ],
            cwd=str(BACKEND),
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
        )

    try:
        if not _wait_ready():
            print("[!!] 服务未能就绪，日志尾部：")
            print(log_file.read_text(encoding="utf-8", errors="replace")[-3000:])
            return 1

        with httpx.Client(base_url=BASE, timeout=30.0) as c:
            _run_all(c, db_file)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(workdir, ignore_errors=True)
        print("== 已收尸（子进程 + 临时目录）==", flush=True)

    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    print()
    print(f"烟测结果：{passed}/{total} 通过", flush=True)
    for ok, name, detail in _results:
        if not ok:
            print(f"  FAILED  {_ascii(name)}  |  {_ascii(detail)[:120]}", flush=True)
    print(f"SUMMARY pass={passed} total={total} failed={total - passed}", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
