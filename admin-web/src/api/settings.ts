/**
 * 设置 API —— WEB-11 F14-4（todo11 A3/A5 决策 #10/#13），契约《01-共享契约冻结》v1.3 §4.1 设置段。
 *
 * - `GET /api/settings` → {dictation_countdown_s, spell_verdict_countdown_s, dictation_hint, ...既有键}
 * - `PUT /api/settings` {dictation_countdown_s?, spell_verdict_countdown_s?, dictation_hint?}
 *   → 全量返回（同 GET）；值域校验：倒计时 1~10 秒；dictation_hint ∈ zh|en（违规 10001）。
 *
 * 【Mock 说明】与 `units.ts` 同一开关（`VITE_USE_MOCK=0` 关闭）：fixture 存
 * localStorage，PUT 校验按后端口径抛 10001（U14-6 的「后端兜底文案」在 Mock 下同样可验）。
 */
import { api, BizError } from './client'

import { ErrorCode } from './types'

/** 报词显示语言（契约 §7 settings 键：dictation_hint）。 */
export type DictationHint = 'zh' | 'en'

/** `GET /api/settings` 返回（后端还会带既有键；本页消费 v1.3 三键 + cloud_api_key）。 */
export interface AppSettings {
  /** 听写：报词后 N 秒自动报下一个（1~10，默认 3）。 */
  dictation_countdown_s: number
  /** 背诵：判定后 N 秒无操作自动下一题（1~10，默认 3）。 */
  spell_verdict_countdown_s: number
  /** 听写报词显示语言。 */
  dictation_hint: DictationHint
  /** 云端语音（百炼）API Key —— 后端返回**掩码**（前4后4），明文不出服务。
   *  PUT 传非空 = 覆盖（保存即生效）；传空串 = 清除覆盖（回落 asr.yaml 种子）。 */
  cloud_api_key: string
}

/** `PUT /api/settings` 载荷 —— 三个键均可选，只改传入的。 */
export type SettingsUpdatePayload = Partial<AppSettings>

/** 值域（契约 §4.1：倒计时 1~10 秒）。 */
export const COUNTDOWN_MIN = 1
export const COUNTDOWN_MAX = 10

// ---------------------------------------------------------------------------
// 本地 Mock（与 units.ts 同一开关与持久化风格）
// ---------------------------------------------------------------------------

const USE_MOCK = (import.meta.env as Record<string, string | undefined>).VITE_USE_MOCK !== '0'

const MOCK_KEY = 'bjsys_mock_settings_v1'

/** 种子 = 契约 §3.1b `005_todo11.sql` + `006_apikey.sql` 的默认值。 */
const DEFAULTS: AppSettings = {
  dictation_countdown_s: 3,
  spell_verdict_countdown_s: 3,
  dictation_hint: 'zh',
  cloud_api_key: '',
}

function loadMock(): AppSettings {
  try {
    const raw = localStorage.getItem(MOCK_KEY)
    if (raw !== null) {
      const parsed: unknown = JSON.parse(raw)
      if (parsed && typeof parsed === 'object') return { ...DEFAULTS, ...(parsed as Partial<AppSettings>) }
    }
  } catch {
    /* 数据损坏 → 回落默认值 */
  }
  return { ...DEFAULTS }
}

function saveMock(value: AppSettings): void {
  try {
    localStorage.setItem(MOCK_KEY, JSON.stringify(value))
  } catch {
    /* ignore */
  }
}

/** 值域校验（口径同后端 10001 兜底，契约 §4.1）。 */
function validatePatch(patch: SettingsUpdatePayload): void {
  for (const key of ['dictation_countdown_s', 'spell_verdict_countdown_s'] as const) {
    const value = patch[key]
    if (value === undefined) continue
    if (!Number.isInteger(value) || value < COUNTDOWN_MIN || value > COUNTDOWN_MAX) {
      throw new BizError(ErrorCode.PARAM_INVALID, `${key} 取值必须为 ${COUNTDOWN_MIN}~${COUNTDOWN_MAX} 的整数`)
    }
  }
  if (patch.dictation_hint !== undefined && patch.dictation_hint !== 'zh' && patch.dictation_hint !== 'en') {
    throw new BizError(ErrorCode.PARAM_INVALID, 'dictation_hint 只允许 zh | en')
  }
  if (patch.cloud_api_key !== undefined) {
    const key = patch.cloud_api_key
    if (typeof key !== 'string' || key.length > 200 || /[\r\n]/.test(key)) {
      throw new BizError(ErrorCode.PARAM_INVALID, 'cloud_api_key 必须是不含换行的字符串（≤200 字符）')
    }
  }
}

function delay(ms = 120): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// ---------------------------------------------------------------------------
// 对外 API
// ---------------------------------------------------------------------------

/** `GET /api/settings`。 */
export async function getSettings(): Promise<AppSettings> {
  if (USE_MOCK) {
    await delay()
    return loadMock()
  }
  return api.get<AppSettings>('/settings')
}

/** `PUT /api/settings` —— 返回全量（同 GET）。 */
export async function putSettings(payload: SettingsUpdatePayload): Promise<AppSettings> {
  if (USE_MOCK) {
    validatePatch(payload)
    await delay()
    const next: AppSettings = { ...loadMock(), ...payload }
    saveMock(next)
    return next
  }
  return api.put<AppSettings>('/settings', payload)
}
