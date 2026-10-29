/**
 * 定位权限与单次定位
 * ==================
 *
 * 这里刻意把“权限查询 / 触发授权 / 取一次坐标”收敛成独立接口：
 * 后续若接入警用终端、蓝牙定位或平台授权，只需替换本文件，
 * 业务页面不用改。
 *
 * 浏览器权限是客户端能力，后端不保存“是否授权”。后端只接收
 * 已授权后上报的坐标，并记录服务端时间与来源。
 */
import { wgs84ToGcj02 } from '@/utils/geo'

export type LocationPermissionState = 'granted' | 'denied' | 'prompt' | 'unsupported'

/** 单次定位的最终结果，比权限状态多区分“无信号 / 超时” */
export type LocationResultState =
  | 'granted'
  | 'denied'
  | 'unavailable'
  | 'timeout'
  | 'unsupported'

export interface CurrentLocation {
  ok: boolean
  latitude: number
  longitude: number
  accuracy: number
  at: number
  state: LocationResultState
  message: string
}

/** 查询当前定位权限，不弹浏览器授权框 */
export async function getLocationPermission(): Promise<LocationPermissionState> {
  if (typeof navigator === 'undefined' || !navigator.geolocation) {
    return 'unsupported'
  }
  if (!navigator.permissions?.query) {
    // Safari 等旧环境没有 Permissions API，只能等到真正定位时才知道
    return 'prompt'
  }
  try {
    const status = await navigator.permissions.query({ name: 'geolocation' })
    return status.state
  } catch {
    return 'prompt'
  }
}

/**
 * 定位权限接口占位。
 *
 * 当前直接复用浏览器 Permissions API；未来设备授权接入时替换实现即可。
 */
export function requestLocationPermission(): Promise<LocationPermissionState> {
  return getLocationPermission()
}

/**
 * 请求一次当前位置。权限提示由浏览器在首次调用时弹出。
 * 返回坐标已完成 WGS84 → GCJ-02，可直接写库并给高德地图使用。
 */
export function getCurrentLocation(): Promise<CurrentLocation> {
  if (typeof navigator === 'undefined' || !navigator.geolocation) {
    return Promise.resolve({
      ok: false,
      latitude: 0,
      longitude: 0,
      accuracy: 0,
      at: Date.now(),
      state: 'unsupported',
      message: '当前环境不支持定位（需 HTTPS 或 localhost）',
    })
  }

  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const [latitude, longitude] = wgs84ToGcj02(
          position.coords.latitude,
          position.coords.longitude,
        )
        resolve({
          ok: true,
          latitude,
          longitude,
          accuracy: position.coords.accuracy ?? 0,
          at: position.timestamp ?? Date.now(),
          state: 'granted',
          message: '',
        })
      },
      (error) => {
        const state: LocationResultState =
          error.code === error.PERMISSION_DENIED
            ? 'denied'
            : error.code === error.TIMEOUT
              ? 'timeout'
              : 'unavailable'
        resolve({
          ok: false,
          latitude: 0,
          longitude: 0,
          accuracy: 0,
          at: Date.now(),
          state,
          message:
            state === 'denied'
              ? '未获得定位授权，请在浏览器设置中允许本站使用位置'
              : state === 'timeout'
                ? '定位超时，请到信号更好的位置重试'
                : '暂时无法获取位置，请稍后重试',
        })
      },
      {
        enableHighAccuracy: true,
        maximumAge: 5000,
        timeout: 15000,
      },
    )
  })
}
