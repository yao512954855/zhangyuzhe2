/**
 * 内容条目 API —— WP-B，契约《01-共享契约冻结》§4.1 单元与内容段。
 *
 * 只调用 `client.ts` 的 `api`（契约 §9），下载类走 `responseType: 'blob'`
 * （client 会自行识别 blob 通道里的 JSON 错误体，页面不写裸 fetch）。
 */
import { api, newIdempotencyKey } from './client'

import type {
  ItemBatchDeleteResult,
  ItemBatchMoveResult,
  ItemImportResult,
  ItemListQuery,
  ItemRow,
  ItemSavePayload,
  ItemUpdatePayload,
  ItemType,
  PageResult,
} from './types'

// ---------------------------------------------------------------------------
// 枚举与 UI 映射（契约 §7：英文值全局唯一，中文只在 UI 层）
// ---------------------------------------------------------------------------

export const ITEM_TYPES: readonly ItemType[] = ['word', 'phrase', 'sentence'] as const

export const ITEM_TYPE_LABEL: Record<ItemType, string> = {
  word: '单词',
  phrase: '词组',
  sentence: '短句',
}

/** 契约 §3.4：同 unit 同 type 下 en 重复 → 20003（软约束，前端先提示、后端兜底）。 */
export const ITEM_DUPLICATE_CODE = 20003

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

/** `GET /api/items?unit_id&type&page&page_size&keyword` —— 服务端分页 + 过滤。 */
export function listItems(query: ItemListQuery): Promise<PageResult<ItemRow>> {
  return api.get<PageResult<ItemRow>>('/items', { params: { ...query } })
}

/** `POST /api/items` {unit_id,type,en,pos?,zh,alt_zh?,sort?}。 */
export function createItem(payload: ItemSavePayload): Promise<ItemRow> {
  return api.post<ItemRow>('/items', payload, { idempotencyKey: newIdempotencyKey() })
}

/** `PUT /api/items/{id}` —— 同字段可选，只改传入的。 */
export function updateItem(id: number, payload: ItemUpdatePayload): Promise<ItemRow> {
  return api.put<ItemRow>(`/items/${id}`, payload)
}

/** `DELETE /api/items/{id}`。 */
export function deleteItem(id: number): Promise<void> {
  return api.delete<void>(`/items/${id}`)
}

/** `POST /api/items/batch-delete` {ids}（todo13 #2）。 */
export function batchDeleteItems(ids: number[]): Promise<ItemBatchDeleteResult> {
  return api.post<ItemBatchDeleteResult>('/items/batch-delete', { ids })
}

/** `POST /api/items/batch-move` {ids, target_unit_id}（todo13 #2）。 */
export function batchMoveItems(ids: number[], targetUnitId: number): Promise<ItemBatchMoveResult> {
  return api.post<ItemBatchMoveResult>('/items/batch-move', {
    ids,
    target_unit_id: targetUnitId,
  })
}

/** `POST /api/items/import-text`（todo13 #4 竖线粘贴导入）。 */
export function importTextItems(
  text: string,
  unitId: number,
  type: ItemType,
): Promise<ItemImportResult> {
  return api.post<ItemImportResult>('/items/import-text', {
    text,
    unit_id: unitId,
    type,
  })
}

// ---------------------------------------------------------------------------
// Excel 导入 / 导出 / 模板
// ---------------------------------------------------------------------------

/** `POST /api/items/import` —— multipart: file(.xlsx) + form unit_id/type。 */
export function importItems(file: File, unitId: number, type: ItemType): Promise<ItemImportResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('unit_id', String(unitId))
  form.append('type', type)
  // xlsx 解析可能略慢，放宽超时
  return api.post<ItemImportResult>('/items/import', form, { timeout: 60000 })
}

/**
 * `GET /api/items/export?unit_id` → xlsx 二进制（todo13 #3：导出**整个选中分类**
 * 的单词/词组/短句混导，带「类型」列；不再按类型细分）。
 */
export async function exportItems(unitId: number): Promise<void> {
  const blob = await api.get<Blob>('/items/export', {
    params: { unit_id: unitId },
    responseType: 'blob',
    timeout: 60000,
  })
  saveBlob(blob, `items-${unitId}.xlsx`)
}

/** `GET /api/items/import-template` → xlsx 二进制（表头 + 1 行示例）。 */
export async function downloadImportTemplate(): Promise<void> {
  const blob = await api.get<Blob>('/items/import-template', {
    responseType: 'blob',
  })
  saveBlob(blob, 'items-import-template.xlsx')
}

/** 浏览器端保存 Blob 为文件下载。 */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
