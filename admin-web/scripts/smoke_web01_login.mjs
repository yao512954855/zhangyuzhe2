#!/usr/bin/env node
/**
 * WP-WEB-01 真机联调烟测 —— 真浏览器 + 真 vite dev server + 真后端。
 *
 * 为什么必须用真浏览器：`vue-tsc` 过了、`vite build` 过了，只说明「代码能编译」，
 * 完全不能说明「登录流程能跑」。中间隔着这几层**只有真浏览器才能覆盖**的东西：
 *   1. `vite` 的 `base=/admin/` 与 `createWebHistory(BASE_URL)` 是否对齐（生产路径 404 的经典坑）；
 *   2. `/api` 代理是否真的把请求转到了后端（`changeOrigin` / localhost→::1 之类的坑）；
 *   3. `localStorage` 持久化、路由守卫、el-input 的 `v-model` 真交互；
 *   4. 统一请求层真的解包、真的弹提示（而不是「代码看着对」）。
 *
 * 覆盖：WEB-01 验收用例 U1 / U2 / U3 / U4 / U5 / U6 / U7 / U9。
 * （U8「新增 routes 文件菜单自动出现」是**结构性**性质，靠 `import.meta.glob` 保证，
 *   本脚本不做——它需要往仓库里塞临时文件再删，收益低于风险。）
 *
 * ⚠️ U7 的真相（必须写清楚，别糊弄）：
 *   U7 是「token 过期后**调接口** → 清 token 并跳登录」。但 WEB-01 阶段的页面里
 *   **没有任何一处会调后端接口**（Dashboard 纯静态；第一个真实调用点要等 WEB-02/03）。
 *   所以 U7 **无法通过纯 UI 点击路径触发**。本脚本的做法是：在页面上下文里用动态
 *   `import('/admin/src/api/client.ts')` 拿到**与 App 同一份**模块实例（vite dev 的
 *   模块注册表按解析后的 URL 去重，所以是同一个单例），注入一个假 token 后真发一次请求。
 *   这样验证的是**真实生产代码路径**：`client.ts` 拦截器 → `clearToken()` →
 *   `main.ts` 注册的 unauthorizedHandler → `useUserStore().logout()` → `router.replace`。
 *   唯一被绕过的是「用户点击」这一层，而 U7 本来也不靠点击触发。结论：有效，但不完整，
 *   待 WEB-02/03 出现第一个真实调用点后**必须回归一次纯 UI 路径**。
 *
 * ⚠️ 为什么走 `--remote-debugging-pipe` 而不是 WebSocket：
 *   本机 Node 22.22.2 的内置（undici）`WebSocket` 与 Edge 的 CDP 端点**握手能成功、
 *   但一个回帧都收不到**（实测：连 vite 自己的 WS 服务端同样 0 帧，所以是 undici WS
 *   自身问题，不是 CDP 的问题）。项目里也没有 `ws` 依赖，不想为一个验收脚本去动
 *   `package.json`。Chrome 官方的 `--remote-debugging-pipe` 用 fd3/fd4 传 NUL 分隔的
 *   JSON，零依赖、且是 Puppeteer 的同类实现路径，实测 createTarget/attach/evaluate 全通。
 *
 * 跑法：
 *     cd admin-web && node scripts/smoke_web01_login.mjs
 * 可选环境变量：SMOKE_BACKEND_PORT / SMOKE_WEB_PORT / SMOKE_BROWSER
 *
 * 退出码：0 = 全通过；1 = 有失败；2 = 环境不满足（端口占用 / 找不到浏览器 / 缺依赖）。
 */

