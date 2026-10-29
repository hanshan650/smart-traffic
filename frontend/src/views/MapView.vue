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
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { officerApi, roadApi } from '@/api'
import { mapStyleFor, useAmap, webglAvailable } from '@/composables/useAmap'
import { useRoutePlan } from '@/composables/useRoutePlan'
import { on as onRealtime } from '@/composables/useWebSocket'
import { useEventStore } from '@/stores/event'
import { useSystemStore } from '@/stores/system'
import { useThemeStore } from '@/stores/theme'
import { useVideoStore } from '@/stores/video'
import {
  CONGESTION_COLOR,
  CONGESTION_LABEL,
  OFFICER_STATUS_COLOR,
  OFFICER_STATUS_LABEL,
} from '@/types/api'
import { formatDistance, haversineKm } from '@/utils/geo'
import { formatDuration } from '@/utils/routePlan'
import type { Officer, OfficerUpdateEvent, RoadNetwork, TrafficEvent } from '@/types/api'
import type { RoutePlan } from '@/utils/routePlan'

const systemStore = useSystemStore()
const videoStore = useVideoStore()
const eventStore = useEventStore()
const themeStore = useThemeStore()

// 注意区分两个对象：
//   · `amap` 是 Map **实例**，用于 add / remove / setZoomAndCenter
//   · `AMap` 是 API **命名空间**，用于 new Polyline / new CircleMarker / new LngLat
//
// 曾经误用 `new amap.value.CircleMarker(...)` —— Map 实例上并没有这些
// 构造函数，会抛 “is not a constructor”。这个错误长期未暴露，因为
// 事件与摄像头图层都依赖 `videoStore.cameras`，而它常因上游不可用而为空，
// 循环体从未真正执行到。
const {
  create,
  destroy,
  toggleTraffic,
  setMapStyle,
  map: amap,
  AMap,
  loading: amapLoading,
  error: amapError,
  viewMode,
  viewSwitching,
  switchViewMode,
} = useAmap()
const network = ref<RoadNetwork | null>(null)

/**
 * 选中详情。
 *
 * `event` / `point` 只在点中事件标记时存在 —— 它们是"出警导航"的终点，
 * 路段、摄像头这些没有可导航的语义，不硬凑一个字段。
 */
interface SelectedDetail {
  title: string
  lines: string[]
  point?: [number, number]
  event?: TrafficEvent
}

const selected = ref<SelectedDetail | null>(null)

/**
 * 路网线段默认**不显示**。
 *
 * `road_network` 里每个路段的 `polyline` 只有起点与终点两个点，画出来是直线 ——
 * 延安高架西段从 [121.32, 31.197] 直连 [121.448, 31.225]，会横穿市区，
 * 与真实高架走向完全不符。这种线不只是多余，而是**误导**：
 * 看上去像有一条路从市区直接穿过去，且与底图自带的路况线缠在一起分不清。
 *
 * 保留绘制而不删掉：等路网数据补上真实路径点后，勾选即可正常显示，
 * 不必再改代码。
 */
const showRoads = ref(false)
const showCameras = ref(true)
const showEvents = ref(true)
const showOfficers = ref(true)
const showTraffic = ref(true)

const officers = ref<Officer[]>([])
let offOfficerLocation: (() => void) | undefined

let roadOverlays: any[] = []
let cameraMarkers: any[] = []
let eventMarkers: any[] = []
let officerMarkers: any[] = []
let navOverlays: any[] = []

const SHANGHAI_CENTER: [number, number] = [121.4737, 31.2304]

// ------------------------------------------------------------ 3D 与出警导航

/** WebGL 不可用时让开关停在禁用态，而不是让用户点进一张空白地图 */
const can3D = webglAvailable()
const viewNote = ref('')
let viewNoteTimer: number | undefined

const { search: searchRoute, error: routeError } = useRoutePlan(amap)

const navEvent = ref<TrafficEvent | null>(null)
const navOfficerId = ref('')
const navPlan = ref<RoutePlan | null>(null)
const navBusy = ref(false)
const navNote = ref('')

/** 有坐标的警员才能当导航起点 */
const navigableOfficers = computed(() =>
  officers.value.filter((officer) => officer.latitude && officer.longitude),
)

function officerPoint(officer: Officer): [number, number] {
  return [officer.longitude, officer.latitude]
}

/** 离事故点最近的警员：默认起点，用户可以在下拉里改 */
function nearestOfficer(point: [number, number]): Officer | null {
  let best: Officer | null = null
  let bestKm = Number.POSITIVE_INFINITY
  for (const officer of navigableOfficers.value) {
    const km = haversineKm(point[1], point[0], officer.latitude, officer.longitude)
    if (km < bestKm) {
      bestKm = km
      best = officer
    }
  }
  return best
}

