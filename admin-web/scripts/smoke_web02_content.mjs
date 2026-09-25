#!/usr/bin/env node
/**
 * WP-WEB-02 真机联调烟测 —— 真浏览器 + 真 vite dev server + 真后端。
 *
 * 为什么是真机：typecheck / build 过了只说明「能编译」。这里跑的是**真实生产代码路径**：
 *   1. 真 Edge（CDP over pipe）打开真 vite dev server 页面；
 *   2. 真实登录（UI 点击）→ localStorage 拿到真 token；
 *   3. 通过页面内 `import('/admin/src/api/*.ts')` 拿到**与 App 同一份**模块单例，
 *      调用真实 `previewWords / commitWords / previewTexts / commitTexts / downloadTemplate
 *      / listUnits / listUnitWords / listUnitTexts / deleteUnit` —— 即前端真实请求层 + 真实后端；
 *   4. 关键 UI 行为（新建单元弹窗、进入内容页/导入页、模板下载）走真实 DOM 点击。
 *
 * 防假绿（与 smoke_web01 同口径，且更严）：
 *   - 后端没起来 / 端口被占用 / 缺依赖 → 退出码 2（env 不满足）或 1（起不来），绝不 0；
 *   - 结尾断言 `results.length >= MIN_CASES`，避免「用例被静默跳过却报通过」；
 *   - 每个断言都是对真实后端返回值的判断，不是「代码看着对」。
 *
 * 测试数据（一次性临时空库，口令不污染任何真实数据）：
 *   - W2  UI 新建单元 SmokeUnitUI；
 *   - W6/W7 词条导入：SmokeWordsUnit（含 2 行非法 → invalid_rows）；
 *   - W9/W10 课文导入：SmokeTextsUnit（4 段，调整背诵范围 [0,2]）。
 *
 * 诚实上报的偏离项（本烟测覆盖不到，需在交付报告说明）：
 *   - F12「有效行明细表格」：后端 words preview **不返回**有效行明细，只给 summary +
 *     invalid_rows，本脚本据此断言 summary/invalid_rows（见交付报告偏离项 #1）；
 *   - F3 删「有练习记录」单元 → 30003 弹「改为停用」：30003 需存在练习记录（WEB-03/04
 *     学生端产生），WP-WEB-02 烟测环境无法自然造出，故 30003 分支只做代码审查 +
 *     文档说明，运行期只验证「删除无记录单元成功」这一可触发分支（W11）。
 *
 * 跑法：
 *     cd admin-web && node scripts/smoke_web02_content.mjs
 * 可选环境变量：SMOKE_BACKEND_PORT / SMOKE_WEB_PORT / SMOKE_BROWSER
 *
 * 退出码：0 = 全通过；1 = 有失败；2 = 环境不满足。
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
const LOG_FILE = join(LOG_DIR, 'smoke_web02.txt')

const isWin = process.platform === 'win32'
const PYTHON = join(BACKEND, '.venv', isWin ? 'Scripts/python.exe' : 'bin/python')
const VITE_BIN = join(ADMIN_WEB, 'node_modules', 'vite', 'bin', 'vite.js')

// --------------------------------------------------------------------------- 配置

const BACKEND_PORT = Number(process.env.SMOKE_BACKEND_PORT || 8811)
const WEB_PORT = Number(process.env.SMOKE_WEB_PORT || 5275)

const BASE = '/admin/'
const BACKEND_BASE = `http://127.0.0.1:${BACKEND_PORT}`
const WEB_BASE = `http://127.0.0.1:${WEB_PORT}`

const ADMIN_USER = 'admin'
const ADMIN_PWD = 'web02-smoke-pass-7788'

const BROWSER_CANDIDATES = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
]

const MIN_CASES = 13

// --------------------------------------------------------------------------- 结果收集

/** @type {{id: string, slug: string, title: string, ok: boolean, detail: string, skipped?: boolean}[]} */
const results = []
const logLines = []