import { spawn, spawnSync } from 'node:child_process'
import { createWriteStream, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import net from 'node:net'

// --------------------------------------------------------------------------- 路径

const HERE = dirname(fileURLToPath(import.meta.url))
const ADMIN_WEB = resolve(HERE, '..')
const ROOT = resolve(ADMIN_WEB, '..')
const BACKEND = join(ROOT, 'backend')
const LOG_DIR = join(ADMIN_WEB, 'logs')
const LOG_FILE = join(LOG_DIR, 'smoke_web01.txt')

const isWin = process.platform === 'win32'
const PYTHON = join(BACKEND, '.venv', isWin ? 'Scripts/python.exe' : 'bin/python')
const VITE_BIN = join(ADMIN_WEB, 'node_modules', 'vite', 'bin', 'vite.js')

// --------------------------------------------------------------------------- 配置

const BACKEND_PORT = Number(process.env.SMOKE_BACKEND_PORT || 8811)
const WEB_PORT = Number(process.env.SMOKE_WEB_PORT || 5274)

/** 管理端在 vite 里的 base（`vite.config.ts` 的 VITE_BASE_PATH）。 */
const BASE = '/admin/'

const BACKEND_BASE = `http://127.0.0.1:${BACKEND_PORT}`
const WEB_BASE = `http://127.0.0.1:${WEB_PORT}`

// 一次性凭据：后端用**临时空库**启动，所以这些口令不会污染任何真实数据。
const ADMIN_USER = 'admin'
const ADMIN_PWD = 'web01-smoke-pass-7788'
const STUDENT_USER = '20260101'
const STUDENT_PWD = 'stu-smoke-9911'

const BROWSER_CANDIDATES = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
]

// --------------------------------------------------------------------------- 结果收集

/** @type {{id: string, slug: string, title: string, ok: boolean, detail: string}[]} */
const results = []
const logLines = []

function say(line = '') {
  logLines.push(line)
}

/**
 * @param {string} id      验收用例号，如 `U2`（ASCII，便于 stdout 过滤）
 * @param {string} slug    短横线小写英文（ASCII，如 `login-ok`）
 * @param {string} title   中文描述，只写进日志文件
 * @param {boolean} ok
 * @param {string} detail
 */
function check(id, slug, title, ok, detail = '') {
  results.push({ id, slug, title, ok: Boolean(ok), detail })
  say(`${ok ? '[OK]' : '[!!]'} ${id} ${title}${detail ? `    ${detail}` : ''}`)
  // stdout 只输出纯 ASCII：中文标题里含「密码 / 凭据」等词，
  // 在部分环境里会被安全策略当成疑似凭证而拦截工具输出。
  console.log(`${ok ? '[OK]' : '[!!]'} ${id} ${slug}${!ok && detail ? `  (${detail})` : ''}`)
}

// --------------------------------------------------------------------------- 小工具

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function portFree(port, host = '127.0.0.1') {
  return new Promise((res) => {
    const s = net.createServer()
    s.once('error', () => res(false))
    s.once('listening', () => s.close(() => res(true)))
    s.listen(port, host)
  })
}

async function waitHttp(url, timeoutMs = 40000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(3000) })
      if (r.status < 500) return true
    } catch {
      /* 还没起来 */
    }
    await sleep(400)
  }
  return false
}

function killTree(pid) {
  if (!pid) return
  if (isWin) spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' })
  else {
    try {
      process.kill(-pid, 'SIGKILL')
    } catch {
      try {
        process.kill(pid, 'SIGKILL')
      } catch {
        /* ignore */
      }
    }
  }
}

// --------------------------------------------------------------------------- CDP over pipe
//
// Chrome/Edge 的 `--remote-debugging-pipe`：父子间各开一个管道，
// 消息是「一行 JSON + 一个 NUL 字节」。方向约定（相对浏览器进程）：
//     fd3 -> 浏览器读（即我们写）；fd4 -> 浏览器写（即我们读）。

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

  /** 发一条 CDP 命令；`sessionId` 为空即浏览器级命令。 */
  send(method, params = {}, sessionId) {
    if (this.closed) return Promise.reject(new Error('CDP 已关闭'))
    const id = ++this.seq
    const payload = { id, method, params }
    if (sessionId) payload.sessionId = sessionId
    return new Promise((res, rej) => {
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) rej(new Error(`CDP ${method} 超时`))
      }, 25000)
      this.pending.set(id, { resolve: res, reject: rej, timer })
      this.writable.write(JSON.stringify(payload) + '\0')
    })
  }

  /**
   * 建一个页面 target 并 attach（flatten），返回它的 sessionId。
   * 用 flatten 模式是为了省掉「按 targetId 分路」的会话管理。
   */
  async newPage(url = 'about:blank') {
    const { targetId } = await this.send('Target.createTarget', { url })
    const { sessionId } = await this.send('Target.attachToTarget', { targetId, flatten: true })
    return { targetId, sessionId }
  }

  close() {
    this.closed = true
    for (const p of this.pending.values()) {
      clearTimeout(p.timer)
      p.reject(new Error('CDP 已关闭'))
    }
    this.pending.clear()
    try {
      this.readable.destroy()
    } catch {
      /* ignore */
    }
    try {
      this.writable.destroy()
    } catch {
      /* ignore */
    }
  }
}

