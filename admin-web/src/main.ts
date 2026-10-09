/**
 * 应用入口。
 */
import { type Component, createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIcons from '@element-plus/icons-vue'

import 'element-plus/dist/index.css'
import '@/styles/index.scss'

import App from '@/App.vue'
import router from '@/router'

const app = createApp(App)

// 全局注册 Element Plus 图标：
// 菜单项用 `meta.icon` 里的**字符串**动态渲染，必须全局注册才能解析。
for (const [name, component] of Object.entries(ElementPlusIcons)) {
  app.component(name, component as Component)
}

app.use(router)
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')
