#!/usr/bin/env node
/**
 * WP-WEB-03 真机联调烟测 —— 真浏览器 + 真 vite dev server + 真后端。
 *
 * 为什么是真机：typecheck / build 过了只说明「能编译」。这里跑的是**真实生产代码路径**：
 *   1. 真 Edge（CDP over pipe）打开真 vite dev server 页面；
 *   2. 真实登录（UI 点击）→ localStorage 拿到真 token；
 *   3. 通过页面内 `import('/admin/src/api/*.ts')` 拿到**与 App 同一份**模块单例，
 *      调用真实 `createClass / listStudents / previewStudentImport / commitStudentImport /
 *      batchAssignClass / resetStudentPassword / patchStudent / getClassOverview /
 *      getUnitOverview / getStudentReport / listAppeals / reviewAppeal / getServerInfo`；
 *   4. 关键 UI 行为（新建班级弹窗、服务地址页二维码、空状态）走真实 DOM 交互。
 *
 * 造数口径（全部走**真接口**，没有任何直接写库）：
 *   - 班级：U1 用 UI 弹窗真实新建；
 *   - 学生：45 行名单（2 行非法）→ preview → commit，`created=43`，**全部未分班**；
 *     另用 `POST /api/auth/register` 造 1 名自主注册学生（U4 用）；
 *   - 词条：`SmokeStatsUnit_*`（英语 3 年级上学期）5 个词条 apple/banana/cat/dog/elephant；
 *   - 答题：2 名学生各走一轮真练习（组卷 → 逐题提交 → 结算），
 *     其中刻意造出 `loose`（apple 答 "apples"）、`empty`（dog 答空）、`wrong`（cat/elephant）；
 *   - 背诵：`SmokeStatsTextUnit_*`（语文《静夜思》4 段）→ 建场次 → 建背诵 → feed → settle。
 *
 * 防假绿（与 smoke_web02 同口径）：
 *   - 后端没起来 / 端口被占用 / 缺依赖 → 退出码 2（env 不满足）或 1（起不来），绝不 0；
 *   - 结尾断言 `results.length >= MIN_CASES`，避免「用例被静默跳过却报通过」；
 *   - 凡是「下载 / 渲染 / 跳转」类要求都**真的执行一次**再断言结果（取 Blob 看魔数、
 *     读 canvas 像素看真的画出了东西），不接受「接口返回了字段就算过」。
 *
 * 诚实上报的覆盖缺口（详见交付说明偏离项）：
 *   - **U13「用手机真扫码唤起 App」无法自动化**：没有真手机，也无法断言 App 的 URL scheme
 *     注册。脚本只机器化验证 `qr_content` 形如 `bjsys://<ip>:<port>` + 二维码画布 ≥240 且
 *     真有像素；真机扫码走人工验收（脚本内以 SKIP 显式标注，不假装覆盖）。
 *   - **U14「无数据状态」在「学生管理」页验证**（真实筛选为空 → 引导性空状态 + 主操作按钮）。
 *     「班级概览」在班级 0 学生时的空状态分支由同一套 `EmptyState` 组件承载，
 *     本次未逐页驱动（避免 Element Plus 下拉交互把烟测搞脆），已如实记入交付说明。
 *
 * 跑法（**必须**带 `env -u PYTHONPATH`）：
 *     cd admin-web && env -u PYTHONPATH SMOKE_BACKEND_PORT=8817 node scripts/smoke_web03_stats.mjs
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
const LOG_FILE = join(LOG_DIR, 'smoke_web03.txt')

const isWin = process.platform === 'win32'
const PYTHON = join(BACKEND, '.venv', isWin ? 'Scripts/python.exe' : 'bin/python')
const VITE_BIN = join(ADMIN_WEB, 'node_modules', 'vite', 'bin', 'vite.js')

// --------------------------------------------------------------------------- 配置

const BACKEND_PORT = Number(process.env.SMOKE_BACKEND_PORT || 8817)
const WEB_PORT = Number(process.env.SMOKE_WEB_PORT || 5285)

const BASE = '/admin/'
const BACKEND_BASE = `http://127.0.0.1:${BACKEND_PORT}`
const WEB_BASE = `http://127.0.0.1:${WEB_PORT}`

const ADMIN_USER = 'admin'
const ADMIN_PWD = 'web03-smoke-pass-7788'
const STUDENT_INIT_PWD = 'Init@12345'

const BROWSER_CANDIDATES = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
]

/** 非跳过用例数下限（U1–U14 共 14 条 + GUARD 1 条）。 */
const MIN_CASES = 14

// --------------------------------------------------------------------------- 结果收集

/** @type {{id: string, slug: string, title: string, ok: boolean, detail: string, skipped?: boolean}[]} */
const results = []
const logLines = []

/**
 * 整轮被异常中断时的错误对象。
 *
 * ⚠️ 为什么需要它（**本脚本第一版踩过的假绿坑**）：中断发生在 U7 之前时，
 * `results` 里只有 U1–U6 这 7 条，且全部 `ok=true` →
 * `finish()` 会打印 `RESULT 7/7 PASS`、「烟测结果：7/7 通过（含 0 个跳过项）」。
 * 退出码虽然是 1，但人不看退出码、只看这句 → **一个断在半路的运行看起来是全绿**。
 * 现在把「中断」本身补成一条失败用例，让结论行与事实一致。
 */
let ABORT_ERROR = null

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

const near = (a, b, eps = 1e-9) => Math.abs(Number(a) - Number(b)) <= eps

// --------------------------------------------------------------------------- 后端直连（造数用；不走前端，属测试夹具）

