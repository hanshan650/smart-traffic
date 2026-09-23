<!--
  路况地图
  ========
  把拥堵点与事件点画在高德地图上。与警务端的 `MapView` 的区别不只是配色：

  |            | 警务端 MapView              | 本组件                     |
  |------------|-----------------------------|----------------------------|
  | 图层       | 路网 + 摄像头 + 事件 + 路况  | 只有拥堵点 + 事件点         |
  | 数据来源   | 摄像头列表反查坐标           | **事件/快照自带坐标**       |
  | 点位信息   | 含摄像头编号、置信度          | 只有道路名与等级            |

  **坐标来自数据本身**，不再反查摄像头列表。这一点是被民众端逼出来的：
  公开接口不能返回摄像头列表，也就无从反查；而且上游的摄像头检索是按
  视野随机抽样的，本来就无法按编号回查。于是把坐标做进了事件模型，
  警务端也一并受益 —— 现在即使摄像头列表拉取失败，地图仍能标出事件位置。

  无高德 Key 时不报错，只显示提示 —— 地图是增强项，不该让整页不可用。
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useAmap, AMAP_STYLE_LIGHT } from '@/composables/useAmap'
import { useSystemStore } from '@/stores/system'
import { CITIZEN_CONGESTION_COLOR, CITIZEN_LEVEL_COLOR } from '@/types/citizen'
import { relativeTime } from '@/utils/time'
import type { CongestionRankItem, PublicEvent, PublicOverview } from '@/types/citizen'

const props = defineProps<{
  rank: CongestionRankItem[]
  events: PublicEvent[]
  overview: PublicOverview | null
}>()

const systemStore = useSystemStore()
// 注意区分两个对象：
//   · `map`  是 Map **实例**，用于 add/remove/setFitView
//   · `AMap` 是 API **命名空间**，用于 new CircleMarker / new LngLat
//
// 二者不可混用 —— Map 实例上并没有那些构造函数。
const { create, destroy, map, AMap, loading, error } = useAmap()

const containerId = 'citizen-map'
const amap = map
const overlays = ref<any[]>([])
const selected = ref<{ title: string; lines: string[] } | null>(null)

// 浮层开合状态。窄屏默认收起 —— 手机上地图本来就小，
// 两个浮层都摊开会把地图挤没。
const isNarrow = typeof window !== 'undefined'
  && window.matchMedia('(max-width: 767px)').matches
const leftOpen = ref(!isNarrow)
const rightOpen = ref(!isNarrow)

const congestionCards = computed(() => {
  const counts = props.overview?.congestionCounts ?? {}
  return [
    { key: 'normal', label: '畅通', value: counts.normal ?? 0 },
    { key: 'light', label: '缓行', value: counts.light ?? 0 },
    { key: 'moderate', label: '拥堵', value: counts.moderate ?? 0 },
    { key: 'heavy', label: '严重', value: counts.heavy ?? 0 },
  ]
})

/** 默认视野中心：河南高速覆盖范围的大致中心 */
const DEFAULT_CENTER: [number, number] = [113.6, 34.0]
const DEFAULT_ZOOM = 7

function clearOverlays(): void {
  if (amap.value && overlays.value.length) {
    amap.value.remove(overlays.value)
  }
  overlays.value = []
}

/** 只有经纬度都有效才画点，避免把 (0,0) 当成几内亚湾的坐标 */
function isValidPoint(latitude?: number | null, longitude?: number | null): boolean {
  return (
    typeof latitude === 'number' &&
    typeof longitude === 'number' &&
    Number.isFinite(latitude) &&
    Number.isFinite(longitude) &&
    latitude !== 0 &&
    longitude !== 0
  )
}

function draw(): void {
  if (!amap.value || !AMap.value) return
  clearOverlays()

  const markers: any[] = []

  // ---- 拥堵点：来自实时快照 ----
  props.rank.forEach((item) => {
    if (!isValidPoint(item.latitude, item.longitude)) return
    const color = CITIZEN_CONGESTION_COLOR[item.congestionLevel] ?? '#64748b'
    const marker = new AMap.value.CircleMarker({
      center: [item.longitude, item.latitude],
      radius: 11,
      strokeColor: color,
      strokeWeight: 2,
      fillColor: color,
      fillOpacity: 0.45,
      zIndex: 110,
      cursor: 'pointer',
    })
    marker.on('click', () => selectRank(item))
    markers.push(marker)
  })

  // ---- 事件点：来自公开路况事件 ----
  props.events.forEach((item) => {
    if (!isValidPoint(item.latitude, item.longitude)) return
    const color = CITIZEN_LEVEL_COLOR[item.level] ?? '#64748b'
    const marker = new AMap.value.CircleMarker({
      center: [item.longitude, item.latitude],
      radius: 9,
      strokeColor: color,
      strokeWeight: 2,
      fillColor: '#ffffff',
      fillOpacity: 0.9,
      zIndex: 130,
      cursor: 'pointer',
    })
    marker.on('click', () => selectEvent(item))
    markers.push(marker)
  })

  overlays.value = markers
  if (markers.length) {
    amap.value.add(markers)
  }
}

