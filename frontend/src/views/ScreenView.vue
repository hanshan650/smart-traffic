<!--
  指挥大屏
  ========
  面向值班室的常驻全屏视图，基准分辨率 1920×1080，按窗口等比缩放。

  布局
  ----
  ┌──────────────────────────────────────────────┐
  │ 标题栏：时钟 / 关键指标                       │
  ├──────────┬────────────────────┬──────────────┤
  │ 实时事件  │  视频墙（2×2）      │ 拥堵排行      │
  │ 流        │                    │ 依赖状态      │
  └──────────┴────────────────────┴──────────────┘

  交互：新告警在中央弹出，值班员可用空格确认、Esc 忽略。
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import VideoTile from '@/components/VideoTile.vue'
import { detectionApi, eventApi } from '@/api'
import { useEventStore } from '@/stores/event'
import { useSystemStore } from '@/stores/system'
import { useVideoStore } from '@/stores/video'
import { buildCameraLabel } from '@/utils/camera'
import {
  CONGESTION_COLOR,
  CONGESTION_LABEL,
  EVENT_LEVEL_LABEL,
  EVENT_TYPE_LABEL,
} from '@/types/api'
import type { CongestionLevel } from '@/types/api'

interface WallCamera {
  cameraNum: string
  label: string
  roadId: string
  streamUrl: string
  detecting: boolean
  totalVehicles: number
  congestionLevel: CongestionLevel
  // 摄像头坐标。检测时顺带上传给后端，让生成的告警能在地图上定位 ——
  // 服务端无法按编号回查摄像头（上游检索接口是按视野随机抽样的）。
  latitude: number
  longitude: number
}

const SCREEN_WIDTH = 1920
const SCREEN_HEIGHT = 1080

const systemStore = useSystemStore()
const videoStore = useVideoStore()
const eventStore = useEventStore()

const scale = ref(1)
const now = ref(new Date())
const wallCameras = ref<WallCamera[]>([])
const rank = ref<{ member: string; score: number }[]>([])
const busy = ref(false)
const hint = ref('')

const stats = computed(() => systemStore.stats)
const health = computed(() => systemStore.health)

const clockText = computed(() =>
  now.value.toLocaleTimeString('zh-CN', { hour12: false }),
)
const dateText = computed(() =>
  now.value.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'long',
  }),
)

const totalVehicles = computed(() =>
  wallCameras.value.reduce((sum, cam) => sum + cam.totalVehicles, 0),
)

const recentEvents = computed(() => eventStore.events.slice(0, 12))

// ---------------------------------------------------------------- 布局缩放

function updateScale(): void {
  scale.value = Math.min(
    window.innerWidth / SCREEN_WIDTH,
    window.innerHeight / SCREEN_HEIGHT,
  )
}

// ---------------------------------------------------------------- 数据装配

async function setupWall(count = 4): Promise<void> {
  if (wallCameras.value.length) return

  const added = await videoStore.loadCameras(count, false)
  if (!added) {
    hint.value = videoStore.error || '无法获取摄像头，请检查网络与视频源配置'
    return
  }

  for (const cam of videoStore.cameras.slice(0, count)) {
    try {
      const streamUrl = await videoStore.getStreamUrl(cam.cameraNum)
      wallCameras.value.push({
        cameraNum: cam.cameraNum,
        label: buildCameraLabel(cam),
        roadId: 'SCREEN',
        streamUrl,
        detecting: false,
        totalVehicles: 0,
        congestionLevel: 'normal',
        latitude: cam.latitude ?? 0,
        longitude: cam.longitude ?? 0,
      })
    } catch {
      // 单路失败不阻断整体
    }
  }

  if (!wallCameras.value.length) {
    hint.value = videoStore.hint || '直播流地址获取失败，请稍后重试'
  } else {
    hint.value = ''
  }
}

async function detectAll(): Promise<void> {
  if (busy.value || !wallCameras.value.length) return
  busy.value = true

  for (const cam of wallCameras.value) {
    cam.detecting = true
    try {
      // 带上摄像头坐标：服务端拿不到「按编号查摄像头」的能力
      // （上游检索接口是按视野随机抽样的），坐标只能由调用方提供。
      // 传过去之后生成的告警才能在地图上定位。
      const result = await detectionApi.snapshot({
        cameraNum: cam.cameraNum,
        roadId: cam.roadId,
        latitude: cam.latitude || undefined,
        longitude: cam.longitude || undefined,
      })
      cam.totalVehicles = result.totalVehicles
      cam.congestionLevel = result.congestionLevel
    } catch {
      // 忽略单路失败，避免打断整轮检测
    } finally {
      cam.detecting = false
    }
  }

  busy.value = false
}

