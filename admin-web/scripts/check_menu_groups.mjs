#!/usr/bin/env node
/**
 * 一次性校验：管理端侧边菜单「按功能分类 + 可折叠」是否真的渲染对了。
 *
 * 为什么必须真浏览器：`vue-tsc` / `vite build` 只能证明「能编译」——
 *   - 多子项组是不是真的渲染成 `el-sub-menu`、单项组有没有被多套一层折叠、
 *   - 点分组标题能否展开、子项真的从 `display:none` 变为可见、
 *   - 收起侧边栏是否降级为浮层（`el-menu--collapse`）、
 *   - 展开状态是否**持久化**（刷新后仍在）、
 *   - 折叠组里的项点得动、`is-active` 高亮与归属组正确，
 * 这些全都要真 DOM 才看得出来。
 *
 * 走**生产静态产物入口**（`http://<host>:8810/admin/`），不是 vite dev ——
 * 姚工实际访问的就是这条链路，而一期烟测全走 vite dev，这条没被走过。
 *
 * CDP over `--remote-debugging-pipe`：零依赖（本机 undici WebSocket 收不到 CDP 回帧，
 * 项目也没有 ws 依赖），骨架与 `admin-web/scripts/smoke_web01_login.mjs` 同源。
 *
 * 跑法：env -u PYTHONPATH node check_menu_groups.mjs
 */
