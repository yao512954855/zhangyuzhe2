/**
 * 班级 / 学生 / 名单导入 API —— WP-WEB-03（F1 ~ F6 / U1 ~ U6，接口设计 §4.2 / §4.3 / §4.4）。
 *
 * ⚠️ 本文件**只调用** `client.ts` 的 `api`，不修改它（独占文件纪律）。
 * 所有错误统一由 client 拦截并弹提示，**页面层不再重复弹**。
 * 需要特殊处理的业务码（如 `CLASS_NOT_FOUND` 写审计等）在调用方 catch `BizError` 后读 `err.data`。
 *
 * 字段命名严格对齐后端实现（`backend/app/schemas/classes.py`、`student.py`、
 * `app/api/students.py`、`app/services/user_service.py`），不照《接口设计》§4 的示例抄。
 *
 * ⚠️ 二进制字段的坑（WEB-02 已踩过，见 02-实现约定 §九「管理端联调」）：
 * 后端给的 `error_report_url` / `credentials_url` 自带 `/api` 前缀（如
 * `/api/import/report/{token}`），而 `client.ts` 的 `baseURL` 也是 `/api`
 * → 会拼成 `/api/api/...` 404。所以**一切后端返回的 URL 都过 `toApiPath()` 归一化**
 * （剥掉前导 `/api`；`http(s)://` 绝对地址原样保留）。
 */
import { api } from './client'
import type { PageResult } from './types'

export type Semester = 'first' | 'second'
export type RegisterSource = 'import' | 'self'
export type UserStatus = 'active' | 'disabled'
export type ClassStatus = 'active' | 'archived'

// ---------------------------------------------------------------------------
// 班级
// ---------------------------------------------------------------------------

export interface ClassItem {
  id: number
  name: string
  grade: number
  semester: Semester
  school_year: string | null
  remark: string | null
  status: ClassStatus
  /** 列表派生：本班在读学生数（含停用）。 */
  student_count: number
  created_at: string | null
  updated_at: string | null
}

export interface ClassListQuery {
  grade?: number
  semester?: Semester
  /** `active` / `archived` / `all`；空串表示全部。 */
  status?: ClassStatus | 'all' | ''
  keyword?: string
  page?: number
  pageSize?: number
}

export interface ClassListResult extends PageResult<ClassItem> {
  /** 全校未分班学生数（顶层，与每班 `student_count` 不同维度）。 */
  unassigned_count: number
}

export interface CreateClassPayload {
  name: string
  grade: number
  semester: Semester
  school_year?: string | null
  remark?: string | null
}

export type ClassPatchPayload = Partial<{
  name: string
  remark: string | null
  status: ClassStatus
}>

export function listClasses(params: ClassListQuery = {}): Promise<ClassListResult> {
  const q: Record<string, unknown> = {
    page: params.page ?? 1,
    page_size: params.pageSize ?? 20,
  }
  if (params.grade) q.grade = params.grade
  if (params.semester) q.semester = params.semester
  if (params.status) q.status = params.status
  if (params.keyword) q.keyword = params.keyword
  return api.get<ClassListResult>('/classes', { params: q })
}

// ---------------------------------------------------------------------------
// 班级选项（下拉「一次拿全」专用）
//
// ⚠️ 下拉**不要**用 `listClasses({pageSize:200})` 去绕分页：契约 §5.1 规定
// `page_size ≤ 100`（`backend/app/core/pagination.py` 的 `PAGE_SIZE_MAX`），超限后端回
// `10001` 参数非法。班级一多，下拉就整页拉空 —— 而 `ClassOverview` / `HomeworkList`
// 是「下拉为空 = 功能不可用」。所以下拉一律走本函数（后端 `/api/classes/options`
// 是「一次给全」语义，上限 500）。**2026-09-29 修**（见 docs/dev/02 §八 #93）。
// ---------------------------------------------------------------------------

export interface ClassOptionItem {
  id: number
  name: string
  grade: number
  semester: Semester
}

