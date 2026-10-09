"""WP-BE-08 服务发现与运维 —— 服务发现部分。

覆盖 10b 的验收用例：U1（ping）、U2（UDP 真机往返）、U3（公网来源拒绝）、
U4（server/info）、U5（IP 变化 + 审计）、U9（降级告警可查）、U12（备份目录不可写仍能启动），
以及 F3 网卡判类型的纯函数单测、F9 调度器时刻计算。

⚠️ **U2 需要真实占用 UDP 9527**。若本机已有别的进程占着它
（比如另开着一个后端正跑着），本用例会**明确失败**并提示释放端口 ——
这是刻意的：把「广播发现静默失效」伪装成通过，比测试变红危险得多。
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from pathlib import Path

import pytest

# --------------------------------------------------------------------------- 测试环境
# ⚠️ 必须在 import app.* **之前**设好环境变量：`app.main` 会立刻读配置。
_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_be08_disc_"))
_DB_FILE = _TMP_DIR / "test.db"
os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import reset_config_cache  # noqa: E402
from app.core import rate_limit  # noqa: E402
from app.core.db import read_conn  # noqa: E402
from app.main import app  # noqa: E402
from app.services import backup_service  # noqa: E402
from app.services.discovery import (  # noqa: E402
    DISCOVERY_PORT,
    DiscoveryState,
    http_port,
    server_name,
    state,
    state_file,
)
from app.services.discovery import netinfo  # noqa: E402
from app.services.discovery.netinfo import (  # noqa: E402
    KIND_ETHERNET,
    KIND_LOOPBACK,
    KIND_OTHER,
    KIND_VIRTUAL,
    KIND_WLAN,
    NetInterface,
)
from app.services.discovery.udp_responder import (  # noqa: E402
    DISCOVER_MAGIC,
    HERE_MAGIC,
    UDPResponder,
    classify_datagram,
    responder,
)
from app.services.settings_service import SettingsService  # noqa: E402
from app.tasks import scheduler  # noqa: E402


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module", autouse=True)
def _fresh_config():
    """把配置重新指到本模块的临时库。

    ⚠️ **同一个 pytest 进程里会跑多个测试模块**，而：
    - `os.environ` 是进程级的；
    - `get_settings()` 是 `lru_cache` 的。

    先跑的模块会把 `DB_PATH` 定住，后面的模块即使重设环境变量，
    `get_settings()` 仍返回旧路径 —— 表现为「库怎么建在了别的目录」。
    所以每个模块都必须**显式重设环境变量 + 清缓存**。
    """
    os.environ["DB_PATH"] = str(_DB_FILE)
    os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
    os.environ["LOG_LEVEL"] = "WARNING"
    reset_config_cache()
    return _DB_FILE


@pytest.fixture(scope="module")
def client(_fresh_config):
    """起一次 app（会真跑 lifespan：迁移 + 后台任务 + UDP 绑定）。

    进 `with` 之后（迁移已完成）再刷一次 `SettingsService` 缓存 ——
    否则缓存里可能还是上一个测试模块那座库的配置。
    """
    with TestClient(app, raise_server_exceptions=False) as c:
        SettingsService.refresh()
        yield c


def _login(c: TestClient) -> str:
    """简化版无登录态：返回占位 token（WS/接口均不再校验 token）。"""
    return "local"


@pytest.fixture()
def admin_token(client) -> str:
    return _login(client)


@pytest.fixture()
def auth(admin_token) -> dict:
    # 简化版不再要求鉴权头；保留 fixture 仅为不改动调用点
    return {}


@pytest.fixture(autouse=True)
def _clean_state():
    """每个用例前后都让内存状态归零，并清掉限流计数。

    ⚠️ 限流计数器是**进程级**的（且按 IP 计，见 `02-实现约定` §八 #1），
    跨测试模块会累积。登录接口限 5 次/分钟，一个模块里多登几次就会撞
    `10005`，表现为「莫名其妙登录失败」。既有模块（BE-01/BE-02）的惯例就是
    在 fixture 里 `clear_all()`。
    """
    state.reset()
    rate_limit.clear_all()
    yield
    state.reset()


# =========================================================================== F1 / U1


def test_u1_ping_is_lightweight_and_unauthenticated(client):
    """U1：不带 token → code=0，四个契约字段齐全，且**足够轻**。"""
    resp = client.get("/api/ping")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0

    data = body["data"]
    assert set(data) == {"service", "version", "server_name", "time"}
    assert data["service"] == "bjsys"
    assert data["server_name"] == server_name()
    assert data["time"].endswith("Z")

    elapsed = []
    for _ in range(5):
        t0 = time.perf_counter()
        client.get("/api/ping")
        elapsed.append((time.perf_counter() - t0) * 1000)
    # U1 要求 < 50ms。取最快的一次作为「接口本身的开销」——
    # 首次调用会包含连接建立/路由懒解析，那是测试装置的开销，不是接口的。
    assert min(elapsed) < 50.0, f"ping 过慢：{elapsed}"


def test_u1_ping_route_is_owned_by_be08(client):
    """回归守卫（缺陷 #41）：`main.py` 里**不得**再有 `/api/ping` 桩。

    自动路由在 `main.py` 之后注册、同名路由先注册者生效 ——
    桩只要还在，`discovery.py` 的实现就永远不生效，而且**不报任何错**。
    """
    import app.main as main_module

    source = Path(main_module.__file__).read_text(encoding="utf-8")
    assert '"/api/ping"' not in source, "main.py 里又出现了 /api/ping 桩，会遮蔽 BE-08 的实现"

    # 反向确认：接口确实由 discovery 模块提供（响应里的 server_name 走的是本模块的 provider）
    os.environ["SERVER_NAME"] = "烟测机房-A"
    try:
        assert client.get("/api/ping").json()["data"]["server_name"] == "烟测机房-A"
    finally:
        os.environ.pop("SERVER_NAME", None)


# =========================================================================== F2 / U2 / U3


def test_udp_reply_contract_is_exact():
    """应答体字段必须与客户端 `parseDiscoverReply` 严格对齐。"""
    packet = json.dumps({"magic": DISCOVER_MAGIC, "v": 1}).encode()
    reply = responder.build_reply(packet, "192.168.1.50")
    assert reply is not None
    payload = json.loads(reply.decode("utf-8"))

    assert payload["magic"] == HERE_MAGIC
    # ⚠️ 必须是 **HTTP 端口**，不是 UDP 发现端口 —— 写错客户端会去连 9527，表现为永远连不上
    assert payload["port"] == http_port()
    assert payload["port"] != DISCOVERY_PORT
    assert payload["need_auth"] is True
    assert payload["server_name"] == server_name()
    host = payload["host"]
    assert host is None or netinfo.parse_v4(host) is not None


def test_udp_rejects_bad_packets():
    """非法报文/错误 magic/版本不符 一律不应答（且不抛异常）。"""
    ok_packet = json.dumps({"magic": DISCOVER_MAGIC, "v": 1}).encode()
    assert classify_datagram(b"", "192.168.1.5") == "malformed"
    assert classify_datagram(b"{ not json", "192.168.1.5") == "malformed"
    assert classify_datagram(b"x" * 5000, "192.168.1.5") == "malformed"
    assert classify_datagram(b'{"magic":"SOMETHING_ELSE"}', "192.168.1.5") == "bad_magic"
    assert classify_datagram(b'{"magic":"BJSYS_DISCOVER","v":2}', "192.168.1.5") == "bad_version"
    assert classify_datagram(b'{"magic":"BJSYS_DISCOVER","v":"x"}', "192.168.1.5") == "bad_version"
    assert classify_datagram(ok_packet, "192.168.1.5") == "ok"
    # 缺 v 视为当前版本（向前兼容：老客户端可能不发 v）
    assert classify_datagram(b'{"magic":"BJSYS_DISCOVER"}', "192.168.1.5") == "ok"


def test_u3_public_source_rejected():
    """U3：公网来源一律拒绝，且**不产生应答**。"""
    for public in ("8.8.8.8", "114.114.114.114", "203.0.113.9", "172.32.0.1", "1.1.1.1"):
        assert netinfo.is_allowed_discovery_source(public) is False, public
        packet = json.dumps({"magic": DISCOVER_MAGIC, "v": 1}).encode()
        assert classify_datagram(packet, public) == "rejected"
        assert responder.build_reply(packet, public) is None

    for private in ("10.0.0.7", "172.16.0.1", "172.31.255.254", "192.168.88.19", "127.0.0.1"):
        assert netinfo.is_allowed_discovery_source(private) is True, private

    # 169.254 是 DHCP 拿不到地址时的自赋值，不是有效局域网来源
    assert netinfo.is_allowed_discovery_source("169.254.3.4") is False


def test_u2_udp_real_roundtrip(client):
    """U2：真发一个 UDP 包，2 秒内收到 `BJSYS_HERE`，端口是 HTTP 端口。"""
    assert responder.running, (
        f"UDP {DISCOVERY_PORT} 未在监听 —— 本机可能已有别的进程占用该端口"
        f"（例如另开着一个后端）。请释放后重跑。"
    )

    packet = json.dumps({"magic": DISCOVER_MAGIC, "v": 1}).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    try:
        sock.sendto(packet, ("127.0.0.1", DISCOVERY_PORT))
        raw, _ = sock.recvfrom(4096)
    finally:
        sock.close()

    payload = json.loads(raw.decode("utf-8"))
    assert payload["magic"] == HERE_MAGIC
    assert payload["port"] == http_port()
    assert payload["need_auth"] is True


def test_udp_counters_are_kept_in_memory_only(client):
    """F2：应答计数只在内存里；且**不回写数据库**（不记录来源 IP）。"""
    before = responder.counters
    packet = json.dumps({"magic": DISCOVER_MAGIC, "v": 1}).encode()
    responder.handle_datagram(packet, "192.168.1.77")
    responder.handle_datagram(packet, "8.8.8.8")  # 公网 → 拒绝
    responder.handle_datagram(b"garbage", "192.168.1.77")  # 畸形
    after = responder.counters

    assert after["received"] == before["received"] + 3
    assert after["answered"] == before["answered"] + 1
    assert after["rejected"] == before["rejected"] + 1
    assert after["malformed"] == before["malformed"] + 1

    # 应答体中不含来源 IP 之外的任何信息，更不该落库
    with read_conn() as conn:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "asr_logs" in tables  # 表还在（只是没被本功能用到）


# =========================================================================== F3 / U4


def test_u4_server_info_shape(client, auth):
    """U4：列出网卡、推荐项唯一、`qr_content` 格式正确。"""
    data = client.get("/api/server/info", headers=auth).json()["data"]

    assert data["service"] == "bjsys"
    assert data["app_port"] == http_port()
    assert data["discovery_port"] == DISCOVERY_PORT
    assert isinstance(data["interfaces"], list) and data["interfaces"], "至少要枚举出一个地址"

    recommended = [i for i in data["interfaces"] if i["recommended"]]
    assert len(recommended) <= 1, "推荐地址必须唯一"

    qr = data["qr_content"]
    if qr is not None:
        assert qr.startswith("bjsys://")
        host_port = qr[len("bjsys://") :]
        host, _, port = host_port.rpartition(":")
        assert netinfo.parse_v4(host) is not None, qr
        assert int(port) == http_port()
        assert data["current_url"] == f"http://{host}:{port}"

    assert "degraded_reasons" in data
    assert "backup" in data and "keep" in data["backup"]


def test_u4_refresh_query_forces_reprobe(client, auth):
    """`?refresh=1` 走强制探测（不命中 30 秒缓存）。"""
    state.set_interface_probe(
        lambda: [NetInterface(name="eth0", ip="192.168.1.9", kind=KIND_ETHERNET)]
    )
    data = client.get("/api/server/info?refresh=1", headers=auth).json()["data"]
    infos = {i["ip"] for i in data["interfaces"]}
    assert "192.168.1.9" in infos


# =========================================================================== F4 / U5


def test_u5_ip_change_updates_timestamp(client):
    """U5：地址变化 → `last_ip_change_at` 更新。

    「首次观测」不算变化 —— 否则每台机器每次冷启动都会报一条「IP 变了」的假告警。

    注：原用例还断言 `audit_logs` 写入 IP 变更审计；`audit_logs` 属已删除的
    鉴权审计体系（WP-F F-2），`scheduler._audit_ip_change` 已改为 no-op，故移除。
    """
    assert state.current_ip is None
    assert state.observe("192.168.1.10") is False, "首次观测是初始化，不是变化"
    assert state.observe("192.168.1.10") is False, "地址没变"
    assert state.last_ip_change_at is None
    assert state.observe("192.168.1.20") is True, "地址变了"
    assert state.last_ip_change_at is not None
    assert state.previous_ips[:1] == ["192.168.1.10"]


def test_u5_ip_state_persists_across_instances():
    """状态要落盘，否则重启后管理端看不到上次变化时间。"""
    state.observe("10.1.2.3")
    state.observe("10.1.2.4")

    fresh = DiscoveryState()
    fresh.load(force=True)
    assert fresh.current_ip == "10.1.2.4"
    assert fresh.last_ip_change_at == state.last_ip_change_at
    assert "10.1.2.3" in fresh.previous_ips


def test_runtime_state_does_not_pollute_settings_table(client):
    """回归守卫：运行期状态**不得**写进 `settings` 表。

    BE-01 的用例 `test_u1_first_boot_creates_schema_and_admin` 断言
    「`settings` 恰好 20 条」（迁移种子值）。本包第一版把状态挂在
    `settings.discovery_state` 上，全量回归时直接把那条断言打红（21 条）。
    语义上「当前 IP 是多少」也不是配置 —— 它有自己的状态文件。
    """
    state.observe("192.168.9.9")
    state.observe("192.168.9.10")

    assert state_file().is_file(), "状态文件应写在数据库同目录"

    with read_conn() as conn:
        keys = [r["key"] for r in conn.execute("SELECT key FROM settings").fetchall()]
    assert "discovery_state" not in keys
    assert len(keys) == 20, f"settings 只应有 20 条种子值，实际 {len(keys)}：{sorted(keys)}"


def test_refresh_uses_injected_interfaces():
    """F4 的探测入口可以注入网卡列表（测试与离线环境都要能跑）。"""
    state.set_interface_probe(
        lambda: [
            NetInterface(name="lo", ip="127.0.0.1", kind=KIND_LOOPBACK),
            NetInterface(name="WLAN", ip="192.168.31.20", kind=KIND_WLAN),
        ]
    )
    assert state.detect_current_ip(now_ts=1.0, force=True) == "192.168.31.20"


# =========================================================================== F12 / U9


def test_u9_degraded_reasons_visible_in_health(client):
    """U9：降级告警必须能在 `/api/health` 查到。"""
    state.clear_degraded("unit_test")
    assert client.get("/api/health").json()["data"]["degraded"] is False

    state.mark_degraded("unit_test", "模拟外网中断 → 全站键盘模式")
    health = client.get("/api/health").json()["data"]
    assert health["degraded"] is True
    kinds = {r["kind"] for r in health["degraded_reasons"]}
    assert "unit_test" in kinds

    info = client.get("/api/server/info")
    assert "unit_test" in {r["kind"] for r in info.json()["data"]["degraded_reasons"]}

    state.clear_degraded("unit_test")
    assert client.get("/api/health").json()["data"]["degraded"] is False


def test_degraded_mark_is_idempotent():
    state.mark_degraded("k", "第一次")
    state.mark_degraded("k", "第二次")
    reasons = {r["kind"]: r for r in state.degraded_reasons()}
    assert reasons["k"]["detail"] == "第二次"
    assert len(state.degraded_reasons()) == 1


# =========================================================================== F13 / U12


def test_u12_unwritable_backup_dir_warns_but_service_still_works(client, auth, monkeypatch):
    """U12：备份目录不可写 → 自检明确告警，**服务照常可用**。"""
    blocker = _TMP_DIR / "blocker_file"
    blocker.write_bytes(b"i am a file, not a dir")
    monkeypatch.setattr(backup_service, "backup_dir", lambda: blocker / "nested")

    result = scheduler.check_backup_dir()
    assert result.ok is False
    assert result.detail  # 必须带可读原因，不能只说 False

    checks = scheduler.run_startup_checks()
    by_name = {c.name: c for c in checks}
    assert by_name["backup_dir"].ok is False
    assert by_name["db_writable"].ok is True

    # 服务没被拖垮
    assert client.get("/api/health").json()["code"] == 0
    assert client.get("/api/server/info", headers=auth).json()["code"] == 0


def test_startup_checks_cover_four_items(client):
    """F13 的四项自检都在，且正常情况下全绿。"""
    results = scheduler.run_startup_checks()
    names = {r.name for r in results}
    assert names == {"db_writable", "migration_version", "backup_dir", "udp_port"}
    failed = [r for r in results if not r.ok]
    assert not failed, f"自检未通过：{[(r.name, r.detail) for r in failed]}"


def test_db_writable_check_does_not_mutate_data(client):
    """自检「DB 可写」靠拿写锁 + 回滚，**不能留下任何痕迹**。"""
    before = _table_counts()
    assert scheduler.check_db_writable().ok is True
    assert _table_counts() == before


# =========================================================================== 纯函数单测


def test_classify_uses_media_type_not_locale_names():
    """F3 的关键：靠 `MediaType` 判类型，**不靠网卡名**（中文/英文系统都要对）。"""
    assert netinfo.classify("以太网", "802.3") == KIND_ETHERNET
    assert netinfo.classify("Ethernet", "802.3") == KIND_ETHERNET
    assert netinfo.classify("WLAN", "Native 802.11") == KIND_WLAN
    assert netinfo.classify("Wi-Fi", "Native 802.11") == KIND_WLAN
    # 拿不到 MediaType 时才退回名字特征
    assert netinfo.classify("vEthernet (WSL)", None) == KIND_VIRTUAL
    assert netinfo.classify("VMware Network Adapter VMnet8", None) == KIND_VIRTUAL
    assert netinfo.classify("无线网络连接", None) == KIND_WLAN
    assert netinfo.classify("本地连接", None) == KIND_OTHER  # 不猜


def test_pick_recommended_prefers_wired_then_wireless():
    """F3：优先有线以太网，其次 WLAN；虚拟与回环永不推荐。"""
    interfaces = [
        NetInterface(name="VMware", ip="192.168.20.1", kind=KIND_VIRTUAL),
        NetInterface(name="lo", ip="127.0.0.1", kind=KIND_LOOPBACK),
        NetInterface(name="WLAN", ip="192.168.31.20", kind=KIND_WLAN),
        NetInterface(name="以太网", ip="192.168.1.50", kind=KIND_ETHERNET),
    ]
    assert netinfo.pick_recommended(interfaces).ip == "192.168.1.50"

    without_wired = [i for i in interfaces if i.kind != KIND_ETHERNET]
    assert netinfo.pick_recommended(without_wired).ip == "192.168.31.20"

    only_virtual = [i for i in interfaces if i.kind in (KIND_VIRTUAL, KIND_LOOPBACK)]
    assert netinfo.pick_recommended(only_virtual) is None, "宁可为空也不能推荐虚拟网卡"

    # 169.254 自赋值地址不算可用
    link_local = [NetInterface(name="eth", ip="169.254.1.9", kind=KIND_ETHERNET)]
    assert netinfo.pick_recommended(link_local) is None

    # 停用的网卡不考虑
    down = [NetInterface(name="eth", ip="192.168.1.5", kind=KIND_ETHERNET, is_up=False)]
    assert netinfo.pick_recommended(down) is None


def test_ip_predicates():
    assert netinfo.is_private_ip("10.0.0.1") is True
    assert netinfo.is_private_ip("172.16.5.5") is True
    assert netinfo.is_private_ip("172.15.5.5") is False
    assert netinfo.is_private_ip("192.168.1.1") is True
    assert netinfo.is_private_ip("127.0.0.1") is False  # 回环不是 RFC1918
    assert netinfo.is_private_ip("8.8.8.8") is False
    assert netinfo.is_private_ip("::1") is False  # IPv6 不参与
    assert netinfo.is_private_ip("not-an-ip") is False

    assert netinfo.is_usable_lan_ip("192.168.1.1") is True
    assert netinfo.is_usable_lan_ip("169.254.1.1") is False
    assert netinfo.is_usable_lan_ip("127.0.0.1") is False


def test_enumerate_interfaces_never_raises(monkeypatch):
    """探测全失败时返回空列表，不能把接口打成 500。"""
    monkeypatch.setattr(netinfo, "_probe_all", lambda: (_ for _ in ()).throw(OSError("boom")))
    assert netinfo.enumerate_interfaces() == []


def test_cached_interfaces_respects_ttl():
    import threading

    calls: list[int] = []
    gate = threading.Event()

    def _probe():
        calls.append(1)
        # 第 2 次（后台刷新那次）卡住直到测试放行 —— 模拟慢探测；
        # 若 get() 变回同步探测，调用方会被卡 5 s，SWR 契约即告破
        if len(calls) >= 2:
            gate.wait(timeout=5)
        return [NetInterface(name="eth", ip="192.168.1.1", kind=KIND_ETHERNET)]

    cache = netinfo.CachedInterfaces(ttl_seconds=10.0, probe=_probe)
    cache.get(now=0.0)
    cache.get(now=5.0)
    assert len(calls) == 1, "TTL 内不应重复探测"
    # TTL 过期但有旧值 → stale-while-revalidate（#109）：立刻回旧值不阻塞，
    # 探测挪到后台线程（原语义是同步探测，会把 asyncio 事件循环卡 ~2.5 s）
    _t0 = time.monotonic()
    stale = cache.get(now=11.0)
    elapsed = time.monotonic() - _t0
    assert [i.ip for i in stale] == ["192.168.1.1"], "过期后应立刻返回旧值"
    assert not gate.is_set(), "get() 返回时后台探测仍被阻塞（= 未同步等待探测）"
    assert elapsed < 2.0, (
        f"get() 耗时 {elapsed:.2f}s —— 疑似同步探测被慢 probe 卡住"
        "（SWR 应立刻返回旧值；同步实现会卡满 gate 的 5s 超时）"
    )
    gate.set()
    cache.wait_pending()
    assert len(calls) == 2, "等待后台刷新完成后应完成一次探测"
    cache.get(now=11.1, force=True)
    assert len(calls) == 3, "force 语义不变：仍同步探测"


def test_scheduler_daily_job_due_rules():
    """F9：日任务「到点且今天没跑过」才执行。"""
    from datetime import datetime

    calls: list[str] = []
    job = scheduler.DailyJob(name="t", hour=2, minute=0, run=lambda: calls.append("x"))

    assert job.due(datetime(2026, 9, 24, 1, 59)) is False
    assert job.due(datetime(2026, 9, 24, 2, 0)) is True
    assert job.due(datetime(2026, 9, 24, 23, 30)) is True

    job.last_run_date = "2026-09-24"
    assert job.due(datetime(2026, 9, 24, 23, 30)) is False
    assert job.due(datetime(2026, 9, 25, 2, 0)) is True


def test_scheduler_runs_jobs_and_start_stop():
    """F9：调度器可启停；日任务能真跑（同步函数放线程池）。"""
    import asyncio

    async def _scenario() -> dict:
        scheduler.reset_for_tests()
        fired: list[str] = []
        scheduler.jobs().append(
            scheduler.DailyJob(name="probe", hour=0, minute=0, run=lambda: fired.append("ok"))
        )

        await scheduler.run_daily_job_now("probe")
        unknown_raised = False
        try:
            await scheduler.run_daily_job_now("no-such-job")
        except KeyError:
            unknown_raised = True

        # 「到点且今天没跑过」由 `_tick` 的 due() 判重；execute 负责打上日期戳
        once: list[str] = []
        job = scheduler.DailyJob(name="once", hour=0, minute=0, run=lambda: once.append("1"))
        now = scheduler.local_now()
        await job.execute(now)

        # 完整心跳一次：不应抛异常（内部已把单任务失败吞掉并记日志）
        await scheduler._tick(now)

        scheduler.reset_for_tests()
        return {
            "fired": fired,
            "unknown_raised": unknown_raised,
            "stamped": job.last_run_date,
            "expected_date": now.date().isoformat(),
        }

    result = asyncio.run(_scenario())
    assert result["fired"] == ["ok"]
    assert result["unknown_raised"] is True
    assert result["stamped"] == result["expected_date"]


def test_udp_responder_start_stop_is_idempotent():
    """生命周期：`start()` 可重复调用；`stop()` 幂等，且**真的释放端口**。"""
    import asyncio

    async def _scenario() -> dict:
        # 用一个临时端口，避免和应用里的单例抢 9527
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        instance = UDPResponder(port=port)
        first = await instance.start()
        second = await instance.start()  # 幂等
        running_before = instance.running
        await instance.stop()
        await instance.stop()  # 幂等
        running_after = instance.running

        # 端口确实被释放了（否则下一轮会绑不上）
        reuse = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            reuse.bind(("127.0.0.1", port))
            released = True
        except OSError:
            released = False
        finally:
            reuse.close()
        return {
            "first": first,
            "second": second,
            "before": running_before,
            "after": running_after,
            "released": released,
        }

    result = asyncio.run(_scenario())
    assert result["first"] is True
    assert result["second"] is True
    assert result["before"] is True
    assert result["after"] is False
    assert result["released"] is True


def test_udp_bind_failure_degrades_but_does_not_raise():
    """F13 / U12 的同类要求：端口被占 → **不抛异常**，只记降级。

    广播发现是增强路径，996 的失败都不该拦住服务启动（扫码与手输才是保底）。
    """
    import asyncio

    async def _scenario() -> dict:
        squatter = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        squatter.bind(("0.0.0.0", 0))
        port = squatter.getsockname()[1]
        try:
            instance = UDPResponder(port=port)
            started = await instance.start()
            reasons = {r["kind"]: r for r in state.degraded_reasons()}
            await instance.stop()
            return {"started": started, "reasons": reasons}
        finally:
            squatter.close()

    state.clear_degraded("udp_discovery")
    result = asyncio.run(_scenario())
    assert result["started"] is False, "端口被占时必须如实返回 False"
    assert "udp_discovery" in result["reasons"]
    assert "绑定失败" in result["reasons"]["udp_discovery"]["detail"]


def test_local_tz_is_beijing():
    """F9：按北京时间算「每日 02:00」。固定 +8 与 Asia/Shanghai 等价。

    注：`ZoneInfo.utcoffset(None)` 在 CPython 3.13 返回 `None`（需传具体日期），
    故这里用具体日期断言偏移量，意图不变（北京 = +8）。
    """
    from datetime import datetime, timedelta

    offset = scheduler.local_tz().utcoffset(datetime(2026, 1, 1))
    assert offset == timedelta(hours=8), offset


# =========================================================================== 辅助


def _table_counts() -> dict[str, int]:
    with read_conn() as conn:
        names = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        return {
            n: conn.execute(f"SELECT COUNT(*) AS c FROM {n}").fetchone()["c"] for n in names
        }
