/**
 * AI 能力模式开关 —— 批次 D（消费批次 A 的接口）。
 *
 * ⚠️ 本文件**只调用** `client.ts` 的 `api`（独占文件纪律）。
 * 字段命名严格对齐《01-契约冻结》§5（`GET /api/capabilities` / `PUT /api/capabilities/mode`）。
 */
import { api } from './client'

// ---------------------------------------------------------------------------
// 类型（与 capabilities 接口逐字段对应）
// ---------------------------------------------------------------------------

/** 单个能力引擎的实际状态。 */
export interface EngineStatus {
  /** 该能力实际生效模式 */
  mode: 'local' | 'hybrid' | 'cloud'
  /** .env 部署级锁定 */
  locked: boolean
  /** 本地引擎是否就绪（模型齐全 + 包可 import 的轻量探活） */
  local_ready: boolean
  /** 本地引擎详情 / 未就绪原因 */
  local_detail: string
}

/** `GET /api/capabilities` 的完整视图。 */
export interface Capabilities {
  /** 全局设置值 */
  mode: 'local' | 'hybrid' | 'cloud'
  engines: {
    tts: EngineStatus
    ocr: EngineStatus
    asr: EngineStatus
  }
}

/** 模式取值（与 `mode` 字段、PUT 请求体共用）。 */
export type CapabilityMode = Capabilities['mode']

// ---------------------------------------------------------------------------
// 请求
// ---------------------------------------------------------------------------

/** 读取当前 AI 能力模式与三能力的实际状态。 */
export function getCapabilities(): Promise<Capabilities> {
  return api.get<Capabilities>('/capabilities')
}

/** 写入全局模式。`client.ts` 在非零 code 时自动弹 BizError，本层不重复处理。 */
export function setMode(mode: CapabilityMode): Promise<Capabilities> {
  return api.put<Capabilities>('/capabilities/mode', { mode })
}