function say(line = '') {
  logLines.push(line)
}

function check(id, slug, title, ok, detail = '', skipped = false) {
  results.push({ id, slug, title, ok: Boolean(ok), detail, skipped })
  const tag = skipped ? '[SKIP]' : ok ? '[OK]' : '[!!]'
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

// --------------------------------------------------------------------------- 极简 xlsx 写出（无第三方依赖）
//
// 后端 `core/excel.py::read_xlsx` 用 openpyxl `load_workbook(read_only=True, data_only=True)`
// 读 `inlineStr` 单元格，本写出器生成的就是最低限度、openpyxl 可解析的 xlsx。
// 采用 stored（压缩方式 0）写入，CRC32 自行实现，避免依赖 zlib.crc32 的版本差异。

const CRC_TABLE = (() => {
  const t = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    t[n] = c >>> 0
  }
  return t
})()
function crc32(buf) {
  let c = 0xffffffff
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

function colLetter(n) {
  let s = ''
  while (n > 0) {
    const m = (n - 1) % 26
    s = String.fromCharCode(65 + m) + s
    n = Math.floor((n - 1) / 26)
  }
  return s
}

function xmlEscape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function sheetXml(headers, rows) {
  let xml =
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
  // 表头
  xml += '<row r="1">'
  headers.forEach((h, i) => {
    const c = colLetter(i + 1)
    xml += `<c r="${c}1" t="inlineStr"><is><t xml:space="preserve">${xmlEscape(h)}</t></is></c>`
  })
  xml += '</row>'
  // 数据行
  rows.forEach((row, ri) => {
    const r = ri + 2
    xml += `<row r="${r}">`
    row.forEach((cell, i) => {
      const c = colLetter(i + 1)
      if (typeof cell === 'number') {
        xml += `<c r="${c}${r}"><v>${cell}</v></c>`
      } else {
        xml += `<c r="${c}${r}" t="inlineStr"><is><t xml:space="preserve">${xmlEscape(cell)}</t></is></c>`
      }
    })
    xml += '</row>'
  })
  xml += '</sheetData></worksheet>'
  return xml
}

function makeXlsx(headers, rows) {
  const sheet = sheetXml(headers, rows)
  const parts = [
    {
      name: '[Content_Types].xml',
      data: Buffer.from(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
          '<Default Extension="xml" ContentType="application/xml"/>' +
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' +
          '</Types>',
        'utf8',
      ),
    },
    {
      name: '_rels/.rels',
      data: Buffer.from(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
          '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
          '</Relationships>',
        'utf8',
      ),
    },
    {
      name: 'xl/workbook.xml',
      data: Buffer.from(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
          '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'utf8',
      ),
    },
    {
      name: 'xl/_rels/workbook.xml.rels',
      data: Buffer.from(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
          '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>' +
          '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>' +
          '</Relationships>',
        'utf8',
      ),
    },
    {
      name: 'xl/styles.xml',
      data: Buffer.from(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
          '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>' +
          '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>' +
          '<borders count="1"><border/></borders>' +
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>' +
          '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>' +
          '</styleSheet>',
        'utf8',
      ),
    },
    { name: 'xl/worksheets/sheet1.xml', data: Buffer.from(sheet, 'utf8') },
  ]

  const chunks = []
  const central = []
  let offset = 0
  for (const f of parts) {
    const nameBuf = Buffer.from(f.name, 'utf8')
    const crc = crc32(f.data)
    const size = f.data.length
    const local = Buffer.alloc(30)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(20, 4)
    local.writeUInt16LE(0, 6)
    local.writeUInt16LE(0, 8)
    local.writeUInt16LE(0, 10)
    local.writeUInt16LE(0, 12)
    local.writeUInt32LE(crc, 14)
    local.writeUInt32LE(size, 18)
    local.writeUInt32LE(size, 22)
    local.writeUInt16LE(nameBuf.length, 26)
    local.writeUInt16LE(0, 28)
    chunks.push(local, nameBuf, f.data)

    const cen = Buffer.alloc(46)
    cen.writeUInt32LE(0x02014b50, 0)
    cen.writeUInt16LE(20, 4)
    cen.writeUInt16LE(20, 6)
    cen.writeUInt16LE(0, 8)
    cen.writeUInt16LE(0, 10)
    cen.writeUInt16LE(0, 12)
    cen.writeUInt16LE(0, 14)
    cen.writeUInt32LE(crc, 16)
    cen.writeUInt32LE(size, 20)
    cen.writeUInt32LE(size, 24)
    cen.writeUInt16LE(nameBuf.length, 28)
    cen.writeUInt16LE(0, 30)
    cen.writeUInt16LE(0, 32)
    cen.writeUInt16LE(0, 34)
    cen.writeUInt16LE(0, 36)
    cen.writeUInt32LE(0, 38)
    cen.writeUInt32LE(offset, 42)
    central.push(cen, nameBuf)
    offset += local.length + nameBuf.length + f.data.length
  }
  const centralBuf = Buffer.concat(central)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(0, 4)
  end.writeUInt16LE(0, 6)
  end.writeUInt16LE(parts.length, 8)
  end.writeUInt16LE(parts.length, 10)
  end.writeUInt32LE(centralBuf.length, 12)
  end.writeUInt32LE(offset, 16)
  end.writeUInt16LE(0, 20)
  return Buffer.concat([...chunks, centralBuf, end])
}

function b64(buf) {
  return buf.toString('base64')
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
      while ((idx = buf.indexOf(' ')) >= 0) {
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
      this.writable.write(JSON.stringify(payload) + ' ')
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
  throw new Error(`页面求值失败：${last}\n表达式：${String(expression).slice(0, 200)}`)
}

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

// --------------------------------------------------------------------------- 页面操作封装

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

const CLICK_BY_TEXT = `(txt) => {
  const btn = Array.from(document.querySelectorAll('button')).find(b => (b.textContent || '').trim() === txt);
  if (!btn) return 'NOT_FOUND';
  btn.click();
  return 'CLICKED';
}`

async function submitLogin(cdp, sid, username, password) {
  const f1 = await evalJs(cdp, sid, fillExpr('用户名', username))
  const f2 = await evalJs(cdp, sid, fillExpr('密码', password))
  if (f1 === 'NOT_FOUND' || f2 === 'NOT_FOUND') throw new Error('登录表单元素没找到')
  const clicked = await evalJs(cdp, sid, `(() => {
    const btn = document.querySelector('.login-card__submit');
    if (!btn) return 'NOT_FOUND';
    btn.click();
    return 'CLICKED';
  })()`)
  if (clicked !== 'CLICKED') throw new Error('提交按钮没找到')
  await waitFor(
    cdp,
    sid,
    `(document.querySelectorAll('.el-message').length > 0) || !location.pathname.startsWith(${JSON.stringify(BASE + 'login')})`,
    '登录请求返回',
    15000,
  )
}

// 在页面上下文里调用真实 API 模块。import() 走 vite 模块注册表，拿到的是与 App 同一份单例。
// expr 必须是一个 async IIFE，返回可序列化值；抛错时由 tryEval 捕获为 {ok:false}。
async function apiCall(cdp, sid, expr) {
  const r = await tryEval(cdp, sid, expr)
  if (!r.ok) throw new Error(`API 调用失败：${r.error}`)
  return r.value
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

  const tmp = join(tmpdir(), `bjsys_web02_${Date.now()}`)
  mkdirSync(tmp, { recursive: true })
  const dbFile = join(tmp, 'web02.db')
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
      return 1
    }

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
    say(`后端预检：admin 登录 OK`)

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

    if (!(await waitHttp(`${WEB_BASE}${BASE}`, 60000))) {
      console.log('[!!] env-vite-down  vite 未就绪')
      say(readFileSync(viteLog, 'utf8').slice(-3000))
      return 1
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
        '--window-size=1440,900',
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
      return 1
    }
    say(`浏览器：${ver}`)

    const { sessionId: sid } = await cdp.newPage('about:blank')
    say(`CDP session：${sid}`)

    // =====================================================================
    // W1 —— 内容管理页可访问（路由聚合自动生效）
    // =====================================================================
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}login`)
    await submitLogin(cdp, sid, ADMIN_USER, ADMIN_PWD)
    await waitFor(cdp, sid, `location.pathname === ${JSON.stringify(`${BASE}dashboard`)}`, '跳转工作台', 15000)

    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}content/units`)
    const w1 = await evalJs(
      cdp,
      sid,
      `(() => ({
        path: location.pathname,
        hasCreate: Array.from(document.querySelectorAll('button')).some(b => (b.textContent||'').includes('新建单元')),
        hasFilter: !!document.querySelector('.filter-card'),
        title: document.title,
      }))()`,
    )
    check(
      'W1',
      'units-page-loads',
      '内容管理页可访问：路由 /content/units 挂载、含「新建单元」与筛选区',
      w1.path === `${BASE}content/units` && w1.hasCreate && w1.hasFilter,
      JSON.stringify(w1),
    )

    // =====================================================================
    // W2 —— UI 新建单元（真实弹窗 + 真实 createUnit 请求）
    // =====================================================================
    const unitUiName = `SmokeUnitUI_${Date.now()}`
    await evalJs(cdp, sid, `(${CLICK_BY_TEXT})('新建单元')`)
    await waitFor(cdp, sid, `!!document.querySelector('input[placeholder="如 Unit 1 Hello!"]')`, '新建弹窗出现', 8000)
    await evalJs(cdp, sid, fillExpr('如 Unit 1 Hello!', unitUiName))
    await evalJs(cdp, sid, `(${CLICK_BY_TEXT})('保存')`)
    // 列表会重载，等单元名出现在 DOM
    let w2dom = false
    try {
      await waitFor(
        cdp,
        sid,
        `document.body.innerText.includes(${JSON.stringify(unitUiName)})`,
        '列表渲染出新单元',
        10000,
      )
      w2dom = true
    } catch {
      w2dom = false
    }
    const w2api = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/content.ts');
        const res = await m.listUnits({ page: 1, page_size: 100 });
        const found = (res.items || []).find(u => u.name === ${JSON.stringify(unitUiName)});
        return { total: res.total, found: found ? { id: found.id, name: found.name, status: found.status } : null };
      })()`,
    )
    check(
      'W2',
      'create-unit-ui',
      'UI 新建单元：弹窗填写并提交，列表与接口均可见新单元',
      w2dom && Boolean(w2api.found) && w2api.found.status === 'active',
      `dom=${w2dom} api=${JSON.stringify(w2api)}`,
    )

    // =====================================================================
    // W3 —— 筛选（真实 listUnits 带 subject 参数）
    // =====================================================================
    const w3 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/content.ts');
        const all = await m.listUnits({ page: 1, page_size: 100 });
        const en = await m.listUnits({ subject: 'english', page: 1, page_size: 100 });
        const ok = (en.items || []).every(u => u.subject === 'english');
        return { allTotal: all.total, enTotal: en.total, allEnglish: ok };
      })()`,
    )
    check(
      'W3',
      'filter-subject',
      '单元筛选：listUnits(subject=english) 仅返回英语单元（含新建的单元）',
      w3.allEnglish && w3.enTotal >= 1,
      JSON.stringify(w3),
    )

    // =====================================================================
    // W4 —— 导入向导页可访问
    // =====================================================================
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}content/import`)
    const w4 = await evalJs(
      cdp,
      sid,
      `(() => ({
        path: location.pathname,
        hasTypeCard: Array.from(document.querySelectorAll('*')).some(e => (e.textContent||'').includes('英语词条') && (e.textContent||'').includes('语文课文')),
        hasStep: !!document.querySelector('.wizard-steps'),
      }))()`,
    )
    check(
      'W4',
      'import-page-loads',
      '导入向导页可访问：含类型卡片与步骤条',
      w4.path === `${BASE}content/import` && w4.hasTypeCard && w4.hasStep,
      JSON.stringify(w4),
    )

    // =====================================================================
    // W5 —— 模板下载返回 xlsx（真实 downloadTemplate → blob → 头字节 PK）
    // =====================================================================
    const w5 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/imports.ts');
        const blob = await m.downloadTemplate('words');
        const buf = new Uint8Array(await blob.arrayBuffer());
        const head = String.fromCharCode(buf[0]) + String.fromCharCode(buf[1]);
        return { size: buf.length, head };
      })()`,
    )
    check(
      'W5',
      'template-is-xlsx',
      '模板下载：downloadTemplate(words) 返回 xlsx（前 2 字节为 PK）',
      w5.head === 'PK' && w5.size > 0,
      JSON.stringify(w5),
    )

    // =====================================================================
    // W6 —— 词条预览：含 2 行非法 → invalid_rows.length === 2
    // =====================================================================
    const wordsXlsx = makeXlsx(
      ['学科', '年级', '学期', '单元名', '单元序号', '类型', '英文', '中文', '可接受答案', '音标', '词性', '例句英', '例句中', '是否拼读'],
      [
        ['英语', '3', '上学期', 'SmokeWordsUnit', '', '单词', 'apple', '苹果', '', '', '', '', '', '否'],
        ['英语', '3', '上学期', 'SmokeWordsUnit', '', '单词', 'banana', '香蕉', '', '', '', '', '', '否'],
        ['英语', '3', '上学期', 'SmokeWordsUnit', '', '单词', '', '苹果', '', '', '', '', '', '否'], // 缺英文 → 非法
        ['德语', '3', '上学期', 'SmokeWordsUnit', '', '单词', 'cat', '猫', '', '', '', '', '', '否'], // 学科非法 → 非法
      ],
    )
    const w6 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/imports.ts');
        const b64 = ${JSON.stringify(b64(wordsXlsx))};
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], 'words.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        const data = await m.previewWords(file);
        return {
          summary: data.summary,
          invalid: data.invalid_rows.length,
          newUnits: data.new_units.length,
          token: data.preview_token ? true : false,
        };
      })()`,
    )
    check(
      'W6',
      'words-preview-invalid',
      '词条预览：4 行中 2 行非法 → summary.invalid=2 且 invalid_rows 列出 2 行',
      w6.summary.total === 4 && w6.summary.valid === 2 && w6.summary.invalid === 2 && w6.invalid === 2 && w6.newUnits >= 1 && w6.token,
      JSON.stringify(w6),
    )

    // =====================================================================
    // W13 —— 错误报告下载（U6 的「可下载错误报告」）
    //
    // ⚠️ 这条是**补上的**：原烟测只验到「invalid_rows 列出来了」，没验「能下载」，
    //    结果漏掉了 `error_report_url` 与 `client.baseURL` 双 `/api` 前缀的 404 缺陷
    //    （后端给 `/api/import/report/{token}`，baseURL 已是 `/api`）。
    //    所以这里必须**真的把 Blob 取回来并检查 xlsx 魔数**，
    //    而不是断言字符串前缀 —— 字符串断言当年就是漏掉它的原因。
    // =====================================================================
    const w13 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/imports.ts');
        const b64 = ${JSON.stringify(b64(wordsXlsx))};
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], 'words.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        const data = await m.previewWords(file);
        const rawUrl = data.error_report_url;
        const normalized = m.toApiPath(rawUrl);
        const blob = await m.downloadErrorReport(rawUrl);
        const buf = new Uint8Array(await blob.arrayBuffer());
        const head = String.fromCharCode(buf[0]) + String.fromCharCode(buf[1]);
        return { rawUrl, normalized, size: buf.length, head };
      })()`,
    )
    check(
      'W13',
      'error-report-download',
      '错误报告可下载：error_report_url 经 toApiPath 归一化后取回真实 xlsx（PK 魔数）',
      w13.head === 'PK' && w13.size > 0 && w13.rawUrl.startsWith('/api/') && w13.normalized === w13.rawUrl.slice(4),
      JSON.stringify(w13),
    )

    // =====================================================================
    // W7 —— 词条入库：commitWords(createMissingUnits=true) → created=2, units_created=1
    // =====================================================================
    const w7 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/imports.ts');
        const b64 = ${JSON.stringify(b64(wordsXlsx))};
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], 'words.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        // 重新 preview 拿新 token（上一步的 token 可能因时延失效，统一在提交前现取）
        const data = await m.previewWords(file);
        const res = await m.commitWords(data.preview_token, true, 'overwrite');
        return res;
      })()`,
    )
    check(
      'W7',
      'words-commit',
      '词条入库：commitWords 后 created=2、units_created=1',
      w7.created === 2 && w7.units_created === 1,
      JSON.stringify(w7),
    )

    // =====================================================================
    // W8 —— 入库后单元详情可见词条（真实 listUnitWords）
    // =====================================================================
    const w8 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/content.ts');
        const all = await m.listUnits({ page: 1, page_size: 100 });
        const u = (all.items || []).find(x => x.name === 'SmokeWordsUnit');
        if (!u) return { found: false };
        const res = await m.listUnitWords(u.id, { page: 1, page_size: 100 });
        const ens = (res.items || []).map(w => w.en).sort();
        return { found: true, unitId: u.id, count: res.items.length, ens };
      })()`,
    )
    check(
      'W8',
      'words-visible-in-unit',
      '入库后：单元 SmokeWordsUnit 详情可见 2 个词条（apple/banana）',
      w8.found && w8.count === 2 && w8.ens.includes('apple') && w8.ens.includes('banana'),
      JSON.stringify(w8),
    )

    // =====================================================================
    // W9 —— 课文导入：4 段，调整背诵范围 [0,2] 后提交，库里 recite_scope 应为 [0,2]
    // =====================================================================
    const textsXlsx = makeXlsx(
      ['学科', '年级', '学期', '单元名', '课文标题', '作者', '段落正文', '背诵范围'],
      [['语文', '3', '上学期', 'SmokeTextsUnit', '山', '李白', '山。\n水。\n火。\n木。', '']],
    )
    const w9preview = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/imports.ts');
        const b64 = ${JSON.stringify(b64(textsXlsx))};
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], 'texts.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        const data = await m.previewTexts(file);
        const t = data.texts[0];
        return { count: data.texts.length, paraCount: t.paragraphs.length, defaultScope: t.recite_scope, ok: t.ok, row: t.row, token: data.preview_token ? true : false };
      })()`,
    )
    check(
      'W9',
      'texts-preview-paragraphs',
      '课文预览：按换行切出 4 段、默认背诵范围 [0,1,2,3]、拼音校验通过',
      w9preview.count === 1 && w9preview.paraCount === 4 && JSON.stringify(w9preview.defaultScope) === '[0,1,2,3]' && w9preview.ok && w9preview.token,
      JSON.stringify(w9preview),
    )

    const w9commit = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/imports.ts');
        const b64 = ${JSON.stringify(b64(textsXlsx))};
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], 'texts.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        const data = await m.previewTexts(file);
        const row = data.texts[0].row;
        const res = await m.commitTexts(data.preview_token, [{ row, recite_scope: [0, 2] }]);
        return res;
      })()`,
    )
    const w9verify = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/content.ts');
        const all = await m.listUnits({ page: 1, page_size: 100 });
        const u = (all.items || []).find(x => x.name === 'SmokeTextsUnit');
        if (!u) return { found: false };
        const res = await m.listUnitTexts(u.id, {});
        const t = (res.texts || []).find(x => x.title === '山');
        return { found: true, scope: t ? t.recite_scope : null, chars: t ? t.recite_scope_chars : null, total: t ? t.total_chars : null };
      })()`,
    )
    check(
      'W9',
      'texts-commit-scope-adjust',
      '课文入库：按调整后背诵范围 [0,2] 提交，库里 recite_scope=[0,2] 且背诵字数=2',
      w9commit.created === 1 &&
        w9verify.found &&
        JSON.stringify(w9verify.scope) === '[0,2]' &&
        w9verify.chars === 2,
      `commit=${JSON.stringify(w9commit)} verify=${JSON.stringify(w9verify)}`,
    )

    // =====================================================================
    // W10 —— 非法文件类型 → 后端 40001（真实 previewWords 传 .txt）
    // =====================================================================
    const w10 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/imports.ts');
        const file = new File(['this is not an excel file'], 'bad.txt', { type: 'text/plain' });
        try {
          await m.previewWords(file);
          return { threw: false };
        } catch (e) {
          return { threw: true, code: e && e.code, message: e && e.message };
        }
      })()`,
    )
    check(
      'W10',
      'illegal-file-40001',
      '非法文件类型：后端对 .txt 返回 40001（FILE_FORMAT_INVALID）',
      w10.threw && w10.code === 40001,
      JSON.stringify(w10),
    )

    // =====================================================================
    // W11 —— 删除无记录单元成功（F3 可触发分支）
    // =====================================================================
    const w11 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/content.ts');
        const all = await m.listUnits({ page: 1, page_size: 100 });
        const u = (all.items || []).find(x => x.name === ${JSON.stringify(unitUiName)});
        if (!u) return { found: false };
        const res = await m.deleteUnit(u.id);
        // 复查是否已删除
        const after = await m.listUnits({ page: 1, page_size: 100 });
        const gone = !(after.items || []).some(x => x.id === u.id);
        return { found: true, res, gone };
      })()`,
    )
    check(
      'W11',
      'delete-unit-ok',
      '删除无记录单元：deleteUnit 成功并真正移除',
      w11.found && w11.gone === true,
      JSON.stringify(w11),
    )

    // =====================================================================
    // W12 —— 30003 分支（有练习记录不可删）：本环境无法自然触发，记录并标注 SKIP
    // =====================================================================
    check(
      'W12',
      'delete-has-records-30003',
      '删除有练习记录单元 → 30003 弹「改为停用」：需 WEB-03/04 学生端练习记录，本次烟测无法自然触发，代码路径已审查',
      false,
      'SKIPPED: 30003 需练习记录（学生端产生），删除无记录单元成功路径已验证于 W11；30003 分支为代码审查级覆盖',
      true,
    )

    // =====================================================================
    // 防假绿：断言用例数达到下限，避免「用例被静默跳过却报通过」
    // =====================================================================
    const realCases = results.filter((r) => !r.skipped)
    check(
      'GUARD',
      'case-count-floor',
      `用例数下限：实际 ${realCases.length} >= 期望值 ${MIN_CASES}`,
      realCases.length >= MIN_CASES,
      `count=${realCases.length}`,
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

async function tryVersion(cdp) {
  try {
    const r = await cdp.send('Browser.getVersion')
    return r.product || 'unknown'
  } catch {
    return ''
  }
}

function finish(code, backendLog, viteLog, browserLog) {
  const failed = results.filter((r) => !r.ok && !r.skipped)
  say('')
  say('='.repeat(74))
  say(`烟测结果：${results.filter((r) => r.ok).length}/${results.length} 通过（含 ${results.filter((r) => r.skipped).length} 个跳过项）`)
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
  console.log(`RESULT ${results.filter((r) => r.ok).length}/${results.length} ${failed.length ? 'FAIL' : 'PASS'}`)
  if (failed.length) console.log(`FAILED ${failed.map((f) => `${f.id}:${f.slug}`).join(' ')}`)
  console.log(`LOG ${LOG_FILE}`)
  return code === 0 && failed.length ? 1 : code
}

process.exit(await main())
