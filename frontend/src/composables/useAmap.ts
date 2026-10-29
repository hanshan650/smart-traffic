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
        'AMap.AutoComplete',        // 目的地输入提示
        'AMap.Geocoder',            // 手动输入的地址 → 坐标（联想列表没有命中时兜底）
        'AMap.Driving',             // 驾车路线规划
        'AMap.Walking',             // 步行路线规划
        'AMap.Riding',              // 骑行路线规划
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
  /**
   * 地图样式。默认用于警务端的暗色，民众端应传入亮色。
   *
   * 为什么不做成"自动跟随主题"：高德样式是服务器端的预设名，
   * 与 CSS 变量体系无关，只能由调用方明确指定。把它做成参数
   * 比在 composable 里维护一份"某个页面该用什么色"的映射更直接。
   */
  mapStyle?: string
  /**
   * 视图模式。默认 2D。
   *
   * 3D 走 WebGL 渲染，低端机、iOS 低电量模式、部分内嵌浏览器会被禁掉，
   * 所以**不能默认开 3D** —— 一旦渲染不出来，用户看到的是一张空白容器，
   * 比"没有 3D"糟糕得多。是否提供 3D 由调用方用 `webglAvailable()` 判断。
   */
  viewMode?: '2D' | '3D'
  /**
   * 3D 俯仰角（0～83 度）。2D 下强制为 0。
   *
   * 视角不是装饰：俯仰越大越接近"坐在车里往前看"，
   * 但远端会被压缩、遮挡更多，所以不同出行方式应该给不同的值
   * （见 `utils/routePlan.ts` 的 `TRAVEL_MODES`）。
   */
  pitch?: number
}

/** `switchViewMode` 的附加动作：重画覆盖物、重挂事件、目标俯仰角 */
export interface SwitchViewOptions {
  /** 3D 目标俯仰角（0～83）。默认 50 */
  pitch?: number
  /** 重建时的视野中心。缺省沿用切换前的中心 */
  center?: [number, number]
  /** 重建时的缩放级别。缺省沿用切换前的缩放 */
  zoom?: number
  /** 重建完成后重画覆盖物：旧实例的覆盖物已随 destroy 一起消失 */
  draw?: () => void
  /** 重建完成后执行（如重挂 dragstart 等地图级监听） */
  after?: () => void
}

/** 暗色地图样式（警务端监控场景：降低暗环境眩光） */
export const AMAP_STYLE_DARK = 'amap://styles/darkblue'

/** 亮色地图样式（民众端：白天户外可读） */
export const AMAP_STYLE_LIGHT = 'amap://styles/normal'

/**
 * 主题色调 → 高德预设样式。
 *
 * 高德样式名是服务端预设，读不到 CSS 变量，所以必须由主题状态显式映射；
 * 页面在主题切换时调用 ``setMapStyle`` 重新应用，底图才会跟着变。
 */
export function mapStyleFor(tone: 'light' | 'dark'): string {
  return tone === 'dark' ? AMAP_STYLE_DARK : AMAP_STYLE_LIGHT
}

/**
 * 当前环境能否跑 3D 视图（本质是能不能拿到 WebGL 上下文）。
 *
 * 判断放在打开 3D 之前，而不是打开失败后再兜底：高德在 WebGL 不可用时
 * 不会抛错，而是**静默按 2D 渲染** —— 开关显示"已开 3D"、画面却毫无变化，
 * 用户只会以为这个功能是坏的。
 */
export function webglAvailable(): boolean {
  if (typeof document === 'undefined') return false
  try {
    const canvas = document.createElement('canvas')
    return Boolean(
      canvas.getContext('webgl') ||
        canvas.getContext('experimental-webgl') ||
        canvas.getContext('webgl2'),
    )
  } catch {
    return false
  }
}

