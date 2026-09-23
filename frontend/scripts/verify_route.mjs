/**
 * 地理与路段判定验证
 * ==================
 *
 * 这两块是"行进视角"的地基，出错的方式都很隐蔽：
 *   · 坐标系不转换 → 位置偏 250 米，地图上看着"差不多在城市里"，
 *     但"当前路段"会判错，而界面上没有任何提示
 *   · 路段判定阈值写错 → 用户在外省打开，被硬说成"你正在京港澳高速上"
 *
 * 所以拿已知点对和边界情况离线跑一遍，比肉眼看地图可靠。
 *
 * 用法（需先 esbuild 打包，见 README 或随后的命令行）：
 *     node scripts/verify_route.mjs
 */

import assert from 'node:assert/strict'

import { bearingDeg, formatDistance, haversineKm, wgs84ToGcj02 } from './.tmp/geo.mjs'
import { buildRouteContext, roadOptions } from './.tmp/route.mjs'

let passed = 0
let failed = 0

function check(label, fn) {
  try {
    fn()
    passed++
    console.log(`  [OK] ${label}`)
  } catch (err) {
    failed++
    console.log(`  [!!] ${label}`)
    console.log(`       ${err.message.split('\n')[0]}`)
  }
}

const near = (actual, expected, tol, label) =>
  assert.ok(
    Math.abs(actual - expected) <= tol,
    `${label}: 期望 ${expected}±${tol}，实际 ${actual}`,
  )

console.log('='.repeat(66))
console.log('一、坐标系转换（WGS84 → GCJ-02）')
console.log('='.repeat(66))

// 天安门：两端都有公开的权威取值，适合当基准点
const TIANANMEN_WGS = [39.9075, 116.39064]
const TIANANMEN_GCJ = [39.90869, 116.39706]

check('天安门转换结果与已知值一致（±30 米）', () => {
  const [lat, lng] = wgs84ToGcj02(...TIANANMEN_WGS)
  const [refLat, refLng] = TIANANMEN_GCJ
  const errKm = haversineKm(lat, lng, refLat, refLng)
  assert.ok(errKm * 1000 < 30, `偏差 ${(errKm * 1000).toFixed(1)} 米，超过 30 米`)
})

check('偏移量落在 300～800 米（中国境内的合理量级）', () => {
  const [lat, lng] = wgs84ToGcj02(...TIANANMEN_WGS)
  const offsetM = haversineKm(lat, lng, ...TIANANMEN_WGS) * 1000
  assert.ok(offsetM > 300 && offsetM < 800, `偏移 ${offsetM.toFixed(0)} 米`)
})

check('境外坐标原样返回（GCJ-02 只在中国大陆生效）', () => {
  const tokyo = [35.6812, 139.7671]
  const result = wgs84ToGcj02(...tokyo)
  near(result[0], tokyo[0], 1e-9, '纬度')
  near(result[1], tokyo[1], 1e-9, '经度')
})

check('多个地点的偏移量都在合理量级', () => {
  // 刻意**不断言偏移方向**。GCJ-02 是对经纬度做非线性变换，
  // 偏移量和方向都随位置变化 —— 实测河南一带纬度是减小的，
  // 与"一律偏东北"的直觉相反。写这种断言会把正确实现判成错误。
  const spots = [
    ['北京', 39.9042, 116.4074],
    ['郑州', 34.7466, 113.6254],
    ['许昌', 34.0358, 113.8521],
    ['上海', 31.2304, 121.4737],
    ['广州', 23.1291, 113.2644],
  ]
  const offsets = []
  for (const [name, lat, lng] of spots) {
    const [outLat, outLng] = wgs84ToGcj02(lat, lng)
    const m = haversineKm(lat, lng, outLat, outLng) * 1000
    offsets.push(`${name} ${m.toFixed(0)}m`)
    assert.ok(m > 50 && m < 900, `${name} 偏移 ${m.toFixed(0)} 米，超出合理范围`)
  }
  console.log(`       实测偏移：${offsets.join(' / ')}`)
})

console.log()
console.log('='.repeat(66))
console.log('二、距离与方位角')
console.log('='.repeat(66))

check('北京—上海直线距离约 1067 公里', () => {
  const km = haversineKm(39.9042, 116.4074, 31.2304, 121.4737)
  near(Math.round(km), 1067, 12, '距离')
})

check('正东方向的方位角是 90°', () => {
  near(bearingDeg(34, 113, 34, 114), 90, 0.5, '方位角')
})

check('正北方向的方位角是 0°', () => {
  const b = bearingDeg(34, 113, 35, 113)
  assert.ok(b < 0.5 || b > 359.5, `得到 ${b}`)
})

