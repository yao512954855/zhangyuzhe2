/**
 * 单元 API —— WEB-11（todo11 B1），契约《01-共享契约冻结》v1.3 §4.1 单元段。
 *
 * v1.3 变化：单元两级化 ——
 * - `GET /api/units` 返回两级树（节点带 `parent_id` 与 `children`；一级 counts 为
 *   「自身 + 二级」汇总，契约 §4.1「一级含二级 counts 汇总」）；
 * - `POST /api/units` 载荷加 `parent_id?`（空 = 一级）；
 * - `PUT /api/units/{id}` 载荷加 `parent_id?`（变更 = 移动归属；后端校验两级）；
 * - `DELETE /api/units/{id}` 级联删（一级 → 二级 → items，DB ON DELETE CASCADE）。
 *
 * 【Mock 说明】后端 WP-A 本接口未就绪：默认启用本地 Mock（fixture 持久化到
 * localStorage，增删改可真实操作，校验口径与契约 §3.4 两级规则一致，违规返回
 * 10001/30001）。接口路径与字段名与契约逐字一致；`.env` 设 `VITE_USE_MOCK=0`
 * 即切回真实后端，页面代码无需改动。
 */
import { api, BizError, newIdempotencyKey } from './client'

import { ErrorCode, type UnitCounts } from './types'

/** `GET /api/units` 行 —— v1.3 两级树节点（字段名与后端 JSON 同名，契约 §2）。 */
export interface UnitNode {
  id: number
  name: string
  sort: number
  /** 空 = 一级；非空 = 父一级单元 id（契约 §3.1b）。 */
  parent_id: number | null
  counts: UnitCounts
  /** 二级单元列表（按 sort,id 升序）；二级节点恒为空数组。 */
  children: UnitNode[]
}

/** `POST /api/units` 载荷（契约 §4.1：{name,sort,parent_id?}，parent_id 空 = 一级）。 */
export interface UnitCreatePayload {
  name: string
  sort: number
  parent_id?: number
}

/** `PUT /api/units/{id}` 载荷 —— 只改传入字段；parent_id 变更 = 移动归属。 */
export interface UnitUpdatePayload {
  name?: string
  sort?: number
  parent_id?: number
}

/** `POST /api/units` / `PUT` 返回体（新建单元 counts 为 0，后端不回 counts）。 */
export interface UnitSaved {
  id: number
  name: string
  sort: number
}

// ---------------------------------------------------------------------------
// 本地 Mock（后端未就绪期间的 fixture，接口形状与真实后端完全一致）
// ---------------------------------------------------------------------------

/** 读 `.env` 开关；未配置 = 启用 Mock。env.d.ts 未登记该键，这里用宽松取法。 */
const USE_MOCK = (import.meta.env as Record<string, string | undefined>).VITE_USE_MOCK !== '0'

interface MockUnitRow {
  id: number
  name: string
  sort: number
  parent_id: number | null
  counts: UnitCounts
}

const MOCK_KEY = 'bjsys_mock_units_v1'

/** 种子数据：两级结构 + 非 0 counts，方便直接验收 U14-1~U14-3。 */
function seedRows(): MockUnitRow[] {
  return [
    { id: 1, name: '三年级上册', sort: 0, parent_id: null, counts: { word: 0, phrase: 0, sentence: 0 } },
    { id: 2, name: 'Unit 1', sort: 0, parent_id: 1, counts: { word: 8, phrase: 3, sentence: 2 } },
    { id: 3, name: 'Unit 2', sort: 1, parent_id: 1, counts: { word: 5, phrase: 2, sentence: 1 } },
    { id: 4, name: '三年级下册', sort: 1, parent_id: null, counts: { word: 0, phrase: 0, sentence: 0 } },
  ]
}

function saveRows(rows: MockUnitRow[]): void {
  try {
    localStorage.setItem(MOCK_KEY, JSON.stringify(rows))
  } catch {
    /* 隐私模式等场景忽略 */
  }
}

function loadRows(): MockUnitRow[] {
  try {
    const raw = localStorage.getItem(MOCK_KEY)
    if (raw !== null) {
      const parsed: unknown = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed as MockUnitRow[]
    }
  } catch {
    /* 数据损坏 → 重置种子 */
  }
  const rows = seedRows()
  saveRows(rows)
  return rows
}

function findRow(rows: MockUnitRow[], id: number): MockUnitRow | undefined {
  return rows.find((r) => r.id === id)
}

function bySortId(a: MockUnitRow, b: MockUnitRow): number {
  return a.sort - b.sort || a.id - b.id
}

/** 行 → 树节点（一级 counts = 自身 + 二级汇总，契约 §4.1）。 */
function toNode(row: MockUnitRow, rows: MockUnitRow[]): UnitNode {
  const children = rows
    .filter((r) => r.parent_id === row.id)
    .sort(bySortId)
    .map((kid) => toNode(kid, rows))
  const counts: UnitCounts = { ...row.counts }
  for (const kid of children) {
    counts.word += kid.counts.word
    counts.phrase += kid.counts.phrase
    counts.sentence += kid.counts.sentence
  }
  return { id: row.id, name: row.name, sort: row.sort, parent_id: row.parent_id, counts, children }
}

