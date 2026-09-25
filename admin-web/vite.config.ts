/**
 * Vite 配置 —— WP-WEB-01 F1
 *
 * 两个必须记住的约定：
 * 1. `base` 固定 `/admin/`。部署契约《01-共享契约冻结》§11 规定管理端入口是
 *    `http://{host}:8810/admin`，即生产环境由后端（FastAPI）托管 admin-web/dist。
 *    开发环境的 URL 因此也是 `http://127.0.0.1:5273/admin/`，与生产路径结构一致，
 *    避免「开发好好的、一上生产静态资源 404」。
 * 2. 开发代理把 `/api` 转发到本机后端 8810（契约 §11 端口约定）。
 */
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  const apiTarget = env.VITE_API_TARGET || 'http://localhost:8810'
  const devPort = Number(env.VITE_DEV_PORT || 5273)
  const base = env.VITE_BASE_PATH || '/admin/'
  // 默认只绑本机；局域网演示时在 .env.development.local 里设 VITE_DEV_HOST=0.0.0.0
  const devHost = env.VITE_DEV_HOST || '127.0.0.1'

  return {
    base,
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: devHost,
      port: devPort,
      strictPort: true,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
    // 注：vite 7 起 SCSS 默认走 modern compiler API，
    // 不再需要（也不再接受）`css.preprocessorOptions.scss.api` 这个选项。
    build: {
      outDir: 'dist',
      sourcemap: false,
      chunkSizeWarningLimit: 1500,
    },
  }
})
