/**
 * U14 验收脚本（Mock 层）—— WEB-11，规格《51-二轮管理端工作包规格》U14-1~U14-6。
 *
 * 后端未就绪，api/units.ts 与 api/settings.ts 的 Mock 层承载了与契约 §4.1 一致的
 * 业务规则（两级校验、级联、值域）；本脚本在 Node 里直跑 Mock 层验证这些规则。
 * UI 呈现（树渲染、确认弹层文案数字、前端拦截）为手测项，见工作包报告。
 *
 * 运行：
 *   cd admin-web
 *   npx esbuild scripts/u14-mock-check.mts --bundle --format=esm --platform=node \
 *     --define:import.meta.env='{"VITE_USE_MOCK":"1"}' --outfile=scripts/.u14-check.mjs
 *   node scripts/.u14-check.mjs
 */
const mem = new Map<string, string>()
;(globalThis as unknown as { localStorage: Storage }).localStorage = {
  getItem: (k: string) => (mem.has(k) ? (mem.get(k) as string) : null),
  setItem: (k: string, v: string) => void mem.set(k, String(v)),
  removeItem: (k: string) => void mem.delete(k),
  clear: () => mem.clear(),
  key: () => null,
  length: 0,
} as Storage

import { BizError } from '../src/api/client'
import { createUnit, deleteUnit, listUnits, updateUnit } from '../src/api/units'
import type { UnitNode } from '../src/api/units'
import { getSettings, putSettings } from '../src/api/settings'

let pass = 0
const fails: string[] = []

function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    pass++
    console.log(`PASS ${name}`)
  } else {
    fails.push(name)
    console.log(`FAIL ${name} ${detail}`)
  }
}

function sum(u: UnitNode): number {
  return u.counts.word + u.counts.phrase + u.counts.sentence
}

function errCode(e: unknown): number {
  return e instanceof BizError ? e.code : -1
}

async function main(): Promise<void> {
  // ---- U14-1 建一级「四年级上册」→ 建二级挂其下 → 树正确渲染、counts 正确 ----
  const root = await createUnit({ name: '四年级上册', sort: 5 })
  const child = await createUnit({ name: 'Unit 1', sort: 0, parent_id: root.id })
  let tree = await listUnits()
  const created = tree.find((u) => u.id === root.id)
  check(
    'U14-1 一级/二级创建后树结构正确',
    !!created && created.children.length === 1 && created.children[0]!.id === child.id,
  )
  // 种子「三年级上册」：二级 8+3+2 与 5+2+1，汇总应为 word 13 / 总 21
  const seed1 = tree.find((u) => u.name === '三年级上册')
  check(
    'U14-1 一级 counts 含二级汇总（13/3/5，总 21）',
    !!seed1 &&
      seed1.counts.word === 13 &&
      seed1.counts.phrase === 5 &&
      seed1.counts.sentence === 3 &&
      sum(seed1) === 21,
    JSON.stringify(seed1?.counts),
  )

  // ---- U14-2 二级移动到另一一级 → 两边 counts 即时刷新 ----
  const root2 = await createUnit({ name: '四年级下册', sort: 6 })
  await updateUnit(child.id, { parent_id: root2.id })
  tree = await listUnits()
  check('U14-2 移动后原父级二级列表为空', tree.find((u) => u.id === root.id)?.children.length === 0)
  check('U14-2 新父级挂上该二级', tree.find((u) => u.id === root2.id)?.children[0]?.id === child.id)

  // ---- U14-4 给二级建「三级」→ 请求被拒，无脏数据 ----
  let code = 0
  try {
    await updateUnit(child.id, { parent_id: child.id })
  } catch (e) {
    code = errCode(e)
  }
  check('U14-4 二级下挂二级（PUT parent_id=二级id）被拒 10001', code === 10001, String(code))
  code = 0
  try {
    await createUnit({ name: '脏数据', sort: 0, parent_id: child.id })
  } catch (e) {
    code = errCode(e)
  }
  check('U14-4 二级下新建单元被拒 10001', code === 10001, String(code))
  tree = await listUnits()
  check('U14-4 被拒后无脏数据', !tree.some((u) => u.name === '脏数据'))
  code = 0
  try {
    await updateUnit(root.id, { parent_id: root2.id })
  } catch (e) {
    code = errCode(e)
  }
  check('U14-4 一级移动归属被拒 10001', code === 10001, String(code))

  // ---- U14-3 删除含 2 个二级、N 条内容的一级 → 级联删 ----
  await createUnit({ name: 'Unit 9', sort: 1, parent_id: root2.id })
  tree = await listUnits()
  const victim = tree.find((u) => u.id === root2.id)!
  const victimChildCount = victim.children.length
  await deleteUnit(victim.id)
  tree = await listUnits()
  check(
    `U14-3 删除一级（${victimChildCount} 个二级）后二级级联消失`,
    !tree.some((u) => u.id === root2.id) && !tree.some((u) => u.parent_id === root2.id),
  )
  // 种子一级：2 个二级、21 条内容
  await deleteUnit(seed1!.id)
  tree = await listUnits()
  check(
    'U14-3 删除种子一级（2 个二级、21 条）后级联消失',
    !tree.some((u) => u.id === seed1!.id) && !tree.some((u) => u.parent_id === seed1!.id),
  )

  // ---- U14-5 设置改倒计时 5 秒保存 → 回读 5 ----
  await putSettings({ dictation_countdown_s: 5 })
  const s1 = await getSettings()
  check('U14-5 改 5 保存后回读 5', s1.dictation_countdown_s === 5, JSON.stringify(s1))

  // ---- U14-6 倒计时 0 / 11 → 后端（Mock 同口径）10001 兜底 ----
  for (const [key, value] of [
    ['dictation_countdown_s', 0],
    ['dictation_countdown_s', 11],
    ['spell_verdict_countdown_s', 0],
    ['spell_verdict_countdown_s', 11],
  ] as const) {
    let ec = 0
    try {
      await putSettings({ [key]: value })
    } catch (e) {
      ec = errCode(e)
    }
    check(`U14-6 ${key}=${value} 被拒 10001`, ec === 10001, String(ec))
  }

  console.log(`\n合计：${pass} passed, ${fails.length} failed`)
  if (fails.length > 0) process.exit(1)
}

main().catch((e) => {
  console.error('脚本异常：', e)
  process.exit(1)
})
