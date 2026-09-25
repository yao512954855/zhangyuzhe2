"""WP-APP-02 学生端 —— 真机烟测（真 uvicorn + 真 HTTP + 真 Flutter 客户端）。

与 `test/test_app02_*.dart` 的分工：
- 组件测试跑得快，但所有 HTTP 都走**假适配器**，只能证明"客户端按自己的
  想象解析响应"；
- **这里只验「真后端」这件事**：`/api/me/units` 到底有没有 `with_error`、
  语文单元的字段名是不是 `text_total`、学生视图的词条里会不会漏出 `accepted`、
  `POST /api/sessions` 的 `strategy` 字段名后端认不认 —— 全是假适配器
  永远发现不了的问题。

跑法：
    cd student-app
    python scripts/smoke_app02.py

它会：
    ① 用临时库拉起一个真 uvicorn（默认 127.0.0.1:8812）
    ② 等 /api/ping 就绪
    ③ **从 Excel 造数据**：英语词条（3 年级上学期）+ 语文课文（3 年级上学期）
       —— 与 BE-03/BE-05 的烟测同一条链路，导入回归顺带做了
    ④ 跑 `flutter test test/smoke_app02_live_test.dart`
    ⑤ 关服务、清理临时库

后端已在本机跑着时用 `SMOKE_APP02_EXTERNAL=1` 复用现成的（不启停服务、不造数）。

⚠️ **踩过的两个坑（照抄 APP-01，别再踩）**
1. `flutter_test` 默认装全局 `HttpOverrides`，会把真请求吞成 `400 + 空体`，
   不解开就「全绿 = 全 skip」。已在 `smoke_app02_live_test.dart` 里
   `HttpOverrides.global = null`。
2. **「全部跳过」绝不算通过** —— 否则这个脚本会变成橡皮图章。
   下面用 `_parse_test_counts` 把跳过数抠出来，>0 直接判失败。
"""

from __future__ import annotations

import io
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
# 8812：避开 8810 正式口、8897 BE-03、8898 BE-04、8899 BE-02/BE-05
PORT = int(os.environ.get("SMOKE_APP02_PORT", "8812"))
USE_EXTERNAL = os.environ.get("SMOKE_APP02_EXTERNAL") == "1"

ADMIN_USER = "admin"
ADMIN_PASSWORD = "Smoke-App02-Pass"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: 真机联调至少要有这么多用例真跑（防止「改了测试名/文件没被收集」导致假绿）。
#: 当前实际有 17 条，留 2 条余量。
MIN_LIVE_TESTS = 15

#: 造数用的年级学期 —— 必须和 Dart 侧 `PUT /api/me/profile` 写的偏好一致，
#: 否则首页会拿到空列表，测试变成"验证空数据也能渲染"。
SEED_GRADE = 3
SEED_SEMESTER_LONG = "上学期"
SEED_SEMESTER_SHORT = "上"


