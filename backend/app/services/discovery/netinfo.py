"""本机网卡地址枚举与「推荐地址」判定 —— WP-BE-08 F3 / F4。

为什么需要它：服务器是一台**家用/办公局域网的 Windows 电脑**，IP 由 DHCP 下发且经常变。
管理端要告诉用户「用哪个地址扫码」，而一台机器常有多个网卡（有线 + 无线 + VMware +
Hyper-V + 蓝牙 + WSL …），**默认取第一个往往取到虚拟网卡**，用户照着填必然连不上。

本模块只做三件事：
1. `enumerate_interfaces()` —— 枚举本机 IPv4 地址，标注网卡类型（有线/无线/虚拟/回环）
2. `pick_recommended()` —— 从里面挑一个「最可能是学生手机能连上的」地址
3. `primary_lan_ip()` —— 兜底：用 UDP connect 探针问内核「出网会走哪个地址」

### 探测手段的优先级（三者都是**只读**操作，失败就降级）

| 顺序 | 手段 | 得到什么 | 平台 |
|---|---|---|---|
| 1 | PowerShell `Get-NetIPAddress` + `Get-NetAdapter` | 接口别名、**MediaType**（`802.3` 有线 / `Native 802.11` 无线）、启用状态 | Windows |
| 2 | `ip -o -4 addr` | 接口名 | Linux |
| 3 | `socket.getaddrinfo(hostname)` | 只有一组 IP，**没有网卡类型** | 兜底 |

⚠️ **优先用 `MediaType` 而不是网卡名判类型**：
Windows 中文版网卡名是「以太网」「WLAN」「无线网络连接」，英文版是「Ethernet」「Wi-Fi」——
靠名字做字符串匹配会因为**系统语言**而失效（本项目此前已在 `PATHEXT`/`wmic` 上栽过类似的坑）。
`MediaType` 是 IEEE 标识，与语言无关。

⚠️ 本模块**不引入任何新依赖**（契约 §1 要求依赖写死版本，加 `psutil` 要走契约变更流程）。
全部走 stdlib；拿不到信息就如实少给字段，**绝不瞎猜**。
"""

from __future__ import annotations

import ipaddress
import json
import platform
import socket
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- 常量

#: 网卡类型（本模块自定，非契约枚举，仅用于排序与展示）
KIND_ETHERNET = "ethernet"
KIND_WLAN = "wlan"
KIND_VIRTUAL = "virtual"
KIND_LOOPBACK = "loopback"
KIND_OTHER = "other"

#: IEEE MediaType（PowerShell `Get-NetAdapter` 的 `MediaType`，与系统语言无关）
_MEDIA_ETHERNET = "802.3"
_MEDIA_WLAN = "802.11"

#: 虚拟网卡的名字特征（这些一定不是学生手机能连上的）
_VIRTUAL_HINTS = (
    "vmware",
    "virtualbox",
    "vbox",
    "hyper-v",
    "vethernet",
    "docker",
    "wsl",
    "veth",
    "loopback",
    "bluetooth",
    "蓝牙",
    "tap-",
    "tun",
    "npcap",
    "zerotier",
    "tailscale",
    "radmin",
    "hamachi",
)

#: 无线网卡的名字特征（仅当拿不到 MediaType 时才用，且对中英文都覆盖）
_WLAN_HINTS = ("wi-fi", "wifi", "wlan", "wireless", "802.11", "无线")

#: 有线网卡的名字特征
_ETHERNET_HINTS = ("ethernet", "以太网")

#: 推荐地址的偏好顺序：**有线 > 无线 > 其他**（10b F3 明确「优先有线以太网、其次 WLAN」）
_KIND_RANK = {
    KIND_ETHERNET: 0,
    KIND_WLAN: 1,
    KIND_OTHER: 2,
}

