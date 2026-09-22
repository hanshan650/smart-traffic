<!--
  地图态势
  ========
  把三份数据叠在同一张地图上：

  · **上海路网** —— 按拥堵等级着色（数据来自后端 ``/roads/network``）
  · **实时路况** —— 高德官方路况图层（暗红=极度拥堵，绿=畅通）
  · **监控点位 / 事件** —— 视频源摄像头与未处理事件

  坐标说明：地图为 GCJ-02；河南摄像头点位（110~116°E）与上海（121°E）
  相距较远，因此提供「定位到视频源区域」快捷跳转，两者分区域查看。
-->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { roadApi } from '@/api'
import { useAmap } from '@/composables/useAmap'
import { useEventStore } from '@/stores/event'
import { useSystemStore } from '@/stores/system'
import { useVideoStore } from '@/stores/video'
import { CONGESTION_COLOR, CONGESTION_LABEL } from '@/types/api'
import type { RoadNetwork } from '@/types/api'

const systemStore = useSystemStore()
const videoStore = useVideoStore()
const eventStore = useEventStore()

const { create, destroy, toggleTraffic, loading: amapLoading, error: amapError } = useAmap()

const amap = ref<any>(null)
const network = ref<RoadNetwork | null>(null)
const selected = ref<{ title: string; lines: string[] } | null>(null)

const showRoads = ref(true)
const showCameras = ref(true)
const showEvents = ref(true)
const showTraffic = ref(true)

let roadOverlays: any[] = []
let cameraMarkers: any[] = []
let eventMarkers: any[] = []

const SHANGHAI_CENTER: [number, number] = [121.4737, 31.2304]

// ---------------------------------------------------------------- 绘制

function clear(overlays: any[]): void {
  if (amap.value && overlays.length) {
    amap.value.remove(overlays)
  }
  overlays.length = 0
}

function drawRoads(): void {
  clear(roadOverlays)
  if (!amap.value || !network.value || !showRoads.value) return

  network.value.roads.forEach((road) => {
    const color = CONGESTION_COLOR[road.congestionLevel]
    const line = new amap.value.Polyline({
      path: road.polyline,
      strokeColor: color,
      strokeWeight: 7,
      strokeOpacity: 0.9,
      lineJoin: 'round',
      lineCap: 'round',
      zIndex: 60,
      cursor: 'pointer',
    })

    line.on('click', () => {
      selected.value = {
        title: road.name,
        lines: [
          `编号 ${road.id} · ${road.fromId} → ${road.toId}`,
          `长度 ${(road.lengthM / 1000).toFixed(1)} km · 限速 ${road.speedLimit} km/h · ${road.lanes} 车道`,
          `当前状态 ${CONGESTION_LABEL[road.congestionLevel]}`,
        ],
      }
    })

    roadOverlays.push(line)
  })

  amap.value.add(roadOverlays)
}

function drawCameras(): void {
  clear(cameraMarkers)
  if (!amap.value || !showCameras.value) return

  videoStore.cameras.forEach((cam) => {
    if (!cam.longitude || !cam.latitude) return

    const marker = new amap.value.CircleMarker({
      center: [cam.longitude, cam.latitude],
      radius: 5,
      strokeColor: '#22d3ee',
      strokeWeight: 2,
      fillColor: '#0e7490',
      fillOpacity: 0.85,
      zIndex: 120,
      cursor: 'pointer',
    })

    marker.on('click', () => {
      selected.value = {
        title: cam.cameraName || cam.cameraNum,
        lines: [
          `编号 ${cam.cameraNum}`,
          `区域 ${cam.regionName || '—'} · 路段 ${cam.road || '—'}`,
          cam.online ? '● 在线' : '○ 离线',
        ],
      }
    })

    cameraMarkers.push(marker)
  })

  amap.value.add(cameraMarkers)
}

