"""UDP 广播应答 —— WP-BE-08 F2（接口设计 §3.2）。

学生端 App 靠它「自动找到服务器」，省得用户手抄 IP。协议极简：
客户端向 UDP `9527` 广播 `{"magic":"BJSYS_DISCOVER","v":1}`，
服务端**单播**回一条 `BJSYS_HERE`。

### 应答字段（必须与客户端解析严格一致）
客户端实现见 `student-app/lib/core/discovery/broadcast_finder.dart` 的
`parseDiscoverReply()`，它读三个字段：

| 字段 | 用途 | 本项目实际值 |
|---|---|---|
| `magic` | 必须是 `BJSYS_HERE`，否则整包丢弃 | `"BJSYS_HERE"` |
| `host` | 服务地址。**空 / `0.0.0.0` / 回环时客户端改用「包从哪来」兜底** | 当前推荐地址 |
| `port` | **HTTP 端口（8810），不是 UDP 端口（9527）** | `http_port()` |

> ⚠️ `port` 写成 9527 是最容易犯的错：客户端会去连一个只有 UDP 在听的端口，
> 表现为「永远连不上」。客户端源码里专门为此写了警告注释。

### 安全
- 只应答**局域网私有网段 + 回环**（`netinfo.is_allowed_discovery_source`），公网来源一律不应答。
- 应答体**不含任何敏感信息**：没有账号、没有版本号、没有文件路径，只有服务名/端口/是否需鉴权。
- 服务端**不记录来源 IP 到库**，只在内存计数（避免被当成日志注入面）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from app.core.logging import get_logger
from app.services.discovery import (
    DISCOVERY_PORT,
    http_port,
    server_name,
    state,
)
from app.services.discovery.netinfo import is_allowed_discovery_source, primary_lan_ip

logger = get_logger(__name__)

#: 探测包 magic（客户端 `kDiscoverMagic`）
DISCOVER_MAGIC = "BJSYS_DISCOVER"

#: 应答包 magic（客户端 `kHereMagic`）
HERE_MAGIC = "BJSYS_HERE"

#: 协议版本
PROTOCOL_VERSION = 1

#: 单个 UDP 包最大长度（防御：畸形/超长报文直接丢，不解析）
MAX_DATAGRAM = 1024


def classify_datagram(data: bytes, source_ip: str) -> str:
    """给一个数据包分类，返回 `answered` 场景所需的判定，或拒绝原因。

    返回值：`"ok"` / `"malformed"` / `"bad_magic"` / `"bad_version"` / `"rejected"`。

    抽成独立函数是为了**能单测**：不用起 UDP 端口就能覆盖全部拒绝路径。
    """
    if not data or len(data) > MAX_DATAGRAM:
        return "malformed"

    if not is_allowed_discovery_source(source_ip):
        return "rejected"

    try:
        payload: Any = json.loads(data.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, ValueError):
        return "malformed"

    if not isinstance(payload, dict) or payload.get("magic") != DISCOVER_MAGIC:
        return "bad_magic"

    version = payload.get("v", PROTOCOL_VERSION)
    try:
        version_int = int(version)
    except (TypeError, ValueError):
        return "bad_version"
    if version_int != PROTOCOL_VERSION:
        return "bad_version"

    return "ok"


class UDPResponder:
    """UDP 9527 应答器。

    生命周期由 `app.tasks.scheduler` 统一管理（F9），也可以手动 `start()/stop()`。

    ⚠️ **绑定失败不抛异常**，只返回 `False` 并记降级：
    9527 被别的程序占用（或安全软件拦了）时，服务本体必须还能跑 ——
    广播发现只是**增强路径**，扫码与手输才是保底（UI 规范 P1 + 接口设计 §3.2）。
    """

    def __init__(
        self,
        *,
        port: int = DISCOVERY_PORT,
        host_provider: Callable[[], str | None] | None = None,
        http_port_provider: Callable[[], int] = http_port,
        name_provider: Callable[[], str] = server_name,
    ) -> None:
        self.port = int(port)
        self._host_provider = host_provider
        self._http_port_provider = http_port_provider
        self._name_provider = name_provider
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: "_Protocol | None" = None
        self._counters = {"received": 0, "answered": 0, "rejected": 0, "malformed": 0}

    # ------------------------------------------------------------------ 状态

    @property
    def running(self) -> bool:
        return self._transport is not None

    @property
    def counters(self) -> dict[str, int]:
        return dict(self._counters)

    # ------------------------------------------------------------------ 报文

    def _host(self) -> str | None:
        if self._host_provider is not None:
            return self._host_provider()
        return state.current_ip or primary_lan_ip()

    def build_payload(self, source_ip: str) -> dict[str, Any]:
        """构造应答体（纯函数，便于单测断言字段）。"""
        host = self._host()
        return {
            "magic": HERE_MAGIC,
            "v": PROTOCOL_VERSION,
            "host": host,
            "port": self._http_port_provider(),
            "server_name": self._name_provider(),
            "need_auth": True,
        }

    def build_reply(self, data: bytes, source_ip: str) -> bytes | None:
        """入参一包数据 → 返回应答字节；不该应答时返回 `None`。**不计数**（纯函数）。"""
        if classify_datagram(data, source_ip) != "ok":
            return None
        return json.dumps(self.build_payload(source_ip), ensure_ascii=False).encode("utf-8")

    def handle_datagram(self, data: bytes, source_ip: str) -> bytes | None:
        """计数 + 构造应答（协议回调走这里）。"""
        self._counters["received"] += 1
        verdict = classify_datagram(data, source_ip)
        if verdict == "ok":
            self._counters["answered"] += 1
            return self.build_reply(data, source_ip)
        if verdict == "rejected":
            self._counters["rejected"] += 1
            logger.info("UDP 发现：拒绝非私有网段来源 %s", source_ip)
        else:
            self._counters["malformed"] += 1
            logger.debug("UDP 发现：丢弃非法报文（%s），来源 %s", verdict, source_ip)
        return None

    # ------------------------------------------------------------------ 生命周期

    async def start(self) -> bool:
        """绑定端口并开始应答。返回是否成功（**不抛异常**）。"""
        if self.running:
            return True
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _Protocol(self),
                local_addr=("0.0.0.0", self.port),
                allow_broadcast=True,
            )
        except OSError as exc:
            logger.warning(
                "UDP 发现端口 %s 绑定失败（广播发现不可用，扫码/手输仍可用）：%s",
                self.port,
                exc,
            )
            state.mark_degraded("udp_discovery", f"UDP {self.port} 绑定失败：{exc}")
            return False
        self._transport = transport  # type: ignore[assignment]
        self._protocol = protocol  # type: ignore[assignment]
        state.clear_degraded("udp_discovery")
        logger.info("UDP 发现已就绪：0.0.0.0:%s", self.port)
        return True

    async def stop(self) -> None:
        """停止应答并释放端口（幂等）。"""
        transport, self._transport = self._transport, None
        self._protocol = None
        if transport is None:
            return
        try:
            transport.close()
        except Exception as exc:  # noqa: BLE001 - 关闭失败不该影响停机
            logger.warning("UDP 发现关闭异常（忽略）：%s", exc)
        logger.info("UDP 发现已停止。")


class _Protocol(asyncio.DatagramProtocol):
    """把 `datagram_received` 转成「构造应答 → 单播回」。"""

    def __init__(self, owner: UDPResponder) -> None:
        self._owner = owner
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # type: ignore[override]
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._transport is None:
            return
        try:
            reply = self._owner.handle_datagram(data, addr[0])
        except Exception as exc:  # noqa: BLE001 - 单包异常不能打死整个监听
            logger.error("UDP 发现处理异常（已忽略该包）：%s", exc, exc_info=True)
            return
        if reply is not None:
            # **单播**回应答（不是广播），来源端口与来源地址原样返回
            self._transport.sendto(reply, addr)

    def error_received(self, exc: Exception) -> None:
        logger.debug("UDP 发现收发错误：%s", exc)


#: 进程内单例（由 scheduler 启停）
responder = UDPResponder()
