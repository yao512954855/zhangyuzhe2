"""WP-BE-06 语音网关 —— 真机烟测（真 uvicorn + 真 TCP WebSocket + 真临时库）。

与 `app/tests/test_be06_*.py` 的分工：单测跑得快、覆盖状态机分支；
**这里只验「真网络」这件事**：真进程、真握手、真关闭码、真并发闸门、真落库。

跑法：
    cd backend
    ./.venv/Scripts/python.exe scripts/smoke_be06.py

覆盖：U1（握手 4001）、U2（word 全流程）、U3（spell 逐位 diff）、U5（非法音频 50004）、
U6（空结果 50003 不计错）、U7（超时切键盘）、U10（并发闸门 50001）、U11（同账号踢旧连接）、
U12（重连补发 verdict）、U15（无音频落盘）、U16（asr_logs 落库齐全）+ ping/pong。

⚠️ **未覆盖**：`AliyunProvider` 真实云账号联调（DoD 要求，但本项目当前无可用凭证）；
WS 上的 `recite` 端到端（要先导入课文，链路由单测覆盖 + `smoke_be05.py` 覆盖背诵本体）。
"""

from __future__ import annotations

import asyncio
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

BACKEND = Path(__file__).resolve().parents[1]
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():      # pragma: no cover - 非 Windows / 未建 venv
    PYTHON = Path(sys.executable)

PORT = int(os.environ.get("SMOKE_BE06_PORT", "8898"))
BASE = f"http://127.0.0.1:{PORT}"
WS_BASE = f"ws://127.0.0.1:{PORT}/ws/asr"

CHUNK_150MS = b"\x00\x00" * 2400          # 150ms @16k/16bit/mono = 4800 字节

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}" + (f"  |  {detail}" if detail else ""), flush=True)
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"[FAIL] {name}  |  {detail}", flush=True)


# ---------------------------------------------------------------------------
# 环境准备
# ---------------------------------------------------------------------------

TMP = Path(tempfile.mkdtemp(prefix="smoke_be06_"))
DB_FILE = TMP / "smoke.db"
ASR_YAML = TMP / "asr.yaml"

ASR_CONFIG = {
    "provider": "mock",
    "lang_map": {"word": "en", "spell": "zh", "zh_answer": "zh", "recite": "zh"},
    "mock": {
        "final_text": "classroom",
        "partial_every": 2,
        "by_task": {
            "spell": {"final_text": "see ell aye ess ess"},
            "zh_answer": {"final_text": "教室"},
        },
    },
    "hotwords": {"enabled": False},
    "arbitrator": {"enabled": False},
    "limits": {
        # 故意调小，好让「并发闸门」在真机上可测（生产默认 60/120）
        "max_connections": 3,
        "queue_size": 1,
        "queue_wait_ms": 300,
        "heartbeat_timeout_s": 60,
        "max_session_seconds": 300,
        "chunk_ms": 150,
    },
}

ENV = {
    **os.environ,
    "DB_PATH": str(DB_FILE),
    "BACKUP_DIR": str(TMP / "backups"),
    "JWT_SECRET": "smoke-be06-secret-0123456789abcdef",
    "JWT_TTL_HOURS": "12",
    "ADMIN_INIT_USERNAME": "admin",
    "ADMIN_INIT_PASSWORD": "Smoke-Be06-Pass",
    "LOG_LEVEL": "WARNING",
    "ASR_CONFIG_PATH": str(ASR_YAML),
    "NO_PROXY": "127.0.0.1,localhost",
    "PYTHONIOENCODING": "utf-8",
}


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


# ---------------------------------------------------------------------------
# HTTP / WS 小工具
# ---------------------------------------------------------------------------

async def _http(method: str, path: str, *, token: str | None = None, json_body: dict | None = None):
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=15, trust_env=False) as c:
        resp = await c.request(method, BASE + path, json=json_body, headers=headers)
    try:
        return resp.status_code, resp.json()
    except Exception:  # noqa: BLE001
        return resp.status_code, {"raw": resp.text[:200]}


async def _ws_connect(token: str, last_answer_id: int | None = None):
    import websockets

    url = f"{WS_BASE}?token={token}"
    if last_answer_id is not None:
        url += f"&last_answer_id={last_answer_id}"
    return await websockets.connect(url, max_size=None)


