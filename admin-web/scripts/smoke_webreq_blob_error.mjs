#!/usr/bin/env node
/**
 * 请求层「blob 通道」回归 —— 真浏览器 + 真 vite dev server + 真后端。
 *
 * ------------------------------------------------------------------ 为什么需要这个脚本
 *
 * `src/api/client.ts` 属于 **WP-WEB-01**（受保护文件）。WP-WEB-02 为了下载 Excel 模板
 * 给它加了 `responseType: 'blob'`（记为跨包改动 #62）。复核该改动时发现一个**静默缺陷**：
 *
 *   本项目的契约是「**业务失败一律 HTTP 200 + 非零 code**」（client.ts 头部第 6 条 / 契约 §5.1）。
 *   而 axios 在 `responseType: 'blob'` 下会把这段 JSON 错误体**原样包成一个 Blob**。
 *   旧的 blob 分支是 `return response.data as T` —— 直接把它当成合法文件返回：
 *     · 调用方 `URL.createObjectURL(blob)` → 用户下载到一个**内容是 `{"code":10001,...}` 的
 *       「Excel」**（文件名还是 `.xlsx`）；
 *     · 因为**没有任何东西抛错**，本层的统一错误提示（`toastOnce`）**一次都不弹**。
 *   这正好踩中本文件头部第 10 行自己写的红线：「不能靠 HTTP 状态判断成败」。
 *
 * 光看 `typecheck` / `build` 通过**完全看不出来**（编译期没有任何问题）——所以必须
 * 用真浏览器打真后端，把「200 + 非零 code」这个真实响应喂进真实生产代码路径。
 *
 * ------------------------------------------------------------------ 用例设计（防假绿）
 *
 *   C1 对照：**不用** blob（普通 JSON 通道）打同一个错误端点 → 必须抛 `BizError(10001)`
 *            （证明端点、鉴权、业务错误链路本身是通的，C2 的失败不能甩锅给"后端坏了"）
 *   C2 主用例：**用** blob 打同一个错误端点 → 必须抛 `BizError(10001)` + 弹一次中文提示；
 *              若返回了 Blob，把 `type/size/正文开头` 全打出来（这就是"静默下载假文件"的现场）
 *   C3 正例：blob 打**真模板**（`/import/template/words`）→ 必须拿到**真 xlsx**（`PK\x03\x04` 魔数、非空）
 *            （保证修法没把正常下载一起改坏）
 *   C4 正例：blob 打**学生名单模板**（另一个 api 模块）→ 同上
 *   C5 边界：blob 打**不存在的路径** → HTTP 404 也必须抛（不能吞）
 *
 * 反向验证：本脚本在修复**前**必须是 C2 红（返回 Blob、无提示），修复后必须全绿。
 * 若「修前也绿」，说明这条用例没有真正覆盖到缺陷。
 *
 * ------------------------------------------------------------------ 跑法
 *
 *   cd admin-web && env -u PYTHONPATH node scripts/smoke_webreq_blob_error.mjs
 *
 * 可选环境变量：SMOKE_BACKEND_PORT（默认 8818）/ SMOKE_WEB_PORT（默认 5286）/ SMOKE_BROWSER
 * 退出码：0 = 全通过；1 = 有失败；2 = 环境不满足（绝不把"跑不起来"报成 0）。
 */

import { spawn, spawnSync } from 'node:child_process'
import { createWriteStream, existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs'
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
const LOG_FILE = join(LOG_DIR, 'smoke_webreq_blob.txt')

const isWin = process.platform === 'win32'
const PYTHON = join(BACKEND, '.venv', isWin ? 'Scripts/python.exe' : 'bin/python')
const VITE_BIN = join(ADMIN_WEB, 'node_modules', 'vite', 'bin', 'vite.js')

// --------------------------------------------------------------------------- 配置

const BACKEND_PORT = Number(process.env.SMOKE_BACKEND_PORT || 8818)
const WEB_PORT = Number(process.env.SMOKE_WEB_PORT || 5286)

const BASE = '/admin/'
const BACKEND_BASE = `http://127.0.0.1:${BACKEND_PORT}`
const WEB_BASE = `http://127.0.0.1:${WEB_PORT}`

const ADMIN_USER = 'admin'
const ADMIN_PWD = 'webreq-smoke-pass-9911'

const BROWSER_CANDIDATES = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
]

