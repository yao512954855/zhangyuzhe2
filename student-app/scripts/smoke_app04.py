"""WP-APP-04 学生端 —— 真机烟测（真 uvicorn + 真 HTTP + 真 WebSocket + 真 Flutter 客户端）。

与 `test/smoke_app04_live_test.dart` 的分工：
- 组件测试（`test/test_app04_recite_test.dart` / `test/test_app04_teardown_cancel_test.dart`）
  跑得快，但所有 HTTP / WS 都走**假实现**，只能证明"客户端按自己的想象解析响应"；
- **这里只验「真后端 + 真语音链路」这件事**：

  | 验的东西 | 假实现为什么测不出来 |
  |---|---|
  | `POST /api/recitations` 的 F1 响应形状（`char_count_per_paragraph`、**不含正文/拼音**） | 假仓库按自己的想象造字段名，拼错了也"通过" |
  | `POST /api/recitations/{id}/feed` 的 `newly_matched` 是**位置数组**而非数字 | 客户端 `_toInt()` 对数组恒得 0；假适配器无从发现（真机上已修，见 live test 注释） |
  | 提示三档（1=空框 / 2=下一个字 / 3=下一个字+后续 4 字）真的给得出 | 档位语义在服务端引擎里，不在客户端 |
  | `restart` 真的把 `restart_count` 推上去 | 同上 |
  | `settle` **服务端重算**：客户端谎报 `matched_chars=9999` 必须无效（F5 防刷分） | 这是安全线，只能用真后端验 |
  | `POST /api/recitations/{id}/events` 真的接受批量补报 | — |
  | **WS `/ws/asr` 的 `task_type=recite` 回路**：partials 真的推进服务端 `pos` | 组件测试里的假 WS 只回固定帧 |

跑法（**用后端 venv 的 python**，脚本自己要 `httpx` + `openpyxl`）：

    cd student-app
    ../backend/.venv/Scripts/python.exe scripts/smoke_app04.py

它会：
    ① 用临时库 + **临时 `asr.yaml`（provider=mock）** 拉起一个真 uvicorn（默认 127.0.0.1:8892）
    ② 等 `/api/ping` 就绪
    ③ 从 Excel 造数据：语文课文 3 篇（3 年级上学期）
    ④ 跑 `flutter test test/smoke_app04_live_test.dart`
    ⑤ 关服务、清理临时库

后端已在本机跑着时用 `SMOKE_APP04_EXTERNAL=1` 复用现成的（不启停服务、不造数）。

⚠️ **踩过的三个坑（照抄 APP-01/02/03，别再踩）**
1. `flutter_test` 默认装全局 `HttpOverrides`，会把真请求吞成 `400 + 空体`，
   不解开就「全绿 = 全 skip」。已在 `smoke_app04_live_test.dart` 里
   `HttpOverrides.global = null`。
2. **「全部跳过」绝不算通过** —— 否则这个脚本会变成橡皮图章。
   下面用 `_parse_test_counts` 把跳过数抠出来，>0 直接判失败。
3. **`ASR_CONFIG_PATH` 必须指向 mock 配置**：仓库里的 `backend/asr.yaml` 是
   `provider: aliyun` 且 `api_key` 为空（`asr_ready=false`）—— 不覆盖的话
   WS 那条用例必定失败，而且失败原因会被误读成"客户端 WS 写错了"。
   本脚本写一份临时 `asr_mock.yaml` 并 `by_task.recite.final_text` 设成**课文里的字**，
   这样 WS 的 partials 才会真的推进背诵引擎（否则断言只能退化成"连接成功"）。
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

import httpx

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
# 8892：避开 8810 正式口、8812 APP-02、8891 APP-03、8893 APP-05、8895-8899 BE 系列
PORT = int(os.environ.get("SMOKE_APP04_PORT", "8892"))
USE_EXTERNAL = os.environ.get("SMOKE_APP04_EXTERNAL") == "1"

ADMIN_USER = "admin"
ADMIN_PASSWORD = "Smoke-App04-Pass"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: 真机联调至少要有这么多用例真跑（防止「改了测试名/文件没被收集」导致假绿）。
#: 当前实际有 12 条，留 3 条余量。
MIN_LIVE_TESTS = 9

#: 造数用的年级学期 —— 必须和 Dart 侧 `PUT /api/me/profile` 写的偏好一致。
SEED_GRADE = 3
SEED_SEMESTER_LONG = "上"
SEED_SEMESTER_SHORT = "first"

#: mock ASR 在 `task_type=recite` 下要"听到"的话。
#: 必须取自下面课文正文，否则 partials 推进不了引擎，WS 用例只剩"连接成功"这种弱断言。
MOCK_RECITE_FINAL = "床前明月光"


# ---------------------------------------------------------------------------
# Flutter 运行环境（与 `scripts/smoke_app02/03.py` 同一套 PATHEXT 修复）
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
# 临时 ASR 配置：强制 mock
# ---------------------------------------------------------------------------
ASR_MOCK_YAML = """\
# 由 student-app/scripts/smoke_app04.py 在临时目录生成 —— 只为让 WS recite 链路
# 在没有云账号的情况下也能真跑。**不要**把它拷回 backend/。
provider: mock
backup_provider: ""