function selectRank(item: CongestionRankItem): void {
  selected.value = {
    title: item.roadName,
    lines: [`通行状况：${item.congestionLabel}`, `监测到 ${item.vehicleCount} 辆车`],
  }
  focusOn(item.latitude, item.longitude)
}

function selectEvent(item: PublicEvent): void {
  selected.value = {
    title: `${item.eventTypeLabel}${item.roadName ? ` · ${item.roadName}` : ''}`,
    lines: [item.description || '（无补充描述）', `级别：${item.levelLabel}`],
  }
  focusOn(item.latitude, item.longitude)
}

/**
 * 把地图定位到某个点位。
 *
 * 直接使用**数据里的坐标**，而不是从覆盖物对象上反查 ——
 * `Marker` 用 `getPosition()`，而 `CircleMarker` 用 `getCenter()`，
 * 记错就会静默失败（不报错、地图不动）。手头本来就有坐标，
 * 绕一道反而多一个出错点。
 */
function focusOn(latitude?: number | null, longitude?: number | null): void {
  if (!amap.value || !isValidPoint(latitude, longitude)) return
  const zoom = Math.max(Number(amap.value.getZoom?.() ?? 8), 11)
  amap.value.setZoomAndCenter(zoom, [longitude as number, latitude as number])
}

/**
 * 把视野调整到所有点位。
 *
 * 仅在**首次**有数据时执行 —— 之后用户自己拖动过地图，再自动跳回去
 * 会让人烦。
 *
 * 关键细节：`setFitView` 的第一个参数要的是**覆盖物数组**
 * （Marker/CircleMarker 等），不是 `LngLat` 数组。传 LngLat 时它不报错，
 * 但也不会正确计算范围 —— 表现是视野只覆盖了一部分点位，很容易被误认为
 * "数据没取到"。这里直接复用 `overlays`（刚 add 进去的那些覆盖物）。
 */
let fittedOnce = false
function fitView(): void {
  if (!amap.value || fittedOnce) return
  if (!overlays.value.length) return
  // 第二参 false = 不做动画（截屏/首屏更可控），第三参留出边距避免点贴边
  amap.value.setFitView(overlays.value, false, [60, 60, 60, 60])
  fittedOnce = true
}

onMounted(async () => {
  // 高德 Key 从运行时配置读取。已加载过就不重复请求 ——
  // 地图组件可能在列表/地图之间来回切换，每次挂载都拉一次配置没必要。
  if (!systemStore.config) {
    await systemStore.loadConfig()
  }
  await create({
    container: containerId,
    center: DEFAULT_CENTER,
    zoom: DEFAULT_ZOOM,
    mapStyle: AMAP_STYLE_LIGHT,
    // 官方路况图层作为补充：它覆盖更广，但我们自己的点位更能说明具体事件
    showTraffic: true,
  })
  draw()
  fitView()
})

// 数据每 30 秒刷新一次，地图上的点跟着更新
watch(
  () => [props.rank, props.events],
  () => {
    draw()
    fitView()
  },
)

onBeforeUnmount(destroy)
</script>

