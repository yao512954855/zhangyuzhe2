"""真机首次启动烟测（WP-BE-01 F1/F19）。

与 pytest 的区别：**这里起的是真的 uvicorn 进程**，走真实的 socket，
验证「空目录首次启动 → 自动迁移 → 建 admin → /api/health 可访问」这条链路。

用法：
    cd backend
    ./.venv/Scripts/python.exe scripts/smoke_boot.py

退出码 0 = 全部通过。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
PORT = 8810
HOST = "127.0.0.1"

# 故意的空目录：验证「首次启动」而非「复用已有库」
FRESH_DIR = BASE_DIR / "data" / "_bootcheck"
FRESH_DB = FRESH_DIR / "fresh.db"
LOG_FILE = FRESH_DIR / "boot.log"

_checks: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    _checks.append((name, passed, detail))
    flag = "PASS" if passed else "FAIL"
    print(f"  [{flag}] {name}" + (f" — {detail}" if detail else ""))


def port_in_use(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def http_get(path: str, timeout: float = 5.0) -> tuple[int, dict, object]:
    """返回 (status_code, json_body, headers)。

    headers 是 `email.message.Message` 对象 —— **故意不转成 dict**：
    ASGI 出口的响应头是**小写**（`x-trace-id`），转 dict 会丢掉大小写不敏感特性，
    用 `headers.get("X-Trace-Id")` 就取不到了。
    """
    req = urllib.request.Request(f"http://{HOST}:{PORT}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw), resp.headers
    except urllib.error.HTTPError as exc:  # 4xx/5xx
        raw = exc.read().decode("utf-8")
        try:
            body = json.loads(raw)
        except Exception:  # noqa: BLE001
            body = {"_raw": raw}
        return exc.code, body, exc.headers


def wait_ready(proc: subprocess.Popen, timeout: float = 40.0) -> bool:
    """轮询 /api/health 直到服务就绪或进程猝死。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False  # 进程已退出，等下去没意义
        try:
            status, _, _ = http_get("/api/health", timeout=2.0)
            if status == 200:
                return True
        except Exception:  # noqa: BLE001 - 还没起来，继续等
            pass
        time.sleep(0.4)
    return False


def main() -> int:
    print("=" * 70)
    print("WP-BE-01 真机首次启动烟测")
    print("=" * 70)

    if port_in_use(HOST, PORT):
        print(f"✗ 端口 {PORT} 已被占用，先停掉占用进程再跑。")
        return 1

    if FRESH_DIR.exists():
        shutil.rmtree(FRESH_DIR, ignore_errors=True)
    FRESH_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n[准备] 全新空目录：{FRESH_DIR}")
    print(f"[准备] 数据库将首次创建于：{FRESH_DB}")

    env = os.environ.copy()
    # 环境变量优先级高于 .env，因此不会碰开发库
    env["DB_PATH"] = str(FRESH_DB)
    env["LOG_LEVEL"] = "INFO"

    log_fh = LOG_FILE.open("w", encoding="utf-8", newline="")
    proc = subprocess.Popen(
        [
            str(PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            HOST,
            "--port",
            str(PORT),
            "--log-level",
            "info",
        ],
        cwd=str(BASE_DIR),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )

    print(f"\n[启动] uvicorn pid={proc.pid}，日志：{LOG_FILE}\n")
    try:
        ready = wait_ready(proc)
        check("服务在 40s 内就绪（/api/health 返回 200）", ready)
        if not ready:
            print("\n--- 启动日志尾部 ---")
            log_fh.flush()
            tail = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            print("\n".join(tail[-40:]))
            return 1

        status, body, headers = http_get("/api/health")
        check("GET /api/health → HTTP 200", status == 200, f"status={status}")
        check("响应 code=0", body.get("code") == 0, f"code={body.get('code')}")
        data = body.get("data") or {}
        check("db 可达", data.get("db") is True, f"db={data.get('db')}")
        # ⚠️ 断言的是「迁移已经跑过」，不是「版本恰好是 1」——
        #    schema_version 随迁移文件增加而上升（二期后已是 2），写死等于自造测试债。
        check(
            "schema_version 已迁移（>= 1）",
            int(data.get("schema_version") or 0) >= 1,
            f"schema_version={data.get('schema_version')}",
        )
        check("asr_ready 字段存在（值可为 False）", "asr_ready" in data)
        tid = body.get("trace_id") or ""
        check("响应体带 12 位 trace_id", len(tid) == 12, f"trace_id={tid!r}")
        header_tid = headers.get("X-Trace-Id")  # Message 对象，大小写不敏感
        check(
            "响应头 X-Trace-Id 与响应体一致",
            header_tid == tid,
            f"header={header_tid!r}, body={tid!r}",
        )
        check("DB 文件已落盘", FRESH_DB.exists(), str(FRESH_DB))

        status, body, _ = http_get("/api/ping")
        check("GET /api/ping → code=0", status == 200 and body.get("code") == 0)

        # 404：契约要求统一结构，HTTP 码保留 404
        status, body, _ = http_get("/api/definitely-not-exists")
        check(
            "未知路径 → HTTP 404 且响应体统一结构",
            status == 404 and "code" in body and "trace_id" in body,
            f"status={status}, code={body.get('code')}",
        )

        # 错误方式 → 405，响应体同样保持统一结构
        req = urllib.request.Request(
            f"http://{HOST}:{PORT}/api/ping", method="POST", data=b"{}"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status, body = resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = json.loads(exc.read().decode("utf-8"))
        check(
            "POST /api/ping（方法不允许）→ 405 且响应体统一结构",
            status == 405 and "trace_id" in body,
            f"status={status}, code={body.get('code')}",
        )

        # 注：BE-01 阶段所有路由都是免鉴权的（health / ping）。
        # 「未鉴权 → HTTP 200 + 10002」由 pytest 用例 U4/U5 通过挂载测试路由覆盖；
        # 真实的鉴权接口随 BE-02 落地后再补进本烟测。
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_fh.close()

    failed = [c for c in _checks if not c[1]]
    print("\n" + "=" * 70)
    print(f"结果：{len(_checks) - len(failed)}/{len(_checks)} 项通过")
    print("=" * 70)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