#: 私网段（RFC1918）—— 只有这些地址才可能被学生手机访问到
_PRIVATE_V4 = tuple(
    ipaddress.ip_network(net)
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_LOOPBACK_V4 = ipaddress.ip_network("127.0.0.0/8")
_LINK_LOCAL_V4 = ipaddress.ip_network("169.254.0.0/16")  # APIPA：DHCP 没拿到地址时的自赋值

#: PowerShell 调用超时（秒）。探测失败只降级，不阻塞接口。
_PS_TIMEOUT = 4.0


# --------------------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class NetInterface:
    """一块网卡上的一个 IPv4 地址。"""

    name: str
    ip: str
    kind: str = KIND_OTHER
    is_up: bool = True
    mac: str | None = None
    description: str | None = None
    source: str = "socket"  # 探测手段：powershell / ip_cmd / socket
    recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ip": self.ip,
            "kind": self.kind,
            "is_up": self.is_up,
            "mac": self.mac,
            "description": self.description,
            "source": self.source,
            "recommended": self.recommended,
        }


# --------------------------------------------------------------------------- IP 判定


def parse_v4(ip: str) -> ipaddress.IPv4Address | None:
    """把字符串解析成 IPv4 地址；非法（含 IPv6）返回 `None`。"""
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except (ValueError, AttributeError):
        return None
    return addr if addr.version == 4 else None


def is_private_ip(ip: str) -> bool:
    """是否 RFC1918 私网地址（**不含**回环与 169.254 自赋值）。"""
    addr = parse_v4(ip)
    if addr is None:
        return False
    return any(addr in net for net in _PRIVATE_V4)


def is_loopback_ip(ip: str) -> bool:
    addr = parse_v4(ip)
    return addr is not None and addr in _LOOPBACK_V4


def is_link_local_ip(ip: str) -> bool:
    addr = parse_v4(ip)
    return addr is not None and addr in _LINK_LOCAL_V4


def is_usable_lan_ip(ip: str) -> bool:
    """能否作为「给手机连的地址」：私网 + 非自赋值 + 非回环。"""
    return is_private_ip(ip) and not is_link_local_ip(ip) and not is_loopback_ip(ip)


def is_allowed_discovery_source(ip: str) -> bool:
    """UDP 探测包的来源是否允许应答（10b F2：只响应局域网私有网段）。

    ⚠️ **回环也放行**，这是一个**有意的偏离**（见交付说明偏离项）：
    10b 只写了「拒绝公网」，而真机烟测（U2）需要从 `127.0.0.1` 发一个真 UDP 包验证应答。
    回环不是公网地址，放行不违反「拒绝公网」的意图；应答内容也不含任何敏感信息
    （只有服务名/端口/是否需鉴权，见 `udp_responder.build_reply`）。
    """
    if is_loopback_ip(ip):
        return True
    return is_private_ip(ip)


# --------------------------------------------------------------------------- 类型判定


def classify(name: str, media_type: str | None = None) -> str:
    """判断网卡类型。名字特征只在拿不到 `media_type` 时兜底。"""
    mt = (media_type or "").strip()
    if mt.startswith(_MEDIA_ETHERNET):
        return KIND_ETHERNET
    if mt.startswith(_MEDIA_WLAN):
        return KIND_WLAN

    lowered = (name or "").lower()
    if any(h in lowered for h in _VIRTUAL_HINTS):
        return KIND_VIRTUAL
    if "loopback" in lowered or "回环" in (name or ""):
        return KIND_LOOPBACK
    if any(h in lowered for h in _WLAN_HINTS):
        return KIND_WLAN
    if any(h in lowered for h in _ETHERNET_HINTS):
        return KIND_ETHERNET
    # 名字里既没有虚拟特征也没有类型特征 —— 端口名（如 `eth0`/`en0`）通常是真网卡，
    # 但 802.3 系列之外的虚拟网卡也长这样，**不猜**，归 other。
    return KIND_OTHER


# --------------------------------------------------------------------------- 探测：PowerShell


def _powershell(script: str) -> Any:
    """跑一段 PowerShell 并把 stdout 当 JSON 解析。失败返回 `None`。

    `[Console]::OutputEncoding` 显式设成 UTF-8，否则中文网卡名会按 GBK 输出
    （这台机器是中文 Windows），解析出来是乱码 —— 虽然本模块优先用 `MediaType`，
    但 `name` 要展示给用户看，乱码会很难看。
    """
    if platform.system() != "Windows":  # pragma: no cover - 平台分支
        return None
    cmd = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + script,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=_PS_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("PowerShell 探测失败（降级到下一手段）：%s", exc)
        return None

    if proc.returncode != 0:
        logger.debug("PowerShell 返回码 %s，降级", proc.returncode)
        return None

    text = proc.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        logger.debug("PowerShell 输出不是合法 JSON，降级")
        return None