/** 调后端并解包 `{code,message,data}`。`expectOk=false` 时把非零 code 也返回回来。
 *
 * ⚠️ 限流自愈（本项目真踩过）：`core/rate_limit.py` 对**登录**限 5 次/分钟，且
 *    ident 在无 `Authorization` 头时按**客户端 IP** 取（`ip:127.0.0.1`）——
 *    也就是说本机跑烟测时，脚本里所有登录**共用一个桶**。而全部写接口
 *    （POST/PUT/PATCH/DELETE）又共用 `write:ip:127.0.0.1` 的 60 次/分钟桶。
 *    命中限流会返回 **HTTP 200 + code 10005**，如果直接当业务失败断言，
 *    会把「被限流」误判成「功能坏了」。所以这里对 10005 自动等窗口滑过再重试；
 *    重试是安全的：10005 表示请求在中间件层就被拒了，没有产生任何副作用。
 */
async function api(path, { method = 'GET', token, body, form, expectOk = true, retries = 4 } = {}) {
  for (let attempt = 0; ; attempt++) {
    const headers = {}
    if (token) headers.Authorization = `Bearer ${token}`
    let payload
    if (form) {
      payload = form
    } else if (body !== undefined) {
      headers['Content-Type'] = 'application/json'
      payload = JSON.stringify(body)
    }
    const r = await fetch(`${BACKEND_BASE}${path}`, { method, headers, body: payload })
    let j
    try {
      j = await r.json()
    } catch {
      throw new Error(`${method} ${path} 返回非 JSON（HTTP ${r.status}）`)
    }
    if (j.code === 10005 && attempt < retries) {
      say(`  · 触发限流 10005（${method} ${path}），等 15s 让滑动窗口滑过去后重试 #${attempt + 1}`)
      await sleep(15000)
      continue
    }
    if (expectOk && j.code !== 0) {
      throw new Error(`${method} ${path} 失败：code=${j.code} message=${j.message}`)
    }
    return j
  }
}

async function loginAs(username, password, role) {
  const j = await api('/api/auth/login', {
    method: 'POST',
    body: { username, password, role },
    expectOk: false,
  })
  return j
}

// --------------------------------------------------------------------------- 极简 xlsx 写出（无第三方依赖，与 smoke_web02 同款）
//
// 后端 `core/excel.py::read_xlsx` 用 openpyxl `load_workbook(read_only=True, data_only=True)`
// 读 `inlineStr` 单元格。采用 stored（压缩方式 0）写入，CRC32 自行实现。

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
  xml += '<row r="1">'
  headers.forEach((h, i) => {
    const c = colLetter(i + 1)
    xml += `<c r="${c}1" t="inlineStr"><is><t xml:space="preserve">${xmlEscape(h)}</t></is></c>`
  })
  xml += '</row>'
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

  // 登录接口限 5 次/分钟（按 IP），UI 登录也可能吃 10005：此时页面会弹错误并停在登录页，
  // 这里等窗口滑过后重试，别把它当成「登录坏了」。
  for (let attempt = 0; attempt < 4; attempt++) {
    const clicked = await evalJs(cdp, sid, `(() => {
      const btn = document.querySelector('.login-card__submit');
      if (!btn) return 'NOT_FOUND';
      btn.click();
      return 'CLICKED';
    })()`)
    if (clicked !== 'CLICKED') throw new Error('提交按钮没找到')
    try {
      await waitFor(
        cdp,
        sid,
        `!location.pathname.startsWith(${JSON.stringify(BASE + 'login')})`,
        '登录成功并离开登录页',
        8000,
      )
      return
    } catch {
      const msg = await evalJs(
        cdp,
        sid,
        `(Array.from(document.querySelectorAll('.el-message')).map(m => m.textContent || '').join('|'))`,
      )
      if (String(msg).includes('频繁') || String(msg).includes('10005')) {
        say('  · UI 登录触发限流 10005，等 15s 重试')
        await dismissMessages(cdp, sid)
        await sleep(15000)
        continue
      }
      throw new Error(`登录未成功离开登录页（页面提示：${msg || '无'}）`)
    }
  }
  throw new Error('UI 登录重试耗尽（持续被限流）')
}

/** 在页面上下文里调用真实 API 模块（与 App 共用同一份模块单例）。 */
async function apiCall(cdp, sid, expr) {
  const r = await tryEval(cdp, sid, expr)
  if (!r.ok) throw new Error(`API 调用失败：${r.error}`)
  return r.value
}

