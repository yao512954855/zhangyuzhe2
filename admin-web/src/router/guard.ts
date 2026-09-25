/**
 * 路由守卫 —— WP-WEB-01 F5
 *
 * U5：未登录直接访问受保护页面 → 重定向登录页（并把原地址塞进 `redirect`）。
 * F5：已登录访问登录页 → 直接进工作台。
 */
import type { Router } from 'vue-router'

import { useUserStore } from '@/stores/user'

const APP_TITLE = '背记系统 · 管理端'
const HOME_PATH = '/dashboard'

export function setupRouterGuard(router: Router): void {
  router.beforeEach((to) => {
    const userStore = useUserStore()

    if (to.meta.public) {
      // 已登录还去登录页 → 回工作台
      if (to.name === 'login' && userStore.isLoggedIn) {
        return { path: HOME_PATH }
      }
      return true
    }

    if (!userStore.isLoggedIn) {
      return { path: '/login', query: { redirect: to.fullPath } }
    }

    return true
  })

  router.afterEach((to) => {
    document.title = to.meta.title ? `${to.meta.title} · ${APP_TITLE}` : APP_TITLE
  })
}
