/**
 * 内容版本快照路由 —— WP-2I（二期工作包 9）
 *
 * 照 `routes/content.ts` 的结构写。**不许改 `router/index.ts`**，
 * index 用 `import.meta.glob('./routes/*.ts')` 自动聚合本文件。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/content/versions',
    name: 'content-versions',
    component: () => import('@/views/content/VersionList.vue'),
    meta: { title: '内容版本', icon: 'Clock', order: 22 },
  },
]

export default routes
