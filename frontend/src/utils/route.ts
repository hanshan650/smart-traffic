/**
 * 行进路段判定
 * ============
 *
 * 把"一堆路况事件"整理成"**你在哪条路上、前方会碰到什么**"。
 *
 * ---------------------------------------------------------------------------
 * 这套判定是近似的，必须说清楚近似在哪里
 * ---------------------------------------------------------------------------
 *
 * 真实导航软件能算"沿路 3.2 公里"，是因为它手里有路网几何 ——
 * 把位置和事件都投影到同一条道路的里程上再相减。
 *
 * **本项目没有路网数据**（事件来自高速公路监测点，只带经纬度）。
 * 所以这里只能：
 *   1. 用**直线距离**代替沿路里程
 *   2. 用**同路名**代替"同一条道路实体"
 *   3. 用**方位角夹角**代替"沿行进方向的前方"
 *
 * 这在高速场景下不算离谱（路段顺直、监测点稀疏、用户基本在一条路上），
 * 但界面上必须写"约"，不能给出"前方 3.2 公里"这种精确到百米、
 * 让人误以为是导航里程的数字。
 *
 * 离线验证见 `scripts/verify_route.mjs`（`npm run verify:route`）。
 *
 * ---------------------------------------------------------------------------
 * 匹配不到时不要猜
 * ---------------------------------------------------------------------------
 *
 * 演示数据只覆盖河南几条高速。用户位置多半离它们很远
 * （在办公室打开、或在别的省份）。这时最近点位可能有上百公里 ——
 * 硬说"你正在京港澳高速上"是错的，而且一旦用户发现位置离谱，
 * 就不会再信任这一页的任何信息。
 *
 * 所以超过阈值就明确返回"未定位到已覆盖路段"，引导用户手动选一条路。
 */

import { bearingDeg, haversineKm, isAhead } from '@/utils/geo'
import type { PublicEvent } from '@/types/citizen'

/**
 * 认为"用户在该路段覆盖范围内"的最大距离。
 *
 * 取 30 公里：高速监测点间距通常 10～20 公里，一个点的覆盖范围
 * 前后各十余公里；30 公里能容忍相邻点位之间的空档，
 * 又不至于把隔壁省的路算成"当前路段"。
 */
const ROAD_MATCH_MAX_KM = 30

/**
 * 仍然算"前方会遇到"的最大距离。
 *
 * 同一条高速可以绵延数百公里：京港澳高速的许昌段与信阳段相距 211 公里，
 * 但路名相同。若不设上限，用户会被提示"前方 2 个事件"，
 * 而其中一个在 211 公里外 —— 这在出行决策上是误导，不是信息。
 *
 * 取 50 公里：高速上跑这段约需半小时，属于"现在的驾驶计划需要考虑"的范围；
 * 再远就该由路线规划而不是这一页来回答。
 */
export const AHEAD_MAX_KM = 50

export interface EventFix {
  event: PublicEvent
  /** 与用户的直线距离（公里） */
  distanceKm: number
  /** 从用户指向事件的方位角。无坐标时为 null */
  bearing: number | null
  /**
   * 是否在行进方向前方。
   * `null` 表示**无法判断** —— 没有 heading（静止或设备不支持），
   * 或事件无坐标。不要把它当 false 用，那是"已在身后"的意思。
   */
  ahead: boolean | null
}

export interface RouteContext {
  /**
   * 是否拿到了用户位置。
   *
   * `false` 时 `offRoute` 里的事件**没有距离**（`distanceKm` 为 `NaN`），
   * 顺序保持接口返回的原始顺序。界面必须先看这个标志再决定要不要渲染距离 ——
   * 无定位是常态，这条路径必须能正常出内容，不能因为算不出距离就空着。
   */
  hasFix: boolean
  /** 判定出的当前路段名；`''` 表示没匹配到 */
  currentRoad: string
  /** 判定来源：自动匹配 / 用户手动指定 / 未匹配 */
  source: 'nearby' | 'manual' | 'none'
  /** 当前路段上离用户最近的监测点距离；未匹配或未定位为 null */
  roadDistanceKm: number | null
  /** 本路段事件全量，前方优先、其余按距离升序 */
  onRoute: EventFix[]
  /**
   * 本路段**前方且不远**的事件（`AHEAD_MAX_KM` 以内），按距离升序。
   * 这是"前方路况"列表与地图编号标记的数据源。
   */
  ahead: EventFix[]
  /** 本路段、已在身后的事件。不删除 —— 用户掉头回来时还需要 */
  passed: EventFix[]
  /** 本路段但超出 `AHEAD_MAX_KM` 的事件，单独分区避免与"前方"混为一谈 */
  far: EventFix[]
  /** 其他路段事件。有定位时按距离升序，无定位时保持原始顺序 */
  offRoute: EventFix[]
  /** 无坐标、无法参与距离计算的事件 */
  unlocated: PublicEvent[]
}

/**
 * 事件是否带可用坐标。
 *
 * 写成类型谓词而非普通布尔函数：调用方 `find(hasPoint)` / `filter(hasPoint)`
 * 之后元素会被 TS 收窄为"坐标是 number"，不必再逐处非空断言。
 */
