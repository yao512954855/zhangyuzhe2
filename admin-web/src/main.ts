/**
 * 应用入口。
 *
 * 顺序有讲究：**pinia 必须在 router 之前装入** —— 路由守卫里会 `useUserStore()`，
 * pinia 没就绪会直接抛 "getActivePinia() was called but there was no active Pinia"。
 */
import { type Component, createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIcons from '@element-plus/icons-vue'

import 'element-plus/dist/index.css'
import '@/styles/index.scss'

import App from '@/App.vue'
import router from '@/router'
import { setupRouterGuard } from '@/router/guard'
import { setUnauthorizedHandler } from '@/api/client'
import { useUserStore } from '@/stores/user'

const app = createApp(App)

app.use(createPinia())

// 全局注册 Element Plus 图标：
// 菜单项用 `meta.icon` 里的**字符串**动态渲染，必须全局注册才能解析。
for (const [name, component] of Object.entries(ElementPlusIcons)) {
  app.component(name, component as Component)
}

setupRouterGuard(router)

// 登录态失效（HTTP 401 或业务码 10002）→ 清 token 并回登录页（验收用例 U7）
setUnauthorizedHandler(() => {
  useUserStore().logout()

  const current = router.currentRoute.value
  if (current.name !== 'login') {
    void router.replace({ path: '/login', query: { redirect: current.fullPath } })
  }
})

app.use(router)
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')
