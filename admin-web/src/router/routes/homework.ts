/**
 * 作业布置路由 —— WP-2G（二期 G2）
 *
 * 路由自动聚合（`router/index.ts` 用 `import.meta.glob('./routes/*.ts')` 加载本文件），
 * **不修改 index / 路由总表**。菜单由 `meta.title` / `meta.icon` / `meta.order` 自动生成。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/homework',
    name: 'homework',
    component: () => import('@/views/homework/HomeworkList.vue'),
    meta: { title: '作业布置', icon: 'Notebook', order: 40, group: '作业与统计' },
  },
]

export default routes
