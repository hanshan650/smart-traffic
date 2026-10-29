/**
 * 路线规划与沿途判定
 * ==================
 *
 * `utils/route.ts` 解决的是"我在哪条路上"，没有路网几何，只能用直线距离。
 * 本文件解决的是另一半：**一旦规划出了路线，就有了真实的路网几何**，
 * 于是"前方 3.2 公里"这件事第一次可以算准 —— 把事件投影到路线折线上。
 *
 * ---------------------------------------------------------------------------
 * 投影用的是什么数学，误差在哪
 * ---------------------------------------------------------------------------
 *
 * 把每个线段附近的局部区域当成平面，用等距圆柱近似换算米：
 * 纬度 1 度 ≈ 111.32 km，经度 1 度 ≈ 111.32·cos(纬度) km。
 * 在单个线段的尺度上误差远小于监测点本身百米级的坐标精度，
 * 没必要引入完整的球面投影。
 *
 * 所以界面上的"约 X 公里"仍然保留"约"字，但它现在是**沿路里程**，
 * 不再是直线距离 —— 这一点必须在文案上体现，否则两种口径混在一起，
 * 用户无法判断哪个数字可信。
 *
 * ---------------------------------------------------------------------------
 * 为什么"沿途"的横向容差要按出行方式分开
 * ---------------------------------------------------------------------------
 *
 * 监测点装在机动车道上。驾车路线本身就贴着这些点走，容差可以给大一些；
 * 步行 / 骑行会被导到人行道与非机动车道，几何上离机动车道有几十米偏移，
 * 但**用户仍然需要知道旁边主路堵不堵**。容差给小了会漏事件，
 * 给大了会把平行的高架/辅路算进来 —— 两者都会让"沿途路况"失去意义。
 */

import { hasPoint } from '@/utils/route'
import type { PublicEvent } from '@/types/citizen'

// ==========================================================================
// 出行方式
// ==========================================================================

export type TravelMode = 'driving' | 'walking' | 'riding'

export interface TravelModeMeta {
  key: TravelMode
  label: string
  /**
   * 3D 视角的俯仰角。
   *
   * 驾车要贴近路面看远处车流（55°）；步行 / 骑行速度慢、关注周边路口，
   * 视角压低反而更好认方向（35° / 40°）。同一个俯仰角套三种方式，
   * 步行时会变成"趴在地上看"，四周全被建筑挡住。
   */
  pitch: number
  /** 判定"沿途"的横向容差（米） */
  corridorM: number
  /** 路线主色。与地图上其余图层的配色区分开 */
  color: string
  /** 该方式的能力边界，用于界面提示 */
  hint: string
}

export const TRAVEL_MODES: TravelModeMeta[] = [
  {
    key: 'driving',
    label: '驾车',
    pitch: 55,
    corridorM: 300,
    color: '#2563eb',
    hint: '按主干道规划，适合高速与城市快速路',
  },
  {
    key: 'riding',
    label: '骑行',
    pitch: 40,
    corridorM: 150,
    color: '#0d9488',
    hint: '避开高速，优先非机动车道；跨城距离可能无法规划',
  },
  {
    key: 'walking',
    label: '步行',
    pitch: 35,
    corridorM: 120,
    color: '#16a34a',
    hint: '走人行道与过街设施；高速路段无法规划',
  },
]

const MODE_INDEX = new Map<TravelMode, TravelModeMeta>(TRAVEL_MODES.map((m) => [m.key, m]))

export function travelModeMeta(mode: TravelMode): TravelModeMeta {
  return MODE_INDEX.get(mode) ?? TRAVEL_MODES[0]
}

// ==========================================================================
// 规划结果
// ==========================================================================

export interface RoutePlan {
  mode: TravelMode
  /** 起点 [lng, lat]，取自用户定位（GCJ-02） */
  origin: [number, number]
  /** 终点 [lng, lat] */
  destination: [number, number]
  /** 终点名称，用于界面展示；手输地址时为原文 */
  destinationName: string
  /** 路线折线，[lng, lat] 顺序即行进方向 */
  path: [number, number][]
  /** 规划里程（米），来自高德，是真实的沿路里程 */
  distanceM: number
  /** 预计耗时（秒），不含实时路况 */
  durationS: number
}