export function hasPoint(
  event: PublicEvent,
): event is PublicEvent & { latitude: number; longitude: number } {
  return (
    typeof event.latitude === 'number' &&
    typeof event.longitude === 'number' &&
    Number.isFinite(event.latitude) &&
    Number.isFinite(event.longitude) &&
    // (0,0) 在几内亚湾，是缺省值而非真实位置
    !(event.latitude === 0 && event.longitude === 0)
  )
}

/** 按"前方优先 + 距离升序"排列。后方的不是删除，只是排到后面 —— 用户掉头回去时还需要它 */
function sortOnRoute(items: EventFix[]): EventFix[] {
  return [...items].sort((a, b) => {
    const rank = (x: EventFix): number => (x.ahead === false ? 1 : 0)
    return rank(a) - rank(b) || a.distanceKm - b.distanceKm
  })
}

export interface RouteContextOptions {
  events: PublicEvent[]
  /** 用户位置（GCJ-02）。为 null 时只能退回"按时间排列" */
  fix: { lat: number; lng: number; heading: number | null } | null
  /** 用户手动选定的路段，优先级最高 */
  manualRoad?: string
}

export function buildRouteContext(options: RouteContextOptions): RouteContext {
  const { events, fix, manualRoad } = options

  const unlocated = events.filter((event) => !hasPoint(event))

  // ---- 没有定位：不编造距离，但要给得出内容 ----
  // 这条分支很常见（用户不授权、非 HTTPS、室内无信号），
  // 绝不能因为算不出距离就让整页空白。
  if (!fix) {
    return {
      hasFix: false,
      currentRoad: manualRoad ?? '',
      source: manualRoad ? 'manual' : 'none',
      roadDistanceKm: null,
      onRoute: [],
      ahead: [],
      passed: [],
      far: [],
      offRoute: events
        .filter(hasPoint)
        .map((event) => ({ event, distanceKm: Number.NaN, bearing: null, ahead: null })),
      unlocated,
    }
  }

  const located: EventFix[] = []
  for (const event of events) {
    if (!hasPoint(event)) continue

    const lat = event.latitude as number
    const lng = event.longitude as number
    const distanceKm = haversineKm(fix.lat, fix.lng, lat, lng)
    const bearing = bearingDeg(fix.lat, fix.lng, lat, lng)

    located.push({
      event,
      distanceKm,
      bearing,
      ahead: fix.heading === null ? null : isAhead(bearing, fix.heading),
    })
  }

  // ---- 判定当前路段 ----
  // 按路名聚合，取每组里离用户最近的那个点作为该路段的距离。
  // 用最近点而非平均点：路段是线状的，平均点会落在几何中心 ——
  // 对身处路段一端的用户来说，那个距离毫无意义。
  let currentRoad = ''
  let roadDistanceKm: number | null = null
  let source: RouteContext['source'] = 'none'

  const nearestByRoad = new Map<string, number>()
  for (const item of located) {
    const road = item.event.roadName
    if (!road) continue
    const prev = nearestByRoad.get(road)
    if (prev === undefined || item.distanceKm < prev) {
      nearestByRoad.set(road, item.distanceKm)
    }
  }

  const sorted = [...nearestByRoad.entries()].sort((a, b) => a[1] - b[1])
  if (sorted.length && sorted[0][1] <= ROAD_MATCH_MAX_KM) {
    currentRoad = sorted[0][0]
    roadDistanceKm = sorted[0][1]
    source = 'nearby'
  }

  // 手动选择覆盖自动结果，但仍要在列表里标出这条路的距离
  if (manualRoad) {
    currentRoad = manualRoad
    source = 'manual'
    roadDistanceKm = nearestByRoad.get(manualRoad) ?? null
  }

  const onRoute = sortOnRoute(
    located.filter((item) => item.event.roadName === currentRoad && currentRoad !== ''),
  )
  const offRoute = [...located]
    .filter((item) => item.event.roadName !== currentRoad || currentRoad === '')
    .sort((a, b) => a.distanceKm - b.distanceKm)

  // 三段互斥：50 公里内且（方向未知或朝前）→ 前方；50 公里内但已在身后 → 身后；
  // 其余（不论方向）→ 远处。方向未知刻意归入"前方"：用户没动起来时
  // 把附近事件藏进"远处"更糟 —— 那会让人以为前方没事。
  const ahead: EventFix[] = []
  const passed: EventFix[] = []
  const far: EventFix[] = []
  for (const item of onRoute) {
    if (item.distanceKm >= AHEAD_MAX_KM) {
      far.push(item)
    } else if (item.ahead === false) {
      passed.push(item)
    } else {
      ahead.push(item)
    }
  }

  return {
    hasFix: true,
    currentRoad,
    source,
    roadDistanceKm,
    onRoute,
    ahead,
    passed,
    far,
    offRoute,
    unlocated,
  }
}

/**
 * 所有出现过的事件道路名，供手动选择。
 *
 * 按"有事件的条数"降序而不是字母序：用户想找的那条路，
 * 更可能是事件多的那条（也更容易从列表里认出来）。
 */
export function roadOptions(events: PublicEvent[]): string[] {
  const count = new Map<string, number>()
  for (const event of events) {
    if (!event.roadName) continue
    count.set(event.roadName, (count.get(event.roadName) ?? 0) + 1)
  }
  return [...count.entries()].sort((a, b) => b[1] - a[1]).map(([road]) => road)
}