async function loadRank(): Promise<void> {
  try {
    const data = await eventApi.rank(8)
    rank.value = data.items
  } catch {
    rank.value = []
  }
}

// ---------------------------------------------------------------- 人机协同

async function confirmEvent(eventId: string): Promise<void> {
  await eventStore.review(eventId, { action: 'confirm' })
  eventStore.dismissLatest()
}

async function rejectEvent(eventId: string): Promise<void> {
  await eventStore.review(eventId, { action: 'reject' })
  eventStore.dismissLatest()
}

function handleKeydown(event: KeyboardEvent): void {
  const pending = eventStore.latestPending
  if (!pending) return

  if (event.code === 'Space') {
    event.preventDefault()
    void confirmEvent(pending.id)
  } else if (event.code === 'Escape') {
    eventStore.dismissLatest()
  }
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? '--:--:--'
    : date.toLocaleTimeString('zh-CN', { hour12: false })
}

// ---------------------------------------------------------------- 生命周期

let clockTimer: ReturnType<typeof setInterval> | null = null
let detectTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  updateScale()
  window.addEventListener('resize', updateScale)
  window.addEventListener('keydown', handleKeydown)

  await systemStore.bootstrap()
  void eventStore.load({ limit: 50 })
  await setupWall(4)
  await loadRank()
  void detectAll()

  clockTimer = setInterval(() => {
    now.value = new Date()
  }, 1000)

  detectTimer = setInterval(() => {
    void detectAll()
    void loadRank()
    void systemStore.refresh()
  }, 30_000)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateScale)
  window.removeEventListener('keydown', handleKeydown)
  if (clockTimer !== null) clearInterval(clockTimer)
  if (detectTimer !== null) clearInterval(detectTimer)
})
</script>

