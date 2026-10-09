/**
 * 单元 API —— WP-B，契约《01-共享契约冻结》§4.1 单元与内容段。
 *
 * 只调用 `client.ts` 的 `api`（契约 §9）；字段名与后端 JSON 同名（§2 不转驼峰）。
 */
import { api, newIdempotencyKey } from './client'

import type { UnitNode, UnitSavePayload, UnitSaved } from './types'

/** `GET /api/units` —— 全量单元树 [{id,name,sort,counts:{word,phrase,sentence}}]。 */
export function listUnits(): Promise<UnitNode[]> {
  return api.get<UnitNode[]>('/units')
}

/** `POST /api/units` {name,sort}。 */
export function createUnit(payload: UnitSavePayload): Promise<UnitSaved> {
  return api.post<UnitSaved>('/units', payload, { idempotencyKey: newIdempotencyKey() })
}

/** `PUT /api/units/{id}` {name?,sort?} —— 只改传入的字段。 */
export function updateUnit(id: number, payload: Partial<UnitSavePayload>): Promise<UnitSaved> {
  return api.put<UnitSaved>(`/units/${id}`, payload)
}

/** `DELETE /api/units/{id}` —— 级联删单元下全部 items（DB ON DELETE CASCADE）。 */
export function deleteUnit(id: number): Promise<void> {
  return api.delete<void>(`/units/${id}`)
}