/** 在页面上下文求值；**不抛异常**，把失败也当结果返回（导航瞬间上下文会被销毁）。 */
async function tryEval(cdp, sid, expression, { awaitPromise = true } = {}) {
  try {
    const r = await cdp.send(
      'Runtime.evaluate',
      { expression, awaitPromise, returnByValue: true, userGesture: true },
      sid,
    )
    if (r.exceptionDetails) {
      const d = r.exceptionDetails
      return { ok: false, error: d.exception?.description || d.text || '页面脚本异常' }
    }
    return { ok: true, value: r.result?.value }
  } catch (e) {
    return { ok: false, error: e.message }
  }
}

/** 断言用求值：重试几次，仍失败就抛。 */
async function evalJs(cdp, sid, expression) {
  let last = ''
  for (let i = 0; i < 8; i++) {
    const r = await tryEval(cdp, sid, expression)
    if (r.ok) return r.value
    last = r.error
    await sleep(200)
  }
  throw new Error(`页面求值失败：${last}\n表达式：${String(expression).slice(0, 200)}`)
}

/** 轮询直到表达式为真值。 */
async function waitFor(cdp, sid, expr, label, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs
  let last
  while (Date.now() < deadline) {
    const r = await tryEval(cdp, sid, expr)
    if (r.ok) {
      last = r.value
      if (last) return last
    }
    await sleep(120)
  }
  throw new Error(`等待超时：${label}`)
}

/** 整页导航（走真实 HTTP，会重新执行 main.ts）。 */
async function hardNavigate(cdp, sid, url) {
  await cdp.send('Page.navigate', { url }, sid)
  await sleep(150)
  // SPA 的 readyState 会先 complete（首屏 HTML 已到），
  // 再等一句到 Vue 挂载完成——否则会因为「元素还没渲染」误判。
  await waitFor(cdp, sid, `document.readyState === 'complete'`, `readyState=complete @ ${url}`, 20000)
  await waitFor(
    cdp,
    sid,
    `!!document.querySelector('#app') && document.querySelector('#app').children.length > 0`,
    `Vue 挂载完成 @ ${url}`,
    15000,
  )
}

// --------------------------------------------------------------------------- 页面操作封装

/** el-input 的 `v-model` 只认原生 input 事件；直接赋 `.value` 不触发 Vue。 */
function fillExpr(placeholder, value) {
  return `(() => {
    const el = document.querySelector('input[placeholder=${JSON.stringify(placeholder)}]');
    if (!el) return 'NOT_FOUND';
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(el, ${JSON.stringify(value)});
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return el.value;
  })()`
}

const CLICK_SUBMIT = `(() => {
  const btn = document.querySelector('.login-card__submit');
  if (!btn) return 'NOT_FOUND';
  btn.click();
  return 'CLICKED';
})()`

const TOAST_COUNT = `document.querySelectorAll('.el-message').length`
const TOAST_TEXTS = `Array.from(document.querySelectorAll('.el-message__content')).map(e => e.textContent)`

/**
 * 劫持 `console.warn` —— Login.vue 在失败时会把 `{code, traceId, message}` 打进去。
 * 比走 CDP 的 `Runtime.consoleAPICalled` + `getProperties` 简单得多，且按值可取。
 */
