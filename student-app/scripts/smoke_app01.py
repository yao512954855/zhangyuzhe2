"""WP-APP-01 学生端 —— 真机烟测（真 uvicorn + 真 HTTP + 真 Flutter 客户端）。

与 `student-app/test/*_test.dart` 的分工：
- 单测/组件测试跑得快、覆盖逻辑分支，但用的是**假 HTTP 适配器**；
- **这里只验「真后端」这件事**：字段名、错误码、响应包装、token 往返
  —— 那些「假适配器按自己的想象解析」永远发现不了的问题。

跑法：
    cd student-app
    python scripts/smoke_app01.py

它会：
    ① 用临时库拉起一个真 uvicorn（默认 127.0.0.1:8810）
    ② 等 /api/ping 就绪
    ③ 跑 `flutter test test/smoke_app01_live_test.dart`
    ④ 关掉服务、清理临时库

后端已在本机跑着时用 `SMOKE_APP01_EXTERNAL=1` 复用现成的（不启停服务）。

⚠️ **踩过的两个坑（别再踩）**
1. `flutter_test` 默认装全局 `HttpOverrides`，会把真请求吞成 `400 + 空体`，
   不解开就「全绿=全 skip」。已在 `smoke_app01_live_test.dart` 里
   `HttpOverrides.global = null`。
2. **「全部跳过」绝不算通过** —— 否则这个脚本会变成橡皮图章。
   下面用 `_parse_test_counts` 把跳过数抠出来，>0 直接判失败。
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDENT_APP = ROOT / "student-app"
BACKEND = ROOT / "backend"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():  # pragma: no cover - 非 Windows / 未建 venv
    PYTHON = Path(sys.executable)

FLUTTER_HOME = Path(
    os.environ.get(
        "FLUTTER_HOME",
        "D:/2install/flutter/flutter_windows_3.47.5-stable/flutter",
    )
)
FLUTTER = FLUTTER_HOME / "bin" / "flutter.bat"

HOST = "127.0.0.1"
PORT = int(os.environ.get("SMOKE_APP01_PORT", "8810"))
USE_EXTERNAL = os.environ.get("SMOKE_APP01_EXTERNAL") == "1"

#: 真机联调至少要有这么多用例真跑（防止「改了测试名/文件没被收集」导致假绿）。
MIN_LIVE_TESTS = 8


# ---------------------------------------------------------------------------
# Flutter 运行环境
#
# 从 bash（MSYS/Git-Bash）里调 flutter 会崩：MSYS 环境不继承 Windows 的
# PATHEXT，flutter_tools 的 `where git` 返回 null，直接抛
# "Null check operator used on a null value"。
# 所以这里**显式补齐**——与 `scripts/flutter.sh` 保持同一套修复。
# ---------------------------------------------------------------------------
FLUTTER_ENV = {
    **os.environ,
    "PATHEXT": ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC",
    "PUB_HOSTED_URL": os.environ.get("PUB_HOSTED_URL", "https://pub.flutter-io.cn"),
    "FLUTTER_STORAGE_BASE_URL": os.environ.get(
        "FLUTTER_STORAGE_BASE_URL", "https://storage.flutter-io.cn"
    ),
    "ANDROID_HOME": os.environ.get("ANDROID_HOME_OVERRIDE", "D:/2install/androidstudiotool/sdk"),
    "ANDROID_SDK_ROOT": os.environ.get(
        "ANDROID_SDK_ROOT", os.environ.get("ANDROID_HOME_OVERRIDE", "D:/2install/androidstudiotool/sdk")
    ),
    "JAVA_HOME": os.environ.get(
        "JAVA_HOME_OVERRIDE", "D:/2install/androidstudiotool/jbr"
    ),
    "NO_PROXY": "localhost,127.0.0.1,::1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12",
    "PYTHONIOENCODING": "utf-8",
}
FLUTTER_ENV["no_proxy"] = FLUTTER_ENV["NO_PROXY"]


def _listener_pid(port: int) -> str | None:
    """谁占着这个端口（失败返回 None，仅供报错提示用）。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        ).stdout
    except Exception:  # noqa: BLE001
        return None
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line.upper():
            return line.split()[-1]
    return None