function drawEvents(): void {
  clear(eventMarkers)
  if (!amap.value || !showEvents.value) return

  // 事件本身没有独立坐标，这里借用摄像头位置标注
  const byCamera = new Map(videoStore.cameras.map((cam) => [cam.cameraNum, cam]))

  eventStore.events
    .filter((event) => event.status === 'pending')
    .slice(0, 30)
    .forEach((event) => {
      const cam = byCamera.get(event.cameraName) ?? byCamera.get(event.cameraId)
      if (!cam?.longitude) return

      const marker = new amap.value.CircleMarker({
        center: [cam.longitude, cam.latitude],
        radius: 9,
        strokeColor: '#ef4444',
        strokeWeight: 2,
        fillColor: '#ef4444',
        fillOpacity: 0.35,
        zIndex: 150,
        cursor: 'pointer',
      })

      marker.on('click', () => {
        selected.value = {
          title: event.title,
          lines: [
            `置信度 ${(event.confidence * 100).toFixed(0)}%`,
            `点位 ${event.cameraName || event.cameraId}`,
            `车辆数 ${event.vehicleCount}`,
          ],
        }
      })

      eventMarkers.push(marker)
    })

  amap.value.add(eventMarkers)
}

function redrawAll(): void {
  drawRoads()
  drawCameras()
  drawEvents()
}

// ---------------------------------------------------------------- 视图控制

function flyToShanghai(): void {
  amap.value?.setZoomAndCenter(12, SHANGHAI_CENTER)
}

function flyToCameras(): void {
  const valid = videoStore.cameras.filter((cam) => cam.longitude && cam.latitude)
  if (!valid.length) return

  const lng = valid.reduce((sum, cam) => sum + cam.longitude, 0) / valid.length
  const lat = valid.reduce((sum, cam) => sum + cam.latitude, 0) / valid.length
  amap.value?.setZoomAndCenter(valid.length === 1 ? 13 : 7, [lng, lat])
}

function onTrafficToggle(): void {
  toggleTraffic(showTraffic.value)
}

// ---------------------------------------------------------------- 生命周期

onMounted(async () => {
  await systemStore.bootstrap()
  if (!videoStore.cameras.length) {
    void videoStore.loadCameras(20, false)
  }
  void eventStore.load({ status: 'pending', limit: 50 })

  try {
    network.value = await roadApi.network()
  } catch {
    network.value = null
  }

  amap.value = await create({
    container: 'map-canvas',
    center: SHANGHAI_CENTER,
    zoom: 12,
  })

  redrawAll()
})

onBeforeUnmount(() => {
  clear(roadOverlays)
  clear(cameraMarkers)
  clear(eventMarkers)
  destroy()
})
</script>