export interface ClassOptionsQuery {
  grade?: number
  semester?: Semester
  /** 缺省 / 空 → 只看活跃班级；`all` → 含已归档（用于筛「归档班里的学生」）。 */
  status?: ClassStatus | 'all' | ''
}

export interface ClassOptionsResult {
  items: ClassOptionItem[]
  total: number
}

export function listClassOptions(params: ClassOptionsQuery = {}): Promise<ClassOptionsResult> {
  const q: Record<string, unknown> = {}
  if (params.grade) q.grade = params.grade
  if (params.semester) q.semester = params.semester
  if (params.status) q.status = params.status
  return api.get<ClassOptionsResult>('/classes/options', { params: q })
}

export function createClass(payload: CreateClassPayload): Promise<ClassItem> {
  return api.post<ClassItem>('/classes', payload, {
    idempotencyKey: `class-create-${Date.now()}`,
  })
}

export function updateClass(id: number, payload: ClassPatchPayload): Promise<ClassItem> {
  return api.patch<ClassItem>(`/classes/${id}`, payload)
}

/** 导出学生账号对照表：带 `token` 时含初始密码（导入快照，30 分钟有效），不带则为无密码名单。 */
export function exportClassCredentials(classId: number, token?: string | null): Promise<Blob> {
  const raw = token
    ? `/classes/${classId}/students/credentials?token=${encodeURIComponent(token)}`
    : `/classes/${classId}/students/credentials`
  return api.get<Blob>(toApiPath(raw), { responseType: 'blob' })
}

// ---------------------------------------------------------------------------
// 学生
// ---------------------------------------------------------------------------

export interface StudentItem {
  id: number
  username: string
  real_name: string
  class_id: number | null
  class_name: string | null
  register_source: RegisterSource
  status: UserStatus
  preferred_grade: number | null
  preferred_semester: Semester | null
  last_login_at: string | null
  created_at: string | null
  /** 派生：累计答题条数（含空识别）。 */
  practice_count: number
  /** 派生：契约 §8.1 口径 `(exact+loose+fuzzy)/(总数−empty)`，分母 0 → 0。 */
  accuracy: number
}

export interface StudentListQuery {
  /** 传 `null` 表示筛选「未分班」（后端 `_parse_class_filter` 接受 `null`/`unassigned`）。 */
  classId?: number | null
  status?: UserStatus | ''
  registerSource?: RegisterSource | ''
  keyword?: string
  page?: number
  pageSize?: number
}

export interface StudentListResult extends PageResult<StudentItem> {
  /** 全校未分班学生数（顶层）。 */
  unassigned_count: number
}

export function listStudents(params: StudentListQuery = {}): Promise<StudentListResult> {
  const q: Record<string, unknown> = {
    page: params.page ?? 1,
    page_size: params.pageSize ?? 20,
  }
  if (params.classId === null) q.class_id = 'unassigned'
  else if (typeof params.classId === 'number') q.class_id = params.classId
  if (params.status) q.status = params.status
  if (params.registerSource) q.register_source = params.registerSource
  if (params.keyword) q.keyword = params.keyword
  return api.get<StudentListResult>('/students', { params: q })
}

export interface StudentPatchPayload {
  real_name?: string
  status?: UserStatus
  /** `null` = 移出班级（进入未分班）。字段不传 = 不改班级。三态见 schemas/student.py。 */
  class_id?: number | null
}

export function patchStudent(id: number, payload: StudentPatchPayload): Promise<StudentItem> {
  return api.patch<StudentItem>(`/students/${id}`, payload)
}

/** 单个归班：`class_id=null` 表示移出班级。 */
export function assignClass(studentId: number, classId: number | null): Promise<StudentItem> {
  return api.post<StudentItem>(`/students/${studentId}/assign-class`, { class_id: classId })
}

export interface BatchAssignResult {
  success_count: number
  failed_count: number
  success_ids: number[]
  failed: { student_id: number; code: number; message: string }[]
  /** 操作后全校未分班人数。 */
  unassigned_count: number
}