def _as_list(value: Any) -> list[dict]:
    """`ConvertTo-Json` 对单元素结果返回对象而非数组 —— 统一成列表。"""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _interfaces_via_powershell() -> list[NetInterface] | None:
    """用 PowerShell 拿到「地址 + 网卡类型 + 启用状态」。"""
    addrs = _powershell(
        "Get-NetIPAddress -AddressFamily IPv4 | "
        "Select-Object IPAddress,InterfaceAlias,InterfaceIndex | ConvertTo-Json -Compress"
    )
    if addrs is None:
        return None

    adapters = _powershell(
        "Get-NetAdapter | "
        "Select-Object Name,InterfaceIndex,Status,MediaType,MacAddress,InterfaceDescription | "
        "ConvertTo-Json -Compress"
    )

    by_index: dict[str, dict] = {}
    for a in _as_list(adapters):
        idx = a.get("InterfaceIndex")
        if idx is not None:
            by_index[str(idx)] = a
    by_name: dict[str, dict] = {
        str(a.get("Name")): a for a in _as_list(adapters) if a.get("Name")
    }

    out: list[NetInterface] = []
    for row in _as_list(addrs):
        ip = str(row.get("IPAddress") or "").strip()
        if parse_v4(ip) is None:
            continue
        idx = row.get("InterfaceIndex")
        alias = str(row.get("InterfaceAlias") or "").strip()
        adapter = by_index.get(str(idx)) or by_name.get(alias) or {}
        media = str(adapter.get("MediaType") or "").strip() or None
        status = str(adapter.get("Status") or "").strip()
        out.append(
            NetInterface(
                name=alias or f"if{idx}",
                ip=ip,
                kind=classify(alias, media),
                is_up=(status.lower() != "disabled") if status else True,
                mac=(str(adapter.get("MacAddress") or "").strip() or None),
                description=(str(adapter.get("InterfaceDescription") or "").strip() or None),
                source="powershell",
            )
        )

    return out or None


# --------------------------------------------------------------------------- 探测：Linux ip 命令


def _interfaces_via_ip_cmd() -> list[NetInterface] | None:  # pragma: no cover - 平台分支
    try:
        proc = subprocess.run(
            ["ip", "-o", "-4", "addr", "show"],
            capture_output=True,
            timeout=_PS_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    out: list[NetInterface] = []
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        # 形如：`2: eth0    inet 192.168.1.5/24 brd ... scope global eth0`
        if len(parts) < 4 or parts[2] != "inet":
            continue
        ip = parts[3].split("/")[0]
        if parse_v4(ip) is None:
            continue
        name = parts[1]
        out.append(NetInterface(name=name, ip=ip, kind=classify(name), source="ip_cmd"))
    return out or None


# --------------------------------------------------------------------------- 探测：socket 兜底


def primary_lan_ip() -> str | None:
    """问内核「去外网会走哪个本机地址」。

    原理：UDP `connect` **不发包**，只是在路由表里查一条路，因此
    **不需要外网真的通**（离线也能用，只要存在默认路由）。这是各语言里
    获取「本机对外地址」的标准做法，比 `gethostbyname(gethostname())` 可靠得多
    （后者在 hosts 文件被改过时会返回错的地址）。
    """
    probes = (("8.8.8.8", 53), ("223.5.5.5", 53), ("192.168.1.1", 80))
    for host, port in probes:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.3)
                sock.connect((host, port))
                ip = sock.getsockname()[0]
        except OSError:
            continue
        if is_usable_lan_ip(ip):
            return ip
    return None


