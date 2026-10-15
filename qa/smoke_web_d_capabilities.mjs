/**
 * 批次 D 管理端「AI 能力模式」切换页 —— 无头冒烟（Playwright 驱动系统 Edge）。
 *
 * 启动前请确保后端 8810 在跑且 /api/capabilities 返回 mode=hybrid。
 * 脚本结尾会把模式还原成 hybrid（PUT 回去），不把环境留在 local。
 *
 * 运行：node qa/smoke_web_d_capabilities.mjs
 * 依赖：playwright（仅用系统 Edge 的 executablePath，不下载浏览器）。
 */
import pw from 'file:///D:/helloagent/zhanglaoshi_zhangyuze/admin-web/node_modules/playwright/index.js'
const { chromium } = pw
import { execSync } from 'node:child_process'

const BASE = 'http://127.0.0.1:8810/admin/'
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'} · ${name}${detail ? ' · ' + detail : ''}`)
}

async function apiGet() {
  const out = execSync('curl -s http://127.0.0.1:8810/api/capabilities', { encoding: 'utf8' })
  return JSON.parse(out).data
}
async function apiPut(mode) {
  execSync(
    `curl -s -X PUT http://127.0.0.1:8810/api/capabilities/mode -H "Content-Type: application/json" -d "{\\"mode\\":\\"${mode}\\"}"`,
    { encoding: 'utf8' },
  )
}

async function main() {
  const browser = await chromium.launch({ executablePath: EDGE, args: ['--no-sandbox'] })
  const page = await browser.newPage()
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message))
  page.on('console', (m) => {
    if (m.type() === 'error') console.log('CONSOLE.ERR:', m.text())
  })

  try {
    // 前置：确保环境是 hybrid
    await apiPut('hybrid')

    // ---- 1. 菜单出现「AI 能力模式」且可进入 ----
    await page.goto(BASE, { waitUntil: 'load' })
    await page.waitForSelector('.el-menu', { timeout: 15000 })
    const menuItem = page.locator('.el-menu-item', { hasText: 'AI 能力模式' })
    const menuVisible = (await menuItem.count()) > 0 && (await menuItem.first().isVisible())
    check('1. 菜单有「AI 能力模式」且可进入', menuVisible)
    if (menuVisible) {
      await menuItem.first().click()
      await page.waitForURL('**/capabilities', { timeout: 10000 })
      const title = await page.locator('.page-header__title').first().innerText()
      check('1b. 进入后页面标题正确', title.includes('AI 能力模式'), `title="${title}"`)
    } else {
      check('1b. 进入后页面标题正确', false, '菜单项未出现，无法进入')
    }

    // ---- 2. 三档 radio 渲染 + 默认选中 = GET 返回 ----
    await page.waitForSelector('.el-radio-group', { timeout: 10000 })
    const radios = page.locator('.el-radio-button')
    const radioCount = await radios.count()
    const caps = await apiGet()
    // hybrid 对应标签「智能切换（推荐）」
    const expectedActive = { hybrid: '智能切换', local: '纯本地', cloud: '纯云端' }[caps.mode]
    const activeLabel = page.locator('.el-radio-button.is-active')
    const activeText = (await activeLabel.count()) ? await activeLabel.first().innerText() : ''
    const activeOk = activeText.includes(expectedActive)
    check(
      '2. 三档 radio 渲染、默认选中与 GET 一致',
      radioCount === 3 && activeOk,
      `radioCount=${radioCount}, GET mode=${caps.mode}, active="${activeText.trim()}"`,
    )

    // ---- 4. 三行能力状态渲染（先测，避免保存后状态变化干扰）----
    const engineRows = page.locator('.engine-row')
    const engineCount = await engineRows.count()
    const nameTts = await page.locator('.engine-row__name', { hasText: '听写播报' }).count()
    const nameOcr = await page.locator('.engine-row__name', { hasText: '拍照批改' }).count()
    const nameAsr = await page.locator('.engine-row__name', { hasText: '背诵判分' }).count()
    const engineOk = engineCount === 3 && nameTts > 0 && nameOcr > 0 && nameAsr > 0
    check(
      '4. 三行能力状态渲染',
      engineOk,
      `engineCount=${engineCount}, tts/ocr/asr=${nameTts}/${nameOcr}/${nameAsr}`,
    )

    // ---- 3. 切到「纯本地」→ 保存按钮出现 → 点击 → toast → 刷新仍 local ----
    await page.locator('.el-radio-button', { hasText: '纯本地' }).click()
    await page.waitForTimeout(300)
    const saveBarVisible = await page.locator('.save-bar').isVisible().catch(() => false)
    check('3a. 切换后出现保存按钮', saveBarVisible)

    if (saveBarVisible) {
      await page.locator('.save-bar .el-button--primary').click()
      // 等待成功 toast
      let toastOk = false
      try {
        const toast = page.locator('.el-message', { hasText: '已保存' }).first()
        await toast.waitFor({ state: 'visible', timeout: 8000 })
        toastOk = (await toast.innerText()).includes('已保存')
      } catch {
        toastOk = false
      }
      check('3b. 保存后出现成功 toast', toastOk)

      // 等待保存按钮消失（dirty 复位）
      await page.waitForTimeout(500)

      // 刷新页面
      await page.reload({ waitUntil: 'load' })
      await page.waitForSelector('.el-radio-group', { timeout: 10000 })
      const activeAfter = page.locator('.el-radio-button.is-active')
      const activeAfterText = (await activeAfter.count()) ? await activeAfter.first().innerText() : ''
      check(
        '3c. 刷新后仍为 local',
        activeAfterText.includes('纯本地'),
        `activeAfter="${activeAfterText.trim()}"`,
      )
    } else {
      check('3b. 保存后出现成功 toast', false, '保存按钮未出现，跳过')
      check('3c. 刷新后仍为 local', false, '保存按钮未出现，跳过')
    }

    // ---- 5. 保存后 curl GET 确认 settings 已变 ----
    const afterCaps = await apiGet()
    check('5. curl GET 确认 mode=local', afterCaps.mode === 'local', `GET mode=${afterCaps.mode}`)

    // ---- 还原 hybrid ----
    await apiPut('hybrid')
    const restored = await apiGet()
    check('R. 环境已还原 hybrid', restored.mode === 'hybrid', `GET mode=${restored.mode}`)
  } catch (e) {
    check('EXCEPTION', false, e?.message || String(e))
  } finally {
    await browser.close()
  }

  const failed = results.filter((r) => !r.ok)
  console.log(`\n==== 冒烟汇总：${results.length - failed.length}/${results.length} PASS ====`)
  if (failed.length) {
    console.log('FAIL 项：', failed.map((f) => f.name).join('; '))
    process.exit(1)
  }
}

main()