/** 批量归班：`class_id=null` 表示批量移出班级。部分失败不回滚整批，逐条给原因。 */
export function batchAssignClass(
  studentIds: number[],
  classId: number | null,
): Promise<BatchAssignResult> {
  return api.post<BatchAssignResult>('/students/batch-assign-class', {
    student_ids: studentIds,
    class_id: classId,
  })
}

/** 重置学生密码（6–20 位，允许纯数字）。 */
export function resetStudentPassword(
  studentId: number,
  newPassword: string,
): Promise<{ student_id: number; reset: boolean }> {
  return api.post(`/students/${studentId}/reset-password`, { new_password: newPassword })
}

// ---------------------------------------------------------------------------
// 名单导入（两步式，与内容导入同机理；接口由 `app/api/students.py` 持有）
// ---------------------------------------------------------------------------

export interface StudentImportSummary {
  total: number
  valid: number
  invalid: number
  duplicated: number
}

export interface StudentInvalidRow {
  row: number
  reason: string
  raw?: unknown[]
}

export interface StudentDuplicatedRow {
  row: number
  username: string
  user_id: number
  action: 'create' | 'update'
}

export interface StudentImportPreview {
  preview_token: string
  summary: StudentImportSummary
  invalid_rows: StudentInvalidRow[]
  duplicated_rows: StudentDuplicatedRow[]
  /** 自带 `/api` 前缀 → 下载前必须过 `toApiPath()`。 */
  error_report_url: string
}

export interface StudentImportCommitResult {
  created: number
  updated: number
  /** 被跳过的非法行数（= 预览时的 invalid_rows 数）。 */
  skipped: number
  classes: number[]
  /** 自带 `/api` 前缀 → 下载前必须过 `toApiPath()`。含导入那一刻的明文初始密码快照。 */
  credentials_url: string
}

/** 下载学生名单导入模板（xlsx，带班级下拉）。 */
export function downloadStudentTemplate(): Promise<Blob> {
  return api.get<Blob>('/students/import/template', { responseType: 'blob' })
}

export function previewStudentImport(file: File): Promise<StudentImportPreview> {
  const form = new FormData()
  form.append('file', file)
  return api.post<StudentImportPreview>('/students/import', form)
}

export function commitStudentImport(
  previewToken: string,
  defaultPassword?: string,
): Promise<StudentImportCommitResult> {
  return api.post<StudentImportCommitResult>('/students/import/commit', {
    preview_token: previewToken,
    default_password: defaultPassword,
  })
}

/** 下载导入错误明细（xlsx）；入参是后端 `error_report_url`（已带 `/api` 前缀）。 */
export function downloadImportErrorReport(errorReportUrl: string): Promise<Blob> {
  return api.get<Blob>(toApiPath(errorReportUrl), { responseType: 'blob' })
}

/** 下载账号-初始密码对照表（xlsx）；入参是后端 `credentials_url`（已带 `/api` 前缀）。 */
export function downloadCredentials(credentialsUrl: string): Promise<Blob> {
  return api.get<Blob>(toApiPath(credentialsUrl), { responseType: 'blob' })
}

// ---------------------------------------------------------------------------
// 归一化「后端返回的绝对路径」为 `client.baseURL` 下的相对路径。
//
// ⚠️ 后端 `error_report_url` / `credentials_url` 返回的是 **`/api/...`**（含 `/api` 前缀），
// 而 `client.ts` 的 `baseURL` 已经是 `/api`。直接把它丢给 `api.get()`，
// axios 会拼成 **`/api/api/...`** —— 必然 404（WEB-02 已踩，编号 #63）。
// 只去掉**开头**的一个 `/api`；绝对 URL（`http(s)://`）原样返回，免得把一个跨域地址拆坏。
// ---------------------------------------------------------------------------

export function toApiPath(path: string): string {
  const raw = (path ?? '').trim()
  if (!raw) return raw
  if (/^https?:\/\//i.test(raw)) return raw
  return raw.startsWith('/api/') ? raw.slice(4) : raw
}
