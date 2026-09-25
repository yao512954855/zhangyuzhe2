# 背记系统 · 管理端（admin-web）

> 中小学英语单词 / 语文课文背诵系统 —— 管理端
> 对应工作包 **WP-WEB-01 管理端外壳与登录**（规格见 `docs/dev/20-管理端工作包规格.md`）

## 技术栈

Vue 3 + Vite + TypeScript + Element Plus + Pinia + vue-router。
**依赖版本在 `package.json` 里写死到具体版本**，不要改成 `^` / `~`（契约 §1）——
否则不同人装出不同环境，问题极难复现。

> 版本选型说明：没有追最新大版本（vue-router 5 / pinia 4 / vite 8 / TS 7 尚未验证与
> Element Plus 的兼容性），而是选了互相兼容的稳定组合。要升级请整体评估后再动。

## 开发

```bash
cd admin-web
npm install
npm run dev
```

开发地址：**http://127.0.0.1:5273/admin/** —— 注意路径带的 `/admin/` 不是多余的，
它和部署后的真实入口 `http://{host}:8810/admin` 保持一致（契约 §11）。
若去掉它，开发时一切正常、上线后静态资源 404。

页面里的 `/api/*` 请求由 Vite 代理转发到 `http://localhost:8810`（本机后端），
见 `.env.development` 的 `VITE_API_TARGET`。

后端启动方式见 `backend/README.md` 或 `docs/dev/10a-后端工作包规格（上）.md`。

## 构建

```bash
npm run build      # 先 vue-tsc 类型检查，再 vite build
npm run typecheck  # 只做类型检查
npm run build:only # 跳过类型检查（应急用，不建议）
```

产物在 `admin-web/dist/`，由后端在 `/admin` 路径下托管（部署工作包含此步骤）。

## 目录约定

```
src/
  api/
    client.ts          # 统一请求层：自动带 token、解包、抛 BizError、统一弹提示
    types.ts           # ApiResponse / PageResult / ErrorCode 等通用类型
  components/          # 通用组件（PageHeader / EmptyState / LoadingBox）
  layouts/
    AdminLayout.vue    # 左侧菜单 + 顶部栏
  router/
    index.ts           # 用 import.meta.glob 聚合 routes/*.ts
    guard.ts           # 登录守卫
    routes/            # ⭐ 业务路由按文件拆分，新增文件即自动生效
  stores/
    user.ts            # 登录态（token 持久化到 localStorage）
  styles/
    index.scss         # 设计变量 + 重置 + 全局工具类
  utils/
    format.ts          # 时间 / 百分比 / 时长格式化
  views/               # 页面
```

## ⭐ 新增一个页面（WEB-02 / WEB-03 必读）

**不要改 `router/index.ts`。** 只要在 `src/router/routes/` 下新建一个文件：

```ts
// src/router/routes/students.ts
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/students',
    name: 'students',
    component: () => import('@/views/students/StudentList.vue'),
    meta: { title: '学生管理', icon: 'User', order: 20 },
  },
]

export default routes
```

菜单与路由会自动出现（验收用例 U8）。`meta` 约定：

| 字段 | 含义 |
|---|---|
| `title` | 菜单文字 + 页面标题（必填，不填则不进菜单） |
| `icon` | Element Plus 图标组件名（已全局注册），如 `User` |
| `order` | 菜单排序，越小越靠前，默认 999 |
| `public` | 免登录可访问（仅登录页用） |
| `hidden` | 不进侧边菜单（如 404） |

## 请求层铁律（避免各页面写法发散）

1. **所有请求走 `@/api/client` 的 `api.get/post/put/patch/delete`**，不要直接用 axios。
2. `code != 0` 时 client 已经 `BizError` + 弹提示了，**页面里不要再弹一次**（验收用例 U6）。
3. 业务失败是 **HTTP 200 + 非零 code**，**不能靠 HTTP 状态判断成败**（契约 §5.1）。
4. 写操作带上 `idempotencyKey`（`newIdempotencyKey()`）可避免弱网重试重复写入。
5. 时间字段是 UTC ISO 串，展示一律走 `@/utils/format` 的 `formatDateTime`。