<template>
  <div class="map-wrap">
    <div :id="containerId" class="map-canvas" />

    <div v-if="loading" class="map-mask">地图加载中…</div>
    <div v-else-if="error" class="map-mask">
      <div class="mask-title">地图暂不可用</div>
      <div class="mask-detail">{{ error }}</div>
    </div>

    <!-- ======================= 左浮层：通行状况 ======================= -->
    <aside class="overlay overlay-left" :class="{ open: leftOpen }">
      <button class="overlay-head" @click="leftOpen = !leftOpen">
        <span class="overlay-head-main">
          <span class="overlay-dot" :style="{ background: CITIZEN_CONGESTION_COLOR.heavy }" />
          路段通行状况
        </span>
        <span class="overlay-chevron">{{ leftOpen ? '▾' : '▸' }}</span>
      </button>

      <div v-show="leftOpen" class="overlay-body">
        <div class="congestion-grid">
          <div v-for="item in congestionCards" :key="item.key" class="congestion-cell">
            <span class="congestion-value" :style="{ color: CITIZEN_CONGESTION_COLOR[item.key] }">
              {{ item.value }}
            </span>
            <span class="congestion-label">{{ item.label }}</span>
          </div>
        </div>

        <div v-if="rank.length" class="overlay-section">
          <div class="overlay-section-title">车流最集中</div>
          <ul class="mini-list">
            <li
              v-for="(item, index) in rank.slice(0, 6)"
              :key="index"
              class="mini-item clickable"
              @click="selectRank(item)"
            >
              <span
                class="mini-bar"
                :style="{ background: CITIZEN_CONGESTION_COLOR[item.congestionLevel] ?? '#64748b' }"
              />
              <span class="mini-name">{{ item.roadName }}</span>
              <span class="mini-value">{{ item.vehicleCount }}</span>
            </li>
          </ul>
        </div>
        <div v-else class="overlay-empty">暂无路段数据</div>
      </div>
    </aside>

    <!-- ======================= 右浮层：事件列表 ======================= -->
    <aside class="overlay overlay-right" :class="{ open: rightOpen }">
      <button class="overlay-head" @click="rightOpen = !rightOpen">
        <span class="overlay-head-main">
          <span class="overlay-dot" :style="{ background: CITIZEN_LEVEL_COLOR.critical }" />
          路况事件
          <span class="overlay-count">{{ events.length }}</span>
        </span>
        <span class="overlay-chevron">{{ rightOpen ? '▾' : '▸' }}</span>
      </button>

      <div v-show="rightOpen" class="overlay-body">
        <div v-if="!events.length" class="overlay-empty">当前没有需要提醒的路况事件</div>

        <ul v-else class="mini-list">
          <li
            v-for="(item, index) in events.slice(0, 12)"
            :key="index"
            class="mini-item event-mini clickable"
            @click="selectEvent(item)"
          >
            <span
              class="mini-bar"
              :style="{ background: CITIZEN_LEVEL_COLOR[item.level] ?? '#64748b' }"
            />
            <div class="mini-body">
              <div class="mini-row">
                <span class="mini-name">{{ item.eventTypeLabel }}</span>
                <span class="mini-time">{{ relativeTime(item.createdAt) }}</span>
              </div>
              <div v-if="item.roadName" class="mini-sub">{{ item.roadName }}</div>
            </div>
          </li>
        </ul>
      </div>
    </aside>

    <!-- ======================= 点位信息卡 ======================= -->
    <div v-if="selected" class="info-card">
      <div class="info-head">
        <strong>{{ selected.title }}</strong>
        <button class="info-close" @click="selected = null">✕</button>
      </div>
      <p v-for="(line, index) in selected.lines" :key="index" class="info-line">{{ line }}</p>
    </div>

    <!-- ======================= 图例 ======================= -->
    <div v-if="!error && !selected" class="legend">
      <span class="legend-item">
        <i class="dot" :style="{ background: CITIZEN_CONGESTION_COLOR.heavy }" />严重
      </span>
      <span class="legend-item">
        <i class="dot" :style="{ background: CITIZEN_CONGESTION_COLOR.moderate }" />拥堵
      </span>
      <span class="legend-item">
        <i class="dot ring" :style="{ borderColor: CITIZEN_LEVEL_COLOR.critical }" />事件
      </span>
    </div>

    <p class="map-note">点位为大致位置（约百米级）</p>
  </div>
</template>

<style scoped>
.map-wrap {
  position: relative;
  width: 100%;
  /* 地图是这一页的主体，给足高度；浮层要装得下 */
  height: calc(100vh - 250px);
  min-height: 420px;
  border-radius: var(--c-radius);
  overflow: hidden;
  border: 1px solid var(--c-border);
  background: var(--c-surface);
}
.map-canvas {
  width: 100%;
  height: 100%;
}

.map-mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: var(--c-surface-2);
  color: var(--c-text-faint);
  font-size: 13px;
  text-align: center;
  padding: 20px;
}
.mask-title {
  font-size: 14px;
  color: var(--c-text-dim);
}
.mask-detail {
  font-size: 11px;
  line-height: 1.6;
  max-width: 300px;
}

/* ------------------------------------------------------------------ 浮层 */

.overlay {
  position: absolute;
  top: 10px;
  width: 238px;
  max-height: calc(100% - 64px);
  display: flex;
  flex-direction: column;
  border-radius: 12px;
  /* 半透明 + 模糊：地图在下方仍可辨，不至于被完全遮挡 */
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(6px);
  box-shadow: 0 2px 14px rgba(15, 23, 42, 0.16);
  overflow: hidden;
  z-index: 12;
}
.overlay-left {
  left: 10px;
}
.overlay-right {
  right: 10px;
}

