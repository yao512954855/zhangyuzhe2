/**
 * 内容版本快照 API —— WP-2I + 2026-09-30 方案 B 重构。
 *
 * 三级能力：全局最近变更（recent，默认视图）→ 单实体版本历史（可带快照算 diff）→ 回滚。
 * ⚠️ 本文件**只调用** `client.ts` 的 `api`；错误统一由 client 拦截弹提示。
 */
import { api } from './client'
import type { PageResult } from './types'
import type { Semester, Subject } from './content'

export type VersionEntityType = 'unit' | 'word' | 'text'
export type VersionOp = 'create' | 'update' | 'disable' | 'delete'

// ---------------------------------------------------------------------------
// 全局最近变更（GET /api/content/versions/recent）
// ---------------------------------------------------------------------------

export interface RecentVersionItem {
  id: number
  entity_type: VersionEntityType
  entity_id: number
  version: number
  op: VersionOp
  operator_id: number | null
  /** 操作人姓名（LEFT JOIN users；人被删则为 null） */
  operator_name: string | null
  /** 实体展示名：单元=名称、词条="en cn"、课文=标题；实体已物理删除时从快照兜底 */
  entity_name: string | null
  subject: Subject | null
  grade: number | null
  semester: Semester | null
  /** 词条/课文的归属单元名（单元实体为 null） */
  unit_name: string | null
  created_at: string | null
}

export interface RecentVersionQuery {
  entity_type?: VersionEntityType | ''
  op?: VersionOp | ''
  page?: number
  page_size?: number
}

export function listRecentVersions(params: RecentVersionQuery): Promise<PageResult<RecentVersionItem>> {
  const q: Record<string, unknown> = {}
  if (params.entity_type) q.entity_type = params.entity_type
  if (params.op) q.op = params.op
  if (params.page) q.page = params.page
  if (params.page_size) q.page_size = params.page_size
  return api.get<PageResult<RecentVersionItem>>('/content/versions/recent', { params: q })
}

// ---------------------------------------------------------------------------
// 单实体版本历史（GET /api/content/versions?with_snapshot=1）
// ---------------------------------------------------------------------------

export interface VersionItem {
  id: number
  entity_type: VersionEntityType
  entity_id: number
  version: number
  op: VersionOp
  operator_id: number | null
  operator_name: string | null
  created_at: string | null
  /** 仅 `with_snapshot=true` 时返回（解析后的整行快照），供前端算字段级 diff */
  snapshot?: Record<string, unknown>
}

export function listVersions(
  entityType: VersionEntityType,
  entityId: number,
  withSnapshot = false,
): Promise<{ items: VersionItem[] }> {
  return api.get<{ items: VersionItem[] }>('/content/versions', {
    params: { entity_type: entityType, entity_id: entityId, with_snapshot: withSnapshot ? 1 : 0 },
  })
}

// ---------------------------------------------------------------------------
// 回滚（POST /api/content/versions/{id}/rollback）
// ---------------------------------------------------------------------------

export function rollbackVersion(versionId: number): Promise<{
  entity_type: VersionEntityType
  entity_id: number
  rolled_back_to: number
  version: number
}> {
  return api.post(`/content/versions/${versionId}/rollback`)
}
