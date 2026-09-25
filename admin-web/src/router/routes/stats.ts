/**
 * 数据看板 / 申诉路由 —— WP-WEB-03
 *
 * 照 `routes/content.ts` 的结构写。**不许改 `router/index.ts`**，
 * index 用 `import.meta.glob('./routes/*.ts')` 自动聚合本文件。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/stats/class',
    name: 'stats-class',
    component: () => import('@/views/stats/ClassOverview.vue'),
    meta: { title: '班级概览', icon: 'DataLine', order: 40 },
  },
  {
    path: '/stats/unit',
    name: 'stats-unit',
    component: () => import('@/views/stats/UnitMastery.vue'),
    meta: { title: '单元掌握度', icon: 'DataAnalysis', order: 41 },
  },
  {
    path: '/stats/student',
    name: 'stats-student',
    component: () => import('@/views/stats/StudentReport.vue'),
    meta: { title: '学生报告', icon: 'UserFilled', order: 42 },
  },
  {
    path: '/stats/appeals',
    name: 'stats-appeals',
    component: () => import('@/views/stats/AppealList.vue'),
    meta: { title: '申诉复核', icon: 'Bell', order: 43 },
  },
]

export default routes
