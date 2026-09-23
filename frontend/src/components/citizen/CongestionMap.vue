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
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useAmap, AMAP_STYLE_LIGHT } from '@/composables/useAmap'
import { useSystemStore } from '@/stores/system'
import { CITIZEN_CONGESTION_COLOR, CITIZEN_LEVEL_COLOR } from '@/types/citizen'
import type { CongestionRankItem, PublicEvent } from '@/types/citizen'

const props = defineProps<{
  rank: CongestionRankItem[]
  events: PublicEvent[]
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
  for (const item of props.rank) {
    if (!isValidPoint(item.latitude, item.longitude)) continue
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
    marker.on('click', () => {
      selected.value = {
        title: item.roadName,
        lines: [`通行状况：${item.congestionLabel}`, `监测到 ${item.vehicleCount} 辆车`],
      }
    })
    markers.push(marker)
  }

  // ---- 事件点：来自公开路况事件 ----
  for (const item of props.events) {
    if (!isValidPoint(item.latitude, item.longitude)) continue
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
    marker.on('click', () => {
      selected.value = {
        title: `${item.eventTypeLabel}${item.roadName ? ` · ${item.roadName}` : ''}`,
        lines: [item.description || '（无补充描述）', `级别：${item.levelLabel}`],
      }
    })
    markers.push(marker)
  }

  overlays.value = markers
  if (markers.length) {
    amap.value.add(markers)
  }
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

    <!-- 图例 -->
    <div v-if="!error" class="legend">
      <span class="legend-title">图例</span>
      <span class="legend-item">
        <i class="dot" :style="{ background: CITIZEN_CONGESTION_COLOR.heavy }" />严重拥堵
      </span>
      <span class="legend-item">
        <i class="dot" :style="{ background: CITIZEN_CONGESTION_COLOR.moderate }" />拥堵
      </span>
      <span class="legend-item">
        <i class="dot ring" :style="{ borderColor: CITIZEN_LEVEL_COLOR.critical }" />事件
      </span>
    </div>

    <!-- 点位信息 -->
    <div v-if="selected" class="info-card">
      <div class="info-head">
        <strong>{{ selected.title }}</strong>
        <button class="info-close" @click="selected = null">✕</button>
      </div>
      <p v-for="(line, index) in selected.lines" :key="index" class="info-line">{{ line }}</p>
    </div>

    <p class="map-note">
      点位为监测路段的大致位置（约百米级），非精确坐标。
    </p>
  </div>
</template>

<style scoped>
.map-wrap {
  position: relative;
  width: 100%;
  height: 60vh;
  min-height: 340px;
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

/* ------------------------------------------------------------------ 图例 */

.legend {
  position: absolute;
  left: 10px;
  bottom: 10px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 1px 6px rgba(15, 23, 42, 0.16);
  font-size: 11px;
  color: var(--c-text-dim);
}
.legend-title {
  color: var(--c-text-faint);
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 4px;
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

/* ------------------------------------------------------------ 点位信息卡 */

.info-card {
  position: absolute;
  left: 10px;
  right: 10px;
  top: 10px;
  padding: 10px 12px;
  border-radius: 11px;
  background: rgba(255, 255, 255, 0.97);
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.18);
}
.info-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 4px;
}
.info-head strong {
  font-size: 13.5px;
  line-height: 1.4;
}
.info-close {
  border: none;
  background: none;
  color: var(--c-text-faint);
  font-size: 14px;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
}
.info-line {
  margin: 0;
  font-size: 12px;
  color: var(--c-text-dim);
  line-height: 1.6;
}

.map-note {
  position: absolute;
  right: 10px;
  bottom: 10px;
  margin: 0;
  padding: 4px 9px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.94);
  font-size: 10.5px;
  color: var(--c-text-faint);
  box-shadow: 0 1px 6px rgba(15, 23, 42, 0.12);
}

@media (min-width: 768px) {
  .info-card {
    max-width: 360px;
  }
}
</style>
