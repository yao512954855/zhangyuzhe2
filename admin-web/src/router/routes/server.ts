/**
 * 服务地址与二维码路由 —— WP-WEB-03（F11 / F12）
 *
 * 照 `routes/content.ts` 的结构写。**不许改 `router/index.ts`**，
 * index 用 `import.meta.glob('./routes/*.ts')` 自动聚合本文件。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/server/address',
    name: 'server-address',
    component: () => import('@/views/server/ServerAddress.vue'),
    meta: { title: '服务地址', icon: 'Connection', order: 50 },
  },
]

export default routes
