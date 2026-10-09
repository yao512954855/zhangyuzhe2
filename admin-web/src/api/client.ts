/**
 * 统一请求层 —— 简化版（WP-F 拆鉴权后）。
 *
 * 硬性要求（契约《01-共享契约冻结》§4/§9）：
 * 1. **无鉴权**：不携带 `Authorization` 头，后续包不得加回；
 * 2. 统一解包 `{code, message, data, trace_id}`，成功时**只把 `data` 交出去**；
 * 3. `code != 0` → 抛 `BizError`（含 code / message / trace_id）；
 * 4. **错误提示只在本层弹一次**，页面里不要再 `ElMessage.error`；
 * 5. 业务失败一律 HTTP 200 + 非零 code，所以**不能靠 HTTP 状态判断成败**（契约 §4）。
 */
import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
} from 'axios'
import { ElMessage } from 'element-plus'

import { ErrorCode, type ApiResponse } from './types'

// ---------------------------------------------------------------------------
// 业务异常
// ---------------------------------------------------------------------------

/** `code != 0`（或 HTTP 层失败）时抛出。**必带 trace_id**，便于与后端日志对账。 */
export class BizError extends Error {
  readonly code: number
  readonly traceId: string
  readonly data: unknown
  readonly httpStatus: number | null

  constructor(
    code: number,
    message: string,
    traceId = '',
    data: unknown = null,
    httpStatus: number | null = null,
  ) {
    super(message)
    this.name = 'BizError'
    this.code = code
    this.traceId = traceId
    this.data = data
    this.httpStatus = httpStatus
  }

  /** 面向用户的完整文案（自带 trace_id，验收用例 U3 要求错误提示含 trace_id）。 */
  get display(): string {
    return this.traceId ? `${this.message}（trace_id: ${this.traceId}）` : this.message
  }
}

// ---------------------------------------------------------------------------
// 错误提示去重（任意非零 code **只弹一次**）
// ---------------------------------------------------------------------------

const TOAST_DEDUPE_MS = 1200
let lastToastText = ''
let lastToastAt = 0

function toastOnce(text: string): void {
  const now = Date.now()
  if (text === lastToastText && now - lastToastAt < TOAST_DEDUPE_MS) return
  lastToastText = text
  lastToastAt = now
  ElMessage.error(text)
}

// ---------------------------------------------------------------------------
// HTTP 状态码 → 业务错误码（契约 §5.1 列举的非 200 场景）
// ---------------------------------------------------------------------------

const HTTP_CODE_MAP: Record<number, number> = {
  400: ErrorCode.PARAM_INVALID,
  401: ErrorCode.UNAUTHORIZED,
  403: ErrorCode.FORBIDDEN,
  404: ErrorCode.NOT_FOUND,
  413: ErrorCode.FILE_TOO_LARGE,
  429: ErrorCode.TOO_MANY_REQUESTS,
}

function normalizeAxiosError(error: unknown): BizError {
  if (error instanceof BizError) return error

  if (error instanceof AxiosError) {
    const status = error.response?.status ?? null
    if (status === null) {
      // 没有响应 = 网络层失败（后端没起、代理不通、断网）
      return new BizError(
        ErrorCode.INTERNAL_ERROR,
        '无法连接服务器，请检查网络或确认后端服务已启动',
        '',
        null,
        null,
      )
    }
    const code = HTTP_CODE_MAP[status] ?? ErrorCode.INTERNAL_ERROR
    const body = error.response?.data as Partial<ApiResponse> | undefined
    return new BizError(
      typeof body?.code === 'number' ? body.code : code,
      body?.message || `请求失败（HTTP ${status}）`,
      typeof body?.trace_id === 'string' ? body.trace_id : '',
      body?.data ?? null,
      status,
    )
  }

  return new BizError(ErrorCode.INTERNAL_ERROR, (error as Error)?.message || '未知错误')
}

// ---------------------------------------------------------------------------
// blob 通道的错误体识别
//
// 背景见 `request()` 里 blob 分支的注释：`responseType: 'blob'` 会把
// 「HTTP 200 + 非零 code」的 JSON 错误体一起包成 Blob，必须在返回前认出来。
// ---------------------------------------------------------------------------

/**
 * 判断这个 MIME 更像「错误体」而不是「真文件」。
 *
 * - `application/json` / `*+json`：后端业务错误统一走 `JSONResponse`；
 * - `text/*`：网关 / 反向代理出错时常见 `text/html` 错误页。
 *
 * 本项目正常下载一律是 xlsx（`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`），
 * 与上述两类不会重合，所以这个判断不会误伤真文件。
 */
function isErrorBodyMime(mime: string): boolean {
  const m = (mime || '').toLowerCase()
  return m.includes('application/json') || m.includes('+json') || m.startsWith('text/')
}

