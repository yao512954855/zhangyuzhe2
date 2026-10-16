/**
 * 单元管理路由 —— WEB-11（todo11 B1 / 契约 v1.3）。
 *
 * 替换原 routes/content.ts 里的 /units（旧一级扁平「分类」页 UnitList.vue），
 * 指向新的两级树形管理页；路径仍为 /units，旧入口自然接管，无孤儿页。
 * **不许改 `router/index.ts`** —— index 用 `import.meta.glob('./routes/*.ts')`
 * 自动聚合本文件，菜单由 AdminLayout 依据 `meta` 渲染。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/units',
    name: 'unit-tree',
    component: () => import('@/views/unit/UnitTree.vue'),
    meta: { title: '单元管理', icon: 'FolderOpened', order: 10, group: '内容' },
  },
]

export default routes
