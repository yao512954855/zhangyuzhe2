/**
 * 练习设置路由 —— WEB-11 F14-5（todo11 A3/A5 决策 #13）。
 *
 * 挂侧边栏「系统」组，与「服务地址」「AI 能力模式」同级。
 * **不许改 `router/index.ts`** —— index 用 `import.meta.glob('./routes/*.ts')`
 * 自动聚合本文件。
 *
 * order=55：现役 server 组 order=50（服务地址）、capabilities order=60，不撞号。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/settings',
    name: 'practice-settings',
    component: () => import('@/views/server/SettingsPage.vue'),
    meta: { title: '练习设置', icon: 'Timer', order: 55, group: '系统' },
  },
]

export default routes
