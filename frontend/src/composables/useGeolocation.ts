/**
 * 浏览器定位
 * ==========
 *
 * 为"行进视角"提供当前位置。用 `watchPosition` 而不是 `getCurrentPosition`：
 * 用户在开车，位置必须持续更新；只定位一次的话，开了十公里地图还停在原地。
 *
 * ---------------------------------------------------------------------------
 * 为什么这件事必须优雅降级
 * ---------------------------------------------------------------------------
 *
 * 定位失败是**常态而非异常**：
 *   · 用户不点"允许"（浏览器提示常被无视）
 *   · 非 HTTPS 且非 localhost 时，浏览器**直接禁用** Geolocation API ——
 *     局域网 IP 访问开发服务器就会遇到，此时 `navigator.geolocation`
 *     是 undefined 而不是报错
 *   · 室内 / 隧道内长时间拿不到信号
 *
 * 所以这个 composable 只负责"如实报告状态"，由界面决定怎么呈现。
 * 绝不能因为没有位置就让整个路况页不可用 —— 没有定位照样能看路况。
 */
import { onBeforeUnmount, ref, shallowRef } from 'vue'

import { wgs84ToGcj02 } from '@/utils/geo'

export type GeoState =
  | 'unsupported'  // 浏览器不支持（或非安全上下文直接禁用了）
  | 'idle'         // 尚未开始
  | 'locating'     // 已发起，等首个结果
  | 'ok'
  | 'denied'       // 用户拒绝授权
  | 'unavailable'  // 信号不可用（室内、隧道、设备无 GPS）
  | 'timeout'

export interface GeoFix {
  /** GCJ-02 纬度（**已转换**，可直接给高德用） */
  lat: number
  /** GCJ-02 经度 */
  lng: number
  /** 定位精度半径（米）。数值越大越不可信 */
  accuracy: number
  /** 行进方向（正北为 0，顺时针）。设备静止或不支持时为 null */
  heading: number | null
  /** 速度（米/秒），null 表示不可知 */
  speed: number | null
  /** 拿到该定位的时间戳（毫秒） */
  at: number
}

/** 精度超过此值就认为"知道大概在哪儿，但不足以判断在哪条路" */
export const ACCURACY_TRUSTWORTHY_M = 500

export function useGeolocation() {
  const state = ref<GeoState>('idle')
  const fix = shallowRef<GeoFix | null>(null)
  const message = ref('')

  let watchId: number | null = null

  function stop(): void {
    if (watchId !== null && typeof navigator !== 'undefined') {
      navigator.geolocation.clearWatch(watchId)
      watchId = null
    }
  }

  /**
   * 把 `GeolocationPositionError.code` 翻成我们的状态。
   *
   * 分开 denied / unavailable / timeout 三种而不是笼统报错：
   * 用户看到"未授权定位"会去改设置，看到"信号弱"只会等一会儿，
   * 这两种提示引导的动作完全不同。
   */
  function mapError(err: GeolocationPositionError): void {
    switch (err.code) {
      case err.PERMISSION_DENIED:
        state.value = 'denied'
        message.value = '未获得定位授权'
        break
      case err.POSITION_UNAVAILABLE:
        state.value = 'unavailable'
        message.value = '暂时无法获取位置信号'
        break
      case err.TIMEOUT:
        state.value = 'timeout'
        message.value = '定位超时，仍在尝试'
        break
      default:
        state.value = 'unavailable'
        message.value = err.message || '定位失败'
    }
  }

  function start(): void {
    // 非安全上下文下 geolocation 可能整个不存在，先确认再调用
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      state.value = 'unsupported'
      message.value = '当前环境不支持定位（需 HTTPS 或 localhost）'
      return
    }

    // 重复调用 start 时先清掉旧的监听，否则会叠加多个 watch
    stop()
    state.value = fix.value ? 'ok' : 'locating'
    message.value = ''

    watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const { latitude, longitude, accuracy, heading, speed } = pos.coords

        // **必须转换坐标系**：GPS 给的是 WGS84，高德要 GCJ-02，
        // 直接混用会有数十到数百米偏移（详见 utils/geo.ts 的说明）
        const [lat, lng] = wgs84ToGcj02(latitude, longitude)

        /*
          静止时不要把 heading 当真。
          很多手机静止时会报告 heading = 0（磁力计朝向正北），
          而不是 null —— 于是"落在正南方的事件"会被判成"已在身后"，
          静静停在路边也会看到前方事件凭空少一半。

          用 speed 兜底：几乎不动时方向没有意义。
          阈值取 0.5 m/s（约 1.8 km/h）—— 比步行还慢，只用来排除静止。
        */
        const still = typeof speed === 'number' && Number.isFinite(speed) && speed < 0.5
        const headingAvailable =
          !still && typeof heading === 'number' && Number.isFinite(heading)

        fix.value = {
          lat,
          lng,
          accuracy: accuracy ?? 0,
          // 方向未知时明确给 null，让调用方跳过"前方/后方"判断，
          // 而不是收到 0 之后误以为用户正朝正北走
          heading: headingAvailable ? (heading as number) : null,
          speed: typeof speed === 'number' && Number.isFinite(speed) ? speed : null,
          at: pos.timestamp ?? Date.now(),
        }
        state.value = 'ok'
        message.value = ''
      },
      mapError,
      {
        enableHighAccuracy: true,
        // 5 秒内的缓存位置可以接受：行车中每秒重定位既费电，
        // 也只会得到几乎相同的点
        maximumAge: 5000,
        // 不给超时会让状态永远停在 locating —— 首屏看不出是"还没定位到"
        // 还是"挂了"，无法给用户任何反馈
        timeout: 15000,
      },
    )
  }

  onBeforeUnmount(stop)

  return { state, fix, message, start, stop }
}
