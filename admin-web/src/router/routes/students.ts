/**
 * 学生 / 班级路由 —— WP-WEB-03
 *
 * 照 `routes/content.ts` 的结构写。**不许改 `router/index.ts`**，
 * index 用 `import.meta.glob('./routes/*.ts')` 自动聚合本文件。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/students/classes',
    name: 'students-classes',
    component: () => import('@/views/students/ClassList.vue'),
    meta: { title: '班级管理', icon: 'Files', order: 30 },
  },
  {
    path: '/students/list',
    name: 'students-list',
    component: () => import('@/views/students/StudentList.vue'),
    meta: { title: '学生管理', icon: 'User', order: 31 },
  },
  {
    path: '/students/import',
    name: 'students-import',
    component: () => import('@/views/students/StudentImportWizard.vue'),
    meta: { title: '学生导入', icon: 'UploadFilled', order: 32 },
  },
]

export default routes
