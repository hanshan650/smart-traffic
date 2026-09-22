/**
 * 高德地图封装
 * ============
 * 两个职责：
 *
 *  1. **按需加载** —— JS API 体积较大，只在真正需要的页面加载，
 *     并用模块级 Promise 缓存，避免多页面重复注入脚本
 *  2. **生命周期托管** —— 统一创建 / 销毁地图实例，防止内存泄漏
 *
 * 坐标系为 **GCJ-02**（高德标准）。若数据来自 GPS 设备（WGS84），
 * 需先做坐标转换，否则会有数百米偏移。
 */
import AMapLoader from '@amap/amap-jsapi-loader'
import { ref, shallowRef } from 'vue'

import { useSystemStore } from '@/stores/system'

let loaderPromise: Promise<any> | null = null

/** 高德 JS API 会读取该全局变量做安全校验 */
declare global {
  interface Window {
    _AMapSecurityConfig?: { securityJsCode?: string }
  }
}

/**
 * 加载高德 JS API（进程内只加载一次）。
 *
 * ``securityCode`` 为 JS API 2.0 必需的**安全密钥**。它必须挂在
 * ``window._AMapSecurityConfig`` 上，且必须在加载脚本**之前**完成——
 * 否则加载会失败并报 ``INVALID_USER_SCODE``（即使 Key 本身完全正确）。
 * 该变量在控制台与 Key 配对生成。
 */
export function loadAmap(key: string, securityCode = ''): Promise<any> {
  if (!key) {
    return Promise.reject(new Error('未配置高德 JS API Key，请在 backend/.env 设置 AMAP_JS_KEY'))
  }

  if (securityCode) {
    window._AMapSecurityConfig = { securityJsCode: securityCode }
  }

  if (!loaderPromise) {
    loaderPromise = AMapLoader.load({
      key,
      version: '2.0',
      plugins: [
        'AMap.TileLayer.Traffic',   // 实时路况图层
        'AMap.AutoComplete',        // 输入提示
        'AMap.Driving',             // 驾车路线规划
      ],
    })
  }
  return loaderPromise
}

export interface CreateMapOptions {
  container: string | HTMLElement
  center?: [number, number]
  zoom?: number
  showTraffic?: boolean
}

export function useAmap() {
  const map = shallowRef<any>(null)
  const AMap = shallowRef<any>(null)
  const trafficLayer = shallowRef<any>(null)

  const loading = ref(false)
  const error = ref('')

  /**
   * 创建地图实例。
   * Key 缺省时从 systemStore 的运行时配置中读取。
   */
  async function create(options: CreateMapOptions): Promise<any | null> {
    const systemStore = useSystemStore()
    const key = systemStore.config?.amapJsKey ?? ''
    const securityCode = systemStore.config?.amapJsSecurityCode ?? ''

    loading.value = true
    error.value = ''

    try {
      const api = await loadAmap(key, securityCode)
      AMap.value = api

      const center = options.center ??
        (systemStore.config?.shanghaiCenter as [number, number] | undefined) ??
        [121.4737, 31.2304]

      const instance = new api.Map(options.container, {
        zoom: options.zoom ?? 12,
        center,
        viewMode: '2D',
        mapStyle: 'amap://styles/darkblue',
      })

      if (options.showTraffic !== false) {
        // 官方实时路况图层：暗红=极度拥堵，绿=畅通，灰=数据缺失
        trafficLayer.value = new api.TileLayer.Traffic({ zIndex: 10, autoRefresh: true, interval: 180 })
        instance.add(trafficLayer.value)
      }

      map.value = instance
      return instance
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      return null
    } finally {
      loading.value = false
    }
  }

  function toggleTraffic(visible: boolean): void {
    if (!trafficLayer.value) return
    visible ? trafficLayer.value.show() : trafficLayer.value.hide()
  }

  function destroy(): void {
    map.value?.destroy?.()
    map.value = null
    trafficLayer.value = null
  }

  return { map, AMap, trafficLayer, loading, error, create, toggleTraffic, destroy }
}
