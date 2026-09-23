import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: 'prompt',
      // dev 下也注册 SW，否则没法在本机验证「安装到桌面」与离线行为。
      // 代价是开发时可能拿到缓存资源，所以只对 /public 接口做运行时缓存，
      // 构建产物在 dev 下不预缓存（injectRegister 已按模式区分）。
      devOptions: { enabled: true },
      includeAssets: ['icons/favicon-32.png'],
      manifest: {
        name: '高速出行助手',
        short_name: '出行助手',
        description: '实时路况查询与路况事件上报',
        lang: 'zh-CN',
        start_url: '/citizen',
        scope: '/',
        display: 'standalone',
        orientation: 'portrait',
        background_color: '#f2f5f9',
        theme_color: '#1d4ed8',
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          {
            src: '/icons/icon-maskable-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
        shortcuts: [
          { name: '查看路况', url: '/citizen' },
          { name: '上报路况', url: '/citizen/report' },
          { name: '查上报进度', url: '/citizen/track' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        // 单个预缓存文件上限。主 chunk 约 600KB，默认 2MB 够用
        maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
        navigateFallback: '/index.html',
        // 不要把 API 与上传文件交给 SPA fallback，否则接口 404 会返回 HTML，
        // 前端拿到一坨 HTML 去 JSON.parse，报出难以定位的错误
        navigateFallbackDenylist: [/^\/api\//, /^\/static\//],
        runtimeCaching: [
          {
            // 只有**公开接口**做运行时缓存。
            //
            // 警务端的接口一律不缓存，两个原因：
            //   1. 警情、警员、车主信息属敏感数据，没有理由留在设备上；
            //   2. 这些数据变化很快，一个过期的派警状态比"加载失败"更危险 ——
            //      值班员会以为警情还没派出去。
            //
            // NetworkFirst 而非 CacheFirst：路况要尽量新鲜，缓存只在断网时兜底。
            urlPattern: /\/api\/v1\/public\//,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'public-api',
              networkTimeoutSeconds: 5,
              expiration: {
                maxEntries: 40,
                // 路况数据 10 分钟后基本没有参考价值，超过就没必要留着
                maxAgeSeconds: 600,
              },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            // 地图瓦片由高德自己管理缓存，这里不插手 ——
            // 第三方资源跨域缓存容易因 opaque response 填满配额
            urlPattern: /^https:\/\/.*\.amap\.com\//,
            handler: 'NetworkOnly',
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    /*
      监听所有网卡，手机才能在同一局域网内真机预览。
      默认只绑 localhost（实测绑到 ::1 这个 IPv6 回环），手机连不上。

      注意 proxy 的 target 仍然写 127.0.0.1：那是 Vite 服务进程自己去连后端，
      与"谁可以连 Vite"无关，改监听地址不影响它。

      开发者机器上开着这个没问题；如果要长期对外开放，
      记得局域网里任何人都能访问 /api（含 AMAP Key 的运行时配置）。
    */
    host: true,
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