/** 关掉可能挡住点击的 el-message 浮层。 */
async function dismissMessages(cdp, sid) {
  await tryEval(
    cdp,
    sid,
    `(async () => {
      await new Promise(r => setTimeout(r, 1300));   // client.ts 的 1.2s 去重窗口过了再关
      document.querySelectorAll('.el-message').forEach(m => m.remove());
      return true;
    })()`,
  )
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

  const tmp = join(tmpdir(), `bjsys_web03_${Date.now()}`)
  mkdirSync(tmp, { recursive: true })
  const dbFile = join(tmp, 'web03.db')
  const backendLog = join(tmp, 'backend.log')
  const browserLog = join(tmp, 'browser.log')
  const viteLog = join(tmp, 'vite.log')
  const profile = join(tmp, 'browser-profile')

  const procs = []
  let cdp = null

  const TS = Date.now()
  const className = `SmokeClassUI_${TS}`
  const englishUnit = `SmokeStatsUnit_${TS}`
  const chineseUnit = `SmokeStatsTextUnit_${TS}`

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
      // ⚠️ 早退也走 finish()：否则证据不落盘，事后只能靠猜（本脚本第一版就吃过这个亏）
      return finish(1, backendLog, viteLog, browserLog)
    }

    const adminLogin = await loginAs(ADMIN_USER, ADMIN_PWD, 'admin')
    if (adminLogin.code !== 0) {
      console.log(`[!!] env-admin-login-failed  code=${adminLogin.code}`)
      return 1
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

    // UI 登录一次，后续页面直接用登录态
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}login`)
    await submitLogin(cdp, sid, ADMIN_USER, ADMIN_PWD)
    await waitFor(cdp, sid, `location.pathname === ${JSON.stringify(`${BASE}dashboard`)}`, '跳转工作台', 15000)

    // =====================================================================
    // U1 —— 新建班级（真实 UI 弹窗 + 真实 createClass）
    // =====================================================================
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}students/classes`)
    await evalJs(cdp, sid, `(${CLICK_BY_TEXT})('新建班级')`)
    await waitFor(cdp, sid, `!!document.querySelector('input[placeholder="如 三年级(1)班"]')`, '新建班级弹窗出现', 8000)
    await evalJs(cdp, sid, fillExpr('如 三年级(1)班', className))
    await evalJs(cdp, sid, `(${CLICK_BY_TEXT})('保存')`)
    await dismissMessages(cdp, sid)

    const u1api = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/students.ts');
        const res = await m.listClasses({ status: 'all', page: 1, pageSize: 100 });
        const found = (res.items || []).find(c => c.name === ${JSON.stringify(className)});
        return { total: res.total, unassigned: res.unassigned_count,
                 found: found ? { id: found.id, name: found.name, grade: found.grade,
                                  semester: found.semester, students: found.student_count } : null };
      })()`,
    )
    let u1dom = false
    try {
      await waitFor(
        cdp,
        sid,
        `document.body.innerText.includes(${JSON.stringify(className)})`,
        '班级列表渲染出新班级',
        10000,
      )
      u1dom = true
    } catch {
      u1dom = false
    }
    check(
      'U1',
      'create-class-ui',
      '新建班级：UI 弹窗提交后列表与接口都能看到，且初始学生数为 0',
      u1dom && Boolean(u1api.found) && u1api.found.students === 0 && u1api.found.grade === 3,
      `dom=${u1dom} api=${JSON.stringify(u1api.found)}`,
    )
    const classId = u1api.found.id

    // =====================================================================
    // U2 —— 导入 45 名学生（2 行错误）→ 预览列错误行；确认后 created=43
    //       名单刻意**不填班级** → 43 人全部落在「未分班」（U3/U4 要用）
    // =====================================================================
    const studentRows = []
    for (let i = 1; i <= 45; i++) {
      studentRows.push([`s${TS}${String(i).padStart(2, '0')}`, `学生${i}`, '', STUDENT_INIT_PWD])
    }
    // 第 7 行：学号为空（非法）；第 45 行：姓名为空（非法）
    studentRows[6] = ['', '缺学号', '', STUDENT_INIT_PWD]
    studentRows[44] = [`s${TS}45`, '', '', STUDENT_INIT_PWD]
    const studentsXlsx = makeXlsx(['学号', '姓名', '班级', '初始密码'], studentRows)

    const preview = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/students.ts');
        const b64 = ${JSON.stringify(studentsXlsx.toString('base64'))};
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], 'students.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        const data = await m.previewStudentImport(file);
        return { summary: data.summary, invalid: data.invalid_rows.length,
                 invalidRows: data.invalid_rows.map(r => r.row), token: !!data.preview_token,
                 reportUrl: data.error_report_url, dup: data.duplicated_rows.length };
      })()`,
    )
    const commit = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/students.ts');
        const b64 = ${JSON.stringify(studentsXlsx.toString('base64'))};
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], 'students.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        const data = await m.previewStudentImport(file);
        const res = await m.commitStudentImport(data.preview_token, ${JSON.stringify(STUDENT_INIT_PWD)});
        return res;
      })()`,
    )
    check(
      'U2',
      'students-import-45-with-2-invalid',
      '导入 45 行（2 行非法）：预览列出错误行（第 7、45 行）；确认后 created=43、invalid=2',
      preview.summary.total === 45 &&
        preview.summary.invalid === 2 &&
        preview.invalid === 2 &&
        // ⚠️ 后端 `invalid_rows[].row` 给的是 **Excel 语义的行号**（表头为第 1 行），
        //    所以第 1 条数据行是 2。我们刻意把第 7、45 条数据行写坏 → 报 8、46。
        //    第一版烟测按「数据序号」写成 [7,45] 才失败 —— 是断言写错，不是后端错。
        JSON.stringify(preview.invalidRows) === '[8,46]' &&
        preview.token &&
        commit.created === 43 &&
        commit.skipped === 2,
      `preview=${JSON.stringify(preview.summary)} rows=${JSON.stringify(preview.invalidRows)} commit=${JSON.stringify(commit)}`,
    )
    // 顺带把「错误报告真的能下载」也验了（错误报告 URL 自带 /api，必须过 toApiPath）
    const report = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/students.ts');
        const b64 = ${JSON.stringify(studentsXlsx.toString('base64'))};
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], 'students.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        const data = await m.previewStudentImport(file);
        const raw = data.error_report_url;
        const blob = await m.downloadImportErrorReport(raw);
        const buf = new Uint8Array(await blob.arrayBuffer());
        return { raw, head: String.fromCharCode(buf[0]) + String.fromCharCode(buf[1]), size: buf.length };
      })()`,
    )
    check(
      'U2b',
      'student-error-report-download',
      '名单错误报告可下载：error_report_url 经 toApiPath 归一化后取回真实 xlsx（PK 魔数）',
      report.head === 'PK' && report.size > 0 && report.raw.startsWith('/api/'),
      JSON.stringify(report),
    )

    // =====================================================================
    // U4 前置：造 1 名自主注册学生（`POST /api/auth/register`，不选班级）
    // =====================================================================
    const selfUser = `self${TS}`
    const reg = await api('/api/auth/register', {
      method: 'POST',
      body: { username: selfUser, real_name: '自主注册生', password: STUDENT_INIT_PWD },
    })
    say(`自主注册：${selfUser} → id=${reg.data.id ?? reg.data.user_id ?? '?'}`)

    // =====================================================================
    // U3 —— 批量归班 3 名学生 → 班级学生数=3；「未分班」筛选不再包含他们
    // =====================================================================
    const picked = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/students.ts');
        // ⚠️ 必须限定 registerSource='import'：列表按 id 降序，刚刚自主注册的学生 id 最大、
        //    排在最前面，不限定的话会被顺手归班，U4 就永远查不到「未分班的自主注册生」。
        //    （第一版烟测正是踩了这个坑，靠一个最小探针才定位到。）
        const res = await m.listStudents({ classId: null, registerSource: 'import', page: 1, pageSize: 10 });
        const picked3 = (res.items || []).slice(0, 3);
        const ids = picked3.map(s => s.id);
        const names = Object.fromEntries(picked3.map(s => [String(s.id), s.username]));
        const batch = await m.batchAssignClass(ids, ${classId});
        const inClass = await m.listStudents({ classId: ${classId}, page: 1, pageSize: 50 });
        const unassigned = await m.listStudents({ classId: null, page: 1, pageSize: 100 });
        return {
          ids, names, batch,
          classCount: inClass.total,
          classIds: (inClass.items || []).map(s => s.id),
          stillUnassigned: ids.filter(id => (unassigned.items || []).some(s => s.id === id)),
          unassignedTotal: unassigned.total,
        };
      })()`,
    )
    check(
      'U3',
      'batch-assign-3-students',
      '批量归班 3 名学生：班级学生数变 3，且这 3 人不再出现在「未分班」筛选里',
      picked.batch.success_count === 3 &&
        picked.classCount === 3 &&
        picked.stillUnassigned.length === 0 &&
        picked.ids.every((id) => picked.classIds.includes(id)),
      JSON.stringify({ batch: picked.batch, classCount: picked.classCount, stillUnassigned: picked.stillUnassigned }),
    )
    const s1 = picked.ids[0]
    const s2 = picked.ids[1]
    const s3 = picked.ids[2]

    // =====================================================================
    // U4 —— 筛选「未分班」：包含自主注册且未选班的学生
    // =====================================================================
    const u4 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/students.ts');
        const res = await m.listStudents({ classId: null, page: 1, pageSize: 100 });
        const hit = (res.items || []).find(s => s.username === ${JSON.stringify(selfUser)});
        return { total: res.total, hit: hit ? { id: hit.id, source: hit.register_source, cls: hit.class_id } : null };
      })()`,
    )
    check(
      'U4',
      'unassigned-filter',
      '「未分班」是显式筛选项：自主注册（reg_source=self、class_id=null）的学生出现在其中',
      Boolean(u4.hit) && u4.hit.source === 'self' && u4.hit.cls === null,
      JSON.stringify(u4),
    )

    // =====================================================================
    // U5 —— 重置某生密码：用新密码真能登录
    // =====================================================================
    const newPwd = 'Reset@54321'
    const u5reset = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/students.ts');
        const res = await m.resetStudentPassword(${s1}, ${JSON.stringify(newPwd)});
        const list = await m.listStudents({ classId: ${classId}, page: 1, pageSize: 10 });
        const row = (list.items || []).find(s => s.id === ${s1});
        return { res, username: row ? row.username : null };
      })()`,
    )
    const u5login = await loginAs(u5reset.username, newPwd, 'student')
    const u5old = await loginAs(u5reset.username, STUDENT_INIT_PWD, 'student')
    check(
      'U5',
      'reset-password-then-login',
      '重置密码：新密码可登录（code=0），旧密码失效（非 0）',
      u5reset.res.reset === true && u5login.code === 0 && u5old.code !== 0,
      `reset=${u5reset.res.reset} new=${u5login.code} old=${u5old.code}`,
    )

    // =====================================================================
    // U6 —— 停用某生：二次确认走 UI 文案；停用后无法登录
    // =====================================================================
    const u6user = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/students.ts');
        const list = await m.listStudents({ classId: ${classId}, page: 1, pageSize: 10 });
        const row = (list.items || []).find(s => s.id === ${s3});
        await m.patchStudent(${s3}, { status: 'disabled' });
        const after = await m.listStudents({ classId: ${classId}, page: 1, pageSize: 10 });
        const row2 = (after.items || []).find(s => s.id === ${s3});
        return { username: row ? row.username : null, status: row2 ? row2.status : null };
      })()`,
    )
    const u6login = await loginAs(u6user.username, STUDENT_INIT_PWD, 'student')
    check(
      'U6',
      'disable-student-blocks-login',
      '停用学生：状态变 disabled，且该生登录被拒（20003 ACCOUNT_DISABLED）',
      u6user.status === 'disabled' && u6login.code === 20003,
      `status=${u6user.status} loginCode=${u6login.code}`,
    )

    // =====================================================================
    // 造数：英语单元 + 5 词条 → 学生 s1/s2 各走一轮真练习
    //   s1: apple=apples(loose) banana=banana(exact) cat=cat dog=dog elephant=elephant  → 5/5
    //   s2: apple=apples(loose) banana=bananas(loose) cat=dog(wrong) dog=''(empty)
    //       elephant=eleph(wrong)                                                        → 3/4
    //   预期：cat 1/2=50%、elephant 1/2=50%（难词，<60% 标红）
    // =====================================================================
    say('== 造数：英语单元 + 词条 ==')
    const wordsXlsx = makeXlsx(
      ['学科', '年级', '学期', '单元名', '单元序号', '类型', '英文', '中文', '可接受答案', '音标', '词性', '例句英', '例句中', '是否拼读'],
      [
        ['英语', '3', '上学期', englishUnit, '', '单词', 'apple', '苹果', '', '', '', '', '', '否'],
        ['英语', '3', '上学期', englishUnit, '', '单词', 'banana', '香蕉', '', '', '', '', '', '否'],
        ['英语', '3', '上学期', englishUnit, '', '单词', 'cat', '猫', '', '', '', '', '', '否'],
        ['英语', '3', '上学期', englishUnit, '', '单词', 'dog', '狗', '', '', '', '', '', '否'],
        ['英语', '3', '上学期', englishUnit, '', '单词', 'elephant', '大象', '', '', '', '', '', '否'],
      ],
    )
    const unitSetup = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/imports.ts');
        const b64 = ${JSON.stringify(wordsXlsx.toString('base64'))};
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], 'words.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        const data = await m.previewWords(file);
        const res = await m.commitWords(data.preview_token, true, 'overwrite');
        const c = await import('/admin/src/api/content.ts');
        const all = await c.listUnits({ page: 1, page_size: 100 });
        const u = (all.items || []).find(x => x.name === ${JSON.stringify(englishUnit)});
        const ws = await c.listUnitWords(u.id, { page: 1, page_size: 100 });
        return { created: res.created, unitId: u.id,
                 words: (ws.items || []).map(w => ({ id: w.id, en: w.en })) };
      })()`,
    )
    say(`英语单元 id=${unitSetup.unitId} 词条=${JSON.stringify(unitSetup.words.map((w) => w.en))}`)

    const wordId = Object.fromEntries(unitSetup.words.map((w) => [w.en, w.id]))

    /** 用真接口跑一轮练习，返回每题的 answer_id / match_type。 */
    async function runPractice(username, password, answers) {
      const lg = await loginAs(username, password, 'student')
      if (lg.code !== 0) throw new Error(`学生 ${username} 登录失败 code=${lg.code}`)
      const token = lg.data.access_token
      const created = await api('/api/sessions', {
        method: 'POST',
        token,
        body: {
          unit_id: unitSetup.unitId,
          content_type: 'english',
          strategy: { scope: 'unit', count: 0, count_mode: 'all', order: 'sequential', mode: 'cn2en', time_limit_ms: 0 },
        },
      })
      const sessionId = created.data.session_id
      const items = created.data.items || []
      const out = []
      for (const it of items) {
        const spec = answers[it.word_id]
        if (!spec) continue
        const res = await api(`/api/sessions/${sessionId}/answers`, {
          method: 'POST',
          token,
          body: {
            word_id: it.word_id,
            mode: 'cn2en',
            step: 1,
            asr_text: spec.text,
            duration_ms: spec.ms ?? 2200,
            first_voice_ms: 900,
            retry_count: 0,
            used_keyboard: false,
            provider: 'mock',
          },
        })
        out.push({ word_id: it.word_id, answer_id: res.data.answer_id, match_type: res.data.match_type, is_correct: res.data.is_correct, can_appeal: res.data.can_appeal, expected: res.data.expected })
      }
      await api(`/api/sessions/${sessionId}/finish`, { method: 'POST', token, body: { reason: 'completed' } })
      return { sessionId, answers: out }
    }

    const s1info = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/students.ts');
        const r = await m.listStudents({ classId: ${classId}, page: 1, pageSize: 10 });
        const a = (r.items || []).find(s => s.id === ${s1});
        const b = (r.items || []).find(s => s.id === ${s2});
        return { a: { id: a.id, username: a.username }, b: { id: b.id, username: b.username } };
      })()`,
    )

    const run1 = await runPractice(s1info.a.username, newPwd, {
      [wordId.apple]: { text: 'apples' },
      [wordId.banana]: { text: 'banana' },
      [wordId.cat]: { text: 'cat' },
      [wordId.dog]: { text: 'dog' },
      [wordId.elephant]: { text: 'elephant' },
    })
    const run2 = await runPractice(s1info.b.username, STUDENT_INIT_PWD, {
      [wordId.apple]: { text: 'apples' },
      [wordId.banana]: { text: 'bananas' },
      [wordId.cat]: { text: 'dog' },
      [wordId.dog]: { text: '' },
      [wordId.elephant]: { text: 'eleph' },
    })
    say(`练习1 match=${JSON.stringify(run1.answers.map((a) => a.match_type))}`)
    say(`练习2 match=${JSON.stringify(run2.answers.map((a) => a.match_type))}`)
    const appealable = [...run1.answers, ...run2.answers].find((a) => a.can_appeal && a.match_type === 'loose')
    // ⚠️ 第一版烟测只「找到了一条可申诉的作答」却**从没真的提交申诉**，
    //    于是 U11 查待复核列表拿到 0 条，失败。这里必须真打一次申诉接口：
    //    只有 `match_type ∈ {loose, fuzzy}`（`can_appeal=true`）才允许申诉。
    if (!appealable) throw new Error('造数失败：没有产生可申诉（loose/fuzzy）的作答，U11 无从验证')
    {
      const lg = await loginAs(s1info.a.username, newPwd, 'student')
      const app = await api(`/api/answers/${appealable.answer_id}/appeal`, {
        method: 'POST',
        token: lg.data.access_token,
        // ⚠️ 必须发一个（哪怕空的）JSON body：该端点签名为 `payload: AppealIn`（必填），
        //    不发 body 会被 FastAPI 判 422 → 全局映射成 10001「参数不正确：参数」。
        //    （第一版烟测正是漏了这一行，导致整轮从这里中断、且结论行还假报 PASS。）
        body: {},
      })
      say(`申诉已提交：answer_id=${appealable.answer_id} match=${appealable.match_type} → ${JSON.stringify(app.data)}`)
    }

    // 语文：一篇课文 + 一次背诵（U10 的「背诵」块必须是真的）
    say('== 造数：语文课文 + 一次背诵 ==')
    const textsXlsx = makeXlsx(
      ['学科', '年级', '学期', '单元名', '课文标题', '作者', '段落正文', '背诵范围'],
      [['语文', '3', '上学期', chineseUnit, '静夜思', '李白', '床前明月光。\n疑是地上霜。\n举头望明月。\n低头思故乡。', '']],
    )
    const textSetup = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/imports.ts');
        const b64 = ${JSON.stringify(textsXlsx.toString('base64'))};
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], 'texts.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        const data = await m.previewTexts(file);
        const row = data.texts[0].row;
        const res = await m.commitTexts(data.preview_token, [{ row }]);
        const c = await import('/admin/src/api/content.ts');
        const all = await c.listUnits({ page: 1, page_size: 100 });
        const u = (all.items || []).find(x => x.name === ${JSON.stringify(chineseUnit)});
        const ts = await c.listUnitTexts(u.id, {});
        const t = (ts.texts || [])[0];
        return { created: res.created, unitId: u.id, textId: t.id, title: t.title, chars: t.recite_scope_chars };
      })()`,
    )
    say(`语文单元 id=${textSetup.unitId} 课文《${textSetup.title}》背诵字数=${textSetup.chars}`)

    const recite = await (async () => {
      const lg = await loginAs(s1info.b.username, STUDENT_INIT_PWD, 'student')
      const token = lg.data.access_token
      const sess = await api('/api/sessions', {
        method: 'POST',
        token,
        body: { unit_id: textSetup.unitId, content_type: 'chinese', strategy: { scope: 'unit', order: 'sequential', time_limit_ms: 0 } },
      })
      const rec = await api('/api/recitations', {
        method: 'POST',
        token,
        body: { session_id: sess.data.session_id, text_id: textSetup.textId, scope: [] },
      })
      const rid = rec.data.recitation_id
      await api(`/api/recitations/${rid}/feed`, {
        method: 'POST',
        token,
        body: { partial_text: '床前明月光疑是地上霜', silence_ms: 0 },
      })
      const settled = await api(`/api/recitations/${rid}/settle`, {
        method: 'POST',
        token,
        body: {
          transcript: '床前明月光疑是地上霜举头望明月低头思故乡',
          restart_count: 0,
          duration_ms: 26000,
          first_voice_ms: 1100,
          finish_reason: 'completed',
        },
      })
      await api(`/api/sessions/${sess.data.session_id}/finish`, { method: 'POST', token, body: { reason: 'completed' } })
      return { recitationId: rid, matched: settled.data.matched_chars ?? null, total: settled.data.total_chars ?? null }
    })()
    say(`背诵：recitation_id=${recite.recitationId} matched=${recite.matched}/${recite.total}`)

    // =====================================================================
    // U7 —— 班级概览加载：五张卡片 + 7 天趋势 + 错题榜，且派生数字与后端一致
    // =====================================================================
    const u7 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/stats.ts');
        const o = await m.getClassOverview(${classId}, { top_n: 10 });
        return {
          student_count: o.student_count, active: o.active_student_count,
          today_active: o.today_active_students, today_items: o.today_item_count,
          completion_ratio: o.completion_ratio, practiced: o.practiced_word_count, scope: o.scope_word_count,
          accuracy: o.accuracy, correct: o.correct_count, counted: o.counted_count, answers: o.answer_count,
          avg_answer_ms: o.avg_answer_ms, avg_session_ms: o.avg_session_ms,
          trend_len: (o.trend || []).length, top: (o.top_wrong_words || []).length,
          from: o.date_from, to: o.date_to,
        };
      })()`,
    )
    const u7ok =
      u7.student_count === 3 &&
      u7.today_active === 2 &&
      u7.trend_len === 7 &&
      u7.top >= 1 &&
      u7.avg_answer_ms !== null &&
      // 正确率一致性：后端口径 = correct/counted，且响应里的 accuracy 就是它的四舍五入
      // （后端 `round(..., 4)`，所以容差取 1e-4 量级；用 1e-6 会把「四舍五入」误判成不一致）
      (u7.counted > 0 ? near(u7.accuracy, u7.correct / u7.counted, 2e-4) : true) &&
      // 完成率一致性：词条维度 practiced/scope
      u7.scope > 0 &&
      near(u7.completion_ratio, u7.practiced / u7.scope, 2e-4)
    check(
      'U7',
      'class-overview-consistency',
      '班级概览：3 人/今日活跃 2/完成率=已练÷词条总数/正确率=计分正确÷计分题/7 天趋势/错题榜非空，派生数字自洽',
      u7ok,
      JSON.stringify(u7),
    )

    // =====================================================================
    // U8 —— 切换日期范围为近 30 天：趋势点数与聚合结果相应变化
    // =====================================================================
    const u8 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/stats.ts');
        const d = (off) => { const x = new Date(); x.setDate(x.getDate() + off);
          const p = (n) => String(n).padStart(2, '0');
          return x.getFullYear() + '-' + p(x.getMonth() + 1) + '-' + p(x.getDate()); };
        const wide = await m.getClassOverview(${classId}, { date_from: d(-29), date_to: d(0), top_n: 10 });
        const narrow = await m.getClassOverview(${classId}, { date_from: d(0), date_to: d(0), top_n: 10 });
        return { wide_len: (wide.trend || []).length, wide_from: wide.date_from, wide_to: wide.date_to,
                 narrow_len: (narrow.trend || []).length, narrow_answers: narrow.answer_count };
      })()`,
    )
    check(
      'U8',
      'range-switch-30-days',
      '日期范围切换：近 30 天趋势点数=30（近 7 天=7），且极窄区间聚合结果相应收缩',
      u8.wide_len === 30 && u8.narrow_len === 1,
      JSON.stringify(u8),
    )

    // =====================================================================
    // U9 —— 单元掌握度：难词榜按正确率升序、低于 60% 会被前端标红
    // =====================================================================
    const u9 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/stats.ts');
        const o = await m.getUnitOverview(${unitSetup.unitId}, { hard_word_limit: 50 });
        const arr = (o.hard_words || []).map(w => ({ en: w.en, acc: w.accuracy, counted: w.counted_count }));
        const asc = arr.every((w, i) => i === 0 || arr[i - 1].acc <= w.acc);
        return { word_total: o.word_total, accuracy: o.accuracy, mastered_ratio: o.mastered_ratio,
                 student_count: o.student_count,
                 practiced: o.practiced_word_count, hard: arr, asc,
                 hard_under60: arr.filter(w => w.counted > 0 && w.acc < 0.6).map(w => w.en),
                 unfinished: (o.unfinished_students || []).map(s => ({ u: s.username, missing: s.missing_words })) };
      })()`,
    )
    const s2name = picked.names[String(s2)]
    const s3name = picked.names[String(s3)]
    check(
      'U9',
      'unit-mastery-hard-words',
      '单元掌握度：难词榜按正确率升序且 cat/elephant=50% 会被标红；未完成学生=「练过但没练完」的人（s2 因 1 题识别为空少练 1 词 → missing=1）',
      u9.asc &&
        u9.word_total === 5 &&
        u9.hard_under60.includes('cat') &&
        u9.hard_under60.includes('elephant') &&
        u9.unfinished.some((x) => x.u === s2name && x.missing === 1) &&
        // 口径说明（**本用例同时把这条口径限制钉住，避免它悄悄变**）：
        //   `unit_overview` 的「学生宇宙」= `COUNT(DISTINCT answers.student_id)`
        //   = **在本单元答过题的学生**。所以从没练过该单元的学生（s3 已停用、0 次练习）
        //   不会出现在未完成名单里。F8 字面写的是「未完成学生列表」，这里存在口径分歧，
        //   已作为待裁决项上报（02-实现约定 §八），不在此处擅自改后端行为。
        u9.student_count === 2 &&
        !u9.unfinished.some((x) => x.u === s3name),
      JSON.stringify({ ...u9, s2name, s3name }),
    )

    // =====================================================================
    // U10 —— 学生报告：场次、背诵、错词三块数据齐全
    // =====================================================================
    const u10 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/stats.ts');
        const r = await m.getStudentReport(${s2}, { recent_limit: 20 });
        return {
          username: r.username, accuracy: r.accuracy, correct: r.correct_count, counted: r.counted_count,
          mastered: r.mastered_words, sessions: (r.recent_sessions || []).length,
          recitations: (r.recitations || []).length, wrong: (r.wrong_words || []).length,
          wrong_total: r.wrong_word_total,
          wrongEns: (r.wrong_words || []).map(w => w.en),
          sessionTypes: [...new Set((r.recent_sessions || []).map(s => s.content_type))],
        };
      })()`,
    )
    check(
      'U10',
      'student-report-three-blocks',
      '学生报告：最近场次（含英语+语文两类）、背诵记录、错词表三块齐全（错词含 cat/elephant）',
      u10.sessions >= 2 &&
        u10.recitations >= 1 &&
        u10.wrong >= 2 &&
        u10.wrongEns.includes('cat') &&
        u10.wrongEns.includes('elephant') &&
        u10.sessionTypes.includes('english') &&
        u10.sessionTypes.includes('chinese'),
      JSON.stringify(u10),
    )

    // =====================================================================
    // U11 —— 复核一条申诉为「驳回」：列表状态更新；原对错不变
    // =====================================================================
    const u11 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/stats.ts');
        const before = await m.listAppeals({ status: 'pending', page: 1, pageSize: 50 });
        const target = (before.items || []).find(a => a.id === ${appealable?.answer_id ?? -1});
        if (!target) return { found: false, pendingBefore: before.total,
          ids: (before.items || []).map(a => a.id) };
        const res = await m.reviewAppeal(target.id, { result: 'rejected', remark: '烟测：维持原判' });
        const pendingAfter = await m.listAppeals({ status: 'pending', page: 1, pageSize: 50 });
        const rejected = await m.listAppeals({ status: 'rejected', page: 1, pageSize: 50 });
        const row = (rejected.items || []).find(a => a.id === target.id);
        return { found: true, res,
          stillPending: (pendingAfter.items || []).some(a => a.id === target.id),
          afterStatus: row ? row.appeal_result : null,
          afterIsCorrect: row ? row.is_correct : null,
          afterRemark: row ? row.review_remark : null,
          beforeIsCorrect: target.is_correct, matchType: target.match_type };
      })()`,
    )
    check(
      'U11',
      'appeal-review-keeps-judgement',
      '申诉复核「驳回」：列表状态变为 rejected、仍从待复核列表消失，且 is_correct 与复核前完全一致（复核不改判分）',
      u11.found &&
        u11.res.result === 'rejected' &&
        u11.res.changed === true &&
        u11.afterStatus === 'rejected' &&
        u11.afterRemark === '烟测：维持原判' &&
        u11.afterIsCorrect === u11.beforeIsCorrect &&
        u11.stillPending === false,
      JSON.stringify(u11),
    )
    // 页面必须显式说明「复核不改判分」
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}stats/appeals`)
    const u11ui = await evalJs(
      cdp,
      sid,
      `(() => ({
        path: location.pathname,
        hasNote: document.body.innerText.includes('不改判分') || document.body.innerText.includes('只作质量数据'),
        hasTabs: document.body.innerText.includes('待复核'),
      }))()`,
    )
    check(
      'U11b',
      'appeal-page-disclaimer',
      '申诉复核页：页面显式说明「复核只作质量数据、不改判分」，且有「待复核」分档',
      u11ui.path === `${BASE}stats/appeals` && u11ui.hasNote && u11ui.hasTabs,
      JSON.stringify(u11ui),
    )

    // =====================================================================
    // U12 —— 服务地址页：大二维码（≥240）+ 所有网卡地址 + 推荐地址标记
    // =====================================================================
    const serverInfo = await api('/api/server/info', { token: adminToken })
    const hasRecommended = (serverInfo.data.interfaces || []).some((i) => i.recommended)

    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}server/address`)
    await waitFor(
      cdp,
      sid,
      `(() => { const c = document.querySelector('.server-qr-card__canvas'); return !!c && c.width >= 240; })()`,
      '二维码画布出现且 ≥240',
      12000,
    )
    await sleep(600) // 等 QRCode.toCanvas 画完
    const u12 = await evalJs(
      cdp,
      sid,
      `(() => {
        const c = document.querySelector('.server-qr-card__canvas');
        const ctx = c.getContext('2d');
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        let dark = 0;
        for (let i = 0; i < d.length; i += 4) if (d[i] < 128) dark++;
        const rows = document.querySelectorAll('.el-table__row').length;
        const hasRecTag = Array.from(document.querySelectorAll('.el-tag'))
          .some(t => (t.textContent || '').trim() === '推荐');
        const text = document.body.innerText;
        return { w: c.width, h: c.height, dark, rows, hasRecTag,
                 hasScanHint: text.includes('扫码'), hasManual: text.includes('手动输入'),
                 hasAllIfaces: text.includes('本机网卡地址') };
      })()`,
    )
    check(
      'U12',
      'server-address-page',
      '服务地址页：二维码 ≥240×240 且真的画出了点阵；网卡地址表非空；推荐地址有标记；含扫码说明与手动输入兜底',
      u12.w >= 240 &&
        u12.h >= 240 &&
        u12.dark > 500 &&
        u12.rows >= 1 &&
        u12.hasScanHint &&
        u12.hasManual &&
        u12.hasAllIfaces &&
        (hasRecommended ? u12.hasRecTag : true),
      JSON.stringify({ ...u12, hasRecommended }),
    )

    // =====================================================================
    // U13 —— 二维码内容形如 bjsys://<ip>:<port>（真机扫码无法自动化）
    // =====================================================================
    const u13 = await apiCall(
      cdp,
      sid,
      `(async () => {
        const m = await import('/admin/src/api/server.ts');
        const i = await m.getServerInfo();
        const t = document.body.innerText;
        return { qr_content: i.qr_content, current_url: i.current_url,
                 discovery_port: i.discovery_port, app_port: i.app_port,
                 shownOnPage: !!i.qr_content && t.includes(i.qr_content) };
      })()`,
    )
    const qrOk = /^bjsys:\/\/[0-9.]+:\d+$/.test(String(u13.qr_content || ''))
    check(
      'U13',
      'qr-content-format',
      '二维码内容格式：qr_content 形如 bjsys://<ip>:<port>，且页面把明文地址也渲染出来（识别失败可手输）',
      qrOk && u13.shownOnPage && u13.current_url === String(u13.qr_content).replace('bjsys://', 'http://'),
      JSON.stringify(u13),
    )
    check(
      'U13x',
      'manual-qr-scan',
      'U13「用手机扫码唤起 App」：无真手机、无法断言 App 的 URL scheme 注册，需人工验收',
      false,
      'SKIPPED: 已机器化验证 qr_content 格式 + 二维码画布真有像素（U12/U13）；真机扫码属人工验收项',
      true,
    )

    // =====================================================================
    // U14 —— 无数据状态：引导性空状态 + 主操作按钮
    // =====================================================================
    await hardNavigate(cdp, sid, `${WEB_BASE}${BASE}students/list`)
    await waitFor(cdp, sid, `!!document.querySelector('input[placeholder="学号或姓名"]')`, '学生列表筛选区就绪', 10000)
    await evalJs(cdp, sid, fillExpr('学号或姓名', 'zzz_no_such_student_zzz'))
    await evalJs(cdp, sid, `(${CLICK_BY_TEXT})('查询')`)
    await sleep(900)
    const u14 = await evalJs(
      cdp,
      sid,
      `(() => {
        const empty = document.querySelector('.empty-state');
        const btn = empty ? empty.querySelector('button') : null;
        return { hasEmpty: !!empty,
                 text: empty ? (empty.textContent || '').trim() : '',
                 buttonText: btn ? (btn.textContent || '').trim() : '',
                 tableRows: document.querySelectorAll('.el-table__row').length };
      })()`,
    )
    check(
      'U14',
      'empty-state-with-action',
      '无数据状态：筛选无结果时渲染引导性空状态 + 主操作按钮（不是空白页）',
      u14.hasEmpty && u14.buttonText.length > 0 && u14.tableRows === 0,
      JSON.stringify(u14),
    )

    // =====================================================================
    // 防假绿：断言用例数达到下限
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
    ABORT_ERROR = err
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

function finish(code, backendLog, viteLog, browserLog) {
  if (ABORT_ERROR) {
    // 中断 = 后半段用例根本没跑，不能算「全绿」。补一条失败项再出结论。
    results.push({
      id: 'ABORT',
      slug: 'run-aborted',
      title: `整轮被异常中断，后续用例未执行：${ABORT_ERROR?.message || ABORT_ERROR}`,
      ok: false,
      detail: '',
      skipped: false,
    })
    code = 1
  }
  const failed = results.filter((r) => !r.ok && !r.skipped)
  say('')
  say('='.repeat(74))
  say(
    `烟测结果：${results.filter((r) => r.ok).length}/${results.length} 通过（含 ${results.filter((r) => r.skipped).length} 个跳过项）`,
  )
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