lang_map:
  word: "en"
  spell: "zh"
  zh_answer: "zh"
  recite: "zh"

hotwords:
  enabled: false
  sources: []

# 离线烟测关掉仲裁：仲裁要云 key，且 recite 的 match_type 不在触发集里
arbitrator:
  enabled: false
  provider: "aliyun"
  model: "qwen3.5-omni-plus"
  api_key: ""
  buffer_ms: 15000
  trigger: [empty, fuzzy, appeal]

limits:
  connect_timeout_ms: 3000
  chunk_ms: 150
  max_session_seconds: 300
  max_connections: 60
  queue_size: 120
  queue_wait_ms: 5000
  heartbeat_timeout_s: 60
  failover_threshold: 3

mock:
  behavior: normal
  final_text: "classroom"
  partial_every: 1
  confidence: 0.95
  by_task:
    # 语文背诵：Mock 要"听到"课文里的字，服务端的 pos 才会真的往前走。
    recite:
      final_text: "%(recite_final)s"
""" % {"recite_final": MOCK_RECITE_FINAL}


# ---------------------------------------------------------------------------
# 造数（真 Excel 两步式导入，造语文课文）
# ---------------------------------------------------------------------------

def _texts_xlsx() -> bytes:
    """三篇课文（都落在 `SEED_GRADE` / `SEED_SEMESTER_LONG` 上）：

    1. 《静夜思》1 段全背（20 字）—— 主用例；mock ASR 念的就是它的头 5 个字
    2. 《春晓》1 段全背（10 字）—— 噪声 / 短文本用例
    3. 《咏鹅》3 段**只背后 2 段**（背诵范围 `1,2`）—— 「范围不含首段」的字符切片回归用例
       （BE-05 修掉过的真实缺陷：按全段累加会整体错位）
    """
    import openpyxl

    rows = [
        ["语文", SEED_GRADE, SEED_SEMESTER_LONG, "第一单元", "静夜思", "李白",
         "床前明月光疑是地上霜举头望明月低头思故乡", ""],
        ["语文", SEED_GRADE, SEED_SEMESTER_LONG, "第一单元", "春晓", "孟浩然",
         "春眠不觉晓处处闻啼鸟", ""],
        ["语文", SEED_GRADE, SEED_SEMESTER_LONG, "第一单元", "咏鹅", "骆宾王",
         "鹅鹅鹅\n曲项向天歌\n白毛浮绿水", "1,2"],
    ]
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "语文课文"
    sheet.append(["学科", "年级", "学期", "单元名", "课文标题", "作者", "段落正文", "背诵范围"])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _seed(port: int) -> bool:
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

        files = {"file": ("texts.xlsx", _texts_xlsx(), XLSX_MIME)}
        preview = c.post("/api/import/texts/preview", files=files, headers=admin).json()
        if preview.get("code") != 0:
            print(f"❌ 课文 preview 失败：{preview}", flush=True)
            return False
        summary = preview["data"]["summary"]
        if int(summary.get("valid") or 0) != 3:
            print(
                f"❌ 课文 preview 只认出 {summary.get('valid')} 篇有效（期望 3）：{summary}",
                flush=True,
            )
            return False
        print(f"  造数 课文：{summary}", flush=True)

        # `texts` 留空 = 全部按 preview 的默认范围入库（即 Excel 的「背诵范围」列）
        commit = c.post(
            "/api/import/texts/commit",
            json={"preview_token": preview["data"]["preview_token"]},
            headers=admin,
        ).json()
        if commit.get("code") != 0:
            print(f"❌ 课文 commit 失败：{commit}", flush=True)
            return False
        if int(commit["data"].get("created") or 0) != 3:
            print(f"❌ 课文入库数量不对：{commit['data']}", flush=True)
            return False
        print(f"  入库 课文：{commit['data']}", flush=True)
    return True


# ---------------------------------------------------------------------------
# 跑测试
# ---------------------------------------------------------------------------

def _run_flutter_test() -> tuple[int, str]:
    lines: list[str] = []
    proc = subprocess.Popen(
        [
            str(FLUTTER), "test",
            "test/smoke_app04_live_test.dart",
            f"--dart-define=APP04_SMOKE_HOST={HOST}",
            f"--dart-define=APP04_SMOKE_PORT={PORT}",
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

    if USE_EXTERNAL:
        if not _port_in_use(PORT):
            print(f"❌ SMOKE_APP04_EXTERNAL=1，但 {HOST}:{PORT} 上没有服务。", flush=True)
            return 2
        print(f"ℹ️  复用已运行的后端 {HOST}:{PORT}（**不重新造数**）", flush=True)
    else:
        if _port_in_use(PORT):
            pid = _listener_pid(PORT)
            print(
                f"❌ 端口 {PORT} 已被占用（PID={pid or '未知'}）。\n"
                f"   先结束它：taskkill /F /PID {pid or '<pid>'}\n"
                f"   或换端口：SMOKE_APP04_PORT=8894 python scripts/smoke_app04.py\n"
                f"   或复用现成服务：SMOKE_APP04_EXTERNAL=1 ...",
                flush=True,
            )
            return 2

        tmp = Path(tempfile.mkdtemp(prefix="smoke_app04_"))
        asr_yaml = tmp / "asr_mock.yaml"
        asr_yaml.write_text(ASR_MOCK_YAML, encoding="utf-8")

        env = {
            **os.environ,
            "DB_PATH": str(tmp / "smoke.db"),
            "BACKUP_DIR": str(tmp / "backups"),
            "JWT_SECRET": "smoke-app04-secret-0123456789abcdef",
            "ADMIN_INIT_USERNAME": ADMIN_USER,
            "ADMIN_INIT_PASSWORD": ADMIN_PASSWORD,
            "LOG_LEVEL": "WARNING",
            "NO_PROXY": "127.0.0.1,localhost",
            "PYTHONIOENCODING": "utf-8",
            # ⚠️ 仓库里的 asr.yaml 是 aliyun + 空 key。不覆盖 → WS 用例必挂。
            "ASR_CONFIG_PATH": str(asr_yaml),
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
        print(f"⏳ 启动后端 {HOST}:{PORT}（ASR=mock，配置 {asr_yaml.name}）…", flush=True)
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

        print(f"\n▶ 造数（{SEED_GRADE} 年级 {SEED_SEMESTER_LONG}学期：语文课文 3 篇）\n", flush=True)
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

    try:
        print("\n▶ flutter test test/smoke_app04_live_test.dart\n", flush=True)
        code, output = _run_flutter_test()
        passed, skipped = _parse_test_counts(output)

        if skipped > 0:
            print(
                f"\n❌ 有 {skipped} 个真机联调用例被**跳过**（通过 {passed} 个）。\n"
                f"   后端已启动却跳过 = 客户端根本没连上，通常是被 HttpOverrides 拦了，\n"
                f"   或 APP04_SMOKE_HOST/PORT 与真实地址不一致。这一层全跳过不算通过。",
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

    if not USE_EXTERNAL and _port_in_use(PORT):
        print(f"⚠️  {PORT} 仍被占用，可能有残留进程（PID={_listener_pid(PORT)}）。", flush=True)

    print("\n" + ("✅ 烟测通过" if code == 0 else "❌ 烟测失败"), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
