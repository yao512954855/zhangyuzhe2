/**
 * 通用类型 —— 与后端契约《01-共享契约冻结》§3 / §5 一一对应。
 *
 * ⚠️ 铁律（契约 §2）：API 的 JSON 字段名与数据库字段名保持一致，**不做驼峰转换**。
 * 所以这里全是下划线命名，不要「顺手改成驼峰」。
 */

/** 后端统一响应外壳。 */
export interface ApiResponse<T = unknown> {
  code: number
  message: string
  data: T
  trace_id: string
}

/** 分页 data 结构（契约 §5.3）。 */
export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

/** 分页请求参数（契约 §5.1：page 从 1 起，page_size 默认 20、上限 100）。 */
export interface PageQuery {
  page?: number
  page_size?: number
}

// ---------------------------------------------------------------------------
// 单元与内容（WP-B，契约《01-共享契约冻结》§3.1 / §4.1 / §7）
// ---------------------------------------------------------------------------

/** 内容类型枚举（契约 §7 item_type）—— 全局唯一取值，中文映射只放 UI 层。 */
export type ItemType = 'word' | 'phrase' | 'sentence'

/** 单元下三类内容的数量（`GET /api/units` 行内 `counts`）。 */
export interface UnitCounts {
  word: number
  phrase: number
  sentence: number
}

/** `GET /api/units` 返回的单元节点（全量树，不分页）。 */
export interface UnitNode {
  id: number
  name: string
  sort: number
  counts: UnitCounts
}

/** `POST /api/units` / `PUT /api/units/{id}` 载荷。 */
export interface UnitSavePayload {
  name: string
  sort: number
}

/** `POST /api/units` / `PUT` 的返回体（新建后 counts 全 0，后端不回该字段）。 */
export interface UnitSaved {
  id: number
  name: string
  sort: number
}

/** `GET /api/items` 行（后端 JOIN 出 `unit_name` 便于列表展示）。 */
export interface ItemRow {
  id: number
  unit_id: number
  unit_name: string
  type: ItemType
  en: string
  /** 词性，选填（如 adj. / v.），空串表示未填。 */
  pos: string
  zh: string
  /** 其他可接受答案，顿号「、」分隔，空串表示未填。 */
  alt_zh: string
  sort: number
  created_at: string
}

/** `POST /api/items` 载荷。 */
export interface ItemSavePayload {
  unit_id: number
  type: ItemType
  en: string
  pos?: string
  zh: string
  alt_zh?: string
  sort?: number
}

/** `PUT /api/items/{id}` 载荷 —— 同字段可选，只改传入的。 */
export type ItemUpdatePayload = Partial<ItemSavePayload>

/** `GET /api/items` 查询参数（契约 §2：page 1 起，page_size 默认 20 上限 200）。 */
export interface ItemListQuery extends PageQuery {
  unit_id?: number
  type?: ItemType
  /** 服务端过滤（en / zh LIKE），前端不做本地过滤。 */
  keyword?: string
}

/** `POST /api/items/import` 返回（契约 §4.1）。 */
export interface ItemImportResult {
  inserted: number
  duplicated: number
  failed: { row: number; reason: string }[]
}

/** 后端错误码 —— 与 `backend/app/core/errors.py` 的 `ErrorCode` 逐条对齐。 */
export const ErrorCode = {
  PARAM_INVALID: 10001,
  UNAUTHORIZED: 10002,
  FORBIDDEN: 10003,
  NOT_FOUND: 10004,
  TOO_MANY_REQUESTS: 10005,
  INTERNAL_ERROR: 10006,

  USERNAME_EXISTS: 20001,
  BAD_CREDENTIALS: 20002,
  ACCOUNT_DISABLED: 20003,
  CLASS_NOT_FOUND: 20004,

  UNIT_NOT_FOUND: 30001,
  CONTENT_NOT_FOUND: 30002,
  CONTENT_HAS_RECORDS: 30003,
  CONTENT_INACTIVE: 30004,

  FILE_FORMAT_INVALID: 40001,
  TEMPLATE_COLUMN_MISSING: 40002,
  IMPORT_VALIDATION_FAILED: 40003,
  FILE_TOO_LARGE: 40004,

  ASR_UNAVAILABLE: 50001,
  ASR_TIMEOUT: 50002,
  ASR_EMPTY: 50003,
  ASR_BAD_AUDIO: 50004,

  DISCOVERY_FAILED: 60001,
} as const