<template>
  <div class="stage-wrap">
    <div class="stage" :style="{ transform: `scale(${scale})` }">
      <!-- ============ 标题栏 ============ -->
      <header class="header">
        <div class="header-left">
          <span class="brand-mark">ST</span>
          <div class="brand-block">
            <strong>智能交通监测与事故预警平台</strong>
            <small>上海市 · 城市道路与高速路网</small>
          </div>
        </div>

        <div class="metrics">
          <div class="metric">
            <span class="metric-value mono">{{ stats?.cameraTotal ?? 0 }}</span>
            <span class="metric-label">监控点位</span>
          </div>
          <div class="metric">
            <span class="metric-value mono">{{ totalVehicles }}</span>
            <span class="metric-label">当前车流</span>
          </div>
          <div class="metric">
            <span class="metric-value mono accent">{{ stats?.detectionTotal ?? 0 }}</span>
            <span class="metric-label">检测次数</span>
          </div>
          <div class="metric">
            <span
              class="metric-value mono"
              :class="{ danger: (stats?.eventPending ?? 0) > 0 }"
            >
              {{ stats?.eventPending ?? 0 }}
            </span>
            <span class="metric-label">待复核</span>
          </div>
        </div>

        <div class="header-right">
          <div class="clock mono">{{ clockText }}</div>
          <div class="date">{{ dateText }}</div>
        </div>
      </header>

      <!-- ============ 主体三栏 ============ -->
      <div class="body">
        <!-- 左：事件流 -->
        <section class="col col-left">
          <div class="col-title">
            <span>⚠ 实时事件流</span>
            <span class="badge badge-muted">{{ eventStore.total }}</span>
          </div>

          <div class="event-stream scroll-y">
            <div v-if="!recentEvents.length" class="empty-mini">暂无事件</div>

            <article
              v-for="item in recentEvents"
              :key="item.id"
              class="event-row"
              :class="`lv-${item.level}`"
            >
              <div class="event-head">
                <span class="event-type">{{ EVENT_TYPE_LABEL[item.eventType] }}</span>
                <span class="event-time mono">{{ formatTime(item.createdAt) }}</span>
              </div>
              <div class="event-title">{{ item.title }}</div>
              <div class="event-meta">
                <span>{{ item.cameraName || item.cameraId }}</span>
                <span :style="{ color: CONGESTION_COLOR[item.congestionLevel] }">
                  {{ CONGESTION_LABEL[item.congestionLevel] }}
                </span>
                <span class="badge" :class="item.status === 'pending' ? 'badge-warn' : 'badge-ok'">
                  {{ item.status === 'pending' ? '待复核' : '已处理' }}
                </span>
              </div>
            </article>
          </div>
        </section>

        <!-- 中：视频墙 -->
        <section class="col col-center">
          <div class="col-title">
            <span>▦ 实时监控</span>
            <span class="badge badge-info">
              {{ videoStore.sourceName || videoStore.currentSourceKey }}
            </span>
            <span v-if="busy" class="badge badge-warn">检测中…</span>
          </div>

          <div v-if="wallCameras.length" class="wall">
            <VideoTile
              v-for="cam in wallCameras"
              :key="cam.cameraNum"
              :camera-num="cam.cameraNum"
              :label="cam.label"
              :stream-url="cam.streamUrl"
              :detecting="cam.detecting"
              :total-vehicles="cam.totalVehicles"
              :congestion-level="cam.congestionLevel"
              show-labels
            />
          </div>

          <div v-else class="empty-mini tall">
            <div v-if="hint">{{ hint }}</div>
            <div v-else class="spinner" />
          </div>
        </section>

        <!-- 右：排行与状态 -->
        <section class="col col-right">
          <div class="col-title">▲ 拥堵排行</div>
          <div class="rank-list">
            <div v-if="!rank.length" class="empty-mini">暂无排行数据</div>
            <div v-for="(item, index) in rank" :key="item.member" class="rank-row">
              <span class="rank-index" :class="{ top: index < 3 }">{{ index + 1 }}</span>
              <span class="rank-name mono">{{ item.member }}</span>
              <span class="rank-score mono">{{ Math.round(item.score) }}</span>
            </div>
          </div>

          <div class="col-title mt">◎ 依赖状态</div>
          <div class="status-list">
            <div class="status-row">
              <span>服务</span>
              <span class="badge" :class="health?.status === 'ok' ? 'badge-ok' : 'badge-warn'">
                {{ health?.status ?? '未连接' }}
              </span>
            </div>
            <div class="status-row">
              <span>MongoDB</span>
              <span class="badge" :class="health?.mongo ? 'badge-ok' : 'badge-danger'">
                {{ health?.mongo ? '正常' : '异常' }}
              </span>
            </div>
            <div class="status-row">
              <span>Redis</span>
              <span class="badge" :class="health?.redis ? 'badge-ok' : 'badge-warn'">
                {{ health?.redis ? '正常' : '降级' }}
              </span>
            </div>
            <div class="status-row">
              <span>推理模型</span>
              <span class="badge" :class="health?.yolo ? 'badge-ok' : 'badge-warn'">
                {{ health?.yolo ? '就绪' : '未安装' }}
              </span>
            </div>
          </div>
        </section>
      </div>

      <!-- ============ 告警弹窗 ============ -->
      <Transition name="pop">
        <div v-if="eventStore.latestPending" class="alert">
          <div class="alert-top">
            <span class="alert-dot pulse">●</span>
            <strong>{{ EVENT_LEVEL_LABEL[eventStore.latestPending.level] }}级告警</strong>
            <span class="alert-conf mono">
              置信度 {{ (eventStore.latestPending.confidence * 100).toFixed(0) }}%
            </span>
          </div>

          <div class="alert-main">
            <img
              v-if="eventStore.latestPending.snapshotUrl"
              :src="eventStore.latestPending.snapshotUrl"
              class="alert-img"
              alt="事件快照"
            />
            <div class="alert-info">
              <div class="alert-title">{{ eventStore.latestPending.title }}</div>
              <div class="alert-line">
                <span class="k">类型</span>
                {{ EVENT_TYPE_LABEL[eventStore.latestPending.eventType] }}
              </div>
              <div class="alert-line">
                <span class="k">点位</span>
                {{ eventStore.latestPending.cameraName || eventStore.latestPending.cameraId }}
              </div>
              <div class="alert-line">
                <span class="k">车辆</span>
                {{ eventStore.latestPending.vehicleCount }} 辆
              </div>
            </div>
          </div>

          <div class="alert-btns">
            <button class="btn btn-primary" @click="confirmEvent(eventStore.latestPending!.id)">
              ✓ 确认并上报 <kbd>空格</kbd>
            </button>
            <button class="btn btn-danger" @click="rejectEvent(eventStore.latestPending!.id)">
              ✗ 误报
            </button>
            <button class="btn" @click="eventStore.dismissLatest()">稍后 <kbd>Esc</kbd></button>
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.stage-wrap {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-base);
}

.stage {
  width: 1920px;
  height: 1080px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 18px 18px;
  transform-origin: center center;
  background:
    radial-gradient(circle at 20% 0%, rgba(34, 211, 238, 0.08), transparent 45%),
    var(--bg-base);
}

