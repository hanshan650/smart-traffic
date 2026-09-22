/**
 * 摄像头展示辅助
 * ==============
 * 上游返回的 ``cameraName`` 是完整站名，实测可达 60 多个字符，例如：
 *
 *   G1511(日兰高速公路王楼至兰考段)河南开封G1511开封市路达高速公路
 *   开发管理有限公司K456+700上行(日兰高速公路王楼至兰考段)
 *
 * 直接用作瓦片标签会换行并盖住大片画面，因此统一在此收敛为
 * 「区域 + 道路 + 桩号」——这组字段的辨识度最高且长度可控。
 * 完整站名仍可在需要处通过 ``title`` 展示。
 */
import type { CameraInfo } from '@/types/api'

/** 标签最大长度，超出部分由 CSS 做省略号处理 */
export const CAMERA_LABEL_LIMIT = 24

/**
 * 生成简短、稳定的摄像头标签。
 *
 * 优先级：区域 + 道路 + 桩号 → 完整站名 → 编号尾 6 位。
 * 最后一级兜底是为了应对上游字段缺失，保证标签永不为空。
 */
export function buildCameraLabel(camera: CameraInfo): string {
  const parts = [camera.regionName, camera.road, camera.pileNum].filter(Boolean)
  if (parts.length) return parts.join(' ')
  return camera.cameraName || camera.cameraNum.slice(-6)
}