import { spawn, spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

const BACKEND = process.env.CHECK_BACKEND || 'http://127.0.0.1:8810'
const WEB = process.env.CHECK_WEB || 'http://127.0.0.1:8810'
const BASE = '/admin/'
const USER = process.env.CHECK_USER || 'admin'
const PWD = process.env.CHECK_PWD || 'bjsys-dev-2026'
const SHOT = process.env.CHECK_SHOT || join(tmpdir(), 'menu_groups.png')

const BROWSER_CANDIDATES = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
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

/** 点某个折叠组的标题（按分组名匹配 `.el-sub-menu__title`） */
async function toggleGroup(cdp, sid, title) {
  const hit = await evalJs(
    cdp,
    sid,
    `(() => {
       const t = Array.from(document.querySelectorAll('.admin-menu .el-sub-menu__title'))
         .find((e) => (e.textContent || '').trim() === ${JSON.stringify(title)});
       if (!t) return false;
       t.click();
       return true;
     })()`,
  )
  await sleep(450)
  return hit
}

/** 主菜单一级结构快照（区分「平铺项」与「折叠组」） */
const MENU_SNAPSHOT = `(() => Array.from(document.querySelectorAll('.admin-menu > li')).map((li) => {
  if (li.classList.contains('el-sub-menu')) {
    const t = li.querySelector('.el-sub-menu__title');
    return {
      type: 'sub',
      title: (t?.textContent || '').trim(),
      items: Array.from(li.querySelectorAll('.el-menu-item')).map((e) => (e.textContent || '').trim()),
    };
  }
  return { type: 'item', title: (li.textContent || '').trim() };
}))()`

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
  const profile = join(tmpdir(), `bjsys_menucheck_${Date.now()}`)
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

  try {
    // 3) 先到同源页写 localStorage（等价于「刚登录完」），再整页导航到工作台
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
    await cdp.send('Page.navigate', { url: `${WEB}${BASE}dashboard` }, sid)
    await waitFor(cdp, sid, `!!document.querySelector('.admin-header__user')`, 'layout rendered', 20000)
    // 子项是 v-show（不是 v-if），所以折叠组里的项也在 DOM 里 —— 这里等 14 项齐
    await waitFor(cdp, sid, `document.querySelectorAll('.admin-menu .el-menu-item').length >= 14`, 'menu items', 10000)
    await sleep(300)

    // 4) 断言「折叠组 + 平铺项」的混合结构
    const snapshot = await evalJs(cdp, sid, MENU_SNAPSHOT)
    const expected = [
      { type: 'item', title: '工作台' },
      { type: 'sub', title: '内容', items: ['内容管理', '导入向导', '内容版本'] },
      { type: 'sub', title: '学生', items: ['班级管理', '学生管理', '学生导入'] },
      {
        type: 'sub',
        title: '作业与统计',
        items: ['作业布置', '班级概览', '单元掌握度', '学生报告', '申诉复核'],
      },
      { type: 'item', title: '课堂大屏' },
      { type: 'item', title: '服务地址' },
    ]
    const got = JSON.stringify(snapshot)
    check(
      'menu-structure',
      '多子项组渲染为可折叠 el-sub-menu、单项组平铺 el-menu-item，顺序与成员正确',
      got === JSON.stringify(expected),
      `got=${got}`,
    )

    const total =
      snapshot.filter((n) => n.type === 'item').length +
      snapshot.filter((n) => n.type === 'sub').reduce((n, g) => n + g.items.length, 0)
    check('menu-total', '菜单项总数仍是 14（折叠没吞菜单项）', total === 14, `total=${total}`)

    const subCount = snapshot.filter((n) => n.type === 'sub').length
    check('submenu-count', '只有 3 个多子项组做成折叠父级（单项组不多套一层）', subCount === 3, `subCount=${subCount}`)

    // 5) 默认态：当前在 /dashboard（属单项组「总览」），不应有任何组被展开
    const defOpen = await evalJs(
      cdp,
      sid,
      `Array.from(document.querySelectorAll('.admin-menu .el-sub-menu.is-opened')).map((e) => (e.querySelector('.el-sub-menu__title')?.textContent || '').trim())`,
    )
    check('default-collapsed', '默认不展开任何折叠组（避免一进来就一屏菜单）', defOpen.length === 0, JSON.stringify(defOpen))

    // 6) 点「内容」标题 → 展开：is-opened + 子项真可见 + 状态写入 localStorage
    const hitContent = await toggleGroup(cdp, sid, '内容')
    const expanded = await evalJs(
      cdp,
      sid,
      `(() => {
         const li = Array.from(document.querySelectorAll('.admin-menu .el-sub-menu'))
           .find((e) => (e.querySelector('.el-sub-menu__title')?.textContent || '').trim() === '内容');
         if (!li) return { found: false };
         const item = Array.from(li.querySelectorAll('.el-menu-item'))
           .find((e) => (e.textContent || '').trim() === '内容管理');
         return {
           found: true,
           opened: li.classList.contains('is-opened'),
           visible: item ? item.getBoundingClientRect().height > 0 : false,
         };
       })()`,
    )
    const storedOpened = await evalJs(cdp, sid, `localStorage.getItem('bjsys_admin_menu_opened') || ''`)
    check(
      'submenu-expand',
      '点「内容」分组标题 → 组展开、子项从不可见变为可见、展开状态写入 localStorage',
      hitContent && expanded.found && expanded.opened && expanded.visible && storedOpened.includes('内容'),
      JSON.stringify({ hitContent, expanded, storedOpened }),
    )

    // 7) 收起侧边栏 → 整体折叠（折叠父级降级为浮层，不再撑开文字）
    await evalJs(cdp, sid, `document.querySelector('.admin-header__toggle').click(), true`)
    await sleep(450)
    const collapsed = await evalJs(
      cdp,
      sid,
      `(() => {
         const menu = document.querySelector('.admin-menu');
         const aside = document.querySelector('.admin-aside');
         return {
           isCollapseClass: !!menu?.classList.contains('el-menu--collapse'),
           asideWidth: aside?.getBoundingClientRect().width ?? 0,
         };
       })()`,
    )
    check(
      'sidebar-collapse',
      '点折叠按钮 → 侧边栏收成图标条（el-menu--collapse），宽度 < 100',
      collapsed.isCollapseClass && collapsed.asideWidth < 100,
      JSON.stringify(collapsed),
    )

    // 展开回来
    await evalJs(cdp, sid, `document.querySelector('.admin-header__toggle').click(), true`)
    await sleep(450)

    // 8) 折叠组里的项点得动：展开「作业与统计」→ 点「申诉复核」→ 路由 + 高亮 + 归属组
    await toggleGroup(cdp, sid, '作业与统计')
    const nav = await evalJs(
      cdp,
      sid,
      `(() => {
         const el = Array.from(document.querySelectorAll('.admin-menu .el-menu-item'))
           .find((e) => (e.textContent || '').trim() === '申诉复核');
         if (!el) return { clicked: false };
         el.click();
         return { clicked: true };
       })()`,
    )
    await waitFor(cdp, sid, `location.pathname === ${JSON.stringify(BASE + 'stats/appeals')}`, 'navigate appeals', 8000)
    await sleep(400)
    const active = await evalJs(
      cdp,
      sid,
      `(() => {
         const a = document.querySelector('.admin-menu .el-menu-item.is-active');
         const sub = a?.closest('.el-sub-menu');
         return {
           active: (a?.textContent || '').trim(),
           activeGroup: (sub?.querySelector('.el-sub-menu__title')?.textContent || '').trim(),
           groupOpened: sub?.classList.contains('is-opened') ?? false,
           path: location.pathname,
         };
       })()`,
    )
    check(
      'click-in-collapsed-group',
      '点折叠组内的「申诉复核」→ 路由 /stats/appeals、该项高亮、归属「作业与统计」且该组展开',
      nav.clicked && active.path === `${BASE}stats/appeals` && active.active === '申诉复核' &&
        active.activeGroup === '作业与统计' && active.groupOpened,
      JSON.stringify({ nav, active }),
    )

    // 9) 刷新页面 → 展开状态持久化（「内容」「作业与统计」仍在展开）
    await cdp.send('Page.reload', {}, sid)
    await waitFor(cdp, sid, `!!document.querySelector('.admin-header__user')`, 'layout after reload', 20000)
    await waitFor(cdp, sid, `document.querySelectorAll('.admin-menu .el-menu-item').length >= 14`, 'menu after reload', 10000)
    await sleep(400)
    const afterReload = await evalJs(
      cdp,
      sid,
      `Array.from(document.querySelectorAll('.admin-menu .el-sub-menu.is-opened')).map((e) => (e.querySelector('.el-sub-menu__title')?.textContent || '').trim())`,
    )
    check(
      'expand-persisted',
      '刷新后展开状态保持（内容 + 作业与统计 仍在展开）',
      afterReload.includes('内容') && afterReload.includes('作业与统计'),
      JSON.stringify(afterReload),
    )

    // 10) 截图留证
    const shot = await cdp.send('Page.captureScreenshot', { format: 'png' }, sid)
    mkdirSync(dirname(SHOT), { recursive: true })
    writeFileSync(SHOT, Buffer.from(shot.data, 'base64'))
    console.log(`[--] screenshot: ${SHOT}`)
  } finally {
    cdp.close()
    if (br.pid) {
      if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(br.pid), '/T', '/F'], { stdio: 'ignore' })
      else br.kill('SIGKILL')
    }
  }

  const failed = results.filter((r) => !r.ok)
  console.log(`\n===== ${results.length - failed.length}/${results.length} passed =====`)
  return failed.length ? 1 : 0
}

main()
  .then((code) => process.exit(code))
  .catch((e) => {
    console.error(`[!!] crashed: ${e.message}`)
    process.exit(1)
  })
