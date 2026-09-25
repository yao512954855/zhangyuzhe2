/**
 * 格式化工具 —— WP-WEB-01 F9
 *
 * 时间口径（契约 §4.6）：数据库存 UTC，接口出 ISO 8601（形如 `2026-09-23T07:49:59Z`），
 * 展示一律转**本地时区**。
 */

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/**
 * ISO(UTC) → 本地 `YYYY-MM-DD HH:mm`。
 * 非法或空值统一返回 `-`，避免页面上出现 `Invalid Date`。
 */
export function formatDateTime(iso?: string | null): string {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '-'
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}`
  )
}

/** ISO(UTC) → 本地 `YYYY-MM-DD`。 */
export function formatDate(iso?: string | null): string {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '-'
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

/**
 * 百分比，保留两位小数。
 * 入参接受 0~1 或 0~100 —— 后端口径不统一时很容易踩，这里用 `isRatio` 显式区分，
 * **不要靠猜**。
 */
export function formatPercent(value?: number | null, isRatio = true, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  const percent = isRatio ? value * 100 : value
  return `${percent.toFixed(digits)}%`
}

/** 时长：毫秒 → `mm:ss`（超过 1 小时给 `HH:mm:ss`）。 */
export function formatDuration(ms?: number | null): string {
  if (ms === null || ms === undefined || Number.isNaN(ms) || ms < 0) return '-'
  const totalSeconds = Math.floor(ms / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) {
    return `${pad2(hours)}:${pad2(minutes)}:${pad2(seconds)}`
  }
  return `${pad2(minutes)}:${pad2(seconds)}`
}
