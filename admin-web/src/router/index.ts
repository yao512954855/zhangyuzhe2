/**
 * 路由入口 —— WP-WEB-01 F3
 *
 * 【并发开发的关键机制】业务路由用 `import.meta.glob('./routes/*.ts')` 聚合。
 * WEB-02 / WEB-03 各自往 `src/router/routes/` 里新增一个文件即可生效，
 * **谁都不需要改本文件** —— 从根上消灭「多个包抢改同一个文件」的冲突。
 *
 * 【新增路由文件的约定】
 * ```ts
 * import type { RouteRecordRaw } from 'vue-router'
 *
 * const routes: RouteRecordRaw[] = [
 *   {
 *     path: '/students',
 *     name: 'students',
 *     component: () => import('@/views/students/StudentList.vue'),
 *     meta: { title: '学生管理', icon: 'User', order: 20, group: '学生' },
 *   },
 * ]
 *
 * export default routes
 * ```
 * 其余 meta 约定：
 * - `public`：免登录（如登录页）
 * - `hidden`：不进菜单（如 404、详情页）
 * - `group`：**侧边菜单分组名**。菜单按 group 归组渲染，组间顺序取组内最小 order；
 *   未填 group 的路由归入「其他」，不会掉菜单。
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    /** 菜单与页面标题 */
    title?: string
    /** Element Plus 图标组件名（已全局注册），如 'User' */
    icon?: string
    /** 菜单排序，越小越靠前，默认 999 */
    order?: number
    /** 侧边菜单分组名（同类功能归一组）；未填归入「其他」 */
    group?: string
    /** 免登录可访问 */
    public?: boolean
    /** 不进侧边菜单 */
    hidden?: boolean
  }
}

/** 业务路由模块（每个文件 default 导出 `RouteRecordRaw[]`）。 */
const routeModules = import.meta.glob<{ default: RouteRecordRaw[] }>('./routes/*.ts', {
  eager: true,
})

export const businessRoutes: RouteRecordRaw[] = Object.entries(routeModules)
  .flatMap(([file, mod]) => {
    const routes = mod?.default
    if (!Array.isArray(routes)) {
      console.error(`[router] ${file} 必须 default 导出一个 RouteRecordRaw[]`)
      return []
    }
    return routes
  })
  .sort((a, b) => Number(a.meta?.order ?? 999) - Number(b.meta?.order ?? 999))

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录', public: true, hidden: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/AdminLayout.vue'),
    redirect: '/dashboard',
    children: [
      ...businessRoutes,
      {
        path: '/:pathMatch(.*)*',
        name: 'not-found',
        component: () => import('@/views/NotFound.vue'),
        meta: { title: '页面不存在', hidden: true },
      },
    ],
  },
]

const router = createRouter({
  // 部署契约 §11：管理端入口是 `{host}:8810/admin`，因此 history base 必须与 vite `base` 一致
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

export default router
