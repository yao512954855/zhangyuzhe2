/**
 * 分类 / 内容录入路由 —— WP-B（契约《01-共享契约冻结》§9：路由文件放 router/routes/）。
 *
 * 不改 `router/index.ts` —— index 用 `import.meta.glob('./routes/*.ts')` 自动聚合本文件，
 * 菜单由 AdminLayout 依据 `meta` 渲染。
 *
 * 【菜单机制注意】AdminLayout 对「组内 ≥2 项」的分组会折叠成 el-sub-menu（多一个父级标题）。
 * 验收 U1 要求菜单正好三项（分类 / 内容录入 / 服务地址），所以这里给两条路由各配一个
 * 独占分组名 —— 单项组会被平铺成菜单项，分组名不显示，视觉上就是三个平级菜单。
 * order 接现有 server 路由（order 50 / group 系统）之前，不撞号。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/units',
    name: 'units',
    component: () => import('@/views/content/UnitList.vue'),
    meta: { title: '分类', icon: 'FolderOpened', order: 10, group: '内容' },
  },
  {
    path: '/items',
    name: 'items',
    component: () => import('@/views/content/ItemTable.vue'),
    meta: { title: '内容录入', icon: 'EditPen', order: 20, group: '内容录入' },
  },
]

export default routes