<template>
  <div class="page">
    <!-- 控制栏 -->
    <section class="panel controls">
      <div class="chips">
        <label class="chip">
          <input v-model="showRoads" type="checkbox" @change="redrawAll" />
          上海路网
        </label>
        <label class="chip">
          <input v-model="showCameras" type="checkbox" @change="redrawAll" />
          监控点位 ({{ videoStore.cameras.length }})
        </label>
        <label class="chip">
          <input v-model="showEvents" type="checkbox" @change="redrawAll" />
          未处理事件 ({{ eventStore.pendingEvents.length }})
        </label>
        <label class="chip">
          <input v-model="showTraffic" type="checkbox" @change="onTrafficToggle" />
          高德实时路况
        </label>
      </div>

      <div class="actions">
        <button class="btn btn-sm" @click="flyToShanghai">定位上海市区</button>
        <button class="btn btn-sm" :disabled="!videoStore.cameras.length" @click="flyToCameras">
          定位监控点位
        </button>
      </div>
    </section>

    <div class="body">
      <!-- 地图 -->
      <section class="panel map-wrap">
        <div id="map-canvas" class="map-canvas" />

        <div v-if="amapLoading" class="map-mask">
          <div class="spinner" />
          <div>加载地图…</div>
        </div>

        <div v-else-if="amapError" class="map-mask">
          <div class="mask-icon">⚠</div>
          <div>{{ amapError }}</div>
          <div class="text-faint small">
            在 <code>backend/.env</code> 设置 <code>AMAP_JS_KEY</code> 后刷新
          </div>
        </div>

        <!-- 图例 -->
        <div class="legend">
          <div class="legend-title">路网状态</div>
          <div v-for="(color, level) in CONGESTION_COLOR" :key="level" class="legend-row">
            <span class="swatch" :style="{ background: color }" />
            {{ CONGESTION_LABEL[level as keyof typeof CONGESTION_LABEL] }}
          </div>
        </div>
      </section>

      <!-- 侧栏 -->
      <aside class="side">
        <section class="panel">
          <div class="panel-title">⇄ 路网摘要</div>
          <div class="kv-list">
            <div class="kv">
              <span>路口节点</span>
              <span class="mono">{{ network?.summary.intersectionCount ?? '—' }}</span>
            </div>
            <div class="kv">
              <span>路段总数</span>
              <span class="mono">{{ network?.summary.roadCount ?? '—' }}</span>
            </div>
            <div class="kv">
              <span>总里程</span>
              <span class="mono">{{ network?.summary.totalLengthKm ?? '—' }} km</span>
            </div>
            <div class="kv">
              <span>平均路段长度</span>
              <span class="mono">{{ network?.summary.avgLengthKm ?? '—' }} km</span>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-title">⌗ 选中详情</div>
          <div v-if="selected" class="detail">
            <div class="detail-title">{{ selected.title }}</div>
            <div v-for="(line, index) in selected.lines" :key="index" class="detail-line">
              {{ line }}
            </div>
          </div>
          <div v-else class="detail-empty text-faint">
            点击地图上的路段、摄像头或事件查看详情
          </div>
        </section>

        <section class="panel">
          <div class="panel-title">▦ 视频源</div>
          <div class="kv-list">
            <div class="kv">
              <span>当前源</span>
              <span>{{ videoStore.sourceName || '—' }}</span>
            </div>
            <div class="kv">
              <span>点位总数</span>
              <span class="mono">{{ videoStore.totalAll }}</span>
            </div>
            <div class="kv">
              <span>已加载</span>
              <span class="mono">{{ videoStore.cameras.length }}</span>
            </div>
          </div>
        </section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
}

.controls {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 10px 14px;
}
.chips {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}
.chip {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-dim);
  cursor: pointer;
}
.chip input {
  accent-color: var(--accent);
}
.actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
}

.body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 12px;
  min-height: 0;
  flex: 1;
}
@media (max-width: 1100px) {
  .body {
    grid-template-columns: 1fr;
  }
}

.map-wrap {
  position: relative;
  min-height: 560px;
  overflow: hidden;
}
.map-canvas {
  width: 100%;
  height: 100%;
  min-height: 560px;
}

.map-mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: rgba(7, 11, 20, 0.9);
  color: var(--text-dim);
  font-size: 13px;
  text-align: center;
  padding: 20px;
}
.mask-icon {
  font-size: 26px;
  color: var(--warn);
}
.small {
  font-size: 11px;
}

.legend {
  position: absolute;
  left: 12px;
  bottom: 12px;
  padding: 8px 10px;
  background: rgba(15, 23, 42, 0.9);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 11px;
}

.legend-title {
  color: var(--text-faint);
  margin-bottom: 5px;
}
.legend-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 1px 0;
}
.swatch {
  width: 12px;
  height: 3px;
  border-radius: 2px;
}

.side {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.kv-list {
  padding: 8px 14px 12px;
}
.kv {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 0;
  border-bottom: 1px dashed var(--border);
  font-size: 12px;
}
.kv:last-child {
  border-bottom: none;
}

.detail {
  padding: 10px 14px 14px;
}
.detail-title {
  font-weight: 600;
  margin-bottom: 6px;
  font-size: 13px;
}
.detail-line {
  font-size: 12px;
  color: var(--text-dim);
  padding: 2px 0;
}
.detail-empty {
  padding: 18px 14px;
  font-size: 12px;
}
</style>
