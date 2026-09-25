/**
 * 内容管理 API —— WP-WEB-02（单元 / 词条 / 课文）。
 *
 * ⚠️ 本文件**只调用** `client.ts` 的 `api`，不修改它（独占文件纪律）。
 * 所有错误统一由 client 拦截并弹提示，**页面层不再重复弹**。
 * 需要特殊处理的业务码（如 `30003` 有记录不可删）在调用方 catch `BizError` 后读 `err.data`。
 *
 * 字段命名严格对齐后端实现（`backend/app/schemas/content.py`），
 * 不照《接口设计》§5 / §8 的示例抄——示例里 `require_spell: 1` 已被实现改为 `true`，
 * 详见 `docs/dev/02-实现约定与复用经验.md` §八 #6。
 */
import { api } from './client'
import type { PageResult } from './types'

// ---------------------------------------------------------------------------
// 枚举（与契约 §8 一致）
// ---------------------------------------------------------------------------

export type Subject = 'chinese' | 'english'
export type Semester = 'first' | 'second'
export type WordType = 'word' | 'phrase' | 'sentence'
export type ContentStatus = 'active' | 'inactive'

// ---------------------------------------------------------------------------
// 单元
// ---------------------------------------------------------------------------

export interface UnitItem {
  id: number
  subject: Subject
  grade: number
  semester: Semester
  name: string
  order_no: number
  remark: string | null
  status: ContentStatus
  created_at: string | null
  updated_at: string | null
  // 列表派生计数
  word_count: number
  text_count: number
  active_word_count: number
  active_text_count: number
}

export interface UnitListQuery {
  subject?: Subject
  grade?: number
  semester?: Semester
  status?: ContentStatus
  page?: number
  page_size?: number
}

export interface CreateUnitPayload {
  subject: Subject
  grade: number
  semester: Semester
  name: string
  order_no?: number
  remark?: string | null
}

/** PATCH 单元：只有 4 个字段可改（契约 §14.1 / schemas `UnitPatchIn`）。 */
export type UnitPatchPayload = Partial<{
  name: string
  order_no: number
  status: ContentStatus
  remark: string | null
}>

export function listUnits(params: UnitListQuery): Promise<PageResult<UnitItem>> {
  return api.get<PageResult<UnitItem>>('/units', { params: params as Record<string, unknown> })
}

export function createUnit(payload: CreateUnitPayload): Promise<UnitItem> {
  return api.post<UnitItem>('/units', payload, { idempotencyKey: `unit-create-${Date.now()}` })
}

export function updateUnit(id: number, payload: UnitPatchPayload): Promise<UnitItem> {
  return api.patch<UnitItem>(`/units/${id}`, payload)
}

/** 删除单元：有练习记录时后端抛 `30003`（data 含 record_count），调用方自行处理。 */
export function deleteUnit(id: number): Promise<{ unit_id: number; deleted_words: number; deleted_texts: number }> {
  return api.delete(`/units/${id}`)
}

// ---------------------------------------------------------------------------
// 词条（double role：admin 路径返回分页全字段，含 accepted）
// ---------------------------------------------------------------------------

export interface WordItem {
  id: number
  unit_id: number
  type: WordType
  en: string
  cn: string
  accepted: string[]
  phonetic: string | null
  pos: string | null
  example_en: string | null
  example_cn: string | null
  /** 契约 §4.6：API 层一律 true/false（后端示例写的 1 是错的）。 */
  require_spell: boolean
  order_no: number
  status: ContentStatus
  created_at: string | null
  updated_at: string | null
}

/** 管理端词条列表响应：分页 + 单元元信息。 */
export interface WordListResult extends PageResult<WordItem> {
  unit_id: number
  unit_name: string
  subject: Subject
}

export interface WordListQuery {
  keyword?: string
  status?: ContentStatus
  page?: number
  page_size?: number
}

export type WordPatchPayload = Partial<{
  type: WordType
  en: string
  cn: string
  accepted: string[] | null
  phonetic: string | null
  pos: string | null
  example_en: string | null
  example_cn: string | null
  require_spell: boolean
  order_no: number
  status: ContentStatus
}>

export interface BatchStatusResult {
  updated: number
  status: ContentStatus
  requested: number
}

export function listUnitWords(unitId: number, params: WordListQuery): Promise<WordListResult> {
  return api.get<WordListResult>(`/units/${unitId}/words`, { params: params as Record<string, unknown> })
}

export function patchWord(id: number, payload: WordPatchPayload): Promise<WordItem & { history_affected: boolean }> {
  return api.patch<WordItem & { history_affected: boolean }>(`/words/${id}`, payload)
}

export function batchWordStatus(ids: number[], status: ContentStatus): Promise<BatchStatusResult> {
  return api.post<BatchStatusResult>('/words/batch-status', { word_ids: ids, status })
}

/** 删除词条：有记录 → `30003`（data.record_count），调用方处理。 */
export function deleteWord(id: number): Promise<{ record_count: number }> {
  return api.delete(`/words/${id}`)
}

// ---------------------------------------------------------------------------
// 课文（double role：admin 路径返回全字段，含 recite_scope_chars）
// ---------------------------------------------------------------------------

export interface TextBrief {
  id: number
  unit_id: number
  title: string
  author: string | null
  status: ContentStatus
  order_no: number
  /** 背诵范围字数（≡ total_chars，见 §八 #47）—— 用于「背诵 N 字」展示。 */
  total_chars: number
  recite_scope_chars: number
  paragraph_count: number
  recite_paragraph_count: number
  recite_scope: number[]
  last_grade: string | null
  created_at: string | null
}

export interface Paragraph {
  idx: number
  text: string
  chars: number
  recite: boolean
}

export interface TextDetail {
  id: number
  unit_id: number
  title: string
  author: string | null
  status: ContentStatus
  order_no: number
  paragraphs: Paragraph[]
  recite_scope: number[]
  scope_snapshot: { start: number; end: number }
  total_chars: number
  content_raw: string | null
  created_at: string | null
  updated_at: string | null
}

export interface TextListResult {
  unit_id: number
  unit_name: string
  subject: Subject
  texts: TextBrief[]
}

export type TextPatchPayload = Partial<{
  title: string
  author: string | null
  order_no: number
  status: ContentStatus
}>

export function listUnitTexts(unitId: number, params: { status?: ContentStatus } = {}): Promise<TextListResult> {
  return api.get<TextListResult>(`/units/${unitId}/texts`, { params })
}

export function getText(id: number): Promise<TextDetail> {
  return api.get<TextDetail>(`/texts/${id}`)
}

export function patchText(id: number, payload: TextPatchPayload): Promise<TextDetail & { history_affected: boolean }> {
  return api.patch<TextDetail & { history_affected: boolean }>(`/texts/${id}`, payload)
}

/** 删除课文：有背诵记录 → `30003`（data.record_count）。 */
export function deleteText(id: number): Promise<{ record_count: number }> {
  return api.delete(`/texts/${id}`)
}
