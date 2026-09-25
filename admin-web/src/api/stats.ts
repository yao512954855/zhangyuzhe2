/**
 * 数据看板 / 申诉 / 导出 API —— WP-WEB-03（F7 ~ F10，接口设计 §4.7 / §4.8 / §8.9）。
 *
 * ⚠️ 本文件**只调用** `client.ts` 的 `api`（独占文件纪律）。
 * 所有数字口径由后端 `StatsService` 统一给出（正确率走 `UserService._accuracy` 转发，
 * 前端**绝不重算** —— 契约 §13 禁止清单 #8）。
 *
 * 字段命名严格对齐 `backend/app/services/stats_service.py`、`app/api/stats.py`、
 * `app/api/appeals.py`，不照《接口设计》§4.7/§8.9 的示例抄。
 */
import { api } from './client'
import type { PageResult } from './types'
import { toApiPath } from './students'

// ===========================================================================
// 班级概览（F7）
// ===========================================================================

export interface TrendPoint {
  date: string
  active_students: number
  session_count: number
  item_count: number
  counted_count: number
  correct_count: number
  /** 当日正确率（比例，0~1）。 */
  accuracy: number
}

export interface TopWrongWord {
  word_id: number
  en: string
  cn: string
  unit_id: number | null
  unit_name: string | null
  error_count: number
  student_count: number
}

export interface ClassOverview {
  class_id: number
  class_name: string
  grade: number
  semester: string
  school_year: string | null
  status: string
  student_count: number
  active_student_count: number
  today_active_students: number
  today_session_count: number
  today_item_count: number
  today_date: string
  date_from: string
  date_to: string
  answer_count: number
  counted_count: number
  correct_count: number
  accuracy: number
  avg_answer_ms: number | null
  avg_session_ms: number | null
  /** 完成率（词条维度，0~1）。 */
  completion_ratio: number
  practiced_word_count: number
  scope_word_count: number
  trend: TrendPoint[]
  top_wrong_words: TopWrongWord[]
}

export interface ClassOverviewQuery {
  date_from?: string
  date_to?: string
  top_n?: number
}

export function getClassOverview(
  classId: number,
  params: ClassOverviewQuery = {},
): Promise<ClassOverview> {
  return api.get<ClassOverview>(`/stats/class/${classId}`, {
    params: params as Record<string, unknown>,
  })
}

// ===========================================================================
// 单元掌握度（F8）
// ===========================================================================

export interface UnfinishedStudent {
  student_id: number
  username: string
  real_name: string
  status: string
  practiced_words: number
  mastered_words: number
  missing_words: number
  accuracy: number
}

export interface HardWord {
  word_id: number
  en: string
  cn: string
  type: string
  answer_count: number
  counted_count: number
  correct_count: number
  error_count: number
  /** 正确率（比例，0~1）。 */
  accuracy: number
}

export interface UnitOverview {
  unit_id: number
  unit_name: string
  subject: string
  grade: number
  semester: string
  status: string
  word_total: number
  student_count: number
  answer_count: number
  counted_count: number
  correct_count: number
  accuracy: number
  practiced_word_count: number
  completion_ratio: number
  mastered_word_count: number
  mastered_pair_count: number
  mastered_ratio: number
  unfinished_students: UnfinishedStudent[]
  hard_words: HardWord[]
}

export function getUnitOverview(
  unitId: number,
  params: { hard_word_limit?: number } = {},
): Promise<UnitOverview> {
  return api.get<UnitOverview>(`/stats/unit/${unitId}`, {
    params: params as Record<string, unknown>,
  })
}

// ===========================================================================
// 学生个人报告（F9）
// ===========================================================================

export interface RecentSession {
  session_id: number
  content_type: string
  unit_id: number | null
  unit_name: string | null
  strategy: string | null
  total_items: number
  correct_items: number
  hint_count: number
  score: number | null
  status: string
  started_at: string | null
  ended_at: string | null
}

export interface RecitationRecord {
  recitation_id: number
  text_id: number | null
  text_title: string | null
  grade: string | null
  score: number | null
  covered_ratio: number | null
  total_chars: number
  matched_chars: number
  duration_ms: number
  first_voice_ms: number | null
  finish_reason: string | null
  restart_count: number
  created_at: string | null
}

