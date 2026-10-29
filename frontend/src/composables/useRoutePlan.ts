/**
 * 路线规划
 * ========
 *
 * 封装高德的驾车 / 步行 / 骑行三套规划服务，只做三件事：
 *   1. 目的地联想（AutoComplete，不绑定 input，自己渲染下拉，样式才可控）
 *   2. 地址兜底解析（Geocoder，手动输入没命中联想时用）
 *   3. 规划并把结果**展平成一条折线**，交给地图自己画
 *
 * ---------------------------------------------------------------------------
 * 为什么不让高德插件自己画路线
 * ---------------------------------------------------------------------------
 * `new AMap.Driving({ map })` 会把起终点标记、转弯箭头、步骤面板一起画上去，
 * 这些 DOM 的层级、配色、尺寸都不受控 —— 在手机端会直接盖住本页的
 * 顶部路段卡和底部事件栏。所以插件只用来算，路线由 `RouteMap` 统一绘制，
 * 这样才能跟着 2D/3D 一起调线宽、抬图层。
 *
 * 另外，这里刻意**不缓存规划结果**：路况每 30 秒刷新，用户也可能在
 * 三种方式之间来回切，缓存只会让人看到过期的里程。
 */

import { ref } from 'vue'

import { travelModeMeta } from '@/utils/routePlan'
import type { RoutePlan, TravelMode } from '@/utils/routePlan'
import type { Ref } from 'vue'

export interface PlaceSuggestion {
  name: string
  address: string
  lng: number
  lat: number
}

export interface PlanSearchParams {
  mode: TravelMode
  origin: [number, number]
  destination: [number, number]
  destinationName: string
}

/**
 * 规划超时。
 *
 * 高德的回调在网络异常时可能**永远不来**，界面就会一直停在"规划中"。
 * 取 15 秒：驾车跨城路线正常在 1～3 秒内返回，超过 15 秒基本可以判定失败。
 */
const SEARCH_TIMEOUT_MS = 15_000

