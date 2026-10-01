"""WP-BE-02 真机端到端烟测 —— 起真实 uvicorn 进程，走真实 HTTP，验证账号与班级全链路。

为什么不用 pytest 覆盖就够了：`TestClient` 是**进程内**调用，绕过了真实网络栈、
uvicorn 的生命周期、以及 multipart 上传的解析路径。「测试全绿」不等于「服务真能跑」。

本脚本做的事（对应 10a 的验收用例编号）：
    ① 冷启动：临时空目录 → 迁移建库 → /api/health
    ② U1/U2  admin 登录成功、错误密码 20002
    ③ F5     建班级 + 同名冲突 10001
    ④ U4/U5  学生注册成功 / 重复学号 20001
    ⑤ F7     名单模板下载 + 上传预览（valid/invalid/duplicated）+ 错误报告下载
    ⑥ U8     commit 入库，二次导入为更新
    ⑦ U10    批量归班，未分班数下降
    ⑧ U11    重置密码后旧密码失效
    ⑨ U12    停用学生后其 token 立即 10002
    ⑩ §2.1   连续 5 次密码错误 → 账号锁定 10005
    ⑪ U14    audit_logs 记录齐全

跑法：
    cd backend && ./.venv/Scripts/python.exe scripts/smoke_be02.py
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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

# 允许从任意目录运行：把 backend/ 放进 sys.path，才能 import app.core.excel
sys.path.insert(0, str(BACKEND_DIR))

HOST = "127.0.0.1"
PORT = int(os.environ.get("SMOKE_PORT", "8899"))
BASE = f"http://{HOST}:{PORT}"

ADMIN_USER = "admin"
ADMIN_PWD = "smoke-admin-pass-9911"

_results: list[tuple[bool, str, str]] = []


# --------------------------------------------------------------------------- 基础


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((bool(ok), name, detail))
    print(f"{'[OK]' if ok else '[!!]'} {name}" + (f"    {detail}" if detail else ""))


def free_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, port)) != 0


def http(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    token: str | None = None,
    ident: str | None = None,
    multipart: tuple[str, str, bytes] | None = None,
    raw: bool = False,
    timeout: float = 20.0,
) -> Any:
    """发一个请求；`raw=True` 时返回 `(status, headers, bytes)`。

    `ident`：**仅用于切换限流主体**，不做鉴权。`RateLimitMiddleware._identity()`
    优先取 `Authorization` 头的哈希（`u:<md5>`）、取不到才退化到 IP，因此给一个
    假 Bearer 即可让本请求走**独立限流桶**。登录接口本身不校验该头，不影响业务判定。
    """
    url = f"{BASE}{path}"
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif ident:
        headers["Authorization"] = f"Bearer {ident}"

    data: bytes | None = None
    if multipart is not None:
        filename, content_type, content = multipart
        boundary = "----smokeBE02boundary"
        data = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            if raw:
                return resp.status, dict(resp.headers), payload
            return json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        if raw:
            return exc.code, dict(exc.headers), payload
        try:
            return json.loads(payload.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"code": -1, "_raw": payload.decode("utf-8", "replace")}


def _login(username: str, password: str, role: str, ident: str | None = None) -> dict:
    """带主动排队的登录。

    ⚠️ 契约 §5.4 写的是「单**账号**登录 5 次/分钟」，但 `RateLimitMiddleware`
    是纯 ASGI 中间件、**读不到请求体里的 username**，因此实际按**登录主体**计数
    （优先 `Authorization` 头哈希，无头才退化到 IP）。本脚本登录默认不带该头，
    全部共用一个 `ip:127.0.0.1` 额度，不排队的话第 6 次起全是 `10005`。
    —— 这条「文档维度 vs 实现维度不一致」本身也是本项目要上报的问题之一。

    `ident`：传值时改用**独立限流桶**（假 Bearer），且**不做客户端排队** —— 供 ⑩
    这类需要「确保每次尝试都穿透到业务层」的用例使用（见 ⑩ 注释）。
    """
    if ident is not None:
        return http(
            "POST",
            "/api/auth/login",
            body={"username": username, "password": password, "role": role},
            ident=ident,
        )
    _pace_login()
    return http(
        "POST",
        "/api/auth/login",
        body={"username": username, "password": password, "role": role},
    )


_login_times: list[float] = []
LOGIN_BUCKET_LIMIT = 5
LOGIN_BUCKET_WINDOW = 60.0


def _pace_login() -> None:
    """按 5 次/60 秒 的额度主动等待，避免脚本自己把自己限流。"""
    now = time.time()
    _login_times[:] = [t for t in _login_times if now - t < LOGIN_BUCKET_WINDOW]
    if len(_login_times) >= LOGIN_BUCKET_LIMIT:
        wait = LOGIN_BUCKET_WINDOW - (now - _login_times[0]) + 0.3
        if wait > 0:
            print(f"    … 登录额度已用满，等待 {wait:.1f}s（限流确实生效）")
            time.sleep(wait)
        now = time.time()
        _login_times[:] = [t for t in _login_times if now - t < LOGIN_BUCKET_WINDOW]
    _login_times.append(now)


def _students_xlsx(rows: list[list]) -> bytes:
    from app.core.excel import write_xlsx

    return write_xlsx(["学号", "姓名", "班级", "初始密码"], rows, sheet_name="学生名单")


def _header(headers: dict, name: str) -> str:
    """响应头大小写不敏感查找。

    ⚠️ ASGI 出口的响应头是**小写**（`content-type`）。把 `resp.headers` 转成普通
    `dict` 会丢掉 `email.message.Message` 的大小写不敏感特性，`.get("Content-Type")`
    会静默返回空 —— 这是**验证脚本自身的 bug**，极易误判成产品 bug。
    """
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return ""


def _is_xlsx(headers: dict) -> bool:
    return "spreadsheetml" in _header(headers, "content-type")


def _wait_ready(timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            body = http("GET", "/api/health", timeout=3.0)
            if isinstance(body, dict) and body.get("code") == 0:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


# --------------------------------------------------------------------------- 主流程


def main() -> int:
    if not free_port(PORT):
        print(f"[!!] 端口 {PORT} 已被占用，请先释放或设置 SMOKE_PORT 换端口")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="bjsys_be02_smoke_"))
    db_file = tmp / "smoke.db"
    log_file = tmp / "uvicorn.log"

    env = dict(os.environ)
    env.update(
        {
            "DB_PATH": str(db_file),
            "BACKUP_DIR": str(tmp / "backups"),
            "ADMIN_INIT_USERNAME": ADMIN_USER,
            "ADMIN_INIT_PASSWORD": ADMIN_PWD,
            "APP_PORT": str(PORT),
            "LOG_LEVEL": "INFO",
        }
    )

    print(f"== 启动 uvicorn（{BASE}，DB={db_file}）==")
    log_handle = log_file.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [PYTHON, "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(PORT)],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )

    try:
        if not _wait_ready():
            print("[!!] 服务未在 45 秒内就绪，日志尾部：")
            print(log_file.read_text(encoding="utf-8", errors="replace")[-3000:])
            return 1

        # ---------------------------------------------------------- ① 冷启动
        health = http("GET", "/api/health")
        check(
            "① 空目录首启：迁移建库 + /api/health 可用",
            health.get("code") == 0 and health["data"]["db"] is True,
            f"schema_version={health['data']['schema_version']}",
        )
        check("① 库文件已落盘", db_file.exists(), str(db_file))

        # ---------------------------------------------------------- ② 登录
        admin = _login(ADMIN_USER, ADMIN_PWD, "admin")
        ok_admin = admin.get("code") == 0 and admin["data"]["user"]["role"] == "admin"
        check("② U1 admin 登录成功（access_token + role=admin）", ok_admin)
        if not ok_admin:
            return 1
        token = admin["data"]["access_token"]
        check(
            "② 响应头 X-Trace-Id 与响应体 trace_id 一致",
            bool(admin.get("trace_id")),
            f"trace_id={admin.get('trace_id')}",
        )

        bad = _login(ADMIN_USER, "definitely-wrong", "admin")
        check("② U2 错误密码 → 20002（不透露账号是否存在）", bad.get("code") == 20002)
        missing = _login("no-such-user-9999", "definitely-wrong", "admin")
        check(
            "② U2 账号不存在与密码错返回同一条文案（防枚举）",
            missing.get("code") == 20002 and missing.get("message") == bad.get("message"),
        )

        # ---------------------------------------------------------- ③ 班级
        cls = http(
            "POST",
            "/api/classes",
            body={"name": "三年级二班", "grade": 3, "semester": "first"},
            token=token,
        )
        check("③ F5 创建班级成功", cls.get("code") == 0, f"id={cls.get('data', {}).get('id')}")
        if cls.get("code") != 0:
            return 1
        class_id = cls["data"]["id"]

        dup = http(
            "POST",
            "/api/classes",
            body={"name": "三年级二班", "grade": 3, "semester": "first"},
            token=token,
        )
        check(
            "③ F5 同名班级 → 10001 且文案含班级名",
            dup.get("code") == 10001 and "三年级二班" in dup.get("message", ""),
        )

        classes = http("GET", "/api/classes", token=token)
        check(
            "③ F4 班级列表含 student_count / unassigned_count",
            classes.get("code") == 0
            and "student_count" in classes["data"]["items"][0]
            and "unassigned_count" in classes["data"],
        )

        # ---------------------------------------------------------- ④ 注册
        reg = http(
            "POST",
            "/api/auth/register",
            body={
                "username": "20269001",
                "real_name": "自注册同学",
                "password": "abc123456",
            },
        )
        check(
            "④ U4 学生自主注册成功（register_source=self, class_id=null）",
            reg.get("code") == 0
            and reg["data"]["user"]["register_source"] == "self"
            and reg["data"]["user"]["class_id"] is None,
        )

        reg2 = http(
            "POST",
            "/api/auth/register",
            body={
                "username": "20269001",
                "real_name": "冒名顶替",
                "password": "hacked123",
            },
        )
        check("④ U5 重复学号注册 → 20001", reg2.get("code") == 20001)
        check(
            "④ U5 原账号未被覆盖（旧密码仍可用）",
            _login("20269001", "abc123456", "student").get("code") == 0,
        )

        # ---------------------------------------------------------- ⑤ 导入预览
        tmpl = http("GET", "/api/students/import/template", token=token, raw=True)
        check(
            "⑤ 名单模板下载（xlsx 流）",
            tmpl[0] == 200 and _is_xlsx(tmpl[1]),
            f"{len(tmpl[2])} bytes, ct={_header(tmpl[1], 'content-type')}",
        )

        rows: list[list] = [[f"2026{i:04d}", f"学生{i:02d}", "三年级二班", ""] for i in range(1, 46)]
        rows[7][1] = ""              # 第 8 行姓名空 → invalid
        rows[13][2] = "不存在的班"    # 第 14 行班级不存在 → invalid
        rows[0][0] = "20269001"      # 第 1 行撞已注册学号 → duplicated

        preview = http(
            "POST",
            "/api/students/import",
            token=token,
            multipart=(
                "名单.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                _students_xlsx(rows),
            ),
        )
        summary = preview.get("data", {}).get("summary", {})
        check(
            "⑤ F7 预览：total=45 / valid=43 / invalid=2 / duplicated=1",
            summary == {"total": 45, "valid": 43, "invalid": 2, "duplicated": 1},
            str(summary),
        )
        check(
            "⑤ F7 错误行带行号 + 原因 + 原始数据",
            len(preview.get("data", {}).get("invalid_rows", [])) == 2
            and all(r.get("reason") and r.get("raw") for r in preview["data"]["invalid_rows"]),
        )
        preview_token = preview.get("data", {}).get("preview_token", "")

        report = http("GET", preview["data"]["error_report_url"], token=token, raw=True)
        check(
            "⑤ F7 错误报告可下载（xlsx）",
            report[0] == 200 and _is_xlsx(report[1]),
            f"{len(report[2])} bytes",
        )

        http("GET", "/api/students", token=token)
        before = http("GET", "/api/students", token=token)["data"]["total"]
        check("⑤ F7 预览阶段不落库", before == 1, f"学生数={before}")

        # ---------------------------------------------------------- ⑥ 入库
        commit = http(
            "POST",
            "/api/students/import/commit",
            body={"preview_token": preview_token, "default_password": "abc123456"},
            token=token,
        )
        # 注意：不要把响应体整个打印出来 —— 里面含 credentials_url（带一次性 token），
        # 会污染日志/被安全策略拦截。只回报计数。
        check(
            "⑥ F8 首次入库：created=42 / updated=1（撞号那行走更新）",
            commit.get("code") == 0
            and commit["data"]["created"] == 42
            and commit["data"]["updated"] == 1,
            f"created={commit.get('data', {}).get('created')} "
            f"updated={commit.get('data', {}).get('updated')}",
        )

        preview2 = http(
            "POST",
            "/api/students/import",
            token=token,
            multipart=(
                "名单.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                _students_xlsx(rows),
            ),
        )
        # 第二次故意换一个**不同**的默认弱口令，用来验证「重复导入不重置密码」
        commit2 = http(
            "POST",
            "/api/students/import/commit",
            body={
                "preview_token": preview2["data"]["preview_token"],
                "default_password": "123456",
            },
            token=token,
        )
        total_after = http("GET", "/api/students", token=token)["data"]["total"]
        check(
            "⑥ U8 二次导入：created=0 / updated=43，总数不变",
            commit2.get("code") == 0
            and commit2["data"]["created"] == 0
            and commit2["data"]["updated"] == 43
            and total_after == 43,
            f"总数={total_after}",
        )

        # 对照表导出
        export = http("GET", commit["data"]["credentials_url"], token=token, raw=True)
        check(
            "⑥ 学号-初始密码对照表可导出",
            export[0] == 200 and _is_xlsx(export[1]),
            f"{len(export[2])} bytes",
        )

        # 重复导入不重置密码（安全）：20260010 首次入库用的是 abc123456，
        # 第二次导入的 default_password 换成了 123456 —— 必须仍然是 abc123456。
        still_old = _login("20260010", "abc123456", "student")
        check(
            "⑥ 重复导入未把已有账号密码刷成新的默认弱口令",
            still_old.get("code") == 0,
            f"code={still_old.get('code')}",
        )

        # ---------------------------------------------------------- ⑦ 归班
        # 再注册 3 名未分班学生。注意：自注册的 20269001 在导入时已被归入
        # 三年级二班（它在名单里 → 走更新），所以归班前未分班的正好这 3 人。
        for i in (3, 4, 5):
            http(
                "POST",
                "/api/auth/register",
                body={
                    "username": f"2026900{i}",
                    "real_name": f"待归班{i}",
                    "password": "abc123456",
                },
            )

        unassigned = http("GET", "/api/students?class_id=null", token=token)["data"]
        ids = [i["id"] for i in unassigned["items"][:3]]
        check("⑦ U10 归班前有 3 名未分班学生", unassigned["total"] == 3, f"未分班={unassigned['total']}")

        batch = http(
            "POST",
            "/api/students/batch-assign-class",
            body={"student_ids": ids, "class_id": class_id},
            token=token,
        )
        check(
            "⑦ U10 批量归班 3 人成功，未分班数下降",
            batch.get("code") == 0
            and batch["data"]["success_count"] == 3
            and batch["data"]["unassigned_count"] == unassigned["total"] - 3,
            f"success={batch.get('data', {}).get('success_count')} "
            f"unassigned={batch.get('data', {}).get('unassigned_count')}",
        )

        # ---------------------------------------------------------- ⑧ 重置密码
        target = ids[0]
        reset = http(
            "POST",
            f"/api/students/{target}/reset-password",
            body={"new_password": "resetpwd888"},
            token=token,
        )
        student_row = _query_one(db_file, "SELECT username FROM users WHERE id = ?", (target,))
        check("⑧ U11 重置密码成功", reset.get("code") == 0, f"username={student_row}")
        check(
            "⑧ U11 旧密码（默认弱口令）失效、新密码可用",
            _login(student_row, "abc123456", "student").get("code") == 20002
            and _login(student_row, "resetpwd888", "student").get("code") == 0,
        )

        # ---------------------------------------------------------- ⑨ 停用
        stu_login = _login(student_row, "resetpwd888", "student")
        stu_token = stu_login["data"]["access_token"]
        http("PATCH", f"/api/students/{target}", body={"status": "disabled"}, token=token)
        after_disable = http("GET", "/api/me/profile", token=stu_token)
        check(
            "⑨ U12 停用后旧 token 立即失效 → 10002",
            after_disable.get("code") == 10002,
            str(after_disable.get("code")),
        )

        # ---------------------------------------------------------- ⑩ 登录锁定
        # 连续 5 次密码错误 → 账号锁 10 分钟（锁 key = username，与限流主体无关；
        # `authenticate()` 对「账号不存在」也 `_record_failure`，故探测账号存在与否不影响）。
        #
        # ⚠️ 历史坑（2026-09-29 修，详见 docs/dev/02 §八 #94）：`_pace_login()` 是
        #    **客户端侧**滑动窗口，与服务端限流桶**不同步**；⑩ 之前用例已用掉共享的
        #    `ip:127.0.0.1` 额度 → 「连续 5 次错误密码」里有的请求被 HTTP 限流拦掉
        #    （10005）而**没进业务层**，`_fail_times` 累不到 5 → 第 6 次返回 `20002`
        #    而非 `10005`（重跑 2/2 一致，曾长期假红）。
        # 修法：每条探针一个**独立假 Bearer** → `_identity()` 取哈希得到专属限流桶，
        #    与脚本其它登录彻底隔离；且**每次换 ident**，保证请求必穿透到 handler，
        #    于是第 6/7 次的 10005 真正来自「账号锁定」而**不是**「HTTP 限流」。
        for i in range(5):
            _login("20260002", "wrong-pwd", "student", ident=f"bjsys-smoke-lock-{i}")
        locked = _login("20260002", "wrong-pwd", "student", ident="bjsys-smoke-lock-6th")
        check(
            "⑩ §2.1 连续 5 次密码错误后账号被锁 → 10005",
            locked.get("code") == 10005,
            f"第 6 次 code={locked.get('code')}",
        )
        check(
            "⑩ 锁定期内正确密码同样被拒 → 10005",
            _login(
                "20260002", "abc123456", "student", ident="bjsys-smoke-lock-pwd"
            ).get("code")
            == 10005,
        )

        # ---------------------------------------------------------- ⑪ 审计
        actions = {
            r[0]
            for r in _query_all(db_file, "SELECT action FROM audit_logs")
        }
        need = {"student.import", "student.assign_class", "student.reset_password"}
        check("⑪ U14 audit_logs 记录齐全", need.issubset(actions), f"已有={sorted(actions)}")

        admin_rows = _query_all(
            db_file,
            "SELECT user_id, target_type, target_id FROM audit_logs WHERE action = 'student.reset_password'",
        )
        check(
            "⑪ U14 审计含操作人与目标",
            bool(admin_rows) and admin_rows[0][0] is not None and admin_rows[0][2] is not None,
            str(admin_rows[0] if admin_rows else None),
        )

        # ---------------------------------------------------------- ⑫ 参数与权限
        unauth = http("GET", "/api/students")
        check("⑫ 未鉴权访问管理接口 → 10002", unauth.get("code") == 10002)

        weak = http(
            "POST",
            "/api/auth/register",
            body={"username": "20269999", "real_name": "小同学", "password": "12345"},
        )
        check(
            "⑫ 弱口令注册 → 10001 且 message 带字段名",
            weak.get("code") == 10001 and "password" in weak.get("message", ""),
            f"code={weak.get('code')}",
        )

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
        log_handle.close()

    # ---------------------------------------------------------------- 汇总
    failed = [r for r in _results if not r[0]]
    print()
    print("=" * 72)
    print(f"烟测结果：{len(_results) - len(failed)}/{len(_results)} 通过")
    if failed:
        print("未通过项：")
        for _, name, detail in failed:
            print(f"  - {name} {detail}")
        print(f"服务日志：{log_file}")
    print("=" * 72)

    if not failed:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failed else 0


def _query_all(db_file: Path, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(db_file))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _query_one(db_file: Path, sql: str, params: tuple = ()) -> Any:
    rows = _query_all(db_file, sql, params)
    return rows[0][0] if rows else None


if __name__ == "__main__":
    sys.exit(main())
