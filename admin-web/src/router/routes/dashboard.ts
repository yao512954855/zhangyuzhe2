/**
 * 工作台路由 —— WP-WEB-01 F7
 *
 * 示例文件，同时也是「新增路由」的标准写法。WEB-02 / WEB-03 照抄结构即可。
 */
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('@/views/Dashboard.vue'),
    meta: { title: '工作台', icon: 'Odometer', order: 10 },
  },
]

export default routes
