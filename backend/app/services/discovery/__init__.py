"""服务发现状态 —— WP-BE-08 F3 / F4 / F12。

三块职责，都很小但都是「管理端要显示的东西」：

| 关注点 | 字段 | 持久化 |
|---|---|---|
| 当前服务地址 | `current_ip` | ✅ 存 `settings` 的 `discovery_state` |
| 上次 IP 变化时间 | `last_ip_change_at` | ✅ 同上 |
| 历史地址 | `previous_ips` | ✅ 同上（最多 5 个，供排查） |
| 降级告警 | `degraded_reasons` | ❌ 只存内存（进程重启即清，见下） |

### 状态存在哪：**与数据库同目录的一个 JSON 文件**（`discovery_state.json`）

一开始把状态存进了 `settings` 表的 `discovery_state` 键，跑全量回归时被 BE-01 的
用例打回：`test_be01_bootstrap::test_u1` 断言「`settings` 恰好 20 条（种子值）」，
而运行期状态一写进去就变成 21 条 —— **配置表被当成了状态表**。

三条理由说明独立的 JSON 文件才是对的做法：

1. `settings` 的语义是**配置**（用户可改、走 `SettingsService`、新增要走迁移脚本，
   契约 §9.3）。「当前 IP 是多少」不是配置，是**运行期观测结果**。
2. 10b 给 BE-08 的独占文件清单里**没有**迁移脚本，往 `settings` 里塞运行期键
   等于绕过 §9.3，属于灰色操作。
3. 状态文件丢了就丢了（下一次心跳会重新写），而配置表丢失是要命的 ——
   两者用不同的存储介质，风险自然隔离。

写入是**原子的**（先写 `.tmp` 再 `os.replace`），避免断电时留下半截 JSON。

### 为什么 `degraded_reasons` 不持久化
它是「此刻服务处于什么降级状态」的**实时视图**：外网断了标记，外网恢复要自动消失。
持久化反而会造成「重启后横幅还在，但问题早已恢复」的假告警。
进程重启后由启动自检（F13）重新评估一次，比读旧状态更准。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.services.discovery.netinfo import (
    CachedInterfaces,
    NetInterface,
    enumerate_interfaces,
    is_private_ip,
    pick_recommended,
    primary_lan_ip,
)

logger = get_logger(__name__)

#: 运行期状态文件名（与数据库同目录）
STATE_FILE_NAME = "discovery_state.json"

#: 服务显示名。契约 §9.1 的 `.env` 清单里**没有**这一项（见交付说明偏离项），
#: 因此用可选环境变量覆盖，默认值仅作展示用途。
DEFAULT_SERVER_NAME = "背记系统"

#: 保留多少个历史地址
_MAX_PREVIOUS = 5

#: UDP 发现端口（契约 §11 / 接口设计 §3.2，与 HTTP 端口无关）
DISCOVERY_PORT = 9527


def server_name() -> str:
    """服务显示名（`SERVER_NAME` 环境变量覆盖）。"""
    return (os.environ.get("SERVER_NAME") or DEFAULT_SERVER_NAME).strip() or DEFAULT_SERVER_NAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_port() -> int:
    """HTTP 服务端口（`.env` 的 `APP_PORT`）。延迟取，避免 import 期就要求配置齐全。"""
    from app.core.config import get_settings

    return int(get_settings().APP_PORT)


def state_file() -> Path:
    """运行期状态文件路径（与数据库同目录，便于整目录搬迁/备份）。"""
    from app.core.config import get_settings

    return get_settings().db_file.parent / STATE_FILE_NAME


class DiscoveryState:
    """服务地址与降级状态的**进程内单例**。

    线程安全：内部一把 `RLock`。后台调度线程与管理端请求会并发访问。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current_ip: str | None = None
        self._last_ip_change_at: str | None = None
        self._previous_ips: list[str] = []
        self._degraded: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._interfaces = CachedInterfaces(ttl_seconds=30.0)

    # ------------------------------------------------------------------ 持久化

    def load(self, *, force: bool = False) -> None:
        """从状态文件载入历史地址。**失败不抛**（状态是锦上添花，不能拖垮启动）。"""
        with self._lock:
            if self._loaded and not force:
                return
            self._loaded = True
            try:
                raw = json.loads(state_file().read_text(encoding="utf-8"))
            except FileNotFoundError:
                return  # 首次运行：正常情况
            except (OSError, ValueError) as exc:
                logger.warning("discovery_state 读取失败（忽略）：%s", exc)
                return
            if not isinstance(raw, dict):
                return
            current = raw.get("current_ip")
            self._current_ip = str(current) if current else None
            changed = raw.get("last_ip_change_at")
            self._last_ip_change_at = str(changed) if changed else None
            prev = raw.get("previous_ips")
            if isinstance(prev, list):
                self._previous_ips = [str(p) for p in prev][:_MAX_PREVIOUS]

    def _save(self) -> None:
        """原子写回状态文件。失败只告警 —— IP 变了但存不下，不该让接口报错。"""
        payload = {
            "current_ip": self._current_ip,
            "last_ip_change_at": self._last_ip_change_at,
            "previous_ips": self._previous_ips,
            "updated_at": _utc_now(),
        }
        path = state_file()
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # `os.replace` 在同一文件系统内是原子的：要么旧的要么新的，不会有半截文件
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("discovery_state 写入失败（忽略）：%s", exc)

    # ------------------------------------------------------------------ 地址

    @property
    def current_ip(self) -> str | None:
        return self._current_ip

    @property
    def last_ip_change_at(self) -> str | None:
        return self._last_ip_change_at

    @property
    def previous_ips(self) -> list[str]:
        return list(self._previous_ips)

    def observe(self, ip: str | None, *, now: str | None = None) -> bool:
        """记录一次「当前地址」观测，返回**是否发生了变化**。

        - 首次观测（此前为 `None`）→ 视为初始化，**不算变化**，不写审计
          （否则每次冷启动都会报一条「IP 变了」的假告警）。
        - 地址变成 `None`（例如网线拔了）→ 也算变化，但在 `previous_ips` 里保留旧值。
        """
        with self._lock:
            old = self._current_ip
            if old == ip:
                return False
            self._current_ip = ip
            if old is not None:
                if old not in self._previous_ips:
                    self._previous_ips.insert(0, old)
                self._previous_ips = self._previous_ips[:_MAX_PREVIOUS]
                self._last_ip_change_at = now or _utc_now()
            self._loaded = True
        self._save()
        return old is not None

    def detect_current_ip(self, *, now_ts: float, force: bool = False) -> str | None:
        """探测一次当前推荐地址（走带 TTL 的网卡缓存）。"""
        interfaces = self._interfaces.get(now=now_ts, force=force)
        best = pick_recommended(interfaces)
        if best is not None:
            return best.ip
        # 没有「可用局域网地址」时退回探针值；探针拿不到才算真的没有
        return primary_lan_ip()

    def refresh(self, *, now_ts: float, force: bool = False) -> bool:
        """探测并记录一次，返回地址是否变化（F4 的核心调用）。"""
        ip = self.detect_current_ip(now_ts=now_ts, force=force)
        return self.observe(ip)

    def interfaces(self, *, now_ts: float, force: bool = False) -> list[NetInterface]:
        return list(self._interfaces.get(now=now_ts, force=force))

    def clear_interface_cache(self) -> None:
        self._interfaces.clear()

    def set_interface_probe(self, probe) -> None:
        """注入网卡探测函数（测试用），见 `netinfo.CachedInterfaces.set_probe`。"""
        self._interfaces.set_probe(probe)

    def reset(self) -> None:
        """清空内存状态（测试用）。

        ⚠️ 刻意做成**实例方法**而不是「重新赋值 `discovery.state`」：
        调度器与 UDP 应答器在 import 时就抓住了这个对象，
        换掉模块级变量会让它们继续持有旧实例，出现「两个 state 各说各话」的诡异现象。
        """
        with self._lock:
            self._current_ip = None
            self._last_ip_change_at = None
            self._previous_ips = []
            self._degraded = {}
            self._loaded = False
        # 注入的假网卡也要一起还原，否则会泄漏到下一个用例
        self._interfaces.set_probe(None)

    # ------------------------------------------------------------------ 降级告警

    def mark_degraded(self, kind: str, detail: str) -> None:
        """登记一条降级原因（幂等：同 `kind` 覆盖 detail，不重复堆）。"""
        with self._lock:
            if kind not in self._degraded:
                logger.warning("服务降级：%s —— %s", kind, detail)
            self._degraded[kind] = {"kind": kind, "detail": detail, "at": _utc_now()}

    def clear_degraded(self, kind: str) -> None:
        with self._lock:
            if self._degraded.pop(kind, None) is not None:
                logger.info("服务降级已恢复：%s", kind)

    def degraded_reasons(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._degraded.values()]

    @property
    def degraded(self) -> bool:
        return bool(self._degraded)

    # ------------------------------------------------------------------ 视图

    def current_url(self) -> str | None:
        ip = self._current_ip
        if not ip:
            return None
        return f"http://{ip}:{http_port()}"

    def qr_content(self) -> str | None:
        """二维码内容（F5：服务端只产字符串，二维码由管理端渲染）。"""
        ip = self._current_ip
        if not ip:
            return None
        return f"bjsys://{ip}:{http_port()}"

    def snapshot(self, *, now_ts: float | None = None, force_probe: bool = False) -> dict[str, Any]:
        """给 `/api/server/info` 用的完整视图。"""
        import time as _time

        ts = _time.monotonic() if now_ts is None else now_ts
        interfaces = self.interfaces(now_ts=ts, force=force_probe)
        return {
            "interfaces": [i.to_dict() for i in interfaces],
            "current_ip": self._current_ip,
            "current_url": self.current_url(),
            "qr_content": self.qr_content(),
            "discovery_port": DISCOVERY_PORT,
            "last_ip_change_at": self._last_ip_change_at,
            "previous_ips": self.previous_ips,
            "server_name": server_name(),
            "degraded_reasons": self.degraded_reasons(),
        }


#: 进程内单例
state = DiscoveryState()


__all__ = [
    "DEFAULT_SERVER_NAME",
    "DISCOVERY_PORT",
    "DiscoveryState",
    "STATE_FILE_NAME",
    "enumerate_interfaces",
    "http_port",
    "is_private_ip",
    "server_name",
    "state",
    "state_file",
]