function noteView(text: string): void {
  viewNote.value = text
  if (viewNoteTimer !== undefined) window.clearTimeout(viewNoteTimer)
  viewNoteTimer = window.setTimeout(() => {
    if (viewNote.value === text) viewNote.value = ''
  }, 4200)
}

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
    const line = new AMap.value.Polyline({
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

    const marker = new AMap.value.CircleMarker({
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

  // 优先用事件**自带的坐标**；没有时才回退到按摄像头反查。
  //
  // 回退路径是为历史数据保留的：坐标字段是后加的，早期写入的事件里没有。
  // 但新事件都带坐标，不再依赖 `videoStore.cameras` —— 那个列表要在线
  // 请求上游才能拿到，拉取失败时地图上就一个事件都画不出来。
  const byCamera = new Map(videoStore.cameras.map((cam) => [cam.cameraNum, cam]))

  eventStore.events
    .filter((event) => event.status === 'pending')
    .slice(0, 30)
    .forEach((event) => {
      let center: [number, number] | null = null

      // 0 视为缺省值而非真实位置 —— (0, 0) 在几内亚湾
      if (event.latitude && event.longitude) {
        center = [event.longitude, event.latitude]
      } else {
        const cam = byCamera.get(event.cameraName) ?? byCamera.get(event.cameraId)
        if (cam?.longitude && cam.latitude) {
          center = [cam.longitude, cam.latitude]
        }
      }

      if (!center) return
      const point = center

      const marker = new AMap.value.CircleMarker({
        center: point,
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
          point,
          event,
        }
        // 换目标时旧导航线必须撤掉：它指向的是上一个事故点
        clearNav()
        // 事故点是要"看清楚"的对象：自动切 3D 并压近，而不是停在全市俯瞰
        void focusPoint(point, 16)
      })

      eventMarkers.push(marker)
    })

  amap.value.add(eventMarkers)
}

function lastSeenText(value?: string | null): string {
  if (!value) return '未上报定位'
  const timestamp = new Date(value).getTime()
  if (!Number.isFinite(timestamp)) return '定位时间未知'
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000))
  if (seconds < 60) return '刚刚更新'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  return new Date(timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function drawOfficers(): void {
  clear(officerMarkers)
  if (!amap.value || !showOfficers.value) return

  officers.value
    .filter((officer) => officer.latitude && officer.longitude)
    .forEach((officer) => {
      const color = OFFICER_STATUS_COLOR[officer.status]
      const marker = new AMap.value.CircleMarker({
        center: [officer.longitude, officer.latitude],
        radius: 7,
        strokeColor: color,
        strokeWeight: 2,
        fillColor: color,
        fillOpacity: 0.55,
        zIndex: 170,
        cursor: 'pointer',
      })

      marker.on('click', () => {
        const point: [number, number] = [officer.longitude, officer.latitude]
        selected.value = {
          title: `${officer.name || officer.officerId} · ${OFFICER_STATUS_LABEL[officer.status]}`,
          lines: [
            `${officer.unit || '未分配单位'} · 警号 ${officer.officerId}`,
            `坐标 ${officer.latitude.toFixed(5)}, ${officer.longitude.toFixed(5)}`,
            `位置${lastSeenText(officer.lastSeen)} · 精度 ${
              officer.locationAccuracy ? `±${Math.round(officer.locationAccuracy)} m` : '未知'
            }`,
          ],
          point,
        }
        clearNav()
        // 警员位置同样需要俯视街道级：自动切 3D，才能看清他在哪条路上
        void focusPoint(point, 16)
      })

      officerMarkers.push(marker)
    })

  amap.value.add(officerMarkers)
}

function applyOfficerLocation(payload: OfficerUpdateEvent): void {
  const index = officers.value.findIndex((item) => item.officerId === payload.officerId)
  if (index < 0) return
  officers.value[index] = {
    ...officers.value[index],
    latitude: payload.latitude,
    longitude: payload.longitude,
    status: payload.status,
    locationAccuracy: payload.accuracy,
    locationSource: payload.source,
    lastSeen: payload.lastSeen,
  }
  drawOfficers()
}

function redrawAll(): void {
  drawRoads()
  drawCameras()
  drawEvents()
  drawOfficers()
  drawNav()
}

// ---------------------------------------------------------------- 出警导航

const NAV_START_COLOR = '#22c55e'
const NAV_END_COLOR = '#ef4444'

/** 起终点标记的 HTML。样式必须内联：高德把它插进自己的容器，scoped 选择器够不着 */
function navEndpointHtml(text: string, color: string): string {
  return (
    `<div style="width:22px;height:22px;border-radius:50%;background:${color};color:#fff;` +
    'display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;' +
    'border:2px solid #fff;box-sizing:border-box;box-shadow:0 1px 6px rgba(0,0,0,.45);">' +
    `${text}</div>`
  )
}

/**
 * 画导航线。
 *
 * 线宽与配色按警务端暗色底图选：深色描边 + 青色主线，
 * 压在底图自带的路况色（绿/黄/红）上仍然读得出来。
 */
function drawNav(): void {
  clear(navOverlays)
  if (!amap.value || !AMap.value || !navPlan.value) return

  const path = navPlan.value.path
  const casing = new AMap.value.Polyline({
    path,
    strokeColor: '#0b1220',
    strokeWeight: 9,
    strokeOpacity: 0.9,
    lineJoin: 'round',
    lineCap: 'round',
    zIndex: 180,
    clickable: false,
  })
  const line = new AMap.value.Polyline({
    path,
    strokeColor: '#22d3ee',
    strokeWeight: 5,
    strokeOpacity: 1,
    lineJoin: 'round',
    lineCap: 'round',
    zIndex: 181,
    clickable: false,
    showDir: true,
    dirColor: '#22d3ee',
  })
  const start = new AMap.value.Marker({
    position: path[0],
    content: navEndpointHtml('起', NAV_START_COLOR),
    offset: new AMap.value.Pixel(-11, -11),
    zIndex: 190,
    clickable: false,
  })
  const end = new AMap.value.Marker({
    position: path[path.length - 1],
    content: navEndpointHtml('终', NAV_END_COLOR),
    offset: new AMap.value.Pixel(-11, -11),
    zIndex: 190,
    clickable: false,
  })

  navOverlays = [casing, line, start, end]
  amap.value.add(navOverlays)
  // 上下留白比左右大：控制栏与详情都要占位置，路线不能被压到边缘
  amap.value.setFitView?.(navOverlays, false, [70, 40, 90, 40], 17)
}

let navFitTimer: number | undefined

/**
 * 抬头动画结束后再框路线。
 *
 * 按平视算出来的范围在倾斜后会偏，所以 `setViewModeAnimated` 里不立即 fit，
 * 等 650ms 的动画走完再重算一次视野。
 */
function scheduleNavFit(): void {
  if (navFitTimer !== undefined) window.clearTimeout(navFitTimer)
  navFitTimer = window.setTimeout(() => {
    navFitTimer = undefined
    drawNav()
  }, 700)
}

function clearNav(): void {
  if (navFitTimer !== undefined) {
    window.clearTimeout(navFitTimer)
    navFitTimer = undefined
  }
  clear(navOverlays)
  navEvent.value = null
  navPlan.value = null
  navNote.value = ''
}

/** 从「选中事件」发起导航：起点默认取离事故点最近的警员 */
function startNavigation(): void {
  const target = selected.value
  if (!target?.event || !target.point) return

  navEvent.value = target.event
  navOfficerId.value = nearestOfficer(target.point)?.officerId ?? ''
  navPlan.value = null
  navNote.value = ''
  void planNav()
}

async function planNav(): Promise<void> {
  const event = navEvent.value
  const point = selected.value?.point
  if (!event || !point) return

  const officer =
    navigableOfficers.value.find((item) => item.officerId === navOfficerId.value) ??
    nearestOfficer(point)
  if (!officer) {
    navNote.value = '没有带坐标的警员，无法规划出警路线'
    return
  }

  navOfficerId.value = officer.officerId
  navBusy.value = true
  navNote.value = ''

  try {
    const plan = await searchRoute({
      mode: 'driving',
      origin: officerPoint(officer),
      destination: point,
      destinationName: `${officer.name || officer.officerId} → ${event.title}`,
    })
    if (!plan) {
      navNote.value = routeError.value || '路线规划失败，请稍后重试'
      return
    }
    navPlan.value = plan
    drawNav()
  } finally {
    navBusy.value = false
  }
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

function flyToOfficers(): void {
  const valid = officers.value.filter((item) => item.latitude && item.longitude)
  if (!valid.length) return
  const lng = valid.reduce((sum, item) => sum + item.longitude, 0) / valid.length
  const lat = valid.reduce((sum, item) => sum + item.latitude, 0) / valid.length
  amap.value?.setZoomAndCenter(valid.length === 1 ? 13 : 7, [lng, lat])
}

/**
 * 切到 3D 并压近到某个点。
 *
 * 点击警员 / 事故图标时调用。3D 建不出来就退化为 2D 定位 —— 视角是
 * 锦上添花，定位到点才是这次点击要完成的事。
 */
async function focusPoint(point: [number, number], zoom = 15): Promise<void> {
  if (viewSwitching.value) return

  if (can3D && viewMode.value === '2D') {
    // 中心与缩放随重建一起给出：重建后再调 setZoomAndCenter 会打断抬头动画
    const ok = await switchViewMode('3D', {
      pitch: 52,
      center: point,
      zoom,
      draw: () => redrawAll(),
    })
    if (!ok) {
      noteView('3D 视图创建失败，已退回 2D')
      amap.value?.setZoomAndCenter(zoom, point)
    }
    return
  }

  amap.value?.setZoomAndCenter(zoom, point)
}

/** 2D / 3D 导航视角切换（导航面板与顶部开关共用） */
async function setViewModeAnimated(mode: '2D' | '3D'): Promise<void> {
  if (mode === viewMode.value) return
  if (mode === '3D' && !can3D) {
    noteView('当前设备不支持 3D 视图（WebGL 不可用）')
    return
  }
  const ok = await switchViewMode(mode, {
    pitch: mode === '3D' ? 55 : 0,
    // 重建后所有覆盖物都要重画；drawNav 顺带把视野重新框到路线上
    draw: () => redrawAll(),
  })
  if (mode === '3D' && !ok) noteView('3D 视图创建失败，已退回 2D')
  // 抬头/落平动画结束后再框一次，否则按平视算的范围会偏
  if (navPlan.value) scheduleNavFit()
}

function toggleViewMode(): void {
  if (!can3D) return
  void setViewModeAnimated(viewMode.value === '3D' ? '2D' : '3D')
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
  void officerApi
    .list({ limit: 200 })
    .then((result) => {
      officers.value = result.items
      drawOfficers()
    })
    .catch(() => {
      // 警员图层失败不影响地图其它图层
    })
  offOfficerLocation = onRealtime('officer:update', (payload) => {
    applyOfficerLocation(payload as OfficerUpdateEvent)
  })

  try {
    network.value = await roadApi.network()
  } catch {
    network.value = null
  }

  // 不再给 `amap` 赋值 —— 它现在是 useAmap 内部的 Map 实例 ref，
  // create() 会把它设好，这里重复赋值反而会断开与 composable 的联系
  await create({
    container: 'map-canvas',
    center: SHANGHAI_CENTER,
    zoom: 12,
    mapStyle: mapStyleFor(themeStore.tone),
  })

  redrawAll()
})

// 白天 / 黑夜切换时同步底图样式；高德样式是预设名，不会跟随 CSS 变量
watch(
  () => themeStore.tone,
  (tone) => setMapStyle(mapStyleFor(tone)),
)

onBeforeUnmount(() => {
  offOfficerLocation?.()
  if (viewNoteTimer !== undefined) window.clearTimeout(viewNoteTimer)
  clear(roadOverlays)
  clear(cameraMarkers)
  clear(eventMarkers)
  clear(officerMarkers)
  clear(navOverlays)
  destroy()
})
</script>

<template>
  <div class="page">
    <!-- 控制栏 -->
    <section class="panel controls">
      <div class="chips">
        <label class="chip" title="路段折线目前只有起终点，显示为直线而非真实路形">
          <input v-model="showRoads" type="checkbox" @change="redrawAll" />
          上海路网（示意线）
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
          <input v-model="showOfficers" type="checkbox" @change="redrawAll" />
          警员位置 ({{ officers.filter((item) => item.latitude && item.longitude).length }})
        </label>
        <label class="chip">
          <input v-model="showTraffic" type="checkbox" @change="onTrafficToggle" />
          高德实时路况
        </label>
      </div>

      <div class="actions">
        <!--
          3D 开关。
          与民众端同一个交互：点一下切视图，切换过程有遮罩 + 抬头动画。
          点警员 / 事故图标时会自动打开它，这里保留手动入口方便随时切回 2D。
        -->
        <button
          class="switch-3d"
          :class="{ on: viewMode === '3D', disabled: !can3D }"
          type="button"
          role="switch"
          :aria-checked="viewMode === '3D'"
          :disabled="!can3D"
          :title="can3D ? '切换 2D / 3D 视图' : '当前设备不支持 3D（WebGL 不可用）'"
          @click="toggleViewMode"
        >
          <span class="switch-track"><span class="switch-knob" /></span>
          <span>3D</span>
        </button>
        <span v-if="viewNote" class="view-note">{{ viewNote }}</span>
        <button class="btn btn-sm" @click="flyToShanghai">定位上海市区</button>
        <button class="btn btn-sm" :disabled="!videoStore.cameras.length" @click="flyToCameras">
          定位监控点位
        </button>
        <button
          class="btn btn-sm"
          :disabled="!officers.some((item) => item.latitude && item.longitude)"
          @click="flyToOfficers"
        >
          定位警员
        </button>
      </div>
    </section>

    <div class="body">
      <!-- 地图 -->
      <section class="panel map-wrap">
        <div id="map-canvas" class="map-canvas" />

        <!-- 2D/3D 重建的过场：把"销毁—新建"之间的空档盖住 -->
        <Transition name="map-fade">
          <div v-if="viewSwitching" class="map-fade" />
        </Transition>

        <div v-if="amapLoading && !viewSwitching" class="map-mask">
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

            <!--
              出警导航入口。
              起点取离事故点最近的警员，可在下方下拉里换成别的警员；
              视角在 2D / 3D 之间随时切，切换过程有过渡动画。
            -->
            <div v-if="selected.event && selected.point" class="nav-actions">
              <button class="btn btn-sm btn-primary" :disabled="navBusy" @click="startNavigation">
                → 出警导航
              </button>
              <span class="text-faint small">从最近警员到事故点</span>
            </div>

            <div v-if="navBusy || navNote || navPlan" class="nav-block">
              <div v-if="navBusy" class="text-faint small">正在规划出警路线…</div>
              <p v-else-if="navNote" class="nav-note">{{ navNote }}</p>

              <template v-else-if="navPlan">
                <div class="nav-summary">
                  <span class="mono">{{ formatDistance(navPlan.distanceM / 1000) }}</span>
                  <span class="text-faint">·</span>
                  <span class="mono">{{ formatDuration(navPlan.durationS) }}</span>
                </div>

                <div class="nav-row">
                  <label class="text-faint small">起点</label>
                  <select v-model="navOfficerId" class="select nav-select" @change="planNav">
                    <option v-for="item in navigableOfficers" :key="item.officerId" :value="item.officerId">
                      {{ item.name }} · {{ item.unit }}
                    </option>
                  </select>
                </div>

                <div class="nav-row">
                  <span class="text-faint small">导航视角</span>
                  <div class="nav-modes">
                    <button
                      class="btn btn-sm"
                      :class="{ 'btn-primary': viewMode === '2D' }"
                      @click="setViewModeAnimated('2D')"
                    >
                      2D
                    </button>
                    <button
                      class="btn btn-sm"
                      :class="{ 'btn-primary': viewMode === '3D' }"
                      :disabled="!can3D"
                      :title="can3D ? '3D 导航' : '当前设备不支持 3D（WebGL 不可用）'"
                      @click="setViewModeAnimated('3D')"
                    >
                      3D
                    </button>
                    <button class="btn btn-sm" @click="clearNav">结束导航</button>
                  </div>
                </div>
              </template>
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
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* ------------------------------------------------------------ 3D 开关 */

.switch-3d {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-panel-2);
  color: var(--text-dim);
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.18s, color 0.18s;
}
.switch-3d.on {
  color: var(--accent);
  border-color: var(--accent);
}
.switch-3d.disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.switch-track {
  position: relative;
  width: 28px;
  height: 16px;
  border-radius: 999px;
  background: var(--border-strong);
  transition: background 0.2s;
}
.switch-3d.on .switch-track {
  background: var(--accent);
}
.switch-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.2s;
}
.switch-3d.on .switch-knob {
  transform: translateX(12px);
}
.view-note {
  font-size: 11px;
  color: var(--warn);
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

/* 2D/3D 重建的过场遮罩：用面板底色淡入，盖住容器被清空的那一帧 */
.map-fade {
  position: absolute;
  inset: 0;
  z-index: 20;
  background: var(--bg-panel);
  pointer-events: all;
}
.map-fade-enter-active,
.map-fade-leave-active {
  transition: opacity 0.16s ease;
}
.map-fade-enter-from,
.map-fade-leave-to {
  opacity: 0;
}

.map-mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: var(--overlay);
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
  background: var(--overlay);
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

/* -------------------------------------------------------- 出警导航 */

.nav-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}
.nav-block {
  margin-top: 10px;
  padding: 9px 10px;
  background: var(--bg-panel-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.nav-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text);
  margin-bottom: 6px;
}
.nav-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
.nav-select {
  flex: 1;
  min-width: 0;
}
.nav-modes {
  display: flex;
  gap: 6px;
}
.nav-note {
  margin: 0;
  font-size: 12px;
  color: var(--warn);
}
.detail-empty {
  padding: 18px 14px;
  font-size: 12px;
}
</style>