/** 非跳过用例数下限（C1–C5）。少了就是有分支被静默跳过，判失败。 */
const MIN_CASES = 5

// --------------------------------------------------------------------------- 结果收集

/** @type {{id: string, slug: string, title: string, ok: boolean, detail: string, skipped?: boolean}[]} */
const results = []
const logLines = []

/** 整轮被异常中断时的错误（中断本身要算一条失败，否则断在半路看起来是全绿）。 */
let ABORT_ERROR = null

function say(line = '') {
  logLines.push(line)
}

function check(id, slug, title, ok, detail = '') {
  results.push({ id, slug, title, ok: Boolean(ok), detail, skipped: false })
  const tag = ok ? '[OK]' : '[!!]'
  say(`${tag} ${id} ${title}${detail ? `    ${detail}` : ''}`)
  console.log(`${tag} ${id} ${slug}${!ok && detail ? `  (${detail})` : ''}`)
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

/** 直接打后端（Node 侧，用来拿 admin token；不走前端）。 */
async function loginAs(username, password, role) {
  const r = await fetch(`${BACKEND_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, role }),
  })
  return r.json()
}

// --------------------------------------------------------------------------- CDP over pipe

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
      while ((idx = buf.indexOf('\u0000')) >= 0) {
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
    if (this.closed) return Promise.reject(new Error('CDP 已关闭'))
    const id = ++this.seq
    const payload = { id, method, params }
    if (sessionId) payload.sessionId = sessionId
    return new Promise((res, rej) => {
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) rej(new Error(`CDP ${method} 超时`))
      }, 25000)
      this.pending.set(id, { resolve: res, reject: rej, timer })
      this.writable.write(JSON.stringify(payload) + '\u0000')
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

async function evalJs(cdp, sid, expression) {
  let last = ''
  for (let i = 0; i < 8; i++) {
    const r = await tryEval(cdp, sid, expression)
    if (r.ok) return r.value
    last = r.error
    await sleep(200)
  }
  throw new Error(`页面求值失败：${last}\n表达式：${String(expression).slice(0, 240)}`)
}

async function waitFor(cdp, sid, expr, label, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const r = await tryEval(cdp, sid, expr)
    if (r.ok && r.value) return r.value
    await sleep(120)
  }
  throw new Error(`等待超时：${label}`)
}

async function hardNavigate(cdp, sid, url) {
  await cdp.send('Page.navigate', { url }, sid)
  await sleep(150)
  await waitFor(cdp, sid, `document.readyState === 'complete'`, `readyState=complete @ ${url}`, 20000)
  await waitFor(
    cdp,
    sid,
    `!!document.querySelector('#app') && document.querySelector('#app').children.length > 0`,
    `Vue 挂载完成 @ ${url}`,
    15000,
  )
}

/**
 * 在**页面内**执行一次真实 `api.get()`，把结果归一化成可断言的结构。
 *
 * 关键：拿的是 `import('/admin/src/api/client.ts')` —— 与 App **同一份模块单例**，
 * 不是另起炉灶手搓 axios（否则测的就不是生产代码路径了）。
 */
function blobProbeExpr(path, { responseType = null, token } = {}) {
  const opts = responseType ? `{ responseType: ${JSON.stringify(responseType)} }` : 'undefined'
  return `(async () => {
    const m = await import('/admin/src/api/client.ts');
    ${token ? `m.setToken(${JSON.stringify(token)});` : ''}
    try {
      const r = await m.api.get(${JSON.stringify(path)}, ${opts});
      // 没抛错 —— 把拿到的"东西"如实报出来（C2 红的时候这里会是 blob）
      let head = null, size = null, type = null, ctor = null;
      if (r instanceof Blob) {
        ctor = 'Blob'; type = r.type; size = r.size;
        try { head = (await r.text()).slice(0, 160); } catch { head = '(读不出文本)'; }
      } else {
        ctor = r === null ? 'null' : typeof r;
      }
      return { threw: false, ctor, type, size, head, xlsxMagic: null };
    } catch (e) {
      let xlsxMagic = null;
      // 万一抛出来的东西身上挂着 Blob（不该发生），也看一眼
      if (e && e.data instanceof Blob) { try { xlsxMagic = 'has-blob-in-error' } catch {} }
      return {
        threw: true,
        isBizError: e && e.name === 'BizError',
        code: e && typeof e.code === 'number' ? e.code : null,
        message: e && e.message ? String(e.message) : null,
        display: e && e.display ? String(e.display) : null,
        hasTraceId: !!(e && e.traceId),
        ctor: e && e.constructor ? e.constructor.name : null,
      };
    }
  })()`
}

/** 单独探一条「正例下载」：断言拿到真 xlsx。 */
function blobFileProbeExpr(path, token) {
  return `(async () => {
    const m = await import('/admin/src/api/client.ts');
    m.setToken(${JSON.stringify(token)});
    try {
      const r = await m.api.get(${JSON.stringify(path)}, { responseType: 'blob' });
      if (!(r instanceof Blob)) return { threw: false, isBlob: false, ctor: typeof r };
      const buf = new Uint8Array(await r.arrayBuffer());
      const magic = Array.from(buf.slice(0, 4)).map(b => b.toString(16).padStart(2, '0')).join('');
      return { threw: false, isBlob: true, type: r.type, size: r.size, magic };
    } catch (e) {
      return { threw: true, code: e && e.code, message: e && e.message, ctor: e && e.constructor && e.constructor.name };
    }
  })()`
}

// --------------------------------------------------------------------------- 主流程

async function main() {
  // 0. 环境预检
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

  const tmp = join(tmpdir(), `bjsys_webreq_${Date.now()}`)
  mkdirSync(tmp, { recursive: true })
  const dbFile = join(tmp, 'webreq.db')
  const backendLog = join(tmp, 'backend.log')
  const browserLog = join(tmp, 'browser.log')
  const viteLog = join(tmp, 'vite.log')
  const profile = join(tmp, 'browser-profile')

  const procs = []
  let cdp = null

  try {
    // 1. 起后端（临时空库 + 已知口令）
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
      // 早退也走 finish()：否则证据不落盘，事后只能靠猜
      return finish(1, backendLog, viteLog, browserLog)
    }

    const adminLogin = await loginAs(ADMIN_USER, ADMIN_PWD, 'admin')
    if (adminLogin.code !== 0) {
      console.log(`[!!] env-admin-login-failed  code=${adminLogin.code}`)
      say(JSON.stringify(adminLogin))
      return finish(1, backendLog, viteLog, browserLog)
    }
    const adminToken = adminLogin.data.access_token
    say('后端预检：admin 登录 OK')

    // 2. 起 vite dev server
    say('== 启动 vite dev server ==')
    const viteLogStream = createWriteStream(viteLog)
    const vite = spawn(process.execPath, [VITE_BIN], {
      cwd: ADMIN_WEB,
      env: {
        ...process.env,
        VITE_DEV_PORT: String(WEB_PORT),
        VITE_API_TARGET: BACKEND_BASE,
        VITE_DEV_HOST: '127.0.0.1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    procs.push(vite)
    vite.stdout.pipe(viteLogStream)
    vite.stderr.pipe(viteLogStream)

    if (!(await waitHttp(`${WEB_BASE}${BASE}`, 90000))) {
      console.log('[!!] env-vite-down  vite 未就绪（日志见下）')
      say('vite 未就绪，日志尾部：')
      say(readFileSync(viteLog, 'utf8').slice(-4000) || '（vite 一行日志都没有）')
      return finish(1, backendLog, viteLog, browserLog)
    }

    // 3. 起浏览器（CDP over pipe）
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
        '--window-size=1440,1000',
        'about:blank',
      ],
      { stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'] },
    )
    procs.push(br)
    br.stdio[2].pipe(brLogStream)

    cdp = new CDP(br)
    let ver = ''
    for (let i = 0; i < 30 && !ver; i++) {
      try {
        const r = await cdp.send('Browser.getVersion')
        ver = r.product || 'unknown'
      } catch {
        ver = ''
      }
      if (!ver) await sleep(400)
    }
    if (!ver) {
      console.log('[!!] env-browser-cdp-down  浏览器 CDP 管道没起来')
      say(readFileSync(browserLog, 'utf8').slice(-2000))
      return finish(1, backendLog, viteLog, browserLog)
    }
    say(`浏览器：${ver}`)

    const { sessionId: sid } = await cdp.newPage('about:blank')
    say(`CDP session：${sid}`)

    // 打开真 vite 页面（模块 import 必须同源）
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}`)
    say('vite 页面挂载完成（可 import 真实模块）')

    // 自证：拿到的确实是同一份 client 模块
    const modOk = await evalJs(
      cdp,
      sid,
      `(async () => { const m = await import('/admin/src/api/client.ts'); return typeof m.api?.get === 'function' && typeof m.setToken === 'function'; })()`,
    )
    if (!modOk) {
      console.log('[!!] env-module-missing  页面里拿不到 client.ts 的 api/setToken')
      return finish(1, backendLog, viteLog, browserLog)
    }

    // =====================================================================
    // C1 —— 对照组：普通 JSON 通道打同一个错误端点，必须抛 10001
    // =====================================================================
    const c1 = await evalJs(cdp, sid, blobProbeExpr('/import/template/nosuch', { token: adminToken }))
    check(
      'C1',
      'json-channel-error',
      '对照：普通 JSON 通道打错误端点 → 抛 BizError(10001)',
      c1.threw && c1.isBizError && c1.code === 10001,
      JSON.stringify(c1),
    )

    // 错峰 1.4s：绕开本层 1200ms 的提示去重（否则 C2 的提示会被 C1 吃掉）
    await sleep(1400)

    // =====================================================================
    // C2 —— 主用例：blob 通道打同一个错误端点
    // =====================================================================
    const c2 = await evalJs(
      cdp,
      sid,
      blobProbeExpr('/import/template/nosuch2', { responseType: 'blob', token: adminToken }),
    )
    let c2Toast = ''
    try {
      c2Toast = await waitFor(
        cdp,
        sid,
        `(() => { const el = document.querySelector('.el-message'); return el && el.innerText ? el.innerText : ''; })()`,
        'blob 通道出错时弹中文提示',
        4000,
      )
    } catch {
      c2Toast = ''
    }
    const c2Ok = c2.threw && c2.isBizError === true && c2.code === 10001
    check(
      'C2',
      'blob-channel-error',
      'blob 通道打错误端点 → 抛 BizError(10001)（不得静默返回假文件）',
      c2Ok,
      c2Ok
        ? `提示：${c2Toast ? c2Toast.replace(/\n/g, ' ') : '(无 DOM 提示，但已抛业务错)'}`
        : `实际拿到：${JSON.stringify(c2)} —— 这就是"静默下载假文件"现场`,
    )
    if (!c2Ok) {
      say('')
      say('⚠️ C2 复现了缺陷：blob 通道把「HTTP 200 + 非零 code」的 JSON 错误体当成合法文件返回了。')
      say(`   axios 送的 Content-Type：${c2.type}`)
      say(`   下载下来的"文件"大小：${c2.size} 字节`)
      say(`   它的正文开头（本该是 xlsx 的 PK 魔数，实际是 JSON）：`)
      say(`   ${String(c2.head).replace(/\n/g, ' ')}`)
      say('   → 用户会拿到一个扩展名 .xlsx、内容是业务错误 JSON 的文件，且**没有任何错误提示**。')
      say('')
    }

    // =====================================================================
    // C3 / C4 —— 正例：真模板必须还是「真 xlsx」（修法不能误伤正常下载）
    // =====================================================================
    const c3 = await evalJs(cdp, sid, blobFileProbeExpr('/import/template/words', adminToken))
    check(
      'C3',
      'blob-real-template-words',
      'blob 通道下载真模板 → 拿到真 xlsx（PK 魔数）',
      c3.threw === false && c3.isBlob === true && c3.magic === '504b0304' && Number(c3.size) > 0,
      JSON.stringify(c3),
    )

    const c4 = await evalJs(
      cdp,
      sid,
      blobFileProbeExpr('/students/import/template', adminToken),
    )
    check(
      'C4',
      'blob-real-template-students',
      'blob 通道下载学生名单模板 → 拿到真 xlsx（另一个 api 模块）',
      c4.threw === false && c4.isBlob === true && c4.magic === '504b0304' && Number(c4.size) > 0,
      JSON.stringify(c4),
    )

    // =====================================================================
    // C5 —— 边界：blob 通道遇 HTTP 404 也必须抛，不能吞
    // =====================================================================
    const c5 = await evalJs(
      cdp,
      sid,
      blobProbeExpr('/definitely/not/a/real/path', { responseType: 'blob', token: adminToken }),
    )
    check(
      'C5',
      'blob-channel-http404',
      'blob 通道打不存在的路径 → HTTP 404 也必须抛错（不能吞）',
      c5.threw === true && c5.isBizError === true,
      JSON.stringify(c5),
    )

    return finish(0, backendLog, viteLog, browserLog)
  } catch (err) {
    ABORT_ERROR = err
    say(`\n[EXCEPTION] ${err?.stack || err}`)
    console.log(`[!!] exception  ${err?.message || err}`)
    return finish(1, backendLog, viteLog, browserLog)
  } finally {
    if (cdp) cdp.close()
    for (const p of procs) killTree(p.pid)
    await sleep(300)
    try {
      rmSync(tmp, { recursive: true, force: true })
    } catch {
      /* ignore */
    }
  }
}