def _port_in_use(port: int, host: str = HOST) -> bool:
    """是否有进程在 **LISTEN**（而不是「连得上」）。

    ⚠️ 别用 `connect_ex()`：服务刚被 terminate 的那一瞬间，`connect_ex` 可能
    仍返回 0（半关闭/TIME_WAIT 窗口），于是收尾自检会误报「端口仍被占用」，
    但 netstat 里根本没有 LISTENING —— 白折腾一轮排查。
    netstat 的 LISTENING 才是权威判据；netstat 不可用时才退回 connect 探测。
    """
    pid = _listener_pid(port)
    if pid is not None:
        return True
    with socket.socket() as s:
        return s.connect_ex((host, port)) == 0


def _kill_tree(proc: subprocess.Popen | None) -> None:
    """结束子进程并**确认它真的死了**。

    只 `terminate()` 不够：早期版本 terminate 后直接走人，导致 uvicorn
    残留在 8810 上，下一轮烟测一开就报「端口被占用」。
    """
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass


def _wait_backend(timeout_s: float = 90.0) -> bool:
    """等 `/api/ping` 可答。

    ⚠️ 判据用**契约字段** `data["service"] == "bjsys"`（接口设计 §3.1）。
    早先这里写的是 `service == "bjsys"`，而当时 BE-01 的桩只回
    `{"pong": true}` —— 于是「其实已就绪」被误判成超时，白等 60 秒。
    （该桩已按 §3.1 修正，见 `backend/app/main.py`。）
    """
    import httpx

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"http://{HOST}:{PORT}/api/ping", timeout=2, trust_env=False)
            if resp.status_code == 200 and resp.json().get("data", {}).get("service") == "bjsys":
                return True
        except Exception:  # noqa: BLE001 - 还没起来
            time.sleep(0.4)
    return False


def _parse_test_counts(output: str) -> tuple[int, int]:
    """从 `flutter test` 输出里抠出（通过数, 跳过数）。

    形如 `00:00 +11: All tests passed!` / `00:00 +0 ~11: All tests skipped.`
    """
    passed = max((int(m) for m in re.findall(r"\+(\d+)", output)), default=0)
    skipped = max((int(m) for m in re.findall(r"~(\d+)", output)), default=0)
    return passed, skipped


def _run_flutter_test() -> tuple[int, str]:
    """跑真机联调用例：**边跑边打日志**，同时把输出留一份用于判定。"""
    lines: list[str] = []
    env = {**FLUTTER_ENV, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [
            str(FLUTTER), "test",
            "test/smoke_app01_live_test.dart",
            f"--dart-define=APP01_SMOKE_HOST={HOST}",
            f"--dart-define=APP01_SMOKE_PORT={PORT}",
        ],
        cwd=str(STUDENT_APP),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)
        print(line.rstrip("\n"), flush=True)
    proc.wait()
    return proc.returncode, "".join(lines)


