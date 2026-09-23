/**
 * 地理计算工具
 * ============
 *
 * 两件事：**球面距离 / 方位角**，以及 **WGS84 → GCJ-02 坐标转换**。
 *
 * 为什么不直接用高德的 `AMap.GeometryUtil.distance`：
 * 它需要额外声明插件，而且这里只是纯数学，自己算没有依赖、
 * 可以脱离浏览器单独验证（见 `scripts/verify_geo.py` 的对照数据）。
 *
 * ---------------------------------------------------------------------------
 * 坐标系这件事必须讲清楚，否则"当前位置"会飘到路外
 * ---------------------------------------------------------------------------
 *
 * | 来源                            | 坐标系   |
 * |---------------------------------|----------|
 * | 浏览器 `navigator.geolocation`  | **WGS84**（GPS 原始值） |
 * | 高德地图底图与覆盖物            | **GCJ-02**（国测局加密） |
 * | 本项目事件/快照的坐标           | **GCJ-02**（由前端在地图上取点） |
 *
 * 两者在中国境内相差 **50～500 米**，在城市里足以把"我在的主路上"
 * 显示到隔壁街区。所以定位结果必须先 `wgs84ToGcj02`，
 * **绝不能**直接塞给地图。
 */

const EARTH_RADIUS_KM = 6371.0088

const toRad = (deg: number): number => (deg * Math.PI) / 180
const toDeg = (rad: number): number => (rad * 180) / Math.PI

/**
 * 两点间大圆距离（公里）。
 *
 * 注意这是**直线距离**，不是沿道路里程。高速上弯道不剧烈时两者接近，
 * 但跨路网时完全不是一回事 —— 所以界面上凡是显示距离的地方都写"约"，
 * 不能让用户以为是导航软件里那种精确里程。
 */
export function haversineKm(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const dLat = toRad(lat2 - lat1)
  const dLon = toRad(lon2 - lon1)
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(a)))
}

/** 从点 1 指向点 2 的方位角，正北为 0，顺时针增大（0～360） */
export function bearingDeg(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const φ1 = toRad(lat1)
  const φ2 = toRad(lat2)
  const Δλ = toRad(lon2 - lon1)
  const y = Math.sin(Δλ) * Math.cos(φ2)
  const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ)
  return (toDeg(Math.atan2(y, x)) + 360) % 360
}

/** 两个方位角的夹角（0～180），用于判断目标是否在行进方向上 */
export function angleDiff(a: number, b: number): number {
  const d = Math.abs(a - b) % 360
  return d > 180 ? 360 - d : d
}

/**
 * 目标是否在行进方向的前方。
 *
 * `tolerance` 取 75° 而非 90°：高速公路匝道与长弯道会让方位角明显偏离，
 * 但正侧方（接近 90°）的目标算"前方"没有意义 —— 那是另一条路的方向。
 *
 * **这是个近似**。没有路网几何就无法把事件投影到道路里程上，
 * 只能拿方位角凑合。没有 heading（静止或设备不支持）时，
 * 调用方应直接跳过这个判断，不要传默认值假装知道方向。
 */
export function isAhead(
  targetBearing: number,
  heading: number,
  tolerance = 75,
): boolean {
  return angleDiff(targetBearing, heading) <= tolerance
}

// ===========================================================================
// WGS84 → GCJ-02
// ===========================================================================

const GCJ_A = 6378245.0
const GCJ_EE = 0.00669342162296594323

/** 境外不做偏移（GCJ-02 只在中国大陆生效） */
function outOfChina(lat: number, lon: number): boolean {
  return !(lon > 73.66 && lon < 135.05 && lat > 3.86 && lat < 53.55)
}

function transformLat(x: number, y: number): number {
  let ret =
    -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x))
  ret += ((20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0) / 3.0
  ret += ((20.0 * Math.sin(y * Math.PI) + 40.0 * Math.sin((y / 3.0) * Math.PI)) * 2.0) / 3.0
  ret += ((160.0 * Math.sin((y / 12.0) * Math.PI) + 320 * Math.sin((y * Math.PI) / 30.0)) * 2.0) / 3.0
  return ret
}

function transformLon(x: number, y: number): number {
  let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x))
  ret += ((20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0) / 3.0
  ret += ((20.0 * Math.sin(x * Math.PI) + 40.0 * Math.sin((x / 3.0) * Math.PI)) * 2.0) / 3.0
  ret += ((150.0 * Math.sin((x / 12.0) * Math.PI) + 300.0 * Math.sin((x / 30.0) * Math.PI)) * 2.0) / 3.0
  return ret
}

/**
 * GPS 坐标转高德坐标。
 *
 * 返回 `[纬度, 经度]` 以贴合 `GeolocationPosition` 的字段顺序，
 * 避免调用方写成 `[lng, lat]` 后静默画出错误位置
 * （高德 API 用的是 `[lng, lat]`，两种顺序在这个项目里同时存在，
 * 转换函数必须明确自己用哪种）。
 */
export function wgs84ToGcj02(lat: number, lon: number): [number, number] {
  if (outOfChina(lat, lon)) {
    return [lat, lon]
  }

  let dLat = transformLat(lon - 105.0, lat - 35.0)
  let dLon = transformLon(lon - 105.0, lat - 35.0)
  const radLat = (lat / 180.0) * Math.PI
  let magic = Math.sin(radLat)
  magic = 1 - GCJ_EE * magic * magic
  const sqrtMagic = Math.sqrt(magic)

  dLat = (dLat * 180.0) / (((GCJ_A * (1 - GCJ_EE)) / (magic * sqrtMagic)) * Math.PI)
  dLon = (dLon * 180.0) / ((GCJ_A / sqrtMagic) * Math.cos(radLat) * Math.PI)

  return [lat + dLat, lon + dLon]
}

/**
 * 距离展示文案。
 *
 * 分级取整是有意的：近距离精度重要（"约 300 米"和"约 700 米"
 * 对驾驶决策完全不同），远距离再精确也没意义，
 * 反而让人误以为数据很准。
 *
 * **语气词"约"由本函数负责**，调用方不要再拼一遍 ——
 * 那样会写出"最近约 不足 50 米"这种叠词。
 * 也不能写成"前方 300 米"：那听起来像导航软件的沿路里程，
 * 而这里只有直线距离可用（没有路网几何）。
 */
export function formatDistance(km: number): string {
  if (!Number.isFinite(km) || km < 0) return '距离未知'
  // 取整到 50 米会把"就在眼前"变成"约 0 米"，读起来像数据缺失
  if (km < 0.05) return '50 米以内'
  if (km < 1) return `约 ${Math.round((km * 1000) / 50) * 50} 米`
  if (km < 10) return `约 ${km.toFixed(1)} 公里`
  return `约 ${Math.round(km)} 公里`
}
