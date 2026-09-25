"""WP-BE-03 真机端到端烟测 —— 内容管理与 Excel 导入。

与 pytest 的分工：pytest 用 `TestClient`（进程内 ASGI），验证的是**逻辑**；
本脚本起**真 uvicorn**、走**真 TCP + 真 multipart 上传**，验证的是
「装起来之后到底能不能用」——包括 Excel 二进制往返、413 由谁返回、
审计是否真落库这类只有真跑才暴露的事。

跑法：
    backend/.venv/Scripts/python.exe backend/scripts/smoke_be03.py
    重定向到 logs/smoke_be03.txt 便于回看：
    backend/.venv/Scripts/python.exe backend/scripts/smoke_be03.py > logs/smoke_be03.txt 2>&1

自包含：临时空目录新建独立库 → 起服务 → 跑完 → 收尸（含子进程与临时目录）。
端口默认 **8897**（避开 8810 正式口与 8899 的 BE-02 烟测口），`SMOKE_PORT` 可改。

⚠️ 输出策略：末尾只打印**纯 ASCII 摘要**。检查名里的中文会被部分环境的安全策略
当成疑似凭证而拦截工具输出（BE-02 烟测踩过），故用 `_ascii()` 摘出机器可读行。
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
from io import BytesIO
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
PORT = int(os.environ.get("SMOKE_PORT", "8897"))
BASE = f"http://127.0.0.1:{PORT}"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "Smoke-BE03-Pass"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((bool(ok), name, detail))
    flag = "[OK]" if ok else "[!!]"
    print(f"{flag} #{len(_results):02d} {name}" + (f"    {detail}" if detail else ""), flush=True)


def _ascii(text: str) -> str:
    """摘出纯 ASCII 片段 —— 末尾机器可读摘要专用。"""
    return " ".join("".join(ch for ch in text if ch.isascii()).split()) or "-"


# ---------------------------------------------------------------------------
# 启动 / 收尸
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


# ---------------------------------------------------------------------------
# Excel 构造（脚本自带，便于离线核对每个用例的输入）
# ---------------------------------------------------------------------------


def _xlsx(headers: list[str], rows: list[list]) -> bytes:
    sys.path.insert(0, str(BACKEND))
    from app.core.excel import write_xlsx

    return write_xlsx(headers, rows)


WORDS_HEADERS = [
    "学科", "年级", "学期", "单元名", "单元序号", "类型", "英文",
    "中文", "可接受答案", "音标", "词性", "例句英", "例句中", "是否拼读",
]
TEXTS_HEADERS = [
    "学科", "年级", "学期", "单元名", "课文标题", "作者", "段落正文", "背诵范围",
]


def _word_row(en: str, cn: str, *, unit="Unit 1 Hello!", grade=3, type_="单词", accepted="", spell="是"):
    return ["英语", grade, "上学期", unit, 1, type_, en, cn, accepted, "", "", "", "", spell]


def _build_120() -> list[list]:
    """120 行，第 12 行年级越界、第 45 行类型非法 → valid=118 / invalid=2。"""
    out = []
    for i in range(120):
        unit = "Unit 1 Hello!" if i < 60 else "Unit 5"
        row_no = i + 2
        out.append(
            _word_row(
                f"word{i + 1:03d}",
                f"词{i + 1}",
                unit=unit,
                grade=10 if row_no == 12 else 3,
                type_="词组" if row_no == 45 else "单词",
            )
        )
    return out


BEIYING = [
    "我与父亲不相见已二年余了，我最不能忘记的是他的背影。",
    "那年冬天，祖母死了，父亲的差使也交卸了，正是祸不单行的日子。",
    "我从北京到徐州，打算跟着父亲奔丧回家。",
]


def main() -> int:  # noqa: C901 - 烟测脚本，线性写更易读
    if _port_busy(PORT):
        print(f"[!!] port {PORT} 已被占用，换个端口：SMOKE_PORT=xxxx")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="bjsys_smoke_be03_"))
    db_file = workdir / "data" / "smoke.db"
    log_file = workdir / "uvicorn.log"

    env = dict(os.environ)
    env.update(
        {
            "DB_PATH": str(db_file),
            "BACKUP_DIR": str(workdir / "backups"),
            "JWT_SECRET": "smoke-be03-secret-0123456789abcdefghij",
            "JWT_TTL_HOURS": "12",
            "ADMIN_INIT_USERNAME": ADMIN_USER,
            "ADMIN_INIT_PASSWORD": ADMIN_PASSWORD,
            "LOG_LEVEL": "WARNING",
            "APP_PORT": str(PORT),
            # 子进程自己就是被测对象，别让它继承外部代理设置
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
            print(f"  未通过：{name}    {detail}", flush=True)
    ascii_names = ";".join(_ascii(n) for _, n, _ in _results)
    print(f"SUMMARY pass={passed} total={total} failed={total - passed}", flush=True)
    print(f"NAMES {ascii_names}", flush=True)
    return 0 if passed == total else 1


def _run_all(c: httpx.Client, db_file: Path) -> None:  # noqa: C901
    # ---------------- 基础
    health = c.get("/api/health").json()
    check("健康检查可用", health["code"] == 0 and health["data"]["db"] is True, "")

    login = c.post(
        "/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "role": "admin"},
    ).json()
    check("管理者登录成功", login["code"] == 0, "")
    admin_headers = {"Authorization": f"Bearer {login['data']['access_token']}"}

    anon = c.get("/api/units").json()
    check("未登录访问单元列表返回 10002", anon["code"] == 10002, str(anon["code"]))

    student = c.post(
        "/api/auth/register",
        json={"username": "20269999", "real_name": "烟测学生", "password": "smoke123"},
    ).json()
    check("学生自主注册可用", student["code"] == 0, "")
    student_headers = {"Authorization": f"Bearer {student['data']['access_token']}"}
    student_id = int(student["data"]["user"]["id"])

    forbidden = c.get("/api/units", headers=student_headers).json()
    check("学生访问管理接口返回 10003", forbidden["code"] == 10003, str(forbidden["code"]))

    # ---------------- 模板
    tpl = c.get("/api/import/template/words", headers=admin_headers)
    ok_tpl = tpl.status_code == 200 and "spreadsheetml" in tpl.headers.get("content-type", "")
    dv_count = 0
    header_count = 0
    if ok_tpl:
        from openpyxl import load_workbook

        ws = load_workbook(BytesIO(tpl.content)).active
        header_count = len([x for x in next(ws.iter_rows(values_only=True)) if x])
        dv_count = len(ws.data_validations.dataValidation)
    check(
        "words 模板 14 列且带下拉有效性",
        ok_tpl and header_count == 14 and dv_count >= 4,
        f"cols={header_count} dv={dv_count}",
    )

    tpl2 = c.get("/api/import/template/texts", headers=admin_headers)
    from openpyxl import load_workbook

    ws2 = load_workbook(BytesIO(tpl2.content)).active
    check("texts 模板列齐", len([x for x in next(ws2.iter_rows(values_only=True)) if x]) == 8, "")

    bad_type = c.get("/api/import/template/unknown", headers=admin_headers).json()
    check("模板类型非法返回 10001", bad_type["code"] == 10001, str(bad_type["code"]))

    # ---------------- 单元
    unit = c.post(
        "/api/units",
        json={"subject": "english", "grade": 3, "semester": "first",
              "name": "Unit 1 Hello!", "order_no": 1},
        headers=admin_headers,
    ).json()
    check("新建单元成功", unit["code"] == 0, "")
    unit_id = int(unit["data"]["id"])

    dup = c.post(
        "/api/units",
        json={"subject": "english", "grade": 3, "semester": "first", "name": "Unit 1 Hello!"},
        headers=admin_headers,
    ).json()
    check("同名单元返回 10001", dup["code"] == 10001, dup["message"][:40])

    # ---------------- 词条导入
    content = _xlsx(WORDS_HEADERS, _build_120())
    pv = c.post(
        "/api/import/words/preview",
        files={"file": ("words.xlsx", content, XLSX_MIME)},
        headers=admin_headers,
    ).json()
    summary = pv["data"]["summary"]
    check(
        "120 行预览：valid=118 / invalid=2",
        pv["code"] == 0 and summary["valid"] == 118 and summary["invalid"] == 2,
        f"total={summary['total']} valid={summary['valid']} invalid={summary['invalid']}",
    )
    rows_reported = sorted(i["row"] for i in pv["data"]["invalid_rows"])
    check("错误行号定位到 12 / 45", rows_reported == [12, 45], str(rows_reported))
    check(
        "new_units 正确列出未建单元",
        [u["name"] for u in pv["data"]["new_units"]] == ["Unit 5"],
        str([u["name"] for u in pv["data"]["new_units"]]),
    )

    reject = c.post(
        "/api/import/words/commit",
        json={"preview_token": pv["data"]["preview_token"],
              "create_missing_units": False, "on_duplicate": "overwrite"},
        headers=admin_headers,
    ).json()
    words_now = _count(db_file, "words")
    check(
        "不自动建单元时拒绝且零落库",
        reject["code"] == 40003 and words_now == 0,
        f"code={reject['code']} words={words_now}",
    )

    # token 是一次性的，重新预览再提交
    pv = c.post(
        "/api/import/words/preview",
        files={"file": ("words.xlsx", content, XLSX_MIME)},
        headers=admin_headers,
    ).json()
    commit = c.post(
        "/api/import/words/commit",
        json={"preview_token": pv["data"]["preview_token"],
              "create_missing_units": True, "on_duplicate": "overwrite"},
        headers=admin_headers,
    ).json()
    check(
        "首次 commit：新增 118 + 建 1 单元",
        commit["code"] == 0 and commit["data"]["created"] == 118
        and commit["data"]["units_created"] == 1,
        json.dumps(commit["data"]),
    )

    pv2 = c.post(
        "/api/import/words/preview",
        files={"file": ("words.xlsx", content, XLSX_MIME)},
        headers=admin_headers,
    ).json()
    check(
        "二次预览 duplicated=118（不算 invalid）",
        pv2["data"]["summary"]["duplicated"] == 118 and pv2["data"]["summary"]["invalid"] == 2,
        json.dumps(pv2["data"]["summary"]),
    )
    before = _count(db_file, "words")
    skip = c.post(
        "/api/import/words/commit",
        json={"preview_token": pv2["data"]["preview_token"],
              "create_missing_units": True, "on_duplicate": "skip"},
        headers=admin_headers,
    ).json()
    check(
        "二次 commit(skip)：全部跳过且总数不变",
        skip["data"]["skipped"] == 118 and _count(db_file, "words") == before,
        json.dumps(skip["data"]),
    )

    # ---------------- 停用 / overwrite 不覆盖 status
    target = _query(db_file, "SELECT id, en FROM words ORDER BY id LIMIT 1")[0]
    first_id = int(target["id"])
    stopped = c.post(
        "/api/words/batch-status",
        json={"word_ids": [first_id], "status": "inactive"},
        headers=admin_headers,
    ).json()
    check("批量停用返回改动条数", stopped["data"]["updated"] == 1, "")

    rows2 = [_word_row("word001", "词1改名")] + [
        _word_row(f"word{i:03d}", f"词{i}") for i in range(2, 61)
    ] + [_word_row(f"word{i:03d}", f"词{i}", unit="Unit 5") for i in range(61, 121)]
    pv3 = c.post(
        "/api/import/words/preview",
        files={"file": ("words2.xlsx", _xlsx(WORDS_HEADERS, rows2), XLSX_MIME)},
        headers=admin_headers,
    ).json()
    overwritten = c.post(
        "/api/import/words/commit",
        json={"preview_token": pv3["data"]["preview_token"],
              "create_missing_units": True, "on_duplicate": "overwrite"},
        headers=admin_headers,
    ).json()
    status_now = _query(db_file, "SELECT status, cn FROM words WHERE id = ?", (first_id,))[0]
    check(
        "overwrite 更新 cn 但**不覆盖 status**",
        overwritten["data"]["updated"] == 118
        and status_now["status"] == "inactive"
        and status_now["cn"] == "词1改名",
        f"status={status_now['status']} cn={status_now['cn']}",
    )

    # ---------------- 删除保护
    fresh = _query(db_file, "SELECT id FROM words WHERE status='active' ORDER BY id DESC LIMIT 1")[0]
    fresh_id = int(fresh["id"])
    deleted = c.delete(f"/api/words/{fresh_id}", headers=admin_headers).json()
    check("无记录词条可物理删除", deleted["code"] == 0 and deleted["data"]["deleted"] is True, "")

    _seed_answers(db_file, student_id, first_id, 37)
    guarded = c.delete(f"/api/words/{first_id}", headers=admin_headers).json()
    check(
        "有 37 条记录的词条：30003 + record_count=37",
        guarded["code"] == 30003 and guarded["data"]["record_count"] == 37,
        f"code={guarded['code']} count={guarded['data'].get('record_count')}",
    )
    unit_guarded = c.delete(f"/api/units/{unit_id}", headers=admin_headers).json()
    check(
        "含已练内容的单元：30003 拒绝",
        unit_guarded["code"] == 30003 and unit_guarded["data"]["record_count"] == 37,
        str(unit_guarded["data"].get("record_count")),
    )

    # ---------------- 课文导入
    text_content = _xlsx(TEXTS_HEADERS, [["语文", 7, "上学期", "第一单元", "背影", "朱自清", "\n".join(BEIYING), ""]])
    pv_text = c.post(
        "/api/import/texts/preview",
        files={"file": ("texts.xlsx", text_content, XLSX_MIME)},
        headers=admin_headers,
    ).json()
    item = pv_text["data"]["texts"][0]
    check(
        "《背影》预览 pinyin_check=ok 且 3 段",
        item["pinyin_check"] == "ok" and len(item["paragraphs"]) == 3,
        f"check={item['pinyin_check']} paras={len(item['paragraphs'])}",
    )
    sum_chars = sum(p["chars"] for p in item["paragraphs"])
    check("total_chars 等于各段汉字数之和", item["total_chars"] == sum_chars, f"{item['total_chars']} vs {sum_chars}")

    row_no = int(item["row"])
    committed_text = c.post(
        "/api/import/texts/commit",
        json={"preview_token": pv_text["data"]["preview_token"],
              "texts": [{"row": row_no, "recite_scope": [0, 1]}]},
        headers=admin_headers,
    ).json()
    text_row = _query(db_file, "SELECT * FROM texts LIMIT 1")[0]
    expected = item["paragraphs"][0]["chars"] + item["paragraphs"][1]["chars"]
    plain_len = len(text_row["content_plain"])
    pinyin_len = len(text_row["content_pinyin"].split())
    check(
        "commit 回传 scope=[0,1]：范围与 total_chars 均按新范围重算",
        committed_text["code"] == 0
        and text_row["recite_scope"] == "[0, 1]"
        and text_row["total_chars"] == expected,
        f"scope={text_row['recite_scope']} chars={text_row['total_chars']} expect={expected}",
    )
    check(
        "落库后 content_plain 与 content_pinyin **严格等长**",
        plain_len == pinyin_len == expected,
        f"plain={plain_len} pinyin={pinyin_len}",
    )
    check("第三段不在背诵范围内（未进 content_plain）", "奔丧回家" not in text_row["content_plain"], "")

    # 拼音失配
    bad_text = _xlsx(TEXTS_HEADERS, [["语文", 7, "上学期", "第一单元", "生僻字课文", "无名", "他叫𠮷野家。", ""]])
    pv_bad = c.post(
        "/api/import/texts/preview",
        files={"file": ("bad.xlsx", bad_text, XLSX_MIME)},
        headers=admin_headers,
    ).json()
    bad_item = pv_bad["data"]["texts"][0]
    check(
        "生僻字预览标 mismatch 并给出位置",
        bad_item["pinyin_check"] == "mismatch" and bad_item["mismatch_positions"] == [2],
        f"check={bad_item['pinyin_check']} pos={bad_item['mismatch_positions']}",
    )
    before_texts = _count(db_file, "texts")
    bad_commit = c.post(
        "/api/import/texts/commit",
        json={"preview_token": pv_bad["data"]["preview_token"], "texts": []},
        headers=admin_headers,
    ).json()
    check(
        "拼音 mismatch 拒绝入库",
        bad_commit["code"] == 40003 and _count(db_file, "texts") == before_texts,
        f"code={bad_commit['code']} reason={bad_commit['data'].get('reason')}",
    )

    # ---------------- 双角色
    student_view = c.get(
        f"/api/units/{unit_id}/words?mode=cn2en", headers=student_headers
    ).json()
    words = student_view["data"]["words"]
    check(
        "学生端隐藏 en 与 accepted，附 mastery.status",
        words
        and all(w["en"] is None and "accepted" not in w for w in words)
        and all("status" in w["mastery"] for w in words),
        f"n={len(words)}",
    )
    check(
        "停用词条不出现在学生端",
        all(w["id"] != first_id for w in words),
        f"n={len(words)}",
    )
    admin_view = c.get(f"/api/units/{unit_id}/words", headers=admin_headers).json()
    first_item = next((w for w in admin_view["data"]["items"] if w["id"] == first_id), None)
    check(
        "管理端返回全字段（accepted / status / require_spell）",
        admin_view["data"]["total"] > 0
        and first_item is not None
        and first_item["status"] == "inactive"
        and isinstance(first_item["require_spell"], bool),
        f"total={admin_view['data']['total']}",
    )
    check(
        "管理端分页结构完整",
        all(k in admin_view["data"] for k in ("items", "total", "page", "page_size")),
        "",
    )

    # 课文详情 / 列表
    text_id = int(_query(db_file, "SELECT id FROM texts LIMIT 1")[0]["id"])
    detail = c.get(f"/api/texts/{text_id}", headers=admin_headers).json()["data"]
    check(
        "课文详情含 scope_snapshot 且**不下发拼音**",
        "content_pinyin" not in detail and "content_plain" not in detail
        and detail["scope_snapshot"]["end"] == expected,
        str(detail["scope_snapshot"]),
    )
    text_unit_id = int(_query(db_file, "SELECT unit_id FROM texts LIMIT 1")[0]["unit_id"])
    text_list = c.get(f"/api/units/{text_unit_id}/texts", headers=admin_headers).json()
    check(
        "课文列表不含正文（用**语文单元**的 id 查，不是英语那个）",
        text_list["code"] == 0
        and bool(text_list["data"]["texts"])
        and "content_raw" not in text_list["data"]["texts"][0],
        f"n={len(text_list['data'].get('texts', []))}",
    )

    # ---------------- 错误报告（与学生名单共用路径）
    pv4 = c.post(
        "/api/import/words/preview",
        files={"file": ("words3.xlsx", _xlsx(WORDS_HEADERS, _build_120()), XLSX_MIME)},
        headers=admin_headers,
    ).json()
    report = c.get(pv4["data"]["error_report_url"], headers=admin_headers)
    rws = load_workbook(BytesIO(report.content)).active
    rheaders = [x.value for x in rws[1]]
    check(
        "词条错误报告复用同一路径且表头 14 列",
        report.status_code == 200
        and rheaders[:2] == ["原文件行号", "错误原因"]
        and len(rheaders) == 16
        and rws.max_row == 3,
        f"rows={rws.max_row} cols={len(rheaders)}",
    )

    # ---------------- 异常
    not_xlsx = c.post(
        "/api/import/words/preview",
        files={"file": ("a.txt", b"not an excel", "text/plain")},
        headers=admin_headers,
    ).json()
    check("非 xlsx 返回 40001", not_xlsx["code"] == 40001, str(not_xlsx["code"]))

    missing = _xlsx(["学科", "英文"], [["英语", "class"]])
    miss = c.post(
        "/api/import/words/preview",
        files={"file": ("m.xlsx", missing, XLSX_MIME)},
        headers=admin_headers,
    ).json()
    check(
        "缺列返回 40002 且列出缺失列",
        miss["code"] == 40002 and miss["data"]["missing_columns"],
        str(miss["data"]["missing_columns"])[:60],
    )

    big = c.post(
        "/api/import/words/preview",
        files={"file": ("big.xlsx", b"x" * (11 * 1024 * 1024), XLSX_MIME)},
        headers=admin_headers,
    )
    check(
        "11MB 上传：HTTP 413 + code 40004",
        big.status_code == 413 and big.json()["code"] == 40004,
        f"http={big.status_code}",
    )

    # ---------------- 审计
    audits = _query(
        db_file,
        "SELECT COUNT(*) AS c FROM audit_logs WHERE action LIKE 'content.%' OR action LIKE 'unit.%' "
        "OR action LIKE 'word.%' OR action LIKE 'text.%'",
    )[0]["c"]
    check("内容操作写入了审计日志", int(audits) >= 5, f"n={audits}")


# ---------------------------------------------------------------------------
# 直连库（烟测脚本专用，绕过 HTTP 做事实核对）
# ---------------------------------------------------------------------------


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


def _count(db_file: Path, table: str) -> int:
    return int(_query(db_file, f"SELECT COUNT(*) AS c FROM {table}")[0]["c"])


def _seed_answers(db_file: Path, student_id: int, word_id: int, count: int) -> None:
    """给某词条造 `count` 条答题记录（验证删除保护）。"""
    conn = _conn(db_file)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "INSERT INTO sessions (student_id, unit_id, content_type, strategy) "
            "VALUES (?, (SELECT unit_id FROM words WHERE id = ?), 'english', '{}')",
            (student_id, word_id),
        )
        session_id = int(cur.lastrowid or 0)
        for _ in range(count):
            conn.execute(
                "INSERT INTO answers (session_id, student_id, word_id, mode, step, "
                "expected, is_correct, match_type) VALUES (?, ?, ?, 'cn2en', 1, 'x', 1, 'exact')",
                (session_id, student_id, word_id),
            )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