function delay(ms = 120): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function mockList(): Promise<UnitNode[]> {
  return delay().then(() => {
    const rows = loadRows()
    return rows.filter((r) => r.parent_id === null).sort(bySortId).map((r) => toNode(r, rows))
  })
}

function mockCreate(payload: UnitCreatePayload): Promise<UnitSaved> {
  return delay().then(() => {
    const rows = loadRows()
    const name = payload.name.trim()
    if (!name) throw new BizError(ErrorCode.PARAM_INVALID, '单元名称不能为空')
    if (payload.parent_id != null) {
      const parent = findRow(rows, payload.parent_id)
      if (!parent) throw new BizError(ErrorCode.UNIT_NOT_FOUND, '父级单元不存在')
      // 契约 §3.4：parent 的 parent 必须为空（两级硬约束）
      if (parent.parent_id !== null) {
        throw new BizError(ErrorCode.PARAM_INVALID, '二级单元下不能再挂子单元（最多两级）')
      }
    }
    const row: MockUnitRow = {
      id: Math.max(0, ...rows.map((r) => r.id)) + 1,
      name,
      sort: payload.sort ?? 0,
      parent_id: payload.parent_id ?? null,
      counts: { word: 0, phrase: 0, sentence: 0 },
    }
    rows.push(row)
    saveRows(rows)
    return { id: row.id, name: row.name, sort: row.sort }
  })
}

function mockUpdate(id: number, payload: UnitUpdatePayload): Promise<UnitSaved> {
  return delay().then(() => {
    const rows = loadRows()
    const row = findRow(rows, id)
    if (!row) throw new BizError(ErrorCode.UNIT_NOT_FOUND, '单元不存在或已被删除')

    if (payload.parent_id !== undefined) {
      const pid = payload.parent_id
      if (pid === id) throw new BizError(ErrorCode.PARAM_INVALID, '不能把单元挂到自己下面')
      if (pid != null) {
        const parent = findRow(rows, pid)
        if (!parent) throw new BizError(ErrorCode.UNIT_NOT_FOUND, '目标父级单元不存在')
        // 契约 §3.4：二级只能挂一级下（U14-4 的请求被拒场景）
        if (parent.parent_id !== null) {
          throw new BizError(ErrorCode.PARAM_INVALID, '二级单元下不能再挂子单元（最多两级）')
        }
        // 一级移到别人下面会让它的二级变三级，同样违反两级约束
        if (row.parent_id === null) {
          throw new BizError(ErrorCode.PARAM_INVALID, '一级单元不支持移动归属')
        }
      }
      row.parent_id = pid ?? null
    }
    if (payload.name !== undefined) {
      const name = payload.name.trim()
      if (!name) throw new BizError(ErrorCode.PARAM_INVALID, '单元名称不能为空')
      row.name = name
    }
    if (payload.sort !== undefined) row.sort = payload.sort
    saveRows(rows)
    return { id: row.id, name: row.name, sort: row.sort }
  })
}

function mockDelete(id: number): Promise<void> {
  return delay().then(() => {
    const rows = loadRows()
    if (!findRow(rows, id)) throw new BizError(ErrorCode.UNIT_NOT_FOUND, '单元不存在或已被删除')
    // 级联：一级 → 二级（items 由 DB ON DELETE CASCADE 处理，Mock 无 items 层）
    saveRows(rows.filter((r) => r.id !== id && r.parent_id !== id))
  })
}

// ---------------------------------------------------------------------------
// 对外 API（路径/字段与契约 §4.1 逐字一致）
// ---------------------------------------------------------------------------

/** `GET /api/units` —— 全量两级单元树，不分页（一级含二级 counts 汇总）。 */
export function listUnits(): Promise<UnitNode[]> {
  if (USE_MOCK) return mockList()
  return api.get<UnitNode[]>('/units')
}

/** `POST /api/units` {name,sort,parent_id?}。 */
export function createUnit(payload: UnitCreatePayload): Promise<UnitSaved> {
  if (USE_MOCK) return mockCreate(payload)
  return api.post<UnitSaved>('/units', payload, { idempotencyKey: newIdempotencyKey() })
}

/** `PUT /api/units/{id}` {name?,sort?,parent_id?} —— 只改传入的字段。 */
export function updateUnit(id: number, payload: UnitUpdatePayload): Promise<UnitSaved> {
  if (USE_MOCK) return mockUpdate(id, payload)
  return api.put<UnitSaved>(`/units/${id}`, payload)
}

/** `DELETE /api/units/{id}` —— 级联删（一级 → 二级 → items）。 */
export function deleteUnit(id: number): Promise<void> {
  if (USE_MOCK) return mockDelete(id)
  return api.delete<void>(`/units/${id}`)
}
