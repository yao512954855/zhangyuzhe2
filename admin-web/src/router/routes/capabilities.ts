/**
 * AI 能力模式切换路由 —— 批次 D。
 *
 * 照 `routes/server.ts` 的结构写。**不许改 `router/index.ts`**，
 * index 用 `import.meta.glob('./routes/*.ts')` 自动聚合本文件。
 *
 * order=60（现役 server 组 order=50，不撞号 —— 菜单 order 撞号是踩过的坑）；
 * group='系统' 与「服务地址」同组。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/capabilities',
    name: 'capabilities',
    component: () => import('@/views/server/CapabilitySettings.vue'),
    meta: { title: 'AI 能力模式', icon: 'Cpu', order: 60, group: '系统' },
  },
]

export default routes
