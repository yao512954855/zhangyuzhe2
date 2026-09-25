/**
 * 导入 API —— WP-WEB-02（F11 ~ F16）。
 *
 * 两步式（契约 §一 / 接口设计 §6）：先 `preview`（解析校验、存 preview_token、不落库），
 * 再 `commit`（凭一次性 token 入库，后端 pop 防重复提交）。
 *
 * 字段命名严格对齐 `backend/app/services/import_service.py` 的真实返回：
 * - words preview 返回 `summary{total,valid,invalid,duplicated}` + `new_units` + `valid_rows` + `invalid_rows` + `error_report_url`
 *   （`valid_rows` 是 2026-09-25 按契约 §6.2 补齐的：此前只定义 `invalid_rows`，
 *   于是「预览」只能看到**错在哪**、看不到**会进什么**，等于没预览）
 * - texts preview 额外返回 `texts[]`（每段 paragraphs，供背诵范围勾选）+ `pinyin_issues`
 * - commit 返回 `created/updated/skipped/units_created`（words）或 `created/updated/units_created`（texts）
 */
import { api } from './client'
import type { Semester, Subject } from './content'

export type ImportType = 'words' | 'texts'
export type DuplicateStrategy = 'overwrite' | 'skip'

// ---------------------------------------------------------------------------
// preview 响应结构
// ---------------------------------------------------------------------------

export interface ImportSummary {
  total: number
  valid: number
  invalid: number
  duplicated?: number
}

/** 预览阶段将新建的单元（F12 要求用户确认是否自动创建）。 */
export interface NewUnit {
  subject: Subject
  grade: number
  semester: Semester
  name: string
  order_no?: number
  row?: number
}

/** 校验失败行：行号 + 原因（F11）。 */
export interface InvalidRow {
  row: number
  reason: string
}

/**
 * 校验**通过**的行（契约 §6.2 `valid_rows`）——「将入库什么」的逐行明细。
 *
 * 只含关键字段：后端**故意不返回** `accepted`（可接受答案，最多 20 条/词）、
 * 例句、音标、词性 —— 那些留在 preview token 里，不入响应，
 * 否则一个几百行的文件响应会膨胀好几倍。
 */
export interface ValidRow {
  row: number
  subject: Subject
  grade: number
  semester: Semester
  unit_name: string
  type: string
  en: string
  cn: string
}

export interface WordsPreview {
  preview_token: string
  summary: ImportSummary
  new_units: NewUnit[]
  valid_rows: ValidRow[]
  invalid_rows: InvalidRow[]
  error_report_url: string
}

export interface PreviewParagraph {
  idx: number
  text: string
  chars: number
  recite: boolean
}

/** 课文预览项（F13）：段落列表 + 默认背诵范围。 */
export interface PreviewText {
  row: number
  subject: Subject
  grade: number
  semester: Semester
  unit_name: string
  unit_order_no?: number
  title: string
  author: string | null
  content_raw: string
  paragraphs: PreviewParagraph[]
  recite_scope: number[]
  pinyin_check: string
  total_chars: number
  mismatches?: unknown[]
  mismatch_positions?: number[]
  ok: boolean
}

export interface PinyinIssue {
  row: number
  title: string
  positions: number[]
  reason: string
  details?: unknown[]
}

export interface TextsPreview {
  preview_token: string
  summary: ImportSummary
  new_units: NewUnit[]
  texts: PreviewText[]
  invalid_rows: InvalidRow[]
  pinyin_issues: PinyinIssue[]
}

// ---------------------------------------------------------------------------
// commit 响应结构
// ---------------------------------------------------------------------------

export interface WordsCommitResult {
  created: number
  updated: number
  skipped: number
  units_created: number
}

export interface TextsCommitResult {
  created: number
  updated: number
  units_created: number
}

// ---------------------------------------------------------------------------
// 请求
// ---------------------------------------------------------------------------

/** 下载模板：返回 Blob（由调用方触发生成下载）。 */
export function downloadTemplate(type: ImportType): Promise<Blob> {
  return api.get<Blob>(`/import/template/${type}`, { responseType: 'blob' })
}

/**
 * 归一化「后端返回的绝对路径」为 `client.baseURL` 下的相对路径。
 *
 * ⚠️ 后端 `error_report_url` 返回的是 **`/api/import/report/{token}`**（含 `/api` 前缀），
 * 而 `client.ts` 的 `baseURL` 已经是 `/api`。直接把它丢给 `api.get()`，
 * axios 会拼成 **`/api/api/import/report/{token}`** —— 必然 404。
 * （已实测：`axios.getUri({baseURL:'/api', url:'/api/import/report/x'})`
 *   → `/api/api/import/report/x`。这也是 U6「可下载错误报告」的失败根因。）
 *
 * 只去掉**开头**的一个 `/api`；绝对 URL（`http(s)://`）原样返回，
 * 免得把一个跨域地址拆坏。
 */
export function toApiPath(path: string): string {
  const raw = (path ?? '').trim()
  if (!raw) return raw
  if (/^https?:\/\//i.test(raw)) return raw
  return raw.startsWith('/api/') ? raw.slice(4) : raw
}

/** 下载导入错误报告（F11 / U6）。入参是后端 preview 返回的 `error_report_url`。 */
export function downloadErrorReport(errorReportUrl: string): Promise<Blob> {
  return api.get<Blob>(toApiPath(errorReportUrl), { responseType: 'blob' })
}

export function previewWords(file: File): Promise<WordsPreview> {
  const form = new FormData()
  form.append('file', file)
  return api.post<WordsPreview>('/import/words/preview', form)
}

export function commitWords(
  previewToken: string,
  createMissingUnits: boolean,
  onDuplicate: DuplicateStrategy,
): Promise<WordsCommitResult> {
  return api.post<WordsCommitResult>('/import/words/commit', {
    preview_token: previewToken,
    create_missing_units: createMissingUnits,
    on_duplicate: onDuplicate,
  })
}

export function previewTexts(file: File): Promise<TextsPreview> {
  const form = new FormData()
  form.append('file', file)
  return api.post<TextsPreview>('/import/texts/preview', form)
}

/** commit 时回传**调整后的**背诵范围（按 Excel 行号映射，见 schemas.TextScopeIn）。 */
export interface TextCommitScope {
  row: number
  recite_scope: number[]
}

export function commitTexts(previewToken: string, texts: TextCommitScope[]): Promise<TextsCommitResult> {
  return api.post<TextsCommitResult>('/import/texts/commit', {
    preview_token: previewToken,
    texts,
  })
}