def _interfaces_via_socket() -> list[NetInterface]:
    """最后兜底：只能拿到一组地址，**给不出网卡类型**（如实标 `other`）。"""
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass

    primary = primary_lan_ip()
    if primary and primary not in ips:
        ips.insert(0, primary)

    out: list[NetInterface] = []
    for ip in ips:
        if is_loopback_ip(ip):
            kind = KIND_LOOPBACK
        elif is_link_local_ip(ip):
            kind = KIND_OTHER
        else:
            kind = KIND_OTHER
        out.append(
            NetInterface(name=f"socket:{ip}", ip=ip, kind=kind, source="socket")
        )
    if not out:
        out.append(
            NetInterface(name="loopback", ip="127.0.0.1", kind=KIND_LOOPBACK, source="socket")
        )
    return out


# --------------------------------------------------------------------------- 对外


def _probe_all() -> list[NetInterface]:
    """按优先级尝试三种探测手段，返回**第一个成功的**结果。"""
    system = platform.system()
    if system == "Windows":
        got = _interfaces_via_powershell()
        if got:
            return got
    elif system == "Linux":  # pragma: no cover - 平台分支
        got = _interfaces_via_ip_cmd()
        if got:
            return got
    return _interfaces_via_socket()


def pick_recommended(interfaces: list[NetInterface]) -> NetInterface | None:
    """挑出「最可能是学生手机能连上的」地址。

    规则（与 10b F3 一致）：**已启用 + 私网 + 非自赋值 + 非回环 + 非虚拟**，
    然后按 `有线 > 无线 > 其他` 排；同一档里 IP 小的优先（结果稳定，便于测试与截图）。
    一个都不满足时返回 `None` —— 此时管理端应提示「没有可用的局域网地址」，
    **不要拿回环或虚拟网卡地址糊弄用户**。
    """
    candidates = [
        i
        for i in interfaces
        if i.is_up and is_usable_lan_ip(i.ip) and i.kind != KIND_VIRTUAL
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda i: (_KIND_RANK.get(i.kind, 9), i.ip))


def enumerate_interfaces(*, mark_recommended: bool = True) -> list[NetInterface]:
    """枚举本机 IPv4 地址（含虚拟与回环，便于排查），并标注推荐项。

    排序：推荐项最前，然后按类型档位 + IP，保证多次调用顺序稳定。
    """
    try:
        interfaces = _probe_all()
    except Exception as exc:  # noqa: BLE001 - 探测失败不能把接口打成 500
        logger.warning("网卡枚举失败，返回空列表：%s", exc)
        return []

    best = pick_recommended(interfaces) if mark_recommended else None
    best_key = (best.ip, best.name) if best else None

    def _sort_key(i: NetInterface) -> tuple:
        is_best = (i.ip, i.name) == best_key
        return (0 if is_best else 1, _KIND_RANK.get(i.kind, 9), i.ip)

    return [
        NetInterface(
            name=i.name,
            ip=i.ip,
            kind=i.kind,
            is_up=i.is_up,
            mac=i.mac,
            description=i.description,
            source=i.source,
            recommended=((i.ip, i.name) == best_key),
        )
        for i in sorted(interfaces, key=_sort_key)
    ]


class CachedInterfaces:
    """带 TTL 的网卡枚举缓存。

    为什么要缓存：`Get-NetIPAddress` 要起一个 PowerShell 进程（约 0.3–1 s），
    而 IP 变化检测每 60 s 跑一次、管理端也可能反复刷页面 —— 不缓存会造成
    「每次刷页面都起两个 PowerShell」。默认 TTL 30 s，既能及时发现改 IP，又不会打爆。
    """

    def __init__(self, ttl_seconds: float = 30.0, probe: Callable[[], list[NetInterface]] | None = None):
        self._ttl = ttl_seconds
        self._probe = probe
        self._at: float = -1e18
        self._value: list[NetInterface] = []

    def get(self, *, now: float, force: bool = False) -> list[NetInterface]:
        if force or (now - self._at) >= self._ttl or not self._value:
            self._value = (self._probe or enumerate_interfaces)()
            self._at = now
        return self._value

    def set_probe(self, probe: Callable[[], list[NetInterface]] | None) -> None:
        """替换探测函数（测试用：注入一份固定的网卡列表）。

        传 `None` = 恢复默认（真去问系统）。测试的重置逻辑靠它把注入还原，
        否则一个用例注入的假网卡会**泄漏到后面的用例**。
        """
        self._probe = probe
        self.clear()

    def clear(self) -> None:
        self._at = -1e18
        self._value = []