export function useRoutePlan(amap: Ref<any>) {
  const planning = ref(false)
  const error = ref('')
  const suggestions = ref<PlaceSuggestion[]>([])

  /** 插件实例按方式缓存：每次 new 都会重新初始化，白白多一次请求 */
  const engines = new Map<TravelMode, any>()
  let places: any = null
  let geocoder: any = null

  function engine(mode: TravelMode): any {
    const api = amap.value
    if (!api) return null

    const cached = engines.get(mode)
    if (cached) return cached

    let instance: any = null
    if (mode === 'driving') {
      instance = new api.Driving({ policy: api.DrivingPolicy?.LEAST_TIME })
    } else if (mode === 'walking') {
      instance = new api.Walking({})
    } else {
      instance = new api.Riding({})
    }

    if (instance) engines.set(mode, instance)
    return instance
  }

  /** 把 LngLat / [lng,lat] 两种形态统一成数组 */
  function pushPoint(target: [number, number][], point: any): void {
    if (!point) return
    const lng = Number(point.lng ?? point.getLng?.() ?? point[0])
    const lat = Number(point.lat ?? point.getLat?.() ?? point[1])
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) return

    const last = target[target.length - 1]
    // 相邻重复点在折线上会产生零长度线段，投影时 t 的取值会抖动
    if (last && last[0] === lng && last[1] === lat) return
    target.push([lng, lat])
  }

  /**
   * 展平路线。
   *
   * 三种方式的返回结构并不一致：驾车/步行在 `steps[]` 里，骑行在
   * `rides[]` 里，个别版本还直接给 `path`。这里按可能性依次取，
   * 而不是写死一种 —— 写死一种的后果是"骑行永远画不出线"，
   * 而接口本身是成功的，排查时没有任何报错可看。
   */
  function flattenPath(route: any): [number, number][] {
    const groups: any[] = []
    if (Array.isArray(route?.steps)) groups.push(...route.steps)
    if (Array.isArray(route?.rides)) groups.push(...route.rides)
    if (Array.isArray(route?.path)) groups.push(route)

    const path: [number, number][] = []
    for (const group of groups) {
      if (!Array.isArray(group?.path)) continue
      for (const point of group.path) pushPoint(path, point)
    }
    return path
  }

  function search(params: PlanSearchParams): Promise<RoutePlan | null> {
    const api = amap.value
    const instance = engine(params.mode)

    if (!api || !instance) {
      error.value = '地图尚未就绪，暂时无法规划路线'
      return Promise.resolve(null)
    }

    planning.value = true
    error.value = ''

    return new Promise((resolve) => {
      let settled = false
      let timer: number | undefined

      const finish = (plan: RoutePlan | null, message = ''): void => {
        if (settled) return
        settled = true
        if (timer !== undefined) window.clearTimeout(timer)
        planning.value = false
        error.value = message
        resolve(plan)
      }

      timer = window.setTimeout(() => finish(null, '路线规划超时，请稍后重试'), SEARCH_TIMEOUT_MS)

      const origin = new api.LngLat(params.origin[0], params.origin[1])
      const destination = new api.LngLat(params.destination[0], params.destination[1])

      instance.search(origin, destination, (status: string, result: any) => {
        if (status !== 'complete' || !result?.routes?.length) {
          finish(null, planErrorText(status, result, params.mode))
          return
        }

        const route = result.routes[0]
        const path = flattenPath(route)
        if (path.length < 2) {
          finish(null, '未取到路线几何，无法在地图上绘制')
          return
        }

        finish({
          mode: params.mode,
          origin: params.origin,
          destination: params.destination,
          destinationName: params.destinationName,
          path,
          distanceM: Number(route.distance) || 0,
          durationS: Number(route.time) || 0,
        })
      })
    })
  }

  /**
   * 目的地联想。
   *
   * 只接受两个字以上的输入：一个字会返回整座城市的 POI，
   * 既浪费配额，下拉列表也没有任何参考价值。
   */
  function autocomplete(keyword: string): void {
    const api = amap.value
    const text = keyword.trim()

    if (!api?.AutoComplete || text.length < 2) {
      suggestions.value = []
      return
    }

    if (!places) {
      // 不传 input：让插件不要劫持我们的输入框，只做检索
      places = new api.AutoComplete({ city: '全国', citylimit: false })
    }

    places.search(text, (status: string, result: any) => {
      if (status !== 'complete' || !Array.isArray(result?.tips)) {
        suggestions.value = []
        return
      }

      suggestions.value = result.tips
        .filter((tip: any) => tip?.location && tip?.name)
        .slice(0, 6)
        .map((tip: any) => ({
          name: String(tip.name),
          address: String(tip.address || tip.district || ''),
          lng: Number(tip.location.lng),
          lat: Number(tip.location.lat),
        }))
        .filter((tip: PlaceSuggestion) => Number.isFinite(tip.lng) && Number.isFinite(tip.lat))
    })
  }

  /** 手动输入的地址 → 坐标。联想没命中时的最后一条路 */
  function geocode(address: string): Promise<PlaceSuggestion | null> {
    const api = amap.value
    const text = address.trim()
    if (!api?.Geocoder || !text) return Promise.resolve(null)

    if (!geocoder) geocoder = new api.Geocoder({ city: '全国' })

    return new Promise((resolve) => {
      let settled = false
      const finish = (value: PlaceSuggestion | null): void => {
        if (settled) return
        settled = true
        window.clearTimeout(timer)
        resolve(value)
      }
      // 与规划同样的问题：回调可能永远不来，不能让"规划路线"按钮一直转
      const timer = window.setTimeout(() => finish(null), SEARCH_TIMEOUT_MS)

      geocoder.getLocation(text, (status: string, result: any) => {
        const geo = result?.geocodes?.[0]
        if (status !== 'complete' || !geo?.location) {
          finish(null)
          return
        }
        const lng = Number(geo.location.lng)
        const lat = Number(geo.location.lat)
        if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
          finish(null)
          return
        }
        finish({
          name: text,
          address: String(geo.formattedAddress ?? ''),
          lng,
          lat,
        })
      })
    })
  }

  function clearSuggestions(): void {
    suggestions.value = []
  }

  function clearError(): void {
    error.value = ''
  }

  return {
    planning,
    error,
    suggestions,
    search,
    autocomplete,
    geocode,
    clearSuggestions,
    clearError,
  }
}

/**
 * 失败原因翻译。
 *
 * 直接把 `status === 'no_data'` 抛给用户，等于什么都没说；
 * 而"没有可用路线"至少能让人知道不是网络坏了，是这段路本来就走不通
 * （最常见于步行/骑行规划高速路段）。
 */
function planErrorText(status: string, result: any, mode: TravelMode): string {
  const label = travelModeMeta(mode).label

  if (status === 'no_data') {
    return mode === 'driving'
      ? '两点之间没有可用的驾车路线'
      : `两点之间没有可用的${label}路线，可能被高速或禁行路段阻断`
  }
  if (status === 'error') {
    const info = result?.info ? String(result.info) : ''
    return info ? `${label}规划失败：${info}` : `${label}规划服务暂不可用`
  }
  return `${label}路线规划失败，请换个终点试试`
}
