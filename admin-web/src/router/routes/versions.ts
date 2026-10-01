/**
 * 内容变更记录路由 —— WP-2I（二期工作包 9）+ 2026-09-30 方案 B 重构
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
    meta: { title: '内容变更记录', icon: 'Clock', order: 23, group: '内容' },
  },
]

export default routes
