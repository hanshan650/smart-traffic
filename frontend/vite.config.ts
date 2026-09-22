import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // WebSocket 实时推送。
      // 注意：**必须放在 '/api' 之前** —— Vite 按声明顺序匹配，
      // 若 '/api' 先命中，升级请求会因缺少 ws:true 而握手失败。
      '/api/v1/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
        changeOrigin: true,
      },
      // REST 接口
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // 检测快照等静态文件
      '/static': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