def main() -> int:
    if not FLUTTER.exists():
        print(f"❌ 找不到 flutter：{FLUTTER}", flush=True)
        return 2

    tmp: Path | None = None
    proc: subprocess.Popen | None = None
    code = 2

    # ---------------------------------------------------------------- 起后端
    if USE_EXTERNAL:
        if not _port_in_use(PORT):
            print(f"❌ SMOKE_APP01_EXTERNAL=1，但 {HOST}:{PORT} 上没有服务。", flush=True)
            return 2
        print(f"ℹ️  复用已运行的后端 {HOST}:{PORT}", flush=True)
    else:
        if _port_in_use(PORT):
            pid = _listener_pid(PORT)
            print(
                f"❌ 端口 {PORT} 已被占用（PID={pid or '未知'}）。\n"
                f"   先结束它：taskkill /F /PID {pid or '<pid>'}\n"
                f"   或换端口：SMOKE_APP01_PORT=8811 python scripts/smoke_app01.py\n"
                f"   或复用现成服务：SMOKE_APP01_EXTERNAL=1 ...",
                flush=True,
            )
            return 2

        tmp = Path(tempfile.mkdtemp(prefix="smoke_app01_"))
        env = {
            **os.environ,
            "DB_PATH": str(tmp / "smoke.db"),
            "BACKUP_DIR": str(tmp / "backups"),
            "JWT_SECRET": "smoke-app01-secret-0123456789abcdef",
            "ADMIN_INIT_USERNAME": "admin",
            "ADMIN_INIT_PASSWORD": "Smoke-App01-Pass",
            "LOG_LEVEL": "WARNING",
            "NO_PROXY": "127.0.0.1,localhost",
            "PYTHONIOENCODING": "utf-8",
        }
        log_path = tmp / "server.log"
        log_file = log_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            [
                str(PYTHON), "-m", "uvicorn", "app.main:app",
                "--host", HOST, "--port", str(PORT), "--log-level", "warning",
            ],
            cwd=str(BACKEND),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        print(f"⏳ 启动后端 {HOST}:{PORT} …", flush=True)
        if not _wait_backend():
            # 先关掉文件句柄再读，否则可能读到空内容（子进程的写句柄还没释放）
            log_file.flush()
            try:
                os.fsync(log_file.fileno())
            except OSError:  # pragma: no cover
                pass
            log_file.close()
            print("❌ 后端未能在 90 秒内就绪。服务日志：", flush=True)
            try:
                print(log_path.read_text(encoding="utf-8")[-4000:], flush=True)
            except Exception:  # noqa: BLE001
                pass
            # 进程若已退出，把退出码也报出来（最常见是依赖缺失/配置错）
            print(f"   子进程退出码：{proc.poll()}", flush=True)
            _kill_tree(proc)
            shutil.rmtree(tmp, ignore_errors=True)
            return 2
        print("✅ 后端就绪", flush=True)

    # ---------------------------------------------------------------- 跑测试
    try:
        print("\n▶ flutter test test/smoke_app01_live_test.dart\n", flush=True)
        code, output = _run_flutter_test()
        passed, skipped = _parse_test_counts(output)

        # —— 假绿拦截：真机联调层「全跳过」等于什么都没验，必须判失败 ——
        if skipped > 0:
            print(
                f"\n❌ 有 {skipped} 个真机联调用例被**跳过**（通过 {passed} 个）。\n"
                f"   后端已启动却跳过 = 客户端根本没连上，通常是被 HttpOverrides 拦了，\n"
                f"   或 APP01_SMOKE_HOST/PORT 与真实地址不一致。这一层全跳过不算通过。",
                flush=True,
            )
            code = 1
        elif passed < MIN_LIVE_TESTS:
            print(
                f"\n❌ 只跑了 {passed} 个用例，少于预期的 {MIN_LIVE_TESTS} 个"
                f"（是不是测试文件没被收集？）。",
                flush=True,
            )
            code = 1
        else:
            print(
                f"\n{'✅' if code == 0 else '❌'} 真机联调 ran {passed} 通过 / {skipped} 跳过"
                f"（flutter test 退出码 {code}）",
                flush=True,
            )
            if code != 0:
                code = 1
    finally:
        _kill_tree(proc)
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------------------- 收尾自检
    if not USE_EXTERNAL and _port_in_use(PORT):
        print(f"⚠️  {PORT} 仍被占用，可能有残留进程（PID={_listener_pid(PORT)}）。", flush=True)

    print("\n" + ("✅ 烟测通过" if code == 0 else "❌ 烟测失败"), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