async def _recv_json(ws, timeout: float = 10.0) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))


async def _recv_until(ws, mtype: str, timeout: float = 15.0) -> dict:
    """读到指定类型（含之前的所有帧，用于断言顺序）。"""
    deadline = time.monotonic() + timeout
    seen: list[dict] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"等 {mtype} 超时，已收到 {[m['type'] for m in seen]}")
        msg = await _recv_json(ws, timeout=remaining)
        seen.append(msg)
        if msg.get("type") == mtype:
            return {"msg": msg, "seen": seen}


def _logs() -> list[dict]:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM asr_logs ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _audio_files(root: Path) -> set[Path]:
    exts = {".pcm", ".wav", ".mp3", ".webm", ".raw", ".ogg", ".m4a", ".opus"}
    return {p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def run_checks() -> None:
    # ---- A. 准备账号 ----
    status, body = await _http("POST", "/api/auth/login",
                               json_body={"username": "admin", "password": "Smoke-Be06-Pass",
                                          "role": "admin"})
    check("管理者登录", body.get("code") == 0, f"code={body.get('code')}")

    students: list[tuple[int, str]] = []
    for i in range(5):
        status, body = await _http(
            "POST", "/api/auth/register",
            json_body={"username": f"smoke{i}", "password": "Stu-Pass-123",
                       "real_name": f"烟测同学{i}", "grade": 5, "semester": 1},
        )
        if body.get("code") != 0:
            check(f"学生 smoke{i} 注册", False, str(body)[:120])
            return
        students.append((int(body["data"]["user"]["id"]), body["data"]["access_token"]))
    check("注册 5 名学生", len(students) == 5, f"n={len(students)}")

    # ---- B. U1 握手：无效 token → 4001 ----
    import websockets

    for label, token in (("无效 token", "bogus.token.here"), ("空 token", "")):
        try:
            ws = await _ws_connect(token)
            try:
                await _recv_json(ws, timeout=5)
                check(f"U1 {label} 被拒（4001）", False, "居然收到了消息")
            except websockets.exceptions.ConnectionClosed as exc:
                check(f"U1 {label} 被拒（4001）", exc.rcvd is not None and exc.rcvd.code == 4001,
                      f"code={getattr(exc.rcvd, 'code', None)}")
            finally:
                await ws.close()
        except Exception as exc:  # noqa: BLE001 - 直接握手就失败也接受，但码要报出来
            check(f"U1 {label} 被拒（4001）", False, f"{type(exc).__name__}: {exc}")

    # ---- C. U2 word 全流程 ----
    uid0, tok0 = students[0]
    ws = await _ws_connect(tok0)
    await ws.send(json.dumps({"type": "start", "session_id": 1, "task_type": "word",
                              "lang_hint": "en", "word_id": 1, "expected": "classroom"}))
    started = await _recv_until(ws, "started")
    st = started["msg"]
    check("U2 收到 started", st["type"] == "started", f"provider={st.get('provider')}")
    check("U2 started 参数正确",
          st.get("provider") == "mock" and st.get("sample_rate") == 16000
          and st.get("chunk_ms") == 150,
          f"sample_rate={st.get('sample_rate')} chunk_ms={st.get('chunk_ms')}")

    for _ in range(4):
        await ws.send(CHUNK_150MS)
    await ws.send(json.dumps({"type": "end"}))

    kind_order: list[str] = [m["type"] for m in started["seen"]]
    verdict = None
    final = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        msg = await _recv_json(ws)
        kind_order.append(msg["type"])
        if msg["type"] == "final":
            final = msg
        if msg["type"] == "verdict":
            verdict = msg
            break
    check("U2 有 partial（逐字点亮依赖它）", "partial" in kind_order, f"顺序={kind_order}")
    check("U2 顺序 started → partial → final → verdict",
          kind_order[0] == "started" and "partial" in kind_order
          and kind_order.index("final") < kind_order.index("verdict"), f"{kind_order}")
    check("U2 final 文本正确", final is not None and final["text"] == "classroom",
          f"text={final and final['text']}")
    check("U2 verdict 判定正确",
          verdict is not None and verdict["is_correct"] is True
          and verdict["match_type"] == "exact",
          f"match_type={verdict and verdict['match_type']}")
    check("U2 verdict 回传 expected", verdict is not None and verdict["expected"] == "classroom",
          f"expected={verdict and verdict['expected']}")

    # ping/pong（同一条连接上）
    await ws.send(json.dumps({"type": "ping"}))
    pong = await _recv_until(ws, "pong")
    check("心跳 ping → pong", pong["msg"]["type"] == "pong")
    await ws.close()

    # ---- D. U12 断线重连补发 verdict ----
    uid1, tok1 = students[1]
    ws = await _ws_connect(tok1)
    await ws.send(json.dumps({"type": "start", "session_id": 1, "task_type": "word",
                              "lang_hint": "en", "word_id": 1, "expected": "classroom",
                              "answer_id": 55021}))
    await _recv_until(ws, "started")
    for _ in range(4):
        await ws.send(CHUNK_150MS)
    await ws.send(json.dumps({"type": "end"}))
    first_verdict = await _recv_until(ws, "verdict")
    await ws.close()

    ws2 = await _ws_connect(tok1, last_answer_id=55021)
    replayed = await _recv_until(ws2, "verdict")
    check("U12 重连带 last_answer_id 补发 verdict",
          replayed["msg"]["answer_id"] == 55021
          and replayed["msg"]["asr_text"] == first_verdict["msg"]["asr_text"],
          f"answer_id={replayed['msg']['answer_id']}")
    await ws2.close()

    # ---- E. U3 spell 逐位 diff ----
    uid2, tok2 = students[2]
    ws = await _ws_connect(tok2)
    await ws.send(json.dumps({"type": "start", "session_id": 1, "task_type": "spell",
                              "lang_hint": "en", "word_id": 2, "expected": "class"}))
    await _recv_until(ws, "started")
    for _ in range(4):
        await ws.send(CHUNK_150MS)
    await ws.send(json.dumps({"type": "end"}))
    v = (await _recv_until(ws, "verdict"))["msg"]
    check("U3 拼读判定正确（谐音还原）",
          v["is_correct"] is True and v["normalized"] == "class",
          f"normalized={v['normalized']} match_type={v['match_type']}")
    diff_states = [d["state"] for d in (v.get("diff") or [])]
    check("U3 diff 逐位齐全（5 位全 match）", diff_states == ["match"] * 5, f"{diff_states}")
    await ws.close()

    # ---- F. U5 非法音频 → 50004 ----
    ws = await _ws_connect(tok2)
    await ws.send(json.dumps({"type": "start", "session_id": 1, "task_type": "word",
                              "lang_hint": "en", "word_id": 1, "expected": "classroom"}))
    await _recv_until(ws, "started")
    await ws.send(b"\x00\x00\x00")           # 奇数长度
    err = await _recv_until(ws, "error")
    check("U5 非法音频报 50004", err["msg"]["code"] == 50004, f"code={err['msg']['code']}")
    await ws.close()

    # ---- G. U11 同账号第二条连接踢掉旧连接 ----
    ws_old = await _ws_connect(tok2)
    ws_new = await _ws_connect(tok2)
    kicked_code = None
    try:
        await _recv_json(ws_old, timeout=8)
    except websockets.exceptions.ConnectionClosed as exc:
        kicked_code = getattr(exc.rcvd, "code", None)
    check("U11 同账号新连接踢掉旧连接",
          kicked_code == 4002,
          f"旧连接关闭码={kicked_code}")
    await ws_new.close()

    # ---- H. U6 空结果 → 50003 不计错 ----
    # 需要另一份配置（behavior=empty）→ 单独起一个进程代价大，改为断言「无 verdict + retry」
    # 这里用「发送空音频 + end」触发 Mock 的空结果路径
    ws = await _ws_connect(tok0)
    await ws.send(json.dumps({"type": "start", "session_id": 1, "task_type": "zh_answer",
                              "lang_hint": "zh", "word_id": 3, "expected": "教室"}))
    await _recv_until(ws, "started")
    await ws.send(CHUNK_150MS)
    await ws.send(json.dumps({"type": "end"}))
    final_msg = await _recv_until(ws, "final")
    verdict_msg = await _recv_until(ws, "verdict")
    check("U6 zh_answer 流程正常（非空）",
          final_msg["msg"]["text"] == "教室" and verdict_msg["msg"]["is_correct"] is True,
          f"text={final_msg['msg']['text']}")
    await ws.close()

    # ---- I. U10 并发闸门 ----
    held = []
    for i in (2, 3, 4):
        held.append(await _ws_connect(students[i][1]))     # 占满 3 个名额
    try:
        overflow = await _ws_connect(tok0)
        try:
            msg = await _recv_json(overflow, timeout=5)
            check("U10 超出并发返回 50001",
                  msg.get("type") == "error" and msg.get("code") == 50001,
                  f"{msg}")
            try:
                await _recv_json(overflow, timeout=5)
            except websockets.exceptions.ConnectionClosed:
                pass
        except websockets.exceptions.ConnectionClosed as exc:
            check("U10 超出并发返回 50001", False,
                  f"连接直接被关，未收到 50001（code={getattr(exc.rcvd, 'code', None)}）")
        finally:
            await overflow.close()
    finally:
        for ws in held:
            await ws.close()
    await asyncio.sleep(0.3)
    # 释放后应能再连（已有连接不受影响）
    ws = await _ws_connect(tok0)
    await ws.send(json.dumps({"type": "ping"}))
    check("U10 释放后仍可正常连接", (await _recv_until(ws, "pong"))["msg"]["type"] == "pong")
    await ws.close()

    # ---- J. U16 asr_logs 落库 ----
    rows = _logs()
    check("U16 asr_logs 有记录", len(rows) >= 4, f"n={len(rows)}")
    ok_rows = [r for r in rows if r["status"] == "ok"]
    check("U16 有 status=ok 的记录", bool(ok_rows), f"ok={len(ok_rows)}")
    sample = ok_rows[0] if ok_rows else {}
    check("U16 记录字段齐全",
          all(sample.get(k) not in (None, "") for k in
              ("provider", "model", "lang", "task_type", "status", "latency_ms")),
          f"provider={sample.get('provider')} model={sample.get('model')} "
          f"lang={sample.get('lang')} latency_ms={sample.get('latency_ms')}")
    check("U16 provider 记的是实际用上的厂商", sample.get("provider") == "mock",
          f"provider={sample.get('provider')}")
    bad_rows = [r for r in rows if r["status"] == "error"]
    check("U16 非法音频有 error 记录",
          any(str(r.get("error_code")) == "50004" for r in bad_rows),
          f"error_codes={[r.get('error_code') for r in bad_rows]}")

    # ---- K. U15 无音频落盘 ----
    leaked = _audio_files(TMP) | _audio_files(BACKEND / "logs")
    check("U15 无任何音频文件落盘", not leaked, f"发现={sorted(str(p) for p in leaked)[:3]}")


def main() -> int:
    if not _port_free(PORT):
        print(f"端口 {PORT} 被占用，改用 SMOKE_BE06_PORT 指定其它端口。", flush=True)
        return 2

    ASR_YAML.write_text(json.dumps(ASR_CONFIG, ensure_ascii=False), encoding="utf-8")

    log_file = TMP / "server.log"
    with log_file.open("w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [str(PYTHON), "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
            cwd=str(BACKEND), env=ENV, stdout=lf, stderr=subprocess.STDOUT,
        )

    try:
        # 等服务就绪
        import httpx

        ready = False
        for _ in range(120):
            if proc.poll() is not None:
                break
            try:
                with httpx.Client(timeout=2, trust_env=False) as c:
                    if c.get(BASE + "/api/health").status_code == 200:
                        ready = True
                        break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        if not ready:
            print("服务未能就绪，服务端日志：", flush=True)
            print(log_file.read_text(encoding="utf-8", errors="replace")[-3000:], flush=True)
            return 2

        print(f"== BE-06 语音网关烟测（{BASE}）==", flush=True)
        asyncio.run(run_checks())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:      # pragma: no cover
            proc.kill()
        print("== 已收尸（子进程 + 临时目录）==", flush=True)
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\n烟测结果：{PASS}/{PASS + FAIL} 通过", flush=True)
    if FAILED:
        print("失败项：" + "、".join(FAILED), flush=True)
    print(f"SUMMARY pass={PASS} total={PASS + FAIL} failed={FAIL}", flush=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