.overlay-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 9px 11px;
  border: none;
  background: none;
  color: var(--c-text);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
}
.overlay-head:hover {
  background: rgba(241, 245, 249, 0.9);
}
.overlay-head-main {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.overlay-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: 0 0 auto;
}
.overlay-count {
  padding: 0 6px;
  border-radius: 999px;
  background: var(--c-surface-2);
  color: var(--c-text-dim);
  font-size: 11px;
  font-weight: 500;
}
.overlay-chevron {
  color: var(--c-text-faint);
  font-size: 10px;
}

.overlay-body {
  padding: 0 11px 11px;
  overflow-y: auto;
}

/* ------------------------------------------------------------ 拥堵分布 */

.congestion-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 4px;
  padding-bottom: 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid var(--c-border);
}
.congestion-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
}
.congestion-value {
  font-size: 17px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.congestion-label {
  font-size: 10.5px;
  color: var(--c-text-faint);
}

.overlay-section {
  margin-top: 8px;
}
.overlay-section-title {
  margin-bottom: 5px;
  font-size: 11px;
  color: var(--c-text-faint);
}

/* ------------------------------------------------------------ 迷你列表 */

.mini-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.mini-item {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 4px;
  border-radius: 7px;
  font-size: 12px;
  transition: background 0.12s;
}
.mini-item + .mini-item {
  border-top: 1px solid var(--c-border);
}
.mini-item.clickable {
  cursor: pointer;
}
.mini-item.clickable:hover {
  background: var(--c-primary-soft);
}
.mini-bar {
  flex: 0 0 auto;
  width: 3px;
  height: 15px;
  border-radius: 2px;
}
.mini-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mini-value {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
  color: var(--c-text-dim);
  font-size: 11px;
}
.mini-body {
  flex: 1;
  min-width: 0;
}
.mini-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}
.mini-time {
  flex: 0 0 auto;
  font-size: 10.5px;
  color: var(--c-text-faint);
}
.mini-sub {
  font-size: 11px;
  color: var(--c-text-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overlay-empty {
  padding: 14px 0;
  text-align: center;
  font-size: 11.5px;
  color: var(--c-text-faint);
}

/* ------------------------------------------------------------ 点位信息卡 */

.info-card {
  position: absolute;
  left: 50%;
  bottom: 10px;
  transform: translateX(-50%);
  max-width: min(360px, calc(100% - 24px));
  padding: 9px 12px;
  border-radius: 11px;
  background: rgba(255, 255, 255, 0.97);
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.2);
  z-index: 14;
}
.info-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 3px;
}
.info-head strong {
  font-size: 13px;
  line-height: 1.4;
}
.info-close {
  border: none;
  background: none;
  color: var(--c-text-faint);
  font-size: 13px;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
}
.info-line {
  margin: 0;
  font-size: 11.5px;
  color: var(--c-text-dim);
  line-height: 1.6;
}

/* ------------------------------------------------------------------ 图例 */

.legend {
  position: absolute;
  left: 10px;
  bottom: 10px;
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  padding: 5px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 1px 6px rgba(15, 23, 42, 0.14);
  font-size: 10.5px;
  color: var(--c-text-dim);
  z-index: 10;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 4px;
}
.legend-sep {
  width: 1px;
  height: 10px;
  background: var(--c-border-strong);
}
.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  display: inline-block;
}
.dot.ring {
  background: #fff;
  border: 2px solid;
  width: 8px;
  height: 8px;
}

.map-note {
  position: absolute;
  right: 10px;
  bottom: 10px;
  margin: 0;
  padding: 4px 9px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.94);
  font-size: 10px;
  color: var(--c-text-faint);
  box-shadow: 0 1px 6px rgba(15, 23, 42, 0.1);
  z-index: 10;
}

/* ------------------------------------------------------------------ 窄屏 */

@media (max-width: 767px) {
  .map-wrap {
    height: calc(100vh - 210px);
    min-height: 380px;
  }
  /* 两个浮层都收在顶部，各自只占一行；展开时铺满整宽 */
  .overlay {
    width: auto;
    left: 8px;
    right: 8px;
    max-height: calc(100% - 76px);
  }
  .overlay-left {
    top: 8px;
  }
  .overlay-right {
    /* 左浮层收起时贴到第二行 */
    top: 47px;
  }
  /* 展开的那个提升层级并回到顶部覆盖另一个 */
  .overlay.open {
    top: 8px;
    z-index: 13;
  }
  .info-card {
    bottom: 44px;
  }
  .legend,
  .map-note {
    display: none;
  }
}

@media (min-width: 768px) {
  .overlay {
    max-height: calc(100% - 60px);
  }
}
</style>
