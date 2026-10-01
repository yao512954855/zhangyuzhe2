/**
 * 课堂实时大屏路由 —— WP-2D（二期 G1）
 *
 * 照 `routes/stats.ts` 的结构写。**不许改 `router/index.ts`**，
 * index 用 `import.meta.glob('./routes/*.ts')` 自动聚合本文件，菜单与路由自动出现。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/live-board',
    name: 'live-board',
    component: () => import('@/views/live/LiveBoard.vue'),
    meta: { title: '课堂大屏', icon: 'Monitor', order: 45, group: '大屏' },
  },
]

export default routes