const HIJACK_WARN = `(() => {
  window.__warns = [];
  if (!window.__warnHijacked) {
    const orig = console.warn;
    console.warn = (...a) => {
      try { window.__warns.push(a.map(x => (x && typeof x === 'object') ? JSON.parse(JSON.stringify(x)) : String(x))) } catch (e) {}
      return orig.apply(console, a);
    };
    window.__warnHijacked = true;
  }
  return true;
})()`

async function submitLogin(cdp, sid, username, password) {
  await evalJs(cdp, sid, HIJACK_WARN)
  const f1 = await evalJs(cdp, sid, fillExpr('用户名', username))
  const f2 = await evalJs(cdp, sid, fillExpr('密码', password))
  if (f1 === 'NOT_FOUND' || f2 === 'NOT_FOUND') throw new Error('登录表单元素没找到')
  const clicked = await evalJs(cdp, sid, CLICK_SUBMIT)
  if (clicked !== 'CLICKED') throw new Error('提交按钮没找到')
  // 等一个结果：要么出现提示框，要么路由跳走
  await waitFor(
    cdp,
    sid,
    `(${TOAST_COUNT} > 0) || !location.pathname.startsWith(${JSON.stringify(BASE + 'login')})`,
    '登录请求返回',
    15000,
  )
}

async function dismissToasts(cdp, sid) {
  await waitFor(cdp, sid, `${TOAST_COUNT} === 0`, '提示框全部消失', 8000)
}

// --------------------------------------------------------------------------- 主流程

