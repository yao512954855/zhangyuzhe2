/**
 * 内容管理路由 —— WP-WEB-02
 *
 * 照 `routes/dashboard.ts` 的结构写。**不许改 `router/index.ts`**，
 * index 用 `import.meta.glob('./routes/*.ts')` 自动聚合本文件。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/content/units',
    name: 'content-units',
    component: () => import('@/views/content/UnitList.vue'),
    meta: { title: '内容管理', icon: 'Notebook', order: 20 },
  },
  {
    path: '/content/units/:id',
    name: 'content-unit-detail',
    component: () => import('@/views/content/UnitDetail.vue'),
    meta: { title: '单元详情', hidden: true },
  },
  {
    path: '/content/import',
    name: 'content-import',
    component: () => import('@/views/content/ImportWizard.vue'),
    meta: { title: '导入向导', icon: 'UploadFilled', order: 21 },
  },
]

export default routes