// ==========================================================================
// 点到折线的投影
// ==========================================================================

const M_PER_DEG_LAT = 111_320

/** 该纬度上 1 经度对应的米数。局部近似，线段尺度足够 */
function metersPerDegLng(lat: number): number {
  return M_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180)
}

export interface PathProjection {
  /** 投影点到路线起点的沿路里程（公里） */
  alongKm: number
  /** 点偏离路线的横向距离（米） */
  offsetM: number
}

/**
 * 把一个点投影到折线上，返回沿路里程与横向偏移。
 *
 * 取**横向偏移最小**的那一段，而不是"离起点最近"的那一段：
 * 环形路线或回头路会让同一个点被两段覆盖，最近的那段可能是对向车道，
 * 用它的里程会把"前方 2 公里"算成"已在身后 5 公里"。
 */
export function projectOntoPath(
  path: [number, number][],
  lng: number,
  lat: number,
): PathProjection {
  if (!path.length) return { alongKm: 0, offsetM: Number.NaN }

  const mLng = metersPerDegLng(lat)
  const xs = path.map((p) => (p[0] - lng) * mLng)
  const ys = path.map((p) => (p[1] - lat) * M_PER_DEG_LAT)

  let cumM = 0
  let best = { alongM: 0, offsetM: Math.hypot(xs[0], ys[0]) }

  for (let i = 1; i < path.length; i++) {
    const ax = xs[i - 1]
    const ay = ys[i - 1]
    const dx = xs[i] - ax
    const dy = ys[i] - ay
    const len2 = dx * dx + dy * dy
    const len = Math.sqrt(len2)

    // t 是投影参数，夹到 [0,1] 表示只落在线段内，
    // 否则端点外的点会"投射"到延长线上，算出一个不存在的里程
    const t = len2 === 0 ? 0 : Math.min(1, Math.max(0, -(ax * dx + ay * dy) / len2))
    const px = ax + t * dx
    const py = ay + t * dy
    const offsetM = Math.hypot(px, py)

    if (offsetM < best.offsetM) {
      best = { alongM: cumM + t * len, offsetM }
    }
    cumM += len
  }

  return { alongKm: best.alongM / 1000, offsetM: best.offsetM }
}

// ==========================================================================
// 沿途事件
// ==========================================================================

export interface RouteEvent {
  event: PublicEvent
  /** 距起点的沿路里程（公里），**不再是直线距离** */
  alongKm: number
  /** 偏离路线的横向距离（米），用于说明"在路边"还是"在路对面" */
  offsetM: number
}

/**
 * 找出路线走廊内的事件，按沿路里程排序。
 *
 * 无坐标的事件直接丢弃：它们本来就无法投影，留在列表里只会让
 * "沿途 3 个事件"和地图上的 2 个点对不上。
 */
export function eventsAlongRoute(
  events: PublicEvent[],
  path: [number, number][],
  corridorM: number,
): RouteEvent[] {
  if (path.length < 2) return []

  const along: RouteEvent[] = []
  for (const event of events) {
    if (!hasPoint(event)) continue
    const projection = projectOntoPath(path, event.longitude, event.latitude)
    if (!Number.isFinite(projection.offsetM)) continue
    if (projection.offsetM > corridorM) continue
    along.push({ event, alongKm: projection.alongKm, offsetM: projection.offsetM })
  }

  return along.sort((a, b) => a.alongKm - b.alongKm)
}

/**
 * 预计耗时文案。
 *
 * 分钟级取整就够了：导航软件的"18 分钟"本身就是统计值，
 * 精确到秒只会让人以为它很准。超过一小时才补小时位。
 */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '时长未知'
  // 先按**原始秒数**判断不足一分钟：先取整再判断的话，
  // 30 秒会被 Math.round(0.5) 进位成 1 分钟，写成"约 1 分钟"就不对了
  if (seconds < 60) return '1 分钟内'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `约 ${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `约 ${hours} 小时 ${rest} 分钟` : `约 ${hours} 小时`
}