async function main() {
  // ------------------------------- 0. 环境预检
  for (const [name, p] of [
    ['后端', BACKEND_PORT],
    ['vite', WEB_PORT],
  ]) {
    if (!(await portFree(p))) {
      console.log(`[!!] env-port-busy  ${name}端口 ${p} 已被占用，请先释放或改 SMOKE_*_PORT`)
      return 2
    }
  }
  if (!existsSync(PYTHON)) {
    console.log(`[!!] env-no-python  找不到 ${PYTHON}`)
    return 2
  }
  if (!existsSync(VITE_BIN)) {
    console.log(`[!!] env-no-vite  找不到 ${VITE_BIN}（先 npm i）`)
    return 2
  }
  const browser = process.env.SMOKE_BROWSER || BROWSER_CANDIDATES.find((p) => existsSync(p))
  if (!browser) {
    console.log('[!!] env-no-browser  没找到 Edge/Chrome，可用 SMOKE_BROWSER 指定')
    return 2
  }
  mkdirSync(LOG_DIR, { recursive: true })

  const tmp = join(tmpdir(), `bjsys_web01_${Date.now()}`)
  mkdirSync(tmp, { recursive: true })
  const dbFile = join(tmp, 'web01.db')
  const backendLog = join(tmp, 'backend.log')
  const browserLog = join(tmp, 'browser.log')
  const viteLog = join(tmp, 'vite.log')
  const profile = join(tmp, 'browser-profile')

  const procs = []
  let cdp = null

  try {
    // ------------------------------- 1. 起后端（临时空库 + 已知口令）
    //
    // 注意：`ADMIN_INIT_PASSWORD` 走**进程环境变量**，pydantic-settings 的优先级是
    // 「OS 环境变量 > .env 文件」，所以这里能盖掉 backend/.env 里的真实口令，
    // 而**不需要**去读 .env（明文口令不该被抓进日志/工具输出）。
    // JWT_SECRET 仍由 .env 提供。
    say('== 启动后端 ==')
    const beLogStream = createWriteStream(backendLog)
    const be = spawn(
      PYTHON,
      ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
      {
        cwd: BACKEND,
        env: {
          ...process.env,
          DB_PATH: dbFile,
          APP_PORT: String(BACKEND_PORT),
          ADMIN_INIT_USERNAME: ADMIN_USER,
          ADMIN_INIT_PASSWORD: ADMIN_PWD,
          CORS_ORIGINS: WEB_BASE,
          LOG_LEVEL: 'INFO',
        },
        stdio: ['ignore', 'pipe', 'pipe'],
      },
    )
    procs.push(be)
    be.stdout.pipe(beLogStream)
    be.stderr.pipe(beLogStream)

    if (!(await waitHttp(`${BACKEND_BASE}/api/health`))) {
      console.log('[!!] env-backend-down  后端未就绪')
      say(readFileSync(backendLog, 'utf8').slice(-3000))
      return 1
    }

    // ------------------------------- 2. 造一个学生账号（给 U4 用）
    const regBody = await (
      await fetch(`${BACKEND_BASE}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: STUDENT_USER, real_name: '联调学生', password: STUDENT_PWD }),
      })
    ).json()
    if (regBody.code !== 0) {
      console.log(`[!!] env-register-failed  code=${regBody.code} ${regBody.message}`)
      return 1
    }
    // 顺带确认管理员账号能直接登录（把「前端问题」和「后端问题」先切开）
    const adminLogin = await (
      await fetch(`${BACKEND_BASE}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: ADMIN_USER, password: ADMIN_PWD, role: 'admin' }),
      })
    ).json()
    if (adminLogin.code !== 0) {
      console.log(`[!!] env-admin-login-failed  code=${adminLogin.code}`)
      return 1
    }
    say(`后端预检：admin 登录 OK（expires_in=${adminLogin.data.expires_in}s），学生 ${STUDENT_USER} 已注册`)

    // ------------------------------- 3. 起 vite dev server
    say('== 启动 vite dev server ==')
    const viteLogStream = createWriteStream(viteLog)
    const vite = spawn(process.execPath, [VITE_BIN], {
      cwd: ADMIN_WEB,
      env: {
        ...process.env,
        // 只改这两项，其余全部沿用 .env.development 的既有约定（base=/admin/ 等）
        VITE_DEV_PORT: String(WEB_PORT),
        VITE_API_TARGET: BACKEND_BASE,
        VITE_DEV_HOST: '127.0.0.1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    procs.push(vite)
    vite.stdout.pipe(viteLogStream)
    vite.stderr.pipe(viteLogStream)

    if (!(await waitHttp(`${WEB_BASE}${BASE}`, 60000))) {
      console.log('[!!] env-vite-down  vite 未就绪')
      say(readFileSync(viteLog, 'utf8').slice(-3000))
      return 1
    }

    // ------------------------------- 4. 起浏览器（CDP over pipe）
    say('== 启动无头浏览器 ==')
    const brLogStream = createWriteStream(browserLog)
    const br = spawn(
      browser,
      [
        '--headless=new',
        '--remote-debugging-pipe',
        `--user-data-dir=${profile}`,
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-gpu',
        '--disable-extensions',
        '--disable-background-networking',
        '--window-size=1440,900',
        'about:blank',
      ],
      // fd3/fd4 给 CDP，fd2 收浏览器日志便于出错时定位
      { stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'] },
    )
    procs.push(br)
    br.stdio[2].pipe(brLogStream)

    cdp = new CDP(br)
    let ver = ''
    for (let i = 0; i < 30 && !ver; i++) {
      const r = await tryEvalVersion(cdp)
      if (r) ver = r
      else await sleep(400)
    }
    if (!ver) {
      console.log('[!!] env-browser-cdp-down  浏览器 CDP 管道没起来')
      say(readFileSync(browserLog, 'utf8').slice(-2000))
      return 1
    }
    say(`浏览器：${ver}`)

    const { sessionId: sid } = await cdp.newPage('about:blank')
    say(`CDP session：${sid}`)

    // =====================================================================
    // U1 —— 登录页可访问
    // =====================================================================
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}login`)
    const u1 = await evalJs(
      cdp,
      sid,
      `(() => ({
        title: document.title,
        path: location.pathname,
        hasUser: !!document.querySelector('input[placeholder="用户名"]'),
        hasPwd: !!document.querySelector('input[placeholder="密码"]'),
        hasBtn: !!document.querySelector('.login-card__submit'),
      }))()`,
    )
    check(
      'U1',
      'login-page-loads',
      'npm run dev 启动，登录页可访问（标题/用户名/密码/按钮齐全）',
      u1.title === '登录 · 背记系统 · 管理端' &&
        u1.path === `${BASE}login` &&
        u1.hasUser &&
        u1.hasPwd &&
        u1.hasBtn,
      JSON.stringify(u1),
    )
    if (!u1.hasUser) return finish(1, backendLog, viteLog, browserLog)

    // =====================================================================
    // U3 + U6 —— 错误密码：提示含 trace_id、停留登录页、**只弹一次**
    // =====================================================================
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}login`)
    await submitLogin(cdp, sid, ADMIN_USER, 'definitely-wrong-password')
    await sleep(400) // 让 dedupe 窗口（1200ms）内的重复调用有机会发生

    const u3path = await evalJs(cdp, sid, 'location.pathname')
    const u3toasts = await evalJs(cdp, sid, TOAST_TEXTS)
    const warns = JSON.parse(await evalJs(cdp, sid, 'JSON.stringify(window.__warns || [])'))
    const loginFail = warns.find((w) => w[0] === '[登录失败]')?.[1] ?? null

    check(
      'U3',
      'wrong-pwd-trace-id',
      '错误密码登录：提示含 trace_id、停留登录页',
      u3path === `${BASE}login` &&
        u3toasts.length >= 1 &&
        /trace_id/.test(u3toasts.join(' ')) &&
        Boolean(loginFail?.traceId),
      `path=${u3path} toast=${JSON.stringify(u3toasts)} traceId=${loginFail?.traceId ?? '-'}`,
    )
    check(
      'U3',
      'wrong-pwd-code-20002',
      '错误密码的业务码是 20002（前端拿到的是后端真码，不是 HTTP 兜底码）',
      loginFail?.code === 20002,
      `code=${loginFail?.code}`,
    )
    check(
      'U6',
      'error-toast-once',
      '一次失败**只弹一次**提示（页面不再重复 ElMessage.error）',
      u3toasts.length === 1,
      `toast 数=${u3toasts.length}`,
    )
    say(`    U3 toast 原文：${JSON.stringify(u3toasts)}`)
    await dismissToasts(cdp, sid)

    // =====================================================================
    // U4 —— 学生账号登录管理端：被拒 20002
    // =====================================================================
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}login`)
    await submitLogin(cdp, sid, STUDENT_USER, STUDENT_PWD)
    await sleep(400)
    const u4path = await evalJs(cdp, sid, 'location.pathname')
    const u4warns = JSON.parse(await evalJs(cdp, sid, 'JSON.stringify(window.__warns || [])'))
    const u4fail = u4warns.find((w) => w[0] === '[登录失败]')?.[1] ?? null
    check(
      'U4',
      'student-rejected-20002',
      '学生账号登录管理端被拒（role 不符 → 20002），停留登录页',
      u4path === `${BASE}login` && u4fail?.code === 20002,
      `path=${u4path} code=${u4fail?.code}`,
    )
    check(
      'U4',
      'no-token-leak',
      '被拒后 localStorage 里没有 token（没把失败响应当成功）',
      !(await evalJs(cdp, sid, `localStorage.getItem('bjsys_admin_token')`)),
    )
    await dismissToasts(cdp, sid)

    // =====================================================================
    // U2 —— 正确账号登录：进工作台 + token 落 localStorage
    // =====================================================================
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}login`)
    await submitLogin(cdp, sid, ADMIN_USER, ADMIN_PWD)
    await waitFor(cdp, sid, `location.pathname === ${JSON.stringify(`${BASE}dashboard`)}`, '跳转工作台', 15000)
    // 路由跳转后再给渲染一拍
    await waitFor(cdp, sid, `!!document.querySelector('.admin-header__user')`, '主布局渲染', 8000)
    const u2 = await evalJs(
      cdp,
      sid,
      `(() => ({
        path: location.pathname,
        token: localStorage.getItem('bjsys_admin_token') || '',
        storedUser: JSON.parse(localStorage.getItem('bjsys_admin_user') || 'null'),
        title: document.title,
        menus: Array.from(document.querySelectorAll('.el-menu-item')).map(e => e.textContent.trim()),
        headerUser: document.querySelector('.admin-header__user')?.textContent?.trim() || '',
        hasPlaceholder: !!document.querySelector('.dashboard-placeholder'),
      }))()`,
    )
    // 顶部栏文案走 store 的 `displayName = real_name || username`
    // （见 `stores/user.ts`）。后端建初始管理员时 `real_name` 填的是「管理者」
    // （`main.py::_ensure_admin_account`），所以这里期望的是「管理者」而不是 `admin`。
    // ⚠️ 别想当然地把断言写成 `headerUser === ADMIN_USER` —— 那是测试的错，不是产品的错。
    const expectedName = u2.storedUser?.real_name || u2.storedUser?.username || ''
    check(
      'U2',
      'login-ok-dashboard',
      '正确账号登录 → 进入工作台；标题/菜单/顶部栏用户名正确',
      u2.path === `${BASE}dashboard` &&
        u2.title === '工作台 · 背记系统 · 管理端' &&
        u2.menus.includes('工作台') &&
        u2.headerUser === expectedName,
      JSON.stringify({ path: u2.path, title: u2.title, menus: u2.menus, headerUser: u2.headerUser, expectedName }),
    )
    check(
      'U2',
      'header-shows-displayname',
      '顶部栏按 displayName 语义显示（real_name 优先，回退 username），账号本身是 admin',
      u2.headerUser === (u2.storedUser?.real_name || u2.storedUser?.username) &&
        u2.storedUser?.username === ADMIN_USER,
      `header=${u2.headerUser} real_name=${u2.storedUser?.real_name} username=${u2.storedUser?.username}`,
    )
    check(
      'U2',
      'token-persisted',
      'token 与 user 落 localStorage（后者用于刷新后顶部栏不空）',
      u2.token.length > 20 && u2.storedUser?.role === 'admin' && u2.storedUser?.username === ADMIN_USER,
      `token.len=${u2.token.length} user=${JSON.stringify(u2.storedUser)}`,
    )
    check('U2', 'dashboard-placeholder', '工作台渲染出 WEB-03 的服务地址挂载点', u2.hasPlaceholder)

    // =====================================================================
    // U9 —— 刷新后登录态保持
    // =====================================================================
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}dashboard`)
    const u9 = await evalJs(
      cdp,
      sid,
      `(() => ({
        path: location.pathname,
        token: localStorage.getItem('bjsys_admin_token') || '',
        user: document.querySelector('.admin-header__user')?.textContent?.trim() || '',
        storedUser: JSON.parse(localStorage.getItem('bjsys_admin_user') || 'null'),
      }))()`,
    )
    check(
      'U9',
      'refresh-keeps-session',
      '刷新页面：登录态保持（token 在 localStorage，未被守卫踢回登录页）',
      u9.path === `${BASE}dashboard` &&
        u9.token.length > 20 &&
        u9.storedUser?.username === ADMIN_USER &&
        u9.user === (u9.storedUser?.real_name || u9.storedUser?.username),
      JSON.stringify({ path: u9.path, user: u9.user, username: u9.storedUser?.username }),
    )

    // =====================================================================
    // U5 —— 未登录访问受保护页面 → 跳登录并带 redirect
    // =====================================================================
    await evalJs(cdp, sid, `localStorage.clear()`)
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}dashboard`)
    await waitFor(cdp, sid, `location.pathname === ${JSON.stringify(`${BASE}login`)}`, '被踢回登录页', 10000)
    const u5 = await evalJs(cdp, sid, `(() => ({ path: location.pathname, search: location.search }))()`)
    check(
      'U5',
      'guard-redirect',
      '未登录访问 /dashboard → 重定向登录页，且 redirect 参数保留原地址',
      u5.path === `${BASE}login` && new URLSearchParams(u5.search).get('redirect') === '/dashboard',
      JSON.stringify(u5),
    )

    // =====================================================================
    // U7 —— token 失效 → 清 token + 跳登录（见文件头「U7 的真相」）
    // =====================================================================
    await submitLogin(cdp, sid, ADMIN_USER, ADMIN_PWD)
    await waitFor(cdp, sid, `location.pathname === ${JSON.stringify(`${BASE}dashboard`)}`, '再次登录进工作台', 15000)
    await sleep(300)
    const u7 = await evalJs(
      cdp,
      sid,
      `(async () => {
        // 与 App 同一份模块实例：vite dev 的模块注册表按**解析后的 URL** 去重，
        // '@\\/api\\/client' 与 '/admin/src/api/client.ts' 解析到同一 URL。
        const mod = await import('/admin/src/api/client.ts');
        mod.setToken('this-is-not-a-valid-jwt');
        let code = null, message = '';
        try { await mod.api.get('/students') } catch (e) { code = e && e.code; message = e && e.message }
        return { code, message, tokenAfter: mod.getToken(), storedAfter: localStorage.getItem('bjsys_admin_token') };
      })()`,
    )
    check(
      'U7',
      'expired-token-10002',
      '失效 token 调 /api/students → 抛 BizError code=10002',
      u7.code === 10002,
      `code=${u7.code} msg=${u7.message}`,
    )
    check(
      'U7',
      'clear-token',
      '拦截器立即清掉 localStorage 里的 token',
      u7.tokenAfter === '' && !u7.storedAfter,
      `modToken='${u7.tokenAfter}' ls=${u7.storedAfter}`,
    )
    let u7path = ''
    try {
      u7path = await waitFor(
        cdp,
        sid,
        `location.pathname === ${JSON.stringify(`${BASE}login`)} && location.pathname`,
        '自动跳登录页',
        8000,
      )
    } catch {
      u7path = await evalJs(cdp, sid, 'location.pathname')
    }
    const u7search = await evalJs(cdp, sid, 'location.search')
    const u7user = await evalJs(cdp, sid, `localStorage.getItem('bjsys_admin_user')`)
    check(
      'U7',
      'auto-redirect-login',
      'unauthorizedHandler 触发：Pinia 登出 + 路由跳登录页（带 redirect）',
      u7path === `${BASE}login` && new URLSearchParams(u7search).get('redirect') === '/dashboard' && !u7user,
      `path=${u7path} search=${u7search}`,
    )

    return finish(0, backendLog, viteLog, browserLog)
  } catch (err) {
    say(`\n[EXCEPTION] ${err?.stack || err}`)
    console.log(`[!!] exception  ${err?.message || err}`)
    return finish(1, backendLog, viteLog, browserLog)
  } finally {
    cdp?.close()
    for (const p of [...procs].reverse()) killTree(p.pid)
    await sleep(600)
    try {
      rmSync(tmp, { recursive: true, force: true })
    } catch {
      /* ignore */
    }
  }
}

async function tryEvalVersion(cdp) {
  try {
    const r = await cdp.send('Browser.getVersion')
    return r.product || 'unknown'
  } catch {
    return ''
  }
}

function finish(code, backendLog, viteLog, browserLog) {
  const failed = results.filter((r) => !r.ok)
  say('')
  say('='.repeat(74))
  say(`烟测结果：${results.length - failed.length}/${results.length} 通过`)
  if (failed.length) {
    say('未通过项：')
    for (const f of failed) say(`  - ${f.id} ${f.title}${f.detail ? `    ${f.detail}` : ''}`)
    say('')
    say(`后端日志：${backendLog}`)
    say(`vite 日志：${viteLog}`)
    say(`浏览器日志：${browserLog}`)
  }
  say('='.repeat(74))
  writeFileSync(LOG_FILE, logLines.join('\n') + '\n', 'utf8')

  console.log('')
  console.log(`RESULT ${results.length - failed.length}/${results.length} ${failed.length ? 'FAIL' : 'PASS'}`)
  if (failed.length) console.log(`FAILED ${failed.map((f) => `${f.id}:${f.slug}`).join(' ')}`)
  console.log(`LOG ${LOG_FILE}`)
  return code === 0 && failed.length ? 1 : code
}

process.exit(await main())