/* ---------------- 标题栏 ---------------- */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 18px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.brand-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-sm);
  background: linear-gradient(135deg, var(--accent), var(--accent-dim));
  color: #06283d;
  font-weight: 800;
  font-size: 15px;
}
.brand-block {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}
.brand-block strong {
  font-size: 17px;
  letter-spacing: 0.5px;
}
.brand-block small {
  color: var(--text-faint);
  font-size: 11px;
}

.metrics {
  display: flex;
  gap: 34px;
}
.metric {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.metric-value {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.15;
}
.metric-value.accent {
  color: var(--accent);
}
.metric-value.danger {
  color: var(--danger);
}
.metric-label {
  font-size: 11px;
  color: var(--text-faint);
}

.header-right {
  text-align: right;
}
.clock {
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 1px;
}
.date {
  font-size: 11px;
  color: var(--text-faint);
}

/* ---------------- 三栏 ---------------- */
.body {
  flex: 1;
  display: grid;
  grid-template-columns: 380px minmax(0, 1fr) 340px;
  gap: 10px;
  min-height: 0;
}

.col {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
}

.col-title {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-weight: 600;
}
.col-title.mt {
  margin-top: 4px;
}

/* 事件流 */
.event-stream {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding-right: 4px;
}
.event-row {
  padding: 9px 11px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-left: 3px solid var(--border-strong);
  border-radius: var(--radius-sm);
}
.event-row.lv-warning {
  border-left-color: var(--warn);
}
.event-row.lv-critical {
  border-left-color: var(--danger);
}
.event-row.lv-info {
  border-left-color: var(--accent);
}
.event-head {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-faint);
}
.event-type {
  color: var(--accent);
}
.event-title {
  margin: 4px 0;
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.event-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
  color: var(--text-dim);
}

/* 视频墙 */
.wall {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
  gap: 10px;
  min-height: 0;
}
.wall :deep(.tile) {
  min-height: 0;
  aspect-ratio: auto;
  height: 100%;
}

/* 排行 */
.rank-list {
  padding: 8px 12px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.rank-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--border);
  font-size: 12px;
}
.rank-row:last-child {
  border-bottom: none;
}
.rank-index {
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border-radius: 4px;
  background: var(--bg-panel-2);
  color: var(--text-faint);
  font-size: 11px;
}
.rank-index.top {
  background: var(--accent-dim);
  color: #06283d;
  font-weight: 700;
}
.rank-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-dim);
}
.rank-score {
  color: var(--warn);
  font-weight: 600;
}

/* 状态 */
.status-list {
  padding: 8px 12px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px dashed var(--border);
  font-size: 12px;
  color: var(--text-dim);
}
.status-row:last-child {
  border-bottom: none;
}

.empty-mini {
  padding: 20px;
  text-align: center;
  color: var(--text-faint);
  font-size: 12px;
}
.empty-mini.tall {
  flex: 1;
  display: grid;
  place-items: center;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

/* ---------------- 告警弹窗 ---------------- */
.alert {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 620px;
  transform: translate(-50%, -50%);
  background: var(--bg-panel);
  border: 2px solid var(--danger);
  border-radius: var(--radius);
  box-shadow: 0 0 60px rgba(239, 68, 68, 0.35);
  overflow: hidden;
  z-index: 100;
}
.alert-top {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 16px;
  background: rgba(239, 68, 68, 0.15);
  font-size: 15px;
}
.alert-dot {
  color: var(--danger);
  font-size: 18px;
}
.alert-conf {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-dim);
}
.alert-main {
  display: flex;
  gap: 14px;
  padding: 14px 16px;
}
.alert-img {
  width: 250px;
  height: 150px;
  object-fit: cover;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: #000;
}
.alert-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.alert-title {
  font-size: 16px;
  font-weight: 700;
}
.alert-line {
  font-size: 13px;
  color: var(--text-dim);
}
.alert-line .k {
  display: inline-block;
  width: 42px;
  color: var(--text-faint);
}
.alert-btns {
  display: flex;
  gap: 10px;
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}
.alert-btns .btn {
  flex: 1;
  padding: 10px;
  font-size: 14px;
}
kbd {
  padding: 1px 5px;
  border: 1px solid var(--border-strong);
  border-radius: 3px;
  font-size: 10px;
}

.pop-enter-active {
  transition: all 0.2s ease;
}
.pop-enter-from {
  opacity: 0;
  transform: translate(-50%, -50%) scale(0.94);
}
</style>
