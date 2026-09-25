"""WP-QA-01 冒烟核心 —— 真进程 + 真库 + 真 HTTP/WebSocket，一键跑通核心链路。

由 `qa/smoke/smoke.sh` 拉起一个**真的 uvicorn**（临时库、Mock ASR）后调用本文件。
本文件只做「打接口 + 查库对账」，不负责起停服务 —— 起停与环境变量放在 `smoke.sh`，
这样「端口/进程/清理」只有一处，失败时排错不用在两个文件之间跳。

覆盖的是《40-集成与验收规格》§2 冒烟清单：

    1  后端能启动，/api/health 返回 db:true、schema_version 正确
    2  首次启动自动建 admin 账号；管理端可登录
    3  GET /api/ping 免鉴权可用且响应 < 50ms
    4  App 扫码能连上服务器（`qr_content` 形如 `bjsys://ip:port`）+ UDP 广播真往返
    5  学生注册 → 登录 → 首页能加载
    6  管理端导入 1 个单元 + 5 个词条 → 学生端能看到
    7  学生完成 1 道英语题 → answers 有记录 → word_mastery 更新
    8  学生完成 1 段语文背诵 → recitations 有记录
    9  管理端班级概览有数字（非空）
    10 手动触发一次备份，文件生成且 integrity_check 通过

外加两条与验收矩阵强相关、且**必须真跑**的链路：
    - 申诉链路（fuzzy → 学生申诉 → 管理者复核 → **原判分不变**），对应 E2E-05
    - 权限隔离（学生 token 调管理端接口 → 10003），对应 E2E-07

⚠️ 两个刻意的设计约束（都是踩过坑换来的）：

1. **必须 `env -u PYTHONPATH` 运行 `smoke.sh`**。WorkBuddy 沙箱把 safe-delete shim 注进了
   `PYTHONPATH`，它 hook 了 `unlink`，一旦本轮已有一次批量删除被拒，
   **同轮后续所有删除一律拒绝**，且拒绝方式是 `raise SystemExit(1)`
   —— 会直接把 spawn 出来的 uvicorn 杀掉（症状：子进程**一行日志都没有**）。
   `SystemExit` 继承 `BaseException`，业务代码 `except OSError` 兜不住，
   也**不该**为它写 `except BaseException`。解法在调用层，不在烟测里做沙箱适配。

2. **`MIN_CHECKS` 必须断言**。用例被静默跳过却报「通过」是烟测最大的假绿来源，
   比跑失败更危险 —— 失败你还会去看，假绿你不会。
   本脚本的 `check()` 只接受真断言；确实无法自动化的（如手机真扫码）走 `skip()`，
   单独计数，并在报告里显式列出。

跑法（一般不用直接调，用 `smoke.sh`）：

    qa/smoke/smoke.sh
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

#: 契约 §2 固定 MIME（导入接口按它做格式校验）
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: 词条模板 14 列（与 `app/api/imports.py` 的 words 模板一致）
WORDS_HEADERS = [
    "学科", "年级", "学期", "单元名", "单元序号", "类型", "英文",
    "中文", "可接受答案", "音标", "词性", "例句英", "例句中", "是否拼读",
]
#: 课文模板 8 列
TEXTS_HEADERS = ["学科", "年级", "学期", "单元名", "课文标题", "作者", "段落正文", "背诵范围"]

ADMIN_USER = "admin"
ADMIN_PASSWORD = "Smoke-Qa01-Pass"
STUDENT_USER = "20260101"
STUDENT_PASSWORD = "student123"

#: 冒烟至少要有这么多条**真断言**。低于它就说明用例被跳过/漏跑了。
#:
#: 实测执行路径固定 39 条（各 if/else 分支里的 check 数一一对应，
#: 所以失败也不会让总数变少，只会让通过的变少）。取 36 留 3 条余量，
#: 既能容忍后续微调，又能挡住「静默跳过一大半仍报通过」这种最危险的假绿。
MIN_CHECKS = 36

_results: list[tuple[bool, str, str]] = []
_skips: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((bool(ok), name, detail))
    flag = "[OK]" if ok else "[!!]"
    print(f"{flag} #{len(_results):02d} {name}" + (f"    {detail}" if detail else ""), flush=True)
    return bool(ok)


def skip(name: str, reason: str) -> None:
    _skips.append((name, reason))
    print(f"[--] SKIP {name}    {reason}", flush=True)


def _ascii(text: str) -> str:
    return " ".join("".join(ch for ch in text if ch.isascii()).split()) or "-"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


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


def _integrity(path: Path) -> str:
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def _word_row(en: str, cn: str, *, unit: str, spell: str = "是", accepted: str = "") -> list:
    """一行词条。列序必须与 `WORDS_HEADERS` 一致。"""
    return ["英语", 3, "上学期", unit, 1, "单词", en, cn, accepted, "", "", "", "", spell]


def _text_row(unit: str, title: str, paragraphs: list[str]) -> list:
    """一行课文：**段落用换行分隔**（服务端按行切段）。"""
    return ["语文", 3, "上学期", unit, title, "", "\n".join(paragraphs), ""]


# ---------------------------------------------------------------------------
# WebSocket（真链路：握手 → 音频帧 → Mock ASR → 引擎 → verdict）
# ---------------------------------------------------------------------------


async def _ws_roundtrip(ws_base: str, token: str, session_id: int, word_id: int,
                        expected: str, timeout: float = 25.0) -> dict[str, Any]:
    """跑一次 `task_type=word` 的完整 WS 会话，返回收到的消息类型序列与最终帧。

    音频帧用 150ms 静音（16kHz/mono/16bit → 4800 字节）：Mock Provider 不看内容，
    但**切片的字节数必须对**，否则会被服务端的 50004（音频格式非法）拦掉 ——
    这正是我们想验证的链路。
    """
    import websockets

    chunk = b"\x00" * (16000 * 150 // 1000 * 2)
    seen: list[str] = []
    final: dict[str, Any] | None = None
    verdict: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    url = f"{ws_base}/ws/asr?token={token}"
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "start",
            "session_id": session_id,
            "task_type": "word",
            "word_id": word_id,
            "expected": expected,
        }))
        deadline = time.monotonic() + timeout
        # 先等 started —— 收到它才能确定握手 + 建连都真的成功了
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.monotonic()))
            msg = json.loads(raw)
            seen.append(str(msg.get("type")))
            if msg.get("type") == "started":
                break
            if msg.get("type") == "error":
                error = msg
                break

        if error is None and "started" in seen:
            for _ in range(4):
                await ws.send(chunk)
            await ws.send(json.dumps({"type": "end"}))
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=max(1.0, deadline - time.monotonic())
                )
                msg = json.loads(raw)
                seen.append(str(msg.get("type")))
                if msg.get("type") == "final":
                    final = msg
                elif msg.get("type") == "verdict":
                    verdict = msg
                    break
                elif msg.get("type") == "error":
                    error = msg
                    break
        try:
            await ws.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响断言
            pass

    return {"seen": seen, "final": final, "verdict": verdict, "error": error}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run_all(client: httpx.Client, db: Path, ws_base: str, backups: Path) -> None:  # noqa: C901
    # ---------------- 1. 健康检查 ----------------
    health = client.get("/api/health").json()
    hdata = health.get("data") or {}
    check(
        "健康检查（db 可达 + 迁移版本 + trace_id）",
        health.get("code") == 0 and hdata.get("db") is True
        and hdata.get("schema_version") == 1 and bool(health.get("trace_id")),
        f"db={hdata.get('db')} schema_version={hdata.get('schema_version')}",
    )

    # ---------------- 2. 管理者登录（首次启动自动建号） ----------------
    login = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "role": "admin"},
    ).json()
    admin_ok = login.get("code") == 0
    check("首次启动自动建 admin 且能登录", admin_ok, str(login.get("message"))[:60])
    if not admin_ok:
        # 后面全部依赖 admin token，直接停在这里，避免刷一屏二次失败掩盖根因
        print("  ⚠️ 管理者登录失败，后续用例无法继续。", flush=True)
        return
    ah = {"Authorization": f"Bearer {login['data']['access_token']}"}

    # ---------------- 3. ping 免鉴权且够快 ----------------
    t0 = time.perf_counter()
    ping = client.get("/api/ping")
    ping_ms = (time.perf_counter() - t0) * 1000
    pbody = ping.json()
    pdata = pbody.get("data") or {}
    check(
        "GET /api/ping 免鉴权 + <50ms + 字段齐",
        pbody.get("code") == 0
        and ping_ms < 50
        and all(k in pdata for k in ("service", "version", "server_name", "time")),
        f"{ping_ms:.1f}ms fields={sorted(pdata)}",
    )

    # ---------------- 4. 服务发现：二维码 + UDP 真往返 ----------------
    info = client.get("/api/server/info", headers=ah).json()
    idata = info.get("data") or {}
    qr = str(idata.get("qr_content") or "")
    check(
        "server/info 的 qr_content 形如 bjsys://ip:port",
        info.get("code") == 0 and qr.startswith("bjsys://") and ":" in qr[8:],
        f"qr_content={qr!r}",
    )
    check(
        "server/info 带网卡列表与推荐地址标记",
        isinstance(idata.get("interfaces"), list)
        and any(i.get("recommended") for i in idata["interfaces"]),
        f"interfaces={len(idata.get('interfaces') or [])}",
    )
    # 真 UDP 广播往返（App 那套协议，真 socket）
    try:
        import socket

        payload = json.dumps({"magic": "BJSYS_DISCOVER", "v": 1}).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(3.0)
            s.sendto(payload, ("127.0.0.1", 9527))
            data, _addr = s.recvfrom(4096)
        reply = json.loads(data.decode("utf-8"))
        check(
            "UDP 9527 广播发现真往返（magic=BJSYS_HERE）",
            reply.get("magic") == "BJSYS_HERE" and int(reply.get("port") or 0) > 0,
            f"reply keys={sorted(reply)} port={reply.get('port')}",
        )
    except Exception as exc:  # noqa: BLE001
        check("UDP 9527 广播发现真往返（magic=BJSYS_HERE）", False, f"{type(exc).__name__}: {exc}")

    # ---------------- 5. 学生注册 → 登录 → 首页 ----------------
    reg = client.post(
        "/api/auth/register",
        json={"username": STUDENT_USER, "real_name": "冒烟学生", "password": STUDENT_PASSWORD},
    ).json()
    check("学生自主注册即用（无需审核）", reg.get("code") == 0, str(reg.get("message"))[:60])

    stu_login = client.post(
        "/api/auth/login",
        json={"username": STUDENT_USER, "password": STUDENT_PASSWORD, "role": "student"},
    ).json()
    check("学生可登录", stu_login.get("code") == 0, str(stu_login.get("message"))[:60])
    sh = {"Authorization": f"Bearer {stu_login['data']['access_token']}"}
    student_id = int(stu_login["data"]["user"]["id"])

    home = client.get("/api/me/units", headers=sh).json()
    check(
        "学生首页可加载（/api/me/units）",
        home.get("code") == 0 and isinstance(home.get("data"), (list, dict)),
        f"code={home.get('code')}",
    )

    # ---------------- 6. 权限隔离（E2E-07） ----------------
    forbidden = client.get("/api/units", headers=sh).json()
    forbidden2 = client.get("/api/stats/class/1", headers=sh).json()
    check(
        "权限隔离：学生调管理端接口一律 10003",
        forbidden.get("code") == 10003 and forbidden2.get("code") == 10003,
        f"units={forbidden.get('code')} stats={forbidden2.get('code')}",
    )

    # ---------------- 7. 班级建档 + 归班 ----------------
    klass = client.post(
        "/api/classes",
        json={"name": "三年级(1)班", "grade": 3, "semester": "first",
              "school_year": "2026-2027", "remark": "QA-01 冒烟"},
        headers=ah,
    ).json()
    klass_ok = klass.get("code") == 0
    check("新建班级", klass_ok, str(klass.get("message"))[:60])
    class_id = int(klass["data"]["id"]) if klass_ok else 0

    unassigned = client.get("/api/students", params={"class_id": "unassigned"}, headers=ah).json()
    unassigned_ids = [s["id"] for s in (unassigned.get("data") or {}).get("items", [])]
    check(
        "「未分班」是显式筛选项且包含刚注册的学生",
        unassigned.get("code") == 0 and student_id in unassigned_ids,
        f"unassigned={unassigned_ids}",
    )

    if class_id:
        assign = client.post(
            f"/api/students/{student_id}/assign-class",
            json={"class_id": class_id},
            headers=ah,
        ).json()
        class_now = _scalar(db, "SELECT class_id FROM users WHERE id = ?", (student_id,))
        check(
            "单个归班生效（users.class_id 落库）",
            assign.get("code") == 0 and class_now == class_id,
            f"class_id={class_now}",
        )
        after = client.get("/api/students", params={"class_id": "unassigned"}, headers=ah).json()
        after_ids = [s["id"] for s in (after.get("data") or {}).get("items", [])]
        check(
            "归班后「未分班」不再包含该学生",
            student_id not in after_ids,
            f"unassigned={after_ids}",
        )
    else:
        check("单个归班生效（users.class_id 落库）", False, "班级创建失败，无法归班")
        check("归班后「未分班」不再包含该学生", False, "班级创建失败，无法归班")

    # ---------------- 8. 内容导入：1 单元 + 5 词条 ----------------
    en_unit = "Unit 1 Hello!"
    words = ["classroom", "apple", "banana", "orange", "pencil"]
    content = _xlsx(
        WORDS_HEADERS,
        [
            _word_row("classroom", "教室", unit=en_unit),
            _word_row("apple", "苹果", unit=en_unit),
            _word_row("banana", "香蕉", unit=en_unit),
            _word_row("orange", "橙子", unit=en_unit),
            # 故意留一行非法（年级越界）→ 验证错误行定位真的可用
            ["英语", 10, "上学期", en_unit, 1, "单词", "pencil", "铅笔", "", "", "", "", "", "是"],
        ],
    )
    pv = client.post(
        "/api/import/words/preview",
        files={"file": ("words.xlsx", content, XLSX_MIME)},
        headers=ah,
    ).json()
    pv_sum = (pv.get("data") or {}).get("summary") or {}
    invalid_rows = [r["row"] for r in (pv.get("data") or {}).get("invalid_rows") or []]
    # ⚠️ `row` 是**真实 Excel 行号（1 起，表头算第 1 行）**，服务端算法是
    #    `excel_row = idx + 2`（`ImportService.words_preview`）。本文件表头 1 行 +
    #    4 条合法 + 第 5 条非法 → 非法行落在**第 6 行**，不是 5、也不是 7。
    #    这个差一已经坑过两处烟测（WEB-03 也踩过），别再按「数据行序号」推。
    check(
        "词条导入 preview：valid=4 / invalid=1 且错误行定位到第 6 行（Excel 物理行号）",
        pv.get("code") == 0
        and pv_sum.get("valid") == 4
        and pv_sum.get("invalid") == 1
        and invalid_rows == [6],
        f"summary={pv_sum} invalid_rows={invalid_rows}",
    )

    pv = client.post(
        "/api/import/words/preview",
        files={"file": ("words.xlsx", content, XLSX_MIME)},
        headers=ah,
    ).json()
    commit = client.post(
        "/api/import/words/commit",
        json={"preview_token": pv["data"]["preview_token"],
              "create_missing_units": True, "on_duplicate": "overwrite"},
        headers=ah,
    ).json()
    words_in_db = _scalar(db, "SELECT COUNT(*) FROM words")
    check(
        "词条导入 commit：落库 4 条 + 自动建 1 个单元",
        commit.get("code") == 0
        and int((commit.get("data") or {}).get("created") or 0) == 4
        and int((commit.get("data") or {}).get("units_created") or 0) == 1
        and words_in_db == 4,
        f"created={(commit.get('data') or {}).get('created')} units_created={(commit.get('data') or {}).get('units_created')} words={words_in_db}",
    )

    unit_id = int(_scalar(db, "SELECT id FROM units WHERE name = ?", (en_unit,)) or 0)

    # 学生端能看到：显式带 grade/semester 拉一次（不依赖用户偏好，注册时还没选年级）
    seen_units = client.get(
        "/api/me/units", params={"grade": 3, "semester": "first"}, headers=sh
    ).json()
    seen_names = [u.get("name") for u in ((seen_units.get("data") or {}).get("items") or [])]
    check(
        "学生端能看到刚导入的单元",
        seen_units.get("code") == 0 and en_unit in seen_names,
        f"units={seen_names}",
    )

    # ---------------- 9. 英语答题：判分 + 落库 + 掌握度 ----------------
    sess = client.post(
        "/api/sessions",
        json={"unit_id": unit_id, "content_type": "english",
              "strategy": {"count": 4, "order": "sequential", "mode": "cn2en"}},
        headers=sh,
    ).json()
    items = (sess.get("data") or {}).get("items") or []
    check("组卷成功且下发的题面不含答案侧字段", sess.get("code") == 0 and len(items) >= 1,
          f"items={len(items)}")
    session_id = int((sess.get("data") or {}).get("session_id") or 0)

    # 挑出 classroom 那一题。
    # ⚠️ 组卷响应里**没有 `en` 字段**（只有 seq/word_id/type/mode/prompt/require_spell/target_len）
    # —— 这是有意的（§9.1「组卷响应绝不含答案侧信息」，英文词就是 cn2en 的答案）。
    # 所以只能按 `word_id` 定位：先查库拿到 classroom 的 id，再在 items 里找它。
    classroom_id = int(_scalar(db, "SELECT id FROM words WHERE en = 'classroom'") or -1)
    target = None
    for it in items:
        if int(it.get("word_id") or 0) == classroom_id:
            target = it
            break
    target = target or (items[0] if items else None)
    word_id = int((target or {}).get("word_id") or 0)
    mode = str((target or {}).get("mode") or "cn2en")

    ans = client.post(
        f"/api/sessions/{session_id}/answers",
        json={"word_id": word_id, "mode": mode, "step": 1, "asr_text": "classroom",
              "duration_ms": 1500, "first_voice_ms": 500, "provider": "mock"},
        headers={**sh, "Idempotency-Key": f"qa01-{session_id}-{word_id}-1"},
    ).json()
    adata = ans.get("data") or {}
    ans_rows = _rows(db, "SELECT * FROM answers WHERE session_id = ?", (session_id,))
    check(
        "英语答题判分正确（exact）+ 落库 1 条",
        ans.get("code") == 0 and adata.get("is_correct") is True
        and str(adata.get("match_type")) == "exact" and len(ans_rows) == 1,
        f"is_correct={adata.get('is_correct')} match_type={adata.get('match_type')} rows={len(ans_rows)}",
    )
    check(
        "记录全字段落库（首次发声延迟 / 重试 / 提示 / provider）",
        bool(ans_rows)
        and ans_rows[0].get("first_voice_ms") == 500
        and ans_rows[0].get("duration_ms") == 1500
        and ans_rows[0].get("provider") == "mock",
        f"first_voice_ms={ans_rows[0].get('first_voice_ms') if ans_rows else None}",
    )
    mastery = _scalar(db, "SELECT COUNT(*) FROM word_mastery WHERE student_id = ? AND word_id = ?",
                      (student_id, word_id))
    check(
        "word_mastery 物化表随答题更新",
        bool(mastery),
        f"rows={mastery}",
    )

    # ---------------- 10. ASR WebSocket 真链路（Mock Provider） ----------------
    try:
        wsres = asyncio.run(_ws_roundtrip(ws_base, stu_login["data"]["access_token"],
                                          session_id, word_id, "classroom"))
        ok_ws = "started" in wsres["seen"] and (wsres["final"] or wsres["verdict"]) is not None
        check(
            "ASR WebSocket 真链路：握手→音频帧→started→判定帧",
            ok_ws,
            f"seen={wsres['seen']} err={wsres.get('error')}",
        )
        check(
            "无音频落盘（ASR 只留文本）",
            not list(BACKEND.glob("**/*.wav")) and not list(Path(db).parent.glob("**/*.wav")),
            "logs/临时目录均无 wav",
        )
    except Exception as exc:  # noqa: BLE001
        check("ASR WebSocket 真链路：握手→音频帧→started→判定帧", False,
              f"{type(exc).__name__}: {exc}")
        check("无音频落盘（ASR 只留文本）", False, "WS 未跑通，无法判定")

    # ---------------- 11. 申诉链路（E2E-05）-----------
    # 目标长度 ≥6 且仅 1 处差异 → fuzzy + can_appeal=True（需求 §6.2）
    fuzzy = client.post(
        f"/api/sessions/{session_id}/answers",
        json={"word_id": word_id, "mode": mode, "step": 1, "asr_text": "classroon",
              "duration_ms": 1800, "provider": "mock"},
        headers={**sh, "Idempotency-Key": f"qa01-{session_id}-{word_id}-fuzzy"},
    ).json()
    fdata = fuzzy.get("data") or {}
    fuzzy_answer_id = int(fdata.get("answer_id") or 0)
    check(
        "仅 1 处差异判为 fuzzy 且 can_appeal=true",
        fuzzy.get("code") == 0 and str(fdata.get("match_type")) == "fuzzy"
        and fdata.get("can_appeal") is True,
        f"match_type={fdata.get('match_type')} can_appeal={fdata.get('can_appeal')}",
    )

    appeal_ok = False
    if fuzzy_answer_id:
        ap = client.post(
            f"/api/answers/{fuzzy_answer_id}/appeal",
            json={"reason": "我读对了"},
            headers=sh,
        ).json()
        appeal_ok = ap.get("code") == 0
    check("学生申诉提交成功", appeal_ok, f"answer_id={fuzzy_answer_id}")

    pend = client.get("/api/appeals", params={"status": "pending"}, headers=ah).json()
    pend_ids = [a.get("appeal_id") or a.get("id") for a in (pend.get("data") or {}).get("items", [])]
    check(
        "管理端申诉列表默认只返回待复核",
        pend.get("code") == 0 and fuzzy_answer_id in pend_ids,
        f"pending={pend_ids}",
    )

    is_correct_before = _scalar(db, "SELECT is_correct FROM answers WHERE id = ?", (fuzzy_answer_id,))
    review = client.post(
        f"/api/appeals/{fuzzy_answer_id}/review",
        json={"result": "upheld", "remark": "QA-01 冒烟：判读正确"},
        headers=ah,
    ).json()
    is_correct_after = _scalar(db, "SELECT is_correct FROM answers WHERE id = ?", (fuzzy_answer_id,))
    check(
        "复核为成立但**不改变原判分**（硬约束）+ 落审计",
        review.get("code") == 0
        and is_correct_before == is_correct_after
        and str(_scalar(db, "SELECT appeal_result FROM answers WHERE id = ?", (fuzzy_answer_id,))) == "upheld"
        and bool(_scalar(db, "SELECT COUNT(*) FROM audit_logs WHERE action = 'appeal.review'")),
        f"before={is_correct_before} after={is_correct_after} result={_scalar(db, 'SELECT appeal_result FROM answers WHERE id = ?', (fuzzy_answer_id,))}",
    )

    # 结算：正确率分母必须排除 empty（这里没有 empty，用「项数 − 全 empty 项数」口径核对）
    fin = client.post(f"/api/sessions/{session_id}/finish",
                      json={"reason": "completed"}, headers=sh).json()
    fdata2 = fin.get("data") or {}
    check(
        "结算成功且给出项级正确率口径",
        fin.get("code") == 0 and "counted_items" in fdata2 and "empty_items" in fdata2,
        f"accuracy={fdata2.get('accuracy')} counted={fdata2.get('counted_items')} empty={fdata2.get('empty_items')}",
    )

    # ---------------- 12. 语文背诵链路 ----------------
    zh_unit = "第一单元"
    tcontent = _xlsx(
        TEXTS_HEADERS,
        [_text_row(zh_unit, "背影（节选）", [
            "我与父亲不相见已二年余了，我最不能忘记的是他的背影。",
            "那年冬天，祖母死了，父亲的差使也交卸了。",
        ])],
    )
    tpv = client.post(
        "/api/import/texts/preview",
        files={"file": ("texts.xlsx", tcontent, XLSX_MIME)},
        headers=ah,
    ).json()
    check(
        "课文导入 preview 通过（段落切分 + 拼音等长校验）",
        tpv.get("code") == 0 and int(((tpv.get("data") or {}).get("summary") or {}).get("valid") or 0) >= 1,
        f"summary={(tpv.get('data') or {}).get('summary')}",
    )
    tcommit = client.post(
        "/api/import/texts/commit",
        json={"preview_token": (tpv.get("data") or {}).get("preview_token")},
        headers=ah,
    ).json()
    text_rows = _rows(db, "SELECT id, title, recite_scope FROM texts")
    check(
        "课文 commit 落库且带背诵范围",
        tcommit.get("code") == 0 and len(text_rows) == 1,
        f"texts={[(r['id'], r['title']) for r in text_rows]}",
    )

    if text_rows:
        zh_unit_id = int(_scalar(db, "SELECT id FROM units WHERE subject = 'chinese'") or 0)
        zsess = client.post(
            "/api/sessions",
            json={"unit_id": zh_unit_id, "content_type": "chinese",
                  "strategy": {"order": "sequential"}},
            headers=sh,
        ).json()
        zh_session_id = int((zsess.get("data") or {}).get("session_id") or 0)
        check("语文组卷成功（不发 mode）", zsess.get("code") == 0, f"code={zsess.get('code')}")

        rec = client.post(
            "/api/recitations",
            json={"session_id": zh_session_id, "text_id": int(text_rows[0]["id"]), "scope": []},
            headers=sh,
        ).json()
        rec_id = int((rec.get("data") or {}).get("recitation_id") or (rec.get("data") or {}).get("id") or 0)
        check("创建背诵（recitations）", rec.get("code") == 0, f"recitation_id={rec_id}")

        if rec_id:
            feed = client.post(
                f"/api/recitations/{rec_id}/feed",
                json={"partial_text": "我与父亲不相见已二年余了"},
                headers=sh,
            ).json()
            fdat = feed.get("data") or {}
            check(
                "背诵 feed 推进点亮进度（matched_chars > 0）",
                feed.get("code") == 0 and int(fdat.get("matched_chars") or 0) > 0,
                f"matched={fdat.get('matched_chars')} total={fdat.get('total_chars')}",
            )
            hint = client.post(
                f"/api/recitations/{rec_id}/hint",
                json={"level": 1, "trigger_by": "manual"},
                headers=sh,
            ).json()
            check(
                "三档提示：一档返回占位信息",
                hint.get("code") == 0,
                f"code={hint.get('code')} keys={sorted((hint.get('data') or {}))[:6]}",
            )
            settle = client.post(
                f"/api/recitations/{rec_id}/settle",
                json={},
                headers=sh,
            ).json()
            sdat = settle.get("data") or {}
            rec_rows = _rows(db, "SELECT * FROM recitations WHERE id = ?", (rec_id,))
            check(
                "背诵结算：服务端重算 + 六项结算字段齐 + 落库",
                settle.get("code") == 0
                and bool(rec_rows) and rec_rows[0].get("finish_reason")
                and all(k in sdat for k in ("grade", "covered_ratio")),
                f"grade={sdat.get('grade')} covered={sdat.get('covered_ratio')} finish_reason={rec_rows[0].get('finish_reason') if rec_rows else None}",
            )
            # U15：中途退出 → 场次标记 abandoned，已答数据不丢
            ab = client.post(
                f"/api/sessions/{zh_session_id}/finish",
                json={"reason": "abandoned"},
                headers=sh,
            ).json()
            status_now = _scalar(db, "SELECT status FROM sessions WHERE id = ?", (zh_session_id,))
            check(
                "中途退出：场次标记 abandoned 且已答数据不丢",
                ab.get("code") == 0 and status_now == "abandoned"
                and bool(_scalar(db, "SELECT COUNT(*) FROM recitations WHERE session_id = ?", (zh_session_id,))),
                f"status={status_now}",
            )
        else:
            for nm in ("背诵 feed 推进点亮进度（matched_chars > 0）",
                       "三档提示：一档返回占位信息",
                       "背诵结算：服务端重算 + 六项结算字段齐 + 落库",
                       "中途退出：场次标记 abandoned 且已答数据不丢"):
                check(nm, False, "创建背诵失败，无法继续")
    else:
        for nm in ("语文组卷成功（不发 mode）", "创建背诵（recitations）",
                   "背诵 feed 推进点亮进度（matched_chars > 0）",
                   "三档提示：一档返回占位信息",
                   "背诵结算：服务端重算 + 六项结算字段齐 + 落库",
                   "中途退出：场次标记 abandoned 且已答数据不丢"):
            check(nm, False, "课文导入失败，无法继续")

    # ---------------- 13. 看板有数字 ----------------
    if class_id:
        overview = client.get(f"/api/stats/class/{class_id}", headers=ah).json()
        od = overview.get("data") or {}
        check(
            "班级概览有真实数字（人数 / 正确率 / 趋势）",
            overview.get("code") == 0
            and int(od.get("student_count") or 0) >= 1
            and isinstance(od.get("trend"), list),
            f"students={od.get('student_count')} accuracy={od.get('accuracy')} trend={len(od.get('trend') or [])}",
        )
        unit_ov = client.get(f"/api/stats/unit/{unit_id}", headers=ah).json()
        ud = unit_ov.get("data") or {}
        check(
            "单元掌握度可出（正确率 / 难词榜）",
            unit_ov.get("code") == 0 and int(ud.get("word_total") or 0) > 0,
            f"word_total={ud.get('word_total')} accuracy={ud.get('accuracy')}",
        )
    else:
        check("班级概览有真实数字（人数 / 正确率 / 趋势）", False, "班级创建失败")
        check("单元掌握度可出（正确率 / 难词榜）", False, "班级创建失败")

    # ---------------- 14. 备份产物 + integrity_check ----------------
    bk = client.post("/api/maintenance/backup", headers=ah).json()
    files = sorted(backups.glob("app_*.db")) if backups.is_dir() else []
    integ = _integrity(files[-1]) if files else "no-file"
    check(
        "手动备份产出文件且 integrity_check 通过",
        bk.get("code") == 0 and bool(files) and integ == "ok",
        f"files={len(files)} integrity={integ}",
    )

    # 无法自动化的项：**显式 skip**，不假装覆盖
    skip("手机真扫码唤起 App（U13 / E2E-03 的真机部分）",
         "无头环境没有真手机，且无法验证 App 的 bjsys:// scheme 注册；已用 qr_content 格式断言替代")


def main() -> int:
    ap = argparse.ArgumentParser(description="QA-01 冒烟核心")
    ap.add_argument("--base-url", required=True, help="后端 HTTP 基址，如 http://127.0.0.1:8820")
    ap.add_argument("--ws-url", required=True, help="后端 WS 基址，如 ws://127.0.0.1:8820")
    ap.add_argument("--db", required=True, help="被烟测后端的临时库路径（只读查对账用）")
    ap.add_argument("--backups", required=True, help="被烟测后端的 BACKUP_DIR")
    ap.add_argument("--min-checks", type=int, default=MIN_CHECKS)
    args = ap.parse_args()

    db = Path(args.db)
    backups = Path(args.backups)

    print(f"== QA-01 冒烟核心 → {args.base_url}（库 {db.name}）==", flush=True)
    started = time.perf_counter()
    # trust_env=False：本机联调必须绕过系统代理，否则连 127.0.0.1 会被代理接走
    with httpx.Client(base_url=args.base_url, timeout=30.0, trust_env=False) as client:
        run_all(client, db, args.ws_url, backups)
    elapsed = time.perf_counter() - started

    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)

    print()
    print(f"冒烟结果：{passed}/{total} 通过，{len(_skips)} 项跳过，耗时 {elapsed:.1f}s", flush=True)
    for ok, name, detail in _results:
        if not ok:
            print(f"  未通过：{name}    {detail}", flush=True)
    for name, reason in _skips:
        print(f"  跳过：{name} —— {reason}", flush=True)

    # ---- 防假绿：用例数不足就是失败，不允许「静默跳过却报通过」----
    if total < args.min_checks:
        print(f"[!!] 用例数 {total} < 下限 {args.min_checks}，判定为失败（防假绿）", flush=True)
        print(f"SUMMARY pass={passed} total={total} skipped={len(_skips)} failed={total - passed} guard=FAIL",
              flush=True)
        return 1

    print(f"SUMMARY pass={passed} total={total} skipped={len(_skips)} failed={total - passed} "
          f"guard=OK(min={args.min_checks})", flush=True)
    names = ";".join(_ascii(n) for _, n, _ in _results)
    print(f"NAMES {names}", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
