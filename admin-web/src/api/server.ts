/**
 * 服务地址与运维 API —— WP-WEB-03（F11 / F12，接口设计 §4.8）。
 *
 * ⚠️ 本文件**只调用** `client.ts` 的 `api`（独占文件纪律）。
 * 字段命名严格对齐 `backend/app/api/discovery.py` 与 `app/services/discovery/__init__.py`，
 * 不照《接口设计》§3.1 的旧示例抄（`/api/ping` 的返回体在 BE-08 已按 §3.1 修正为
 * `service/version/server_name/time`，见 02-实现约定 §八 #41）。
 */
import { api } from './client'

// ---------------------------------------------------------------------------
// 类型（与 `discovery.py` / `netinfo.NetInterface.to_dict()` 逐字段对应）
// ---------------------------------------------------------------------------

/** 一块网卡上一个 IPv4 地址。`recommended=true` 表示服务端挑的「学生最可能连得上」的地址。 */
export interface NetInterface {
  name: string
  ip: string
  kind: string
  is_up: boolean
  mac: string | null
  description: string | null
  source: string
  recommended: boolean
}

/** `GET /api/server/info` 的完整视图（F11 / F12）。 */
export interface ServerInfo {
  interfaces: NetInterface[]
  current_ip: string | null
  current_url: string | null
  /** 二维码内容：`bjsys://{ip}:{port}`，App 扫码识别。无可用地址时为 `null`。 */
  qr_content: string | null
  discovery_port: number
  last_ip_change_at: string | null
  previous_ips: string[]
  server_name: string
  degraded_reasons: unknown[]
  service: string
  version: string
  app_port: number
  discovery_port_extra?: number
  broadcast_enabled?: boolean
  degraded?: unknown[]
  backup?: unknown
}

/** `GET /api/ping` 的返回体（免鉴权、要快，见接口设计 §3.1）。 */
export interface PingResult {
  service: string
  version: string
  server_name: string
  time: string
}

// ---------------------------------------------------------------------------
// 请求
// ---------------------------------------------------------------------------

/** 服务地址与运行状态。`refresh=1` 强制重新探测网卡（约 0.3–1s）。 */
export function getServerInfo(refresh = false): Promise<ServerInfo> {
  return api.get<ServerInfo>('/server/info', { params: refresh ? { refresh: '1' } : {} })
}

/** 极简探活（免鉴权）。后端按接口设计 §3.1 定型返回 `service/version/server_name/time`。 */
export function ping(): Promise<PingResult> {
  return api.get<PingResult>('/ping')
}
