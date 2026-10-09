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