export interface WrongWord {
  word_id: number
  en: string
  cn: string
  unit_id: number | null
  unit_name: string | null
  answer_count: number
  counted_count: number
  correct_count: number
  error_count: number
  accuracy: number
}

export interface StudentReport {
  student_id: number
  username: string
  real_name: string
  status: string
  class_id: number | null
  class_name: string | null
  register_source: string
  created_at: string | null
  last_login_at: string | null
  practice_days: number
  session_count: number
  finished_session_count: number
  answer_count: number
  counted_count: number
  correct_count: number
  accuracy: number
  mastered_words: number
  streak_days: number
  recitation_count: number
  recent_sessions: RecentSession[]
  recitations: RecitationRecord[]
  wrong_word_total: number
  wrong_words: WrongWord[]
}

export function getStudentReport(
  studentId: number,
  params: { recent_limit?: number } = {},
): Promise<StudentReport> {
  return api.get<StudentReport>(`/stats/student/${studentId}`, {
    params: params as Record<string, unknown>,
  })
}

// ===========================================================================
// 申诉复核（F10）
// ===========================================================================

export interface AppealItem {
  id: number
  appeal_id: number
  session_id: number | null
  student_id: number
  word_id: number | null
  mode: string | null
  step: number | null
  /** 标准答案（复核页面展示用，不回传学生端）。 */
  expected: string | null
  /** 学生端识别文本。 */
  asr_text: string | null
  letter_seq: string | null
  target_seq: string | null
  answer_text: string | null
  is_correct: number
  match_type: string
  asr_confidence: number | null
  duration_ms: number | null
  retry_count: number | null
  used_keyboard: number | null
  appeal_result: string | null
  provider: string | null
  created_at: string | null
  username: string
  real_name: string
  class_id: number | null
  class_name: string | null
  en: string | null
  cn: string | null
  unit_id: number | null
  unit_name: string | null
  audio_available: boolean
  review_remark: string | null
  review_result: string | null
  reviewed_at: string | null
  reviewer_name: string | null
}

export interface AppealListQuery {
  status?: 'pending' | 'upheld' | 'rejected' | 'all'
  student_id?: number
  class_id?: number
  date_from?: string
  date_to?: string
  page?: number
  pageSize?: number
}

export interface AppealListResult extends PageResult<AppealItem> {}

export function listAppeals(params: AppealListQuery = {}): Promise<AppealListResult> {
  const q: Record<string, unknown> = {
    status: params.status ?? 'pending',
    page: params.page ?? 1,
    page_size: params.pageSize ?? 20,
  }
  if (params.student_id) q.student_id = params.student_id
  if (params.class_id) q.class_id = params.class_id
  if (params.date_from) q.date_from = params.date_from
  if (params.date_to) q.date_to = params.date_to
  return api.get<AppealListResult>('/appeals', { params: q })
}

export type AppealResult = 'upheld' | 'rejected'

export interface ReviewAppealPayload {
  result: AppealResult
  /** 备注 ≤200 字；后端落 `audit_logs.detail`，不落 `answers` 列（DDL 无该列）。 */
  remark?: string
}

export interface ReviewAppealResult {
  appeal_id: number
  result: AppealResult
  remark: string | null
  previous_result: string | null
  changed: boolean
  /** ⚠️ 复核**不改** `is_correct`（硬约束），这里原样回传，供页面明示「判分未变」。 */
  is_correct: number
  message: string
}

export function reviewAppeal(
  appealId: number,
  payload: ReviewAppealPayload,
): Promise<ReviewAppealResult> {
  return api.post<ReviewAppealResult>(`/appeals/${appealId}/review`, payload)
}

// ===========================================================================
// 导出（F11）：返回 xlsx 二进制，不走 `ok()` 包装（接口设计 §4.7）。
// ===========================================================================

export function exportStats(
  type: 'class' | 'student',
  id: number,
): Promise<Blob> {
  const raw = `/stats/export?type=${type}&id=${id}`
  return api.get<Blob>(toApiPath(raw), { responseType: 'blob' })
}
