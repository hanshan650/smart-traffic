/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

/**
 * 高德地图 JS API Loader
 * 官方包未提供类型声明，这里给出最小可用的声明。
 * 地图对象使用 `any`：AMap 的类体系庞大，逐一声明收益有限。
 */
declare module '@amap/amap-jsapi-loader' {
  const AMapLoader: {
    load(options: Record<string, unknown>): Promise<any>
  }
  export default AMapLoader
}

interface ImportMetaEnv {
  readonly VITE_API_BASE: string
  readonly VITE_WS_PATH: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