export function useAmap() {
  const map = shallowRef<any>(null)
  const AMap = shallowRef<any>(null)
  const trafficLayer = shallowRef<any>(null)

  const loading = ref(false)
  const error = ref('')

  /** 当前视图模式。构造后由 `switchViewMode` 维护 */
  const viewMode = ref<'2D' | '3D'>('2D')

  /**
   * 是否正在 2D ↔ 3D 的重建过程中。
   *
   * 重建必须先 destroy 再 new，中间会有一小段容器是空的；调用方用这个
   * 标志盖一层纯色遮罩 + 淡入淡出，把"地图被拔掉"的那一帧藏起来。
   */
  const viewSwitching = ref(false)

  /** 记住创建参数：切视图要按同一套配置重建，否则样式/路况图层会丢 */
  let lastOptions: CreateMapOptions | null = null
  let switching = false
  let pendingMode: '2D' | '3D' | null = null
  let pendingOptions: SwitchViewOptions | null = null

  const wait = (ms: number): Promise<void> =>
    new Promise((resolve) => {
      setTimeout(resolve, ms)
    })

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

      const is3D = options.viewMode === '3D'

      lastOptions = { ...options }
      viewMode.value = is3D ? '3D' : '2D'

      const instance = new api.Map(options.container, {
        zoom: options.zoom ?? 12,
        center,
        viewMode: is3D ? '3D' : '2D',
        // 3D 下必须给定俯仰角：pitch 为 0 时 3D 渲染和 2D 看起来一模一样，
        // 只会白白多吃一份 WebGL 开销
        pitch: is3D ? (options.pitch ?? 50) : 0,
        rotation: 0,
        // 3D 楼块负责建立"高度感"。警务端俯瞰不需要，由调用方决定是否开 3D
        showBuildingBlock: is3D,
        // 允许手势俯仰 / 旋转：用户把视角弄歪之后得能自己掰回来
        rotateEnable: is3D,
        pitchEnable: is3D,
        mapStyle: options.mapStyle ?? AMAP_STYLE_DARK,
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

  /**
   * 运行时切换底图样式（主题切换时调用）。
   *
   * 用可选链：地图实例可能已随组件卸载销毁，而主题切换的回调仍可能触发一次。
   */
  function setMapStyle(style: string): void {
    map.value?.setMapStyle?.(style)
  }

  /**
   * 切换 2D / 3D。
   *
   * ---------------------------------------------------------------------------
   * 为什么是"重建"而不是"改属性"
   * ---------------------------------------------------------------------------
   * JSAPI 2.0 的 `viewMode` 只存在于 `MapOptions`（构造参数）中：
   * `setStatus` 管的是拖拽/缩放这些交互开关，`getViewMode` / `setViewMode`
   * 都不存在。所以运行时只能 `destroy()` 之后用新的 viewMode 重新 new。
   *
   * ---------------------------------------------------------------------------
   * 动画怎么来
   * ---------------------------------------------------------------------------
   * 重建本身是**硬切**，靠两层把它抹平：
   *
   *  · 2D → 3D：先用 0～90ms 把遮罩淡入 → 重建（俯仰角 0）→ 遮罩淡出，
   *    同时 `setPitch(target, false, 650)` 把镜头从平视抬到目标角度。
   *  · 3D → 2D：先 `setPitch(0, false, 420)` 把镜头放平（这段是可见的
   *    连续动画），落平后再重建 2D，遮罩只负责盖住重建的那一瞬间。
   *
   * `draw` 在重建后调用，用于重画覆盖物（旧实例的覆盖物已随销毁消失）；
   * `after` 留给调用方重挂事件监听。返回 false 表示 3D 建不出来，
   * 此时已自动退回 2D，调用方应把开关弹回去。
   */
  async function switchViewMode(
    mode: '2D' | '3D',
    options: SwitchViewOptions = {},
  ): Promise<boolean> {
    if (switching) {
      // 重建期间又拨了一次开关：记下来，等这次结束后按最新状态再来一遍
      pendingMode = mode
      pendingOptions = options
      return true
    }
    if (mode === viewMode.value) {
      options.draw?.()
      options.after?.()
      return true
    }

    const instance = map.value
    const base = lastOptions
    if (!instance || !base) return false

    switching = true
    const center = instance.getCenter?.()
    const targetCenter: [number, number] | undefined =
      options.center ??
      (center && Number.isFinite(center.lng) && Number.isFinite(center.lat)
        ? [center.lng, center.lat]
        : base.center)
    const savedZoom = Number(instance.getZoom?.())
    const zoom = options.zoom ?? (Number.isFinite(savedZoom) ? savedZoom : base.zoom)
    const pitch = options.pitch ?? 50

    try {
      if (mode === '2D') {
        // 把"放平"演出来，而不是直接换成另一张图
        instance.setPitch?.(0, false, 420)
        await wait(440)
      }

      viewSwitching.value = true
      // 等遮罩淡入（CSS 0.16s）再拆地图，否则会先看到一帧空白
      await wait(150)
      destroy()

      let created = await create({
        ...base,
        center: targetCenter,
        zoom,
        viewMode: mode,
        // 3D 先按 0 建，建好后再把俯仰角抬起来 —— 这样才有"立起来"的动画
        pitch: 0,
      })

      if (!created && mode === '3D') {
        created = await create({
          ...base,
          center: targetCenter,
          zoom,
          viewMode: '2D',
          pitch: 0,
        })
        options.draw?.()
        options.after?.()
        return false
      }
      if (!created) return false

      options.draw?.()
      options.after?.()
      return true
    } finally {
      viewSwitching.value = false
      switching = false

      // 3D 的抬头动画放在遮罩淡出之后，用户才能看见
      if (mode === '3D' && viewMode.value === '3D' && map.value) {
        map.value.setPitch?.(pitch, false, 650)
      }

      const next = pendingMode
      const nextOptions = pendingOptions
      pendingMode = null
      pendingOptions = null
      if (next && next !== viewMode.value) {
        void switchViewMode(next, nextOptions ?? options)
      }
    }
  }

  /**
   * 调整俯仰角。3D 下用它按出行方式换视角。
   *
   * `durationMs > 0` 时走高德的过渡动画（`setPitch(deg, false, duration)`）：
   * 从 2D 切到 3D 后紧跟着设角度，若是一次到位，看起来就像"啪"地弹起来。
   */
  function setPitch(deg: number, durationMs = 0): void {
    const instance = map.value
    if (!instance) return
    if (typeof instance.setPitch === 'function') {
      if (durationMs > 0) instance.setPitch(deg, false, durationMs)
      else instance.setPitch(deg)
    } else {
      instance.setStatus?.({ pitch: deg })
    }
  }

  /**
   * 旋转地图（0 = 正北）。
   *
   * 只在有 heading 时调用：静止时很多设备会报 0，直接把地图拧成正北
   * 会让用户以为地图在"自己乱转"。
   */
  function setRotation(deg: number): void {
    const instance = map.value
    if (!instance) return
    if (typeof instance.setRotation === 'function') {
      instance.setRotation(deg)
    } else {
      instance.setStatus?.({ rotation: deg })
    }
  }

  function destroy(): void {
    map.value?.destroy?.()
    map.value = null
    trafficLayer.value = null
  }

  return {
    map,
    AMap,
    trafficLayer,
    loading,
    error,
    viewMode,
    viewSwitching,
    create,
    toggleTraffic,
    setMapStyle,
    switchViewMode,
    setPitch,
    setRotation,
    destroy,
  }
}
