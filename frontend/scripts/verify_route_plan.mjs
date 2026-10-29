/**
 * 路线投影验证
 * ============
 *
 * `projectOntoPath` 决定了"沿途事件"的排序与距离，出错的方式同样隐蔽：
 * 投影算到延长线上、里程从终点往回算、横向容差判错，界面上都不会报错，
 * 只会安静地把"前方 200 米"写成"前方 4 公里"。
 *
 * 所以拿构造好的折线与已知距离对一遍。期望值用 haversine 现算，
 * 而不是照抄实现里的等距圆柱公式 —— 否则只是把同一个公式验了两遍。
 *
 * 用法：
 *     npm run verify:plan
 */

import assert from 'node:assert/strict'

import { haversineKm } from './.tmp/geo.mjs'
import { eventsAlongRoute, formatDuration, projectOntoPath } from './.tmp/routePlan.mjs'

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

/** 沿折线的期望里程，用 haversine 逐段累加，与实现无关 */
function alongKm(path, point) {
  let total = 0
  for (let i = 1; i < path.length; i++) {
    total += haversineKm(path[i - 1][1], path[i - 1][0], path[i][1], path[i][0])
  }
  return total
}

const near = (actual, expected, tol, label) =>
  assert.ok(
    Math.abs(actual - expected) <= tol,
    `${label}: 期望 ${expected}±${tol}，实际 ${actual.toFixed(3)}`,
  )

// 东西向直线，纬度取上海附近（经度 1 度的实际长度会被 cos 压缩，
// 正是检验"有没有按纬度换算"的地方）
const STRAIGHT = [
  [121.0, 31.0],
  [121.01, 31.0],
]

console.log('='.repeat(66))
console.log('一、点到折线的投影')
console.log('='.repeat(66))

check('路径中点上：里程 = 全程一半，横向偏移为 0', () => {
  const result = projectOntoPath(STRAIGHT, 121.005, 31.0)
  const half = haversineKm(31.0, 121.0, 31.0, 121.005)
  near(result.alongKm, half, 0.002, '沿路里程')
  assert.ok(result.offsetM < 1, `横向偏移应接近 0，实际 ${result.offsetM.toFixed(1)} 米`)
})

check('路侧点：沿路里程不变，横向偏移 = 到路的中垂距离', () => {
  const result = projectOntoPath(STRAIGHT, 121.005, 31.001)
  const offset = haversineKm(31.0, 121.005, 31.001, 121.005) * 1000
  near(result.alongKm, haversineKm(31.0, 121.0, 31.0, 121.005), 0.002, '沿路里程')
  near(result.offsetM, offset, 2, '横向偏移')
})

check('起点之外：里程夹到 0，不落到延长线上', () => {
  const result = projectOntoPath(STRAIGHT, 120.99, 31.0)
  near(result.alongKm, 0, 1e-6, '沿路里程')
  assert.ok(result.offsetM > 900, `应落在起点外约 950 米，实际 ${result.offsetM.toFixed(0)} 米`)
})

check('终点之外：里程夹到全程，不会超过路线长度', () => {
  const result = projectOntoPath(STRAIGHT, 121.02, 31.0)
  const total = haversineKm(31.0, 121.0, 31.0, 121.01)
  near(result.alongKm, total, 0.002, '沿路里程')
})

check('折线拐弯：里程跨段累加', () => {
  const path = [
    [121.0, 31.0],
    [121.01, 31.0],
    [121.01, 31.01],
  ]
  const first = haversineKm(31.0, 121.0, 31.0, 121.01)
  const secondHalf = haversineKm(31.0, 121.01, 31.005, 121.01)
  const result = projectOntoPath(path, 121.01, 31.005)
  near(result.alongKm, first + secondHalf, 0.003, '沿路里程')
})

check('回头路线：取横向偏移最小的一段，而不是第一段', () => {
  // 去程与回程几乎重合，点落在回程上；若按"碰到即止"会算成去程的里程
  const path = [
    [121.0, 31.0],
    [121.01, 31.0],
    [121.01, 31.00002],
    [121.0, 31.00002],
  ]
  const first = haversineKm(31.0, 121.0, 31.0, 121.01)
  const rise = haversineKm(31.0, 121.01, 31.00002, 121.01)
  const backHalf = haversineKm(31.00002, 121.01, 31.00002, 121.005)
  const result = projectOntoPath(path, 121.005, 31.00002)

  near(result.alongKm, first + rise + backHalf, 0.01, '沿路里程')
  assert.ok(result.offsetM < 3, `横向偏移应接近 0，实际 ${result.offsetM.toFixed(1)} 米`)
  assert.ok(
    result.alongKm > alongKm(path) / 2,
    '应落在回程上（里程超过全程一半），落到去程说明取错了段',
  )
})

console.log('')
console.log('='.repeat(66))
console.log('二、沿途事件筛选')
console.log('='.repeat(66))

function event(lng, lat) {
  return {
    eventType: 'congestion',
    eventTypeLabel: '拥堵',
    level: 'warning',
    levelLabel: '注意',
    title: '',
    description: '',
    roadName: '沪昆高速',
    congestionLevel: 'moderate',
    congestionLabel: '拥堵',
    latitude: lat,
    longitude: lng,
  }
}

check('走廊内的保留，走廊外的丢弃', () => {
  const inside = event(121.005, 31.001) // 约 111 米
  const outside = event(121.005, 31.02) // 约 2.2 公里
  const result = eventsAlongRoute([inside, outside], STRAIGHT, 300)
  assert.equal(result.length, 1)
  assert.equal(result[0].event, inside)
})

check('按沿路里程升序，而不是按输入顺序', () => {
  const far = event(121.009, 31.0)
  const nearStart = event(121.001, 31.0)
  const result = eventsAlongRoute([far, nearStart], STRAIGHT, 300)
  assert.deepEqual(
    result.map((item) => item.event),
    [nearStart, far],
  )
})

check('无坐标事件不进入沿途列表', () => {
  const broken = { ...event(0, 0), latitude: null, longitude: null }
  const result = eventsAlongRoute([broken, event(121.005, 31.0)], STRAIGHT, 300)
  assert.equal(result.length, 1)
})

check('容差按出行方式生效：步行 120 米过滤掉 200 米外的点', () => {
  const close = event(121.005, 31.0005) // 约 55 米
  const edge = event(121.005, 31.0018) // 约 200 米
  const walking = eventsAlongRoute([close, edge], STRAIGHT, 120)
  const driving = eventsAlongRoute([close, edge], STRAIGHT, 300)
  assert.equal(walking.length, 1)
  assert.equal(driving.length, 2)
})

console.log('')
console.log('='.repeat(66))
console.log('三、耗时文案')
console.log('='.repeat(66))

check('不足一分钟 / 分钟 / 小时的分档', () => {
  assert.equal(formatDuration(30), '1 分钟内')
  assert.equal(formatDuration(18 * 60), '约 18 分钟')
  assert.equal(formatDuration(60 * 60), '约 1 小时')
  assert.equal(formatDuration(75 * 60), '约 1 小时 15 分钟')
})

check('非法值不编造数字', () => {
  assert.equal(formatDuration(Number.NaN), '时长未知')
  assert.equal(formatDuration(0), '时长未知')
})

console.log('')
console.log('='.repeat(66))
console.log(`  通过 ${passed} 项，失败 ${failed} 项`)
console.log('='.repeat(66))

process.exit(failed ? 1 : 0)
