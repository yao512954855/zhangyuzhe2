/**
 * 内容录入路由 —— WP-B（契约《01-共享契约冻结》§9：路由文件放 router/routes/）。
 *
 * 【WEB-11 变更】原 /units「分类」页已由 routes/unit.ts 的两级树形管理页接管
 * （todo11 B1 / 契约 v1.3），本文件只剩 /items；旧路径 /units 无需重定向 ——
 * 新路由路径相同，旧入口/书签自然落到新页，无孤儿页。
 *
 * 不改 `router/index.ts` —— index 用 `import.meta.glob('./routes/*.ts')` 自动聚合本文件，
 * 菜单由 AdminLayout 依据 `meta` 渲染。
 *
 * 【菜单机制注意】AdminLayout 对「组内 ≥2 项」的分组会折叠成 el-sub-menu（多一个父级标题）。
 * 所以这里给路由配独占分组名 —— 单项组会被平铺成菜单项，分组名不显示。
 * order 接现有 server 路由（order 50 / group 系统）之前，不撞号。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/items',
    name: 'items',
    component: () => import('@/views/content/ItemTable.vue'),
    meta: { title: '内容录入', icon: 'EditPen', order: 20, group: '内容录入' },
  },
  {
    path: '/items/quick-import',
    name: 'items-quick-import',
    component: () => import('@/views/content/QuickImport.vue'),
    meta: { title: '快速导入', icon: 'Paste', order: 21, group: '内容录入' },
  },
]

export default routes