// --------------------------------------------------------------------------- 收尾

function finish(code, backendLog, viteLog, browserLog) {
  if (ABORT_ERROR) {
    results.push({
      id: 'ABORT',
      slug: 'run-aborted',
      title: `整轮被异常中断（后续用例全部未执行）：${ABORT_ERROR?.message || ABORT_ERROR}`,
      ok: false,
      detail: '',
      skipped: false,
    })
    code = 1
  }

  const failed = results.filter((r) => !r.ok && !r.skipped)
  const ran = results.filter((r) => !r.skipped).length
  const verdict = failed.length === 0 && ran >= MIN_CASES ? 'PASS' : 'FAIL'
  if (failed.length === 0 && ran < MIN_CASES) {
    say(`\n❌ 只跑了 ${ran} 条用例，少于下限 ${MIN_CASES} —— 有分支被静默跳过，判失败`)
  }

  say('')
  say('='.repeat(72))
  for (const r of results) say(`${r.ok ? '[OK]' : '[!!]'} ${r.id}  ${r.title}`)
  say('='.repeat(72))
  say(`RESULT ${results.filter((r) => r.ok).length}/${ran} ${verdict}`)
  if (failed.length) {
    say('失败项：')
    for (const f of failed) say(`  - ${f.id} ${f.title}：${f.detail}`)
  }
  for (const [name, p] of [
    ['后端日志', backendLog],
    ['vite 日志', viteLog],
    ['浏览器日志', browserLog],
  ]) {
    if (p && existsSync(p)) say(`${name}：${p}`)
  }

  const out = logLines.join('\n')
  try {
    createWriteStream(LOG_FILE).end(out + '\n')
  } catch {
    /* ignore */
  }
  console.log(`\n烟测结果：${results.filter((r) => r.ok).length}/${ran} ${verdict === 'PASS' ? '通过' : '未通过'}`)
  console.log(`日志：${LOG_FILE}`)
  return code
}

const rc = await main()
process.exit(rc ?? 0)