# ---------------------------------------------------------------------------
# Flutter 运行环境
#
# 从 bash（MSYS/Git-Bash）里调 flutter 会崩：MSYS 环境不继承 Windows 的
# PATHEXT，flutter_tools 的 `where git` 返回 null，直接抛
# "Null check operator used on a null value"。
# 所以这里**显式补齐** —— 与 `scripts/flutter.sh` 保持同一套修复。
# ---------------------------------------------------------------------------
FLUTTER_ENV = {
    **os.environ,
    "PATHEXT": ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC",
    "PUB_HOSTED_URL": os.environ.get("PUB_HOSTED_URL", "https://pub.flutter-io.cn"),
    "FLUTTER_STORAGE_BASE_URL": os.environ.get(
        "FLUTTER_STORAGE_BASE_URL", "https://storage.flutter-io.cn"
    ),
    "ANDROID_HOME": os.environ.get(
        "ANDROID_HOME_OVERRIDE", "D:/2install/androidstudiotool/sdk"
    ),
    "ANDROID_SDK_ROOT": os.environ.get(
        "ANDROID_SDK_ROOT",
        os.environ.get("ANDROID_HOME_OVERRIDE", "D:/2install/androidstudiotool/sdk"),
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

    ⚠️ 别用 `connect_ex()`：服务刚被 terminate 的那一瞬间它可能仍返回 0
    （TIME_WAIT 窗口），于是收尾自检误报「端口仍被占用」，白折腾一轮。
    netstat 的 LISTENING 才是权威判据。
    """
    pid = _listener_pid(port)
    if pid is not None:
        return True
    with socket.socket() as s:
        return s.connect_ex((host, port)) == 0


def _kill_tree(proc: subprocess.Popen | None) -> None:
    """结束子进程并**确认它真的死了**（只 terminate 会留残留在端口上）。"""
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
    """等 `/api/ping` 可答（判据用**契约字段** `data["service"] == "bjsys"`）。"""
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
    """从 `flutter test` 输出里抠出（通过数, 跳过数）。"""
    passed = max((int(m) for m in re.findall(r"\+(\d+)", output)), default=0)
    skipped = max((int(m) for m in re.findall(r"~(\d+)", output)), default=0)
    return passed, skipped


# ---------------------------------------------------------------------------
# 造数（真 Excel 两步式导入）
# ---------------------------------------------------------------------------


def _words_xlsx() -> bytes:
    """12 个词条 —— 数量是刻意的：够跑「只练错题 1 个」的对比。"""
    import openpyxl

    vocabulary = [
        ("classroom", "教室"),
        ("apple", "苹果"),
        ("banana", "香蕉"),
        ("orange", "橙子"),
        ("grape", "葡萄"),
        ("pencil", "铅笔"),
        ("restaurant", "餐馆"),
        ("watermelon", "西瓜"),
        ("class", "班级"),
        ("dog", "狗"),
        ("cat", "猫"),
        ("book", "书"),
    ]
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "词条"
    sheet.append(
        ["学科", "年级", "学期", "单元名", "序号", "类型", "英文", "中文", "谐音", "音标", "词性", "例句", "例句中文", "要求拼写"]
    )
    for index, (en, cn) in enumerate(vocabulary, start=1):
        sheet.append(
            [
                "英语",
                SEED_GRADE,
                SEED_SEMESTER_LONG,
                "Unit 1 测试单元",
                index,
                "单词",
                en,
                cn,
                "",
                "",
                "n.",
                "",
                "",
                "1",
            ]
        )
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _texts_xlsx() -> bytes:
    """3 篇课文，其中 1 篇带「只背后段」的背诵范围（切片错位的回归用例）。"""
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "课文"
    sheet.append(["学科", "年级", "学期", "单元名", "课文标题", "作者", "段落正文", "背诵范围"])
    sheet.append(
        ["语文", SEED_GRADE, SEED_SEMESTER_SHORT, "第一单元", "春", "朱自清", "盼望着盼望着\n东风来了", ""]
    )
    sheet.append(
        ["语文", SEED_GRADE, SEED_SEMESTER_SHORT, "第一单元", "济南的冬天", "老舍", "对于一个在北平住惯的人", ""]
    )
    sheet.append(
        ["语文", SEED_GRADE, SEED_SEMESTER_SHORT, "第一单元", "背影", "朱自清", "我与父亲不相见\n那年冬天", "1"]
    )
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _seed(port: int) -> bool:
    """登录管理员 → 两步式导入英语词条 + 语文课文。"""
    import httpx

    base = f"http://{HOST}:{port}"
    with httpx.Client(base_url=base, timeout=30.0, trust_env=False) as c:
        body = c.post(
            "/api/auth/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "role": "admin"},
        ).json()
        if body.get("code") != 0:
            print(f"❌ 管理员登录失败：{body}", flush=True)
            return False
        admin = {"Authorization": f"Bearer {body['data']['access_token']}"}

        for label, path, filename, blob, extra in (
            (
                "英语词条",
                "/api/import/words",
                "words.xlsx",
                _words_xlsx(),
                {"create_missing_units": True, "on_duplicate": "overwrite"},
            ),
            ("语文课文", "/api/import/texts", "texts.xlsx", _texts_xlsx(), {}),
        ):
            preview = c.post(
                f"{path}/preview",
                files={"file": (filename, blob, XLSX_MIME)},
                headers=admin,
            ).json()
            if preview.get("code") != 0:
                print(f"❌ {label} preview 失败：{preview}", flush=True)
                return False
            token = preview["data"]["preview_token"]
            commit = c.post(
                f"{path}/commit",
                json={"preview_token": token, **extra},
                headers=admin,
            ).json()
            if commit.get("code") != 0:
                print(f"❌ {label} commit 失败：{commit}", flush=True)
                return False
            print(f"  造数 {label}：{preview['data']['summary']}", flush=True)
    return True


# ---------------------------------------------------------------------------
# 跑测试
# ---------------------------------------------------------------------------


def _run_flutter_test() -> tuple[int, str]:
    """跑真机联调用例：**边跑边打日志**，同时把输出留一份用于判定。"""
    lines: list[str] = []
    proc = subprocess.Popen(
        [
            str(FLUTTER), "test",
            "test/smoke_app02_live_test.dart",
            f"--dart-define=APP02_SMOKE_HOST={HOST}",
            f"--dart-define=APP02_SMOKE_PORT={PORT}",
        ],
        cwd=str(STUDENT_APP),
        env=FLUTTER_ENV,
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
            print(f"❌ SMOKE_APP02_EXTERNAL=1，但 {HOST}:{PORT} 上没有服务。", flush=True)
            return 2
        print(f"ℹ️  复用已运行的后端 {HOST}:{PORT}（**不重新造数**）", flush=True)
    else:
        if _port_in_use(PORT):
            pid = _listener_pid(PORT)
            print(
                f"❌ 端口 {PORT} 已被占用（PID={pid or '未知'}）。\n"
                f"   先结束它：taskkill /F /PID {pid or '<pid>'}\n"
                f"   或换端口：SMOKE_APP02_PORT=8813 python scripts/smoke_app02.py\n"
                f"   或复用现成服务：SMOKE_APP02_EXTERNAL=1 ...",
                flush=True,
            )
            return 2

        tmp = Path(tempfile.mkdtemp(prefix="smoke_app02_"))
        env = {
            **os.environ,
            "DB_PATH": str(tmp / "smoke.db"),
            "BACKUP_DIR": str(tmp / "backups"),
            "JWT_SECRET": "smoke-app02-secret-0123456789abcdef",
            "ADMIN_INIT_USERNAME": ADMIN_USER,
            "ADMIN_INIT_PASSWORD": ADMIN_PASSWORD,
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
            print(f"   子进程退出码：{proc.poll()}", flush=True)
            _kill_tree(proc)
            shutil.rmtree(tmp, ignore_errors=True)
            return 2
        print("✅ 后端就绪", flush=True)

        # ------------------------------------------------------------ 造数据
        print(f"\n▶ 造数（{SEED_GRADE} 年级 {SEED_SEMESTER_LONG}：英语 12 词条 + 语文 3 课文）\n", flush=True)
        try:
            if not _seed(PORT):
                _kill_tree(proc)
                shutil.rmtree(tmp, ignore_errors=True)
                return 2
        except Exception as exc:  # noqa: BLE001
            print(f"❌ 造数抛异常：{exc!r}", flush=True)
            _kill_tree(proc)
            shutil.rmtree(tmp, ignore_errors=True)
            return 2
        print("✅ 造数完成", flush=True)

    # ---------------------------------------------------------------- 跑测试
    try:
        print("\n▶ flutter test test/smoke_app02_live_test.dart\n", flush=True)
        code, output = _run_flutter_test()
        passed, skipped = _parse_test_counts(output)

        # —— 假绿拦截：真机联调层「全跳过」等于什么都没验，必须判失败 ——
        if skipped > 0:
            print(
                f"\n❌ 有 {skipped} 个真机联调用例被**跳过**（通过 {passed} 个）。\n"
                f"   后端已启动却跳过 = 客户端根本没连上，通常是被 HttpOverrides 拦了，\n"
                f"   或 APP02_SMOKE_HOST/PORT 与真实地址不一致。这一层全跳过不算通过。",
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
