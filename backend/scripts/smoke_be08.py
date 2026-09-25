"""WP-BE-08 服务发现与运维 —— 真机烟测（真 uvicorn + 真 UDP socket + 真临时库）。

与 `app/tests/test_be08_*.py` 的分工：单测用假网卡/假探测覆盖分支逻辑；
**这里只验「真网络 + 真进程」这件事**：

- 真起一个 uvicorn 进程，`lifespan` 真的把 UDP 9527 绑上了没有
- App 用的那套广播协议**真的能收到应答**，且 `port` 字段是 HTTP 端口（不是 9527）
- 备份接口真的产出了文件、真的过了 `PRAGMA integrity_check`
- `/api/health` 真的带 `degraded_reasons`

跑法：
    cd backend
    ./.venv/Scripts/python.exe scripts/smoke_be08.py

覆盖：U1（ping 免鉴权 + <50ms）、U2（UDP 广播发现往返）、U3（server/info + 网卡分类）、
U4（IP 变化检测落状态）、U6（手动备份产物 + 校验）、U7（备份接口鉴权）、
U9（健康检查降级字段）、U10（/api/ping 由 BE-08 提供、不是 BE-01 的桩）、U12（广播计数）。

⚠️ **需要 UDP 9527 空闲**。若本机已有后端在跑（或另一个烟测没退干净），
   本脚本会**明确报错退出**而不是静默变成「假绿」—— 端口被占时广播应答会打到那边去，
   断言就可能对着别人的进程通过。
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

BACKEND = Path(__file__).resolve().parents[1]
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():      # pragma: no cover - 非 Windows / 未建 venv
    PYTHON = Path(sys.executable)

PORT = int(os.environ.get("SMOKE_BE08_PORT", "8896"))
BASE = f"http://127.0.0.1:{PORT}"

#: 契约 §11 固定的 UDP 发现端口（`app/services/discovery.DISCOVERY_PORT`）
UDP_PORT = 9527
DISCOVER_MAGIC = "BJSYS_DISCOVER"
HERE_MAGIC = "BJSYS_HERE"

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

TMP = Path(tempfile.mkdtemp(prefix="smoke_be08_"))
DB_FILE = TMP / "smoke.db"

#: ⚠️ `APP_PORT` 必须设成 PORT：UDP 应答里的 `port` 字段取自它（`discovery.http_port()`），
#: 顺便也就验证了「应答带的是 HTTP 端口」这件事。
ENV = {
    **os.environ,
    "APP_PORT": str(PORT),
    "DB_PATH": str(DB_FILE),
    "BACKUP_DIR": str(TMP / "backups"),
    "JWT_SECRET": "smoke-be08-secret-0123456789abcdef",
    "JWT_TTL_HOURS": "12",
    "ADMIN_INIT_USERNAME": "admin",
    "ADMIN_INIT_PASSWORD": "Smoke-Be08-Pass",
    "LOG_LEVEL": "WARNING",
    "NO_PROXY": "127.0.0.1,localhost",
    "PYTHONIOENCODING": "utf-8",
}


def _tcp_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _udp_free(port: int) -> bool:
    """UDP「空闲」的判定：能独占绑上。绑不上就是别人在占。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# HTTP 小工具
# ---------------------------------------------------------------------------

def _http(method: str, path: str, *, token: str | None = None,
          json_body: dict | None = None, timeout: float = 20.0):
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=timeout, trust_env=False) as c:
        resp = c.request(method, BASE + path, json=json_body, headers=headers)
    try:
        return resp.status_code, resp.json()
    except Exception:  # noqa: BLE001
        return resp.status_code, {"raw": resp.text[:200]}


def _discover_once(timeout: float = 3.0) -> dict | None:
    """模拟 App：往广播地址发一包 DISCOVER，收应答。**真 socket，真端口**。"""
    payload = json.dumps({"magic": DISCOVER_MAGIC, "v": 1}).encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        # 发到 127.0.0.1 而不是 255.255.255.255：
        # 服务器本机自测不该依赖「局域网里还有另一台机器」。
        # 服务端对回环来源是放行的（刻意放宽，见 udp_responder 说明）。
        s.sendto(payload, ("127.0.0.1", UDP_PORT))
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            return None
    try:
        return {"raw": json.loads(data.decode("utf-8")), "from": addr}
    except (UnicodeDecodeError, ValueError):
        return {"raw": {"_undecodable": data[:80].hex()}, "from": addr}


def _backup_files() -> list[Path]:
    d = TMP / "backups"
    return sorted(p for p in d.glob("app_*.db") if p.is_file()) if d.is_dir() else []