check('距离分档文案：语气词由函数负责', () => {
  // 很近时不给数字：取整到 50 米会把 20 米显示成"约 0 米"，像数据缺失
  assert.equal(formatDistance(0.02), '50 米以内')
  assert.equal(formatDistance(0.06), '约 50 米')
  assert.equal(formatDistance(0.48), '约 500 米')
  assert.equal(formatDistance(3.24), '约 3.2 公里')
  assert.equal(formatDistance(41.6), '约 42 公里')
  // "约"由本函数输出，调用方再拼一遍就会得到"最近约 不足 50 米"
  assert.ok(formatDistance(3.24).startsWith('约 '))
  // 无定位时 distanceKm 是 NaN，必须给出占位而不是 "NaN 公里"
  assert.equal(formatDistance(Number.NaN), '距离未知')
})

console.log()
console.log('='.repeat(66))
console.log('三、路段判定')
console.log('='.repeat(66))

// 许昌段事件点（GCJ-02）
const XUCHANG = { latitude: 34.0358, longitude: 113.8521, roadName: '京港澳高速', level: 'critical' }
const XINYANG = { latitude: 32.147, longitude: 114.0919, roadName: '京港澳高速', level: 'warning' }
const ZHENGZHOU = { latitude: 34.7466, longitude: 113.6254, roadName: '连霍高速', level: 'warning' }
const NANYANG = { latitude: 32.9908, longitude: 112.5286, roadName: '沪陕高速', level: 'info' }

const makeEvent = (src) => ({
  eventType: 'accident',
  eventTypeLabel: '交通事故',
  level: src.level,
  levelLabel: '严重',
  title: '',
  description: '测试',
  roadName: src.roadName,
  congestionLevel: 'normal',
  congestionLabel: '畅通',
  latitude: src.latitude,
  longitude: src.longitude,
  createdAt: '2026-09-23T03:00:00Z',
})

const EVENTS = [XUCHANG, XINYANG, ZHENGZHOU, NANYANG].map(makeEvent)

check('有定位且紧邻某路段 → 自动匹配到该路段', () => {
  const ctx = buildRouteContext({
    events: EVENTS,
    fix: { lat: 34.0354, lng: 113.8517, heading: null },
  })
  assert.equal(ctx.hasFix, true)
  assert.equal(ctx.currentRoad, '京港澳高速')
  assert.equal(ctx.source, 'nearby')
  assert.equal(ctx.onRoute.length, 2, '京港澳高速应有 2 条事件')
  // 最近的应当是许昌那条
  assert.ok(ctx.onRoute[0].distanceKm < 0.1, `最近距离 ${ctx.onRoute[0].distanceKm}`)
  // 其他路段按距离升序
  assert.equal(ctx.offRoute[0].event.roadName, '连霍高速')
})

check('远离所有路段（>30 公里）→ 不猜，返回未匹配', () => {
  // 上海，离最近的河南点位上千公里
  const ctx = buildRouteContext({
    events: EVENTS,
    fix: { lat: 31.2304, lng: 121.4737, heading: null },
  })
  assert.equal(ctx.currentRoad, '', '不应该硬说用户在某条路上')
  assert.equal(ctx.source, 'none')
  assert.equal(ctx.onRoute.length, 0)
  assert.equal(ctx.offRoute.length, 4, '所有事件仍应可见')
  assert.ok(ctx.offRoute[0].distanceKm > 500, '距离应当是真实的大数')
})

check('没有定位 → 不算距离，但仍给出全部事件', () => {
  const ctx = buildRouteContext({ events: EVENTS, fix: null })
  assert.equal(ctx.hasFix, false)
  assert.equal(ctx.currentRoad, '')
  assert.equal(ctx.onRoute.length, 0)
  assert.equal(ctx.offRoute.length, 4, '无定位是常态，绝不能空着')
  assert.ok(Number.isNaN(ctx.offRoute[0].distanceKm), 'distanceKm 应为 NaN')
  assert.equal(ctx.offRoute[0].event.roadName, '京港澳高速', '应保持原始顺序')
})

check('手动指定路段覆盖自动判定', () => {
  const ctx = buildRouteContext({
    events: EVENTS,
    fix: { lat: 34.0354, lng: 113.8517, heading: null },
    manualRoad: '连霍高速',
  })
  assert.equal(ctx.currentRoad, '连霍高速')
  assert.equal(ctx.source, 'manual')
  assert.equal(ctx.onRoute.length, 1)
  assert.ok(ctx.roadDistanceKm > 60, `该路段离用户 ${ctx.roadDistanceKm} 公里`)
})

