#!/usr/bin/env node
/**
 * 一次性校验：7 处「下拉预取」是否真的改走了 options 接口、且没再传超限的 page_size。
 *
 * 背景（2026-09-29）：管理端多个列表页在加载时预拉班级 / 单元 / 学生下拉，原先一律传
 * `pageSize: 200` 想「一次拿全」，但契约 §5.1 的 `page_size` 上限是 **100**
 * （`backend/app/core/pagination.py` 的 `PAGE_SIZE_MAX`）→ 后端回 `10001`
 * 「page_size 必须在 1 到 100 之间」→ 下拉拉空。而像 `ClassOverview` / `UnitMastery`
 * 这种「下拉为空 = 整页显示空态卡片」的页面，直接就**功能不可用**了。
 *
 * 为什么必须真浏览器：`vue-tsc` 只能证明类型对、`vite build` 只能证明能打包，
 * 而「页面运行时到底发了哪个请求」只有真跑一次才看得见 —— 这正是这个 bug 能长期
 * 潜伏的原因（编译 / 构建 / 既有烟测**全都过**）。所以这里直接读
 * `performance.getEntriesByType('resource')`，看页面真实发出的 `/api/` 请求。
 *
 * 走**生产静态产物入口**（`http://<host>:8810/admin/`），不是 vite dev ——
 * 姚工实际访问的就是这条链路。
 *
 * CDP over `--remote-debugging-pipe`：零依赖，骨架与 `check_menu_groups.mjs` 同源。
 *
 * 跑法：env -u PYTHONPATH node scripts/check_page_options.mjs
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BACKEND = process.env.CHECK_BACKEND || 'http://127.0.0.1:8810'
const WEB = process.env.CHECK_WEB || 'http://127.0.0.1:8810'
const BASE = '/admin/'
const USER = process.env.CHECK_USER || 'admin'
const PWD = process.env.CHECK_PWD || 'bjsys-dev-2026'

const BROWSER_CANDIDATES = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
]

/** 每个页面「加载时应当发出的请求」—— 就是本次改动的 7 处预取。 */
const PAGES = [
  { path: 'stats/class', expect: ['/api/classes/options'], label: '班级概览 · 班级下拉' },
  { path: 'stats/unit', expect: ['/api/units/options'], label: '单元掌握度 · 单元下拉' },
  { path: 'stats/student', expect: ['/api/students'], label: '学生报告 · 学生下拉（远程搜索）' },
  { path: 'students/list', expect: ['/api/classes/options'], label: '学生管理 · 班级筛选下拉' },
  { path: 'live-board', expect: ['/api/classes/options'], label: '课堂大屏 · 班级下拉' },
  {
    path: 'homework',
    expect: ['/api/classes/options', '/api/units/options'],
    label: '作业布置 · 班级下拉 + 单元下拉',
  },
]

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []

function check(slug, desc, ok, detail = '') {
  results.push({ slug, ok })
  console.log(`${ok ? '[OK]' : '[!!]'} ${slug}  ${desc}${ok ? '' : `  <- ${detail}`}`)
  return ok
}

// ---------------------------------------------------------------- CDP over pipe

class CDP {
  constructor(child) {
    this.child = child
    this.writable = child.stdio[3]
    this.readable = child.stdio[4]
    this.seq = 0
    this.pending = new Map()
    this.closed = false

    let buf = ''
    this.readable.on('data', (chunk) => {
      buf += chunk.toString('utf8')
      let idx
      while ((idx = buf.indexOf('\0')) >= 0) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 1)
        if (!raw) continue
        let msg
        try {
          msg = JSON.parse(raw)
        } catch {
          continue
        }
        if (typeof msg.id !== 'number') continue
        const p = this.pending.get(msg.id)
        if (!p) continue
        this.pending.delete(msg.id)
        clearTimeout(p.timer)
        if (msg.error) p.reject(new Error(`${msg.error.message} (code ${msg.error.code})`))
        else p.resolve(msg.result)
      }
    })
  }

  send(method, params = {}, sessionId) {
    if (this.closed) return Promise.reject(new Error('CDP closed'))
    const id = ++this.seq
    const payload = { id, method, params }
    if (sessionId) payload.sessionId = sessionId
    return new Promise((res, rej) => {
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) rej(new Error(`CDP ${method} timeout`))
      }, 25000)
      this.pending.set(id, { resolve: res, reject: rej, timer })
      this.writable.write(JSON.stringify(payload) + '\0')
    })
  }

  async newPage(url = 'about:blank') {
    const { targetId } = await this.send('Target.createTarget', { url })
    const { sessionId } = await this.send('Target.attachToTarget', { targetId, flatten: true })
    return { targetId, sessionId }
  }

  close() {
    this.closed = true
    for (const p of this.pending.values()) {
      clearTimeout(p.timer)
      p.reject(new Error('CDP closed'))
    }
    this.pending.clear()
    try {
      this.readable.destroy()
    } catch {}
    try {
      this.writable.destroy()
    } catch {}
  }
}