/** 把 blob 通道里的错误体还原成 `BizError`（保留 `trace_id`，便于与后端日志对账）。 */
async function bizErrorFromBlob(blob: Blob, status: number): Promise<BizError> {
  let raw = ''
  try {
    raw = await blob.text()
  } catch {
    raw = ''
  }
  try {
    const body = JSON.parse(raw) as Partial<ApiResponse>
    if (typeof body?.code === 'number') {
      return new BizError(
        body.code,
        body.message || '操作失败',
        body.trace_id ?? '',
        body.data ?? null,
        status,
      )
    }
  } catch {
    /* 不是 JSON（比如 HTML 错误页）—— 走下面兜底 */
  }
  return new BizError(
    ErrorCode.INTERNAL_ERROR,
    '服务器没有返回文件，请稍后重试或联系管理员',
    '',
    raw ? raw.slice(0, 200) : null,
    status,
  )
}

// ---------------------------------------------------------------------------
// axios 实例
// ---------------------------------------------------------------------------

export const http: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 20000,
})

// ---------------------------------------------------------------------------
// 对外请求方法
// ---------------------------------------------------------------------------

export interface RequestOptions {
  params?: Record<string, unknown>
  headers?: Record<string, string>
  /** 写操作幂等键（契约 §5.1）。传了才加 `Idempotency-Key` 头。 */
  idempotencyKey?: string
  timeout?: number
  /** 设为 `blob` 时跳过 code 解包，直接返回 Blob（用于下载 Excel 模板）。 */
  responseType?: 'json' | 'blob'
}

function buildConfig(method: string, url: string, options: RequestOptions, data?: unknown): AxiosRequestConfig {
  const headers: Record<string, string> = { ...(options.headers ?? {}) }
  if (options.idempotencyKey) {
    headers['Idempotency-Key'] = options.idempotencyKey
  }
  return {
    method,
    url,
    data,
    params: options.params,
    headers,
    timeout: options.timeout,
    responseType: options.responseType ?? 'json',
  }
}

async function request<T>(method: string, url: string, options: RequestOptions = {}, data?: unknown): Promise<T> {
  try {
    const response = await http.request<ApiResponse<T> | Blob>(buildConfig(method, url, options, data))

    // 下载类接口不走统一解包
    if (options.responseType === 'blob') {
      const payload = response.data as Blob | null

      // ⚠️ 拿到 blob **必须再确认一次「这真的是文件」**（跨包复核期抓到的静默缺陷）：
      //    本项目的契约是「业务失败一律 HTTP 200 + 非零 code」（见本文件头部第 6 条 / 契约 §5.1）。
      //    在 `responseType: 'blob'` 下，这段 JSON 错误体会被 axios **原样包成一个 `Blob`** ——
      //    若直接 `return`，调用方会把它当成合法 xlsx 下载下去：
      //    **用户拿到一个内容是 `{"code":10001,...}` 的「Excel」，而本层的统一错误提示
      //    一次都不会弹**（没有任何东西抛错，下面的 catch 分支根本不进）。
      //    这就是本文件头部第 10 行自己写的红线：「不能靠 HTTP 状态判断成败」。
      //    所以按 `Content-Type` 把错误体认出来，转成 `BizError` 走统一错误链路。
      //
      //    为什么不会误伤真文件：本项目**所有下载端点都是 xlsx**，统一走
      //    `backend/app/core/excel.py::xlsx_response_headers`（`...spreadsheetml.sheet`），
      //    全仓不存在 `text/*` / csv 的正常下载（已逐端点核对）。
      if (payload instanceof Blob && isErrorBodyMime(payload.type)) {
        throw await bizErrorFromBlob(payload, response.status)
      }
      return payload as T
    }

    const body = response.data as ApiResponse<T>

    if (!body || typeof body.code !== 'number') {
      throw new BizError(
        ErrorCode.INTERNAL_ERROR,
        '服务返回的数据格式不正确',
        body?.trace_id ?? '',
        null,
        response.status,
      )
    }

    if (body.code !== 0) {
      throw new BizError(body.code, body.message || '操作失败', body.trace_id ?? '', body.data, response.status)
    }

    return body.data
  } catch (error) {
    const bizError = normalizeAxiosError(error)

    toastOnce(bizError.display)
    throw bizError
  }
}

export const api = {
  get: <T>(url: string, options?: RequestOptions) => request<T>('GET', url, options),

  post: <T>(url: string, data?: unknown, options?: RequestOptions) =>
    request<T>('POST', url, options, data),

  put: <T>(url: string, data?: unknown, options?: RequestOptions) => request<T>('PUT', url, options, data),

  patch: <T>(url: string, data?: unknown, options?: RequestOptions) =>
    request<T>('PATCH', url, options, data),

  delete: <T>(url: string, options?: RequestOptions) => request<T>('DELETE', url, options),
}

/** 生成幂等键（写操作建议带上，避免弱网重试造成重复写入）。 */
export function newIdempotencyKey(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `idem-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}
