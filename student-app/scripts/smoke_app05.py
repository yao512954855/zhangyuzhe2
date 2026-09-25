"""WP-APP-05 学生端 —— 真机烟测（真 uvicorn + 真 HTTP + 真 Flutter 客户端）。

与 `test/test_app05_*.dart`（假适配器组件测试）的分工：
- 组件测试跑得快，但所有 HTTP 都走**假适配器**，只能证明"客户端按自己的
  想象解析响应"；
- **这里只验「真后端」这件事**，且聚焦 APP-05 专属的两条链：
  ① 答错 → `/api/me/wrong-words` 真能查到这道题（错题本页的数据源）；
  ② `scope=wrong` 组卷出的题**全部来自**错题本（"只练错题"整条链）。
  这两条若只靠假适配器，永远发现不了"字段名对不上 / 后端静默退化"的问题。

跑法：
    cd student-app
    python scripts/smoke_app05.py

它会：
    ① 用临时库拉起一个真 uvicorn（默认 127.0.0.1:8893）
    ② 等 /api/ping 就绪
    ③ 管理员登录 → 两步式导入英语词条（3 年级上学期）
    ④ **造数（真 HTTP）**：注册学生 → 练一遍故意答错 2 题 → 制造错题
       → 验证 `/api/me/wrong-words` 真能查到 → 验证 `scope=wrong` 组卷出的题
         全部来自错题
    ⑤ 跑 `flutter test test/smoke_app05_live_test.dart`（复用上面造好的账号与错题）
    ⑥ 关服务、清理临时库

后端已在本机跑着时用 `SMOKE_APP05_EXTERNAL=1` 复用现成的（不启停服务、不造数）。

⚠️ **踩过的坑（照抄 APP-02，别再踩）**
1. `flutter_test` 默认装全局 `HttpOverrides`，会把真请求吞成 `400 + 空体`，
   不解开就「全绿 = 全 skip」。已在 `smoke_app05_live_test.dart` 里
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
# 8893：避开 8810 正式口、8812 APP-02、8897 BE-03、8898 BE-04、8899 BE-02/BE-05
PORT = int(os.environ.get("SMOKE_APP05_PORT", "8893"))
USE_EXTERNAL = os.environ.get("SMOKE_APP05_EXTERNAL") == "1"

ADMIN_USER = "admin"
ADMIN_PASSWORD = "Smoke-App05-Pass"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: 真机联调至少要有这么多用例真跑（防止「改了测试名/文件没被收集」导致假绿）。
#: 当前实际有 5 条，留 1 条余量。
MIN_LIVE_TESTS = 7

#: 造数用的年级学期 —— 必须和 Dart 侧 profile 偏好一致。
SEED_GRADE = 3
SEED_SEMESTER_LONG = "上学期"
SEED_SEMESTER_SHORT = "上"
UNIT_NAME = "Unit 1 APP05 测试单元"

#: 烟测固定账号（Python 造好、Dart 登录，二者通过 --dart-define 对齐）。
SMOKE_USER = "smoke05"
SMOKE_PASSWORD = "smoke05pass"
SMOKE_REAL_NAME = "烟测生"
#: 故意答错多少个词条（制造错题本数据）。
WRONG_N = 2


# ---------------------------------------------------------------------------
# Flutter 运行环境（与 scripts/flutter.sh 同一套修复，见 APP-02 注释）
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
    pid = _listener_pid(port)
    if pid is not None:
        return True
    with socket.socket() as s:
        return s.connect_ex((host, port)) == 0


def _kill_tree(proc: subprocess.Popen | None) -> None:
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
# 造数（真 Excel 两步式导入 + 真练习造错题）
# ---------------------------------------------------------------------------


def _words_xlsx() -> bytes:
    """6 个英语词条（够制造 2 道错题 + 验证「只练错题」对比）。"""
    import openpyxl

    vocabulary = [
        ("classroom", "教室"),
        ("apple", "苹果"),
        ("banana", "香蕉"),
        ("orange", "橙子"),
        ("pencil", "铅笔"),
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
                UNIT_NAME,
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


def _seed(port: int) -> bool:
    """管理员登录 → 导入英语词条 → 注册学生 → 练错 2 题 → 验证错题本链路。"""
    import httpx

    base = f"http://{HOST}:{port}"
    with httpx.Client(base_url=base, timeout=30.0, trust_env=False) as c:
        # ---- 管理员导入英语词条 ----
        body = c.post(
            "/api/auth/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "role": "admin"},
        ).json()
        if body.get("code") != 0:
            print(f"❌ 管理员登录失败：{body}", flush=True)
            return False
        admin = {"Authorization": f"Bearer {body['data']['access_token']}"}

        preview = c.post(
            "/api/import/words/preview",
            files={"file": ("words.xlsx", _words_xlsx(), XLSX_MIME)},
            headers=admin,
        ).json()
        if preview.get("code") != 0:
            print(f"❌ 词条 preview 失败：{preview}", flush=True)
            return False
        commit = c.post(
            "/api/import/words/commit",
            json={"preview_token": preview["data"]["preview_token"], "create_missing_units": True, "on_duplicate": "overwrite"},
            headers=admin,
        ).json()
        if commit.get("code") != 0:
            print(f"❌ 词条 commit 失败：{commit}", flush=True)
            return False
        print(f"  造数 英语词条：{commit['data']}", flush=True)

        # ---- 注册学生 ----
        reg = c.post(
            "/api/auth/register",
            json={"username": SMOKE_USER, "real_name": SMOKE_REAL_NAME, "password": SMOKE_PASSWORD},
        ).json()
        if reg.get("code") != 0:
            print(f"❌ 学生注册失败：{reg}", flush=True)
            return False
        student = {"Authorization": f"Bearer {reg['data']['access_token']}"}

        # 设年级学期偏好（wrong-words 不传 unit_id 时按偏好过滤）
        # ⚠️ 契约 §8.2 是 **`PUT`** `/api/me/profile`（见 `home_repository.dart:152`）。
        #    写成 `post` 会拿到 405，而项目的异常包装会把它表现成业务码 **10001**
        #    「填写的内容不正确」—— 看起来像 body 字段写错了，实际是 HTTP 方法不对。
        #    （QA-01 集成时修正：本脚本上一轮跑到一半被中断，这处一直没暴露。）
        prof = c.put(
            "/api/me/profile",
            json={"preferred_grade": SEED_GRADE, "preferred_semester": "first"},
            headers=student,
        ).json()
        if prof.get("code") != 0:
            print(f"❌ 设年级偏好失败：{prof}", flush=True)
            return False

        units = c.get("/api/me/units", headers=student).json()["data"]["items"]
        unit = next((u for u in units if u["subject"] == "english" and u["name"] == UNIT_NAME), None)
        if unit is None:
            print(f"❌ 找不到造数的英语单元 {UNIT_NAME}：{units}", flush=True)
            return False
        unit_id = unit["unit_id"]

        # ---- 练一遍，故意答错前 WRONG_N 题 ----
        sess = c.post(
            "/api/sessions",
            json={
                "unit_id": unit_id,
                "content_type": "english",
                "strategy": {
                    "scope": "unit",
                    "scope_detail": None,
                    "count": 0,
                    "count_mode": "all",
                    "order": "sequential",
                    "mode": "cn2en",
                    "time_limit_ms": 0,
                },
            },
            headers=student,
        ).json()
        if sess.get("code") != 0:
            print(f"❌ 组卷失败：{sess}", flush=True)
            return False
        session_id = sess["data"]["session_id"]
        items = sess["data"]["items"]

        wrong_ids: list[int] = []
        for it in items[:WRONG_N]:
            ans = c.post(
                f"/api/sessions/{session_id}/answers",
                json={"word_id": it["word_id"], "mode": "cn2en", "step": 1, "asr_text": "zzz_wrong_xyz"},
                headers=student,
            ).json()
            if ans.get("code") != 0:
                print(f"❌ 提交答题失败：{ans}", flush=True)
                return False
            if not ans["data"]["is_correct"]:
                wrong_ids.append(it["word_id"])
        fin = c.post(
            f"/api/sessions/{session_id}/finish",
            json={"reason": "completed"},
            headers=student,
        ).json()
        if fin.get("code") != 0:
            print(f"❌ 结算失败：{fin}", flush=True)
            return False

        # ---- 验证 ①：错题本真能查到 ----
        ww = c.get("/api/me/wrong-words", headers=student).json()
        if ww.get("code") != 0:
            print(f"❌ 查错题本失败：{ww}", flush=True)
            return False
        ww_items = ww["data"]["items"]
        ww_ids = {w["word_id"] for w in ww_items}
        if not ww_ids:
            print("❌ 答错后错题本为空，制造错题失败。", flush=True)
            return False
        if not set(wrong_ids).issubset(ww_ids):
            print(f"❌ 答错的 {wrong_ids} 没全进错题本 {ww_ids}。", flush=True)
            return False
        print(f"  错题本验证：答错 {len(wrong_ids)} 题，错题本收录 {len(ww_ids)} 题：{sorted(ww_ids)}", flush=True)

        # ---- 验证 ②：scope=wrong 组卷出的题全部来自错题本 ----
        wrong_sess = c.post(
            "/api/sessions",
            json={
                "unit_id": unit_id,
                "content_type": "english",
                "strategy": {
                    "scope": "wrong",
                    "scope_detail": None,
                    "count": 0,
                    "count_mode": "all",
                    "order": "sequential",
                    "mode": "cn2en",
                    "time_limit_ms": 0,
                },
            },
            headers=student,
        ).json()
        if wrong_sess.get("code") != 0:
            print(f"❌ 只练错题组卷失败：{wrong_sess}", flush=True)
            return False
        w_items = wrong_sess["data"]["items"]
        if not w_items:
            print("❌ scope=wrong 没组出任何题（错题本非空却组不出，说明 scope=wrong 被静默退化）。", flush=True)
            return False
        out_ids = {i["word_id"] for i in w_items}
        if not out_ids.issubset(ww_ids):
            print(f"❌ 只练错题组出了非错题：{out_ids} ⊄ {ww_ids}", flush=True)
            return False
        print(f"  只练错题验证：组出 {len(out_ids)} 题，全部来自错题本 ✅", flush=True)

    return True


# ---------------------------------------------------------------------------
# 跑测试
# ---------------------------------------------------------------------------


def _run_flutter_test() -> tuple[int, str]:
    lines: list[str] = []
    proc = subprocess.Popen(
        [
            str(FLUTTER), "test",
            "test/smoke_app05_live_test.dart",
            f"--dart-define=APP05_SMOKE_HOST={HOST}",
            f"--dart-define=APP05_SMOKE_PORT={PORT}",
            f"--dart-define=APP05_SMOKE_USER={SMOKE_USER}",
            f"--dart-define=APP05_SMOKE_PASSWORD={SMOKE_PASSWORD}",
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
            print(f"❌ SMOKE_APP05_EXTERNAL=1，但 {HOST}:{PORT} 上没有服务。", flush=True)
            return 2
        print(f"ℹ️  复用已运行的后端 {HOST}:{PORT}（**不重新造数**）", flush=True)
    else:
        if _port_in_use(PORT):
            pid = _listener_pid(PORT)
            print(
                f"❌ 端口 {PORT} 已被占用（PID={pid or '未知'}）。\n"
                f"   先结束它：taskkill /F /PID {pid or '<pid>'}\n"
                f"   或换端口：SMOKE_APP05_PORT=8894 python scripts/smoke_app05.py\n"
                f"   或复用现成服务：SMOKE_APP05_EXTERNAL=1 ...",
                flush=True,
            )
            return 2

        tmp = Path(tempfile.mkdtemp(prefix="smoke_app05_"))
        env = {
            **os.environ,
            "DB_PATH": str(tmp / "smoke.db"),
            "BACKUP_DIR": str(tmp / "backups"),
            "JWT_SECRET": "smoke-app05-secret-0123456789abcdef",
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

        print(f"\n▶ 造数（{SEED_GRADE} 年级 {SEED_SEMESTER_LONG}：英语 6 词条 + 制造 {WRONG_N} 道错题）\n", flush=True)
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
        print("\n▶ flutter test test/smoke_app05_live_test.dart\n", flush=True)
        code, output = _run_flutter_test()
        passed, skipped = _parse_test_counts(output)

        # —— 假绿拦截：真机联调层「全跳过」等于什么都没验，必须判失败 ——
        if skipped > 0:
            print(
                f"\n❌ 有 {skipped} 个真机联调用例被**跳过**（通过 {passed} 个）。\n"
                f"   后端已启动却跳过 = 客户端根本没连上，通常是被 HttpOverrides 拦了，\n"
                f"   或 APP05_SMOKE_HOST/PORT 与真实地址不一致。这一层全跳过不算通过。",
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