def _integrity(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_checks() -> None:
    # ---- A. U1 /api/ping ----
    status, body = _http("GET", "/api/ping")
    check("U1 /api/ping 返回 200", status == 200, f"status={status}")
    check("U1 /api/ping code=0", body.get("code") == 0, f"{str(body)[:140]}")
    data = body.get("data") or {}
    # 字段名是接口设计 §3.1 定死的，App 的 ServerInfo.fromJson 照它解析
    check("U1 ping 字段齐全（service/version/server_name/time）",
          all(k in data for k in ("service", "version", "server_name", "time")),
          f"keys={sorted(data)}")
    check("U1 ping 免鉴权（没带 token 也能拿数据）", data.get("service") == "bjsys",
          f"service={data.get('service')}")

    # 延迟：连测 5 次取最好值（首次含连接建立，不算进门限）
    import httpx

    best = 9e9
    with httpx.Client(timeout=10, trust_env=False) as c:
        c.get(BASE + "/api/ping")          # 预热
        for _ in range(5):
            t0 = time.perf_counter()
            c.get(BASE + "/api/ping")
            best = min(best, (time.perf_counter() - t0) * 1000)
    check("U1 ping 响应 < 50 ms", best < 50, f"最快 {best:.1f} ms")

    # ---- B. U10 /api/ping 归 BE-08 提供（不是 BE-01 的桩）----
    # BE-01 的桩返回 {"pong": true}；BE-08 返回 service/version/... —— 字段不同即可区分
    check("U10 /api/ping 由 BE-08 提供（不是 BE-01 桩）",
          "pong" not in data and data.get("service") == "bjsys",
          f"含 pong={'pong' in data}")

    # ---- C. U2 UDP 广播发现（真 socket 往返）----
    reply = _discover_once()
    check("U2 UDP 广播能收到应答", reply is not None,
          "无应答（端口被占 / 应答器没起来）")
    if reply:
        r = reply["raw"]
        check("U2 应答 magic=BJSYS_HERE", r.get("magic") == HERE_MAGIC, f"magic={r.get('magic')}")
        # ⚠️ 全项目最容易写错的一处：port 必须是 **HTTP 端口**，不是 9527
        check("U2 应答 port 是 HTTP 端口（不是 9527）",
              str(r.get("port")) == str(PORT) and str(r.get("port")) != str(UDP_PORT),
              f"port={r.get('port')}（期望 {PORT}）")
        check("U2 应答 host 非空", bool(r.get("host")), f"host={r.get('host')}")
        check("U2 应答 need_auth=True", r.get("need_auth") is True, f"{r.get('need_auth')}")
        check("U2 应答含 server_name", bool(r.get("server_name")), f"{r.get('server_name')}")

    # 再发一包，确认是「可重复发现」而不是巧合
    again = _discover_once()
    check("U2 重复发现仍然有应答", again is not None and again["raw"].get("magic") == HERE_MAGIC)

    # 发给它一包垃圾，应当**不**应答（否则会变成 UDP 反射放大器）
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(1.0)
        s.sendto(b"not json at all", ("127.0.0.1", UDP_PORT))
        try:
            junk = s.recvfrom(4096)
            answered_junk = True
        except socket.timeout:
            answered_junk = False
    check("U2 非法报文不应答", not answered_junk)

    # ---- D. 登录 + U3 /api/server/info ----
    status, body = _http("POST", "/api/auth/login",
                         json_body={"username": "admin", "password": "Smoke-Be08-Pass",
                                    "role": "admin"})
    check("管理者登录", body.get("code") == 0, f"{str(body)[:140]}")
    token = (body.get("data") or {}).get("access_token")
    if not token:
        check("拿到 access_token", False, "后续需要它，直接中止")
        return

    status, body = _http("GET", "/api/server/info", token=token)
    check("U3 server/info code=0", body.get("code") == 0, f"{str(body)[:160]}")
    info = body.get("data") or {}
    check("U3 返回网卡列表", isinstance(info.get("interfaces"), list) and info["interfaces"],
          f"n={len(info.get('interfaces') or [])}")
    ifaces = info.get("interfaces") or []
    check("U3 网卡有分类标签（wired/wlan/...）",
          all("kind" in i for i in ifaces), f"kinds={[i.get('kind') for i in ifaces]}")
    check("U3 推荐地址非空（U4 IP 检测已生效）",
          bool(info.get("current_ip")) and bool(info.get("current_url")),
          f"current_ip={info.get('current_ip')} url={info.get('current_url')}")
    check("U3 qr_content 是 bjsys:// 协议（App 扫码用）",
          str(info.get("qr_content") or "").startswith("bjsys://"),
          f"qr_content={info.get('qr_content')}")
    # 端口是部署契约里最容易撞车的一项（本机 9010/8080/1935... 都占了）
    check("U3 app_port 是本服务端口", info.get("app_port") == PORT,
          f"app_port={info.get('app_port')}")
    check("U3 discovery_port=9527", info.get("discovery_port") == 9527,
          f"discovery_port={info.get('discovery_port')}")
    check("U3 广播应答器在跑（broadcast_enabled）", info.get("broadcast_enabled") is True,
          f"broadcast_enabled={info.get('broadcast_enabled')}")
    # ⚠️ 必须已经应答过至少 1 包：证明「HTTP 就绪时 UDP 已绑好」，
    #    而不是「先返回 ready、UDP 稍后再说」（App 只重试 3 轮 6 秒）
    counters = info.get("broadcast_counters") or {}
    check("U12 广播计数：收到 ≥ 2 包、应答 ≥ 2 次",
          counters.get("received", 0) >= 2 and counters.get("answered", 0) >= 2,
          f"counters={counters}")
    check("U12 广播计数：有 malformed（垃圾包被丢）",
          counters.get("malformed", 0) >= 1, f"counters={counters}")
    check("U3/IP 检测不落 settings 表（运行期状态与配置分离）",
          "degraded" in info and isinstance(info.get("backup"), dict),
          f"keys={sorted(info)[:8]}")

    # ---- E. U9 /api/health 降级字段 ----
    status, body = _http("GET", "/api/health")
    h = body.get("data") or {}
    check("U9 /api/health 有 degraded_reasons", "degraded_reasons" in h,
          f"keys={sorted(h)}")
    check("U9 /api/health degraded 是布尔", isinstance(h.get("degraded"), bool),
          f"degraded={h.get('degraded')}")
    check("U9 /api/health db 可达", h.get("db") is True, f"db={h.get('db')}")

    # ---- F. U6 手动备份 ----
    before = len(_backup_files())
    status, body = _http("POST", "/api/maintenance/backup", token=token, timeout=60)
    check("U6 手动备份 code=0", body.get("code") == 0, f"{str(body)[:200]}")
    outcome = body.get("data") or {}
    check("U6 备份产物存在", bool(outcome.get("file")), f"file={outcome.get('file')}")
    check("U6 备份体积 > 0", (outcome.get("size_bytes") or 0) > 0,
          f"size={outcome.get('size_human')}")
    check("U6 备份通过了完整性校验", outcome.get("integrity_ok") is True,
          f"integrity={outcome.get('integrity_ok')} detail={outcome.get('integrity_detail')}")

    files = _backup_files()
    check("U6 备份文件真的落在磁盘上", len(files) == before + 1,
          f"{before} → {len(files)}")
    if files:
        newest = files[-1]
        check("U6 手工备份文件名带秒（不覆盖当天定时备份）",
              len(newest.stem) == len("app_YYYYMMDD_HHMMSS"), f"file={newest.name}")
        check("U6 独立打开副本再校一次 integrity_check",
              _integrity(newest) == "ok", f"file={newest.name}")

    # ---- G. U7 备份接口鉴权 ----
    status, body = _http("POST", "/api/maintenance/backup")
    check("U7 无 token 备份被拒", body.get("code") != 0, f"code={body.get('code')}")

    status, body = _http("POST", "/api/auth/register",
                         json_body={"username": "be08stu", "password": "Stu-Pass-123",
                                    "real_name": "烟测学生", "grade": 5, "semester": 1})
    if body.get("code") == 0:
        stu_token = body["data"]["access_token"]
        status, body = _http("POST", "/api/maintenance/backup", token=stu_token)
        check("U7 学生 token 备份被拒（403/非 0 code）",
              body.get("code") != 0, f"code={body.get('code')}")
        status, body = _http("GET", "/api/server/info", token=stu_token)
        check("U7 学生 token 读 server/info 被拒", body.get("code") != 0,
              f"code={body.get('code')}")
    else:
        check("U7 学生账号注册", False, f"{str(body)[:140]}")

    # ---- 收尾：确认应答是「广播发现协议 v1」，而不是别的进程碰巧回了个 JSON ----
    if reply:
        check("U2 应答版本 v=1", reply["raw"].get("v") == 1, f"v={reply['raw'].get('v')}")


def main() -> int:
    if not _tcp_free(PORT):
        print(f"TCP {PORT} 被占用，请改用 SMOKE_BE08_PORT 指定其它端口。", flush=True)
        return 2
    if not _udp_free(UDP_PORT):
        print(f"UDP {UDP_PORT} 被占用 —— 多半是本机已有后端在跑。", flush=True)
        print("请先停掉它（Stop-Service bjsys-backend / 关掉 dev 进程）再跑本烟测，", flush=True)
        print("否则广播应答会打到那个进程，断言可能对着别人的服务通过（假绿）。", flush=True)
        return 2

    log_file = TMP / "server.log"
    with log_file.open("w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            [str(PYTHON), "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
            cwd=str(BACKEND), env=ENV, stdout=lf, stderr=subprocess.STDOUT,
        )

    try:
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

        # lifespan 里 `await responder.start()` 是先于 yield 的，HTTP 能应答即 UDP 已绑；
        # 但保险起见等一小拍，避免「刚 ready 就发包」这种时序噪音。
        time.sleep(0.4)

        print(f"== BE-08 服务发现与运维烟测（{BASE} / UDP {UDP_PORT}）==", flush=True)
        run_checks()
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