async function tryEval(cdp, sid, expression) {
  try {
    const r = await cdp.send(
      'Runtime.evaluate',
      { expression, awaitPromise: true, returnByValue: true, userGesture: true },
      sid,
    )
    if (r.exceptionDetails) {
      return { ok: false, error: r.exceptionDetails.exception?.description || 'page error' }
    }
    return { ok: true, value: r.result?.value }
  } catch (e) {
    return { ok: false, error: e.message }
  }
}

async function evalJs(cdp, sid, expression) {
  let last = ''
  for (let i = 0; i < 10; i++) {
    const r = await tryEval(cdp, sid, expression)
    if (r.ok) return r.value
    last = r.error
    await sleep(200)
  }
  throw new Error(`eval failed: ${last}`)
}

async function waitFor(cdp, sid, expr, label, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const r = await tryEval(cdp, sid, expr)
    if (r.ok && r.value) return r.value
    await sleep(120)
  }
  throw new Error(`timeout waiting: ${label}`)
}

/** 页面真实发出的 `/api/` 请求（去掉 host，只留路径，便于比对与打印）。 */
const API_CALLS = `performance.getEntriesByType('resource')
  .map((e) => e.name)
  .filter((n) => n.includes('/api/'))
  .map((n) => n.replace(/^https?:\\/\\/[^/]+/, ''))`

// ---------------------------------------------------------------- main

async function main() {
  const browser = process.env.CHECK_BROWSER || BROWSER_CANDIDATES.find((p) => existsSync(p))
  if (!browser) {
    console.log('[!!] env-no-browser')
    return 2
  }

  // 1) 拿真 token（走真登录接口，不伪造）
  const loginRes = await (
    await fetch(`${BACKEND}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: USER, password: PWD, role: 'admin' }),
    })
  ).json()
  if (loginRes.code !== 0) {
    console.log(`[!!] login-failed code=${loginRes.code} ${loginRes.message}`)
    return 2
  }
  const token = loginRes.data.access_token
  const userJson = JSON.stringify(loginRes.data.user)

  // 2) 起无头浏览器
  const profile = join(tmpdir(), `bjsys_optcheck_${Date.now()}`)
  mkdirSync(profile, { recursive: true })
  const br = spawn(
    browser,
    [
      '--headless=new',
      '--remote-debugging-pipe',
      `--user-data-dir=${profile}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-gpu',
      '--window-size=1440,900',
      'about:blank',
    ],
    { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] },
  )
  const cdp = new CDP(br)
  const { sessionId: sid } = await cdp.newPage('about:blank')

  const allApi = []
  try {
    // 3) 先在同源页写 localStorage（等价于「刚登录完」）
    await cdp.send('Page.navigate', { url: `${WEB}${BASE}login` }, sid)
    await waitFor(cdp, sid, `document.readyState === 'complete'`, 'login page load', 20000)
    await sleep(300)
    await evalJs(
      cdp,
      sid,
      `(() => {
         localStorage.setItem('bjsys_admin_token', ${JSON.stringify(token)});
         localStorage.setItem('bjsys_admin_user', ${JSON.stringify(userJson)});
         return true;
       })()`,
    )

    // 4) 逐页导航，看真实发出的 /api/ 请求
    for (const p of PAGES) {
      await cdp.send('Page.navigate', { url: `${WEB}${BASE}${p.path}` }, sid)
      await waitFor(cdp, sid, `!!document.querySelector('.admin-header__user')`, `layout @ ${p.path}`, 20000)
      await sleep(1800) // 等异步预取落地（都是页面 mount 后立刻发）
      const urls = await evalJs(cdp, sid, API_CALLS)
      allApi.push(...urls)

      const missing = p.expect.filter((e) => !urls.some((u) => u.includes(e)))
      check(
        `page:${p.path}`,
        `${p.label} → 发出 ${p.expect.join(' + ')}`,
        missing.length === 0,
        `missing=${JSON.stringify(missing)}  got=${JSON.stringify(urls)}`,
      )
    }

    // 5) 全局：绝不能再有超限的 page_size（这才是原报错的根因）
    const bad = allApi.filter((u) => {
      const m = u.match(/[?&]page_size=(\d+)/)
      return m && Number(m[1]) > 100
    })
    check(
      'no-oversize-page-size',
      '全程没有任何 page_size > 100 的请求（原报错的根因已根除）',
      bad.length === 0,
      JSON.stringify(bad),
    )
  } finally {
    cdp.close()
    try {
      br.kill()
    } catch {}
  }

  const failed = results.filter((r) => !r.ok)
  console.log(`\n${results.length - failed.length}/${results.length} PASS`)
  return failed.length === 0 ? 0 : 1
}

main()
  .then((code) => process.exit(code))
  .catch((e) => {
    console.error('[!!] fatal:', e.message)
    process.exit(2)
  })