check('无定位时手动指定路段依然有效', () => {
  const ctx = buildRouteContext({ events: EVENTS, fix: null, manualRoad: '沪陕高速' })
  assert.equal(ctx.currentRoad, '沪陕高速')
  assert.equal(ctx.source, 'manual')
})

check('无坐标事件进 unlocated，不参与距离计算', () => {
  const broken = { ...makeEvent(XUCHANG), latitude: null, longitude: null }
  const ctx = buildRouteContext({
    events: [...EVENTS, broken],
    fix: { lat: 34.0354, lng: 113.8517, heading: null },
  })
  assert.equal(ctx.unlocated.length, 1)
  assert.equal(ctx.onRoute.length + ctx.offRoute.length, 4, '有坐标的仍是 4 条')
})

check('(0,0) 当作无坐标处理（几内亚湾不是事件位置）', () => {
  const zero = { ...makeEvent(XUCHANG), latitude: 0, longitude: 0 }
  const ctx = buildRouteContext({
    events: [zero],
    fix: { lat: 34.0354, lng: 113.8517, heading: null },
  })
  assert.equal(ctx.unlocated.length, 1)
  assert.equal(ctx.offRoute.length, 0)
})

check('有 heading 时区分前方/后方，前方排在前', () => {
  // 用户站在许昌点南方 1 公里，朝北走 → 许昌点在正前方
  const ctx = buildRouteContext({
    events: [XUCHANG, XINYANG],
    fix: { lat: 34.0268, lng: 113.8521, heading: 0 },
  })
  assert.equal(ctx.currentRoad, '京港澳高速')
  const ahead = ctx.onRoute.filter((i) => i.ahead === true)
  const behind = ctx.onRoute.filter((i) => i.ahead === false)
  assert.equal(ahead.length, 1, '许昌点应在正前方')
  assert.equal(behind.length, 1, '信阳点应在身后')
  assert.equal(ctx.onRoute[0].ahead, true, '前方事件必须排在第一个')
})

check('无 heading 时 ahead 为 null（不是 false）', () => {
  const ctx = buildRouteContext({
    events: [XUCHANG],
    fix: { lat: 34.0268, lng: 113.8521, heading: null },
  })
  assert.equal(
    ctx.onRoute[0].ahead,
    null,
    '把"未知方向"当成"已在身后"会让前方事件全部消失',
  )
})

check('同路段超过 50 公里的事件不算"前方"', () => {
  // 京港澳高速同时有许昌段和信阳段，两者相距 211 公里。
  // 若不加距离上限，用户会被告知"前方 2 个事件"，
  // 其中一个在 200 公里之外 —— 这在出行决策上是误导
  const ctx = buildRouteContext({
    events: EVENTS,
    fix: { lat: 34.0354, lng: 113.8517, heading: null },
  })
  assert.equal(ctx.currentRoad, '京港澳高速')
  assert.equal(ctx.ahead.length, 1, '只有许昌那条算前方')
  assert.equal(ctx.far.length, 1, '信阳那条应进"更远处"')
  assert.ok(ctx.far[0].distanceKm > 200, `实际 ${ctx.far[0].distanceKm.toFixed(0)} 公里`)
  assert.ok(ctx.ahead[0].distanceKm < 1)
})

check('三个分区互斥且覆盖全部本路段事件', () => {
  const ctx = buildRouteContext({
    events: EVENTS,
    fix: { lat: 34.0354, lng: 113.8517, heading: 0 },
  })
  const total = ctx.ahead.length + ctx.passed.length + ctx.far.length
  assert.equal(total, ctx.onRoute.length, '分区之和应等于本路段事件数')
})

check('方向未知时归入"前方"而非"远处"', () => {
  // 用户静止时拿不到 heading。此时把附近事件藏进"远处"
  // 比误标"前方"更糟 —— 那会让人以为前面没事
  const ctx = buildRouteContext({
    events: [XUCHANG],
    fix: { lat: 34.0268, lng: 113.8521, heading: null },
  })
  assert.equal(ctx.ahead.length, 1)
  assert.equal(ctx.far.length, 0)
})

check('路段选项按事件条数降序', () => {
  const roads = roadOptions(EVENTS)
  assert.equal(roads[0], '京港澳高速', '有 2 条事件的路段应排最前')
  assert.equal(roads.length, 3)
})

console.log()
console.log('='.repeat(66))
console.log(`  通过 ${passed} 项，失败 ${failed} 项`)
console.log('='.repeat(66))
process.exit(failed ? 1 : 0)
