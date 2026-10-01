/**
 * 内容管理路由 —— WP-WEB-02
 *
 * 照 `routes/dashboard.ts` 的结构写。**不许改 `router/index.ts`**，
 * index 用 `import.meta.glob('./routes/*.ts')` 自动聚合本文件。
 *
 * 2026-09-30 姚工定稿：内容管理按学科拆两页 ——
 *   - `/content/english` 英语内容管理（词条列表）
 *   - `/content/chinese` 语文内容管理（课文列表）
 * 两页共用 `SubjectContent.vue`，路由 props 固定学科；原 `/content/units`
 * 合并表页退役，旧链接重定向到英语内容管理保旧链。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/content/english',
    name: 'content-english',
    component: () => import('@/views/content/SubjectContent.vue'),
    props: { subject: 'english' },
    meta: { title: '英语内容管理', icon: 'Reading', order: 20, group: '内容' },
  },
  {
    path: '/content/chinese',
    name: 'content-chinese',
    component: () => import('@/views/content/SubjectContent.vue'),
    props: { subject: 'chinese' },
    meta: { title: '语文内容管理', icon: 'EditPen', order: 21, group: '内容' },
  },
  {
    // 原内容管理页（单元合并表）已拆分为英语/语文两页；旧链接重定向保旧链。
    path: '/content/units',
    redirect: '/content/english',
  },
  {
    path: '/content/units/:id',
    redirect: '/content/english',
  },
  {
    path: '/content/import',
    name: 'content-import',
    component: () => import('@/views/content/ImportWizard.vue'),
    meta: { title: '导入向导', icon: 'UploadFilled', order: 22, group: '内容' },
  },
]

export default routes
