<!--
  运行概览
  ========
  大屏之外的一页式总览：关键指标 + 最近事件 + 依赖状态。
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import EventCard from '@/components/EventCard.vue'
import StatCard from '@/components/StatCard.vue'
import { officerApi, roadApi } from '@/api'
import { describeError } from '@/api/client'
import { on as onRealtime } from '@/composables/useWebSocket'
import { useEventStore } from '@/stores/event'
import { useSystemStore } from '@/stores/system'
import { useVideoStore } from '@/stores/video'
import { OFFICER_STATUS_COLOR, OFFICER_STATUS_LABEL } from '@/types/api'
import type { Officer, OfficerStats, OfficerUpdateEvent } from '@/types/api'

const systemStore = useSystemStore()
const eventStore = useEventStore()
const videoStore = useVideoStore()

const roadSummary = ref<{ intersectionCount: number; roadCount: number; totalLengthKm: number } | null>(
  null,
)

const stats = computed(() => systemStore.stats)
const health = computed(() => systemStore.health)

// ---- 警力实时统计 ----
const officerStats = ref<OfficerStats | null>(null)
const officers = ref<Officer[]>([])
const officerError = ref('')
let officerTimer: number | undefined
let statsTimer: number | undefined
let offOfficerLocation: (() => void) | undefined

const onDutyTotal = computed(
  () => (officerStats.value?.onDuty ?? 0) + (officerStats.value?.busy ?? 0),
)
const activeWindowMinutes = computed(() =>
  Math.max(1, Math.round((officerStats.value?.locationActiveWindow ?? 300) / 60)),
)
const locatedOfficers = computed(() =>
  officers.value
    .filter((item) => item.latitude && item.longitude)
    .sort((left, right) => String(right.lastSeen ?? '').localeCompare(String(left.lastSeen ?? '')))
    .slice(0, 8),
)

async function loadOfficerData(): Promise<void> {
  try {
    const [list, overview] = await Promise.all([
      officerApi.list({ limit: 200 }),
      officerApi.stats(),
    ])
    officers.value = list.items
    officerStats.value = overview
    officerError.value = ''
  } catch (error) {
    officerError.value = describeError(error).message
  }
}

function applyOfficerLocation(payload: OfficerUpdateEvent): void {
  const index = officers.value.findIndex((item) => item.officerId === payload.officerId)
  if (index >= 0) {
    officers.value[index] = {
      ...officers.value[index],
      latitude: payload.latitude,
      longitude: payload.longitude,
      status: payload.status,
      locationAccuracy: payload.accuracy,
      locationSource: payload.source,
      lastSeen: payload.lastSeen,
    }
  } else {
    void loadOfficerData()
  }

  // 位置事件可能密集到达，统计聚合做一次短防抖，避免每个事件打一次接口。
  if (statsTimer !== undefined) window.clearTimeout(statsTimer)
  statsTimer = window.setTimeout(() => {
    officerApi
      .stats()
      .then((overview) => {
        officerStats.value = overview
      })
      .catch(() => {
        // 实时推送已经更新了列表，统计失败时保留上一次值
      })
  }, 600)
}

function lastSeenText(value?: string | null): string {
  if (!value) return '暂无上报'
  const timestamp = new Date(value).getTime()
  if (!Number.isFinite(timestamp)) return '时间未知'
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000))
  if (seconds < 60) return '刚刚更新'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  return new Date(timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function accuracyText(item: Officer): string {
  return item.locationAccuracy && item.locationAccuracy > 0
    ? `精度 ±${Math.round(item.locationAccuracy)} m`
    : '精度未知'
}

const sourceBadge = computed(() => {
  const health = systemStore.health
  if (!health) return { text: '未知', cls: 'badge-muted' }
  return health.videoSourceAvailable
    ? { text: `可用 · ${health.videoSource}`, cls: 'badge-ok' }
    : { text: `仅接入位 · ${health.videoSource}`, cls: 'badge-warn' }
})

onMounted(async () => {
  void eventStore.load({ limit: 6 })
  void loadOfficerData()
  offOfficerLocation = onRealtime('officer:update', (payload) => {
    applyOfficerLocation(payload as OfficerUpdateEvent)
  })
  // WebSocket 是主通道；定时拉取用于断线重连后的兜底对账。
  officerTimer = window.setInterval(() => void loadOfficerData(), 60_000)

  try {
    roadSummary.value = await roadApi.summary()
  } catch {
    roadSummary.value = null
  }
})

onBeforeUnmount(() => {
  offOfficerLocation?.()
  if (officerTimer !== undefined) window.clearInterval(officerTimer)
  if (statsTimer !== undefined) window.clearTimeout(statsTimer)
})

async function handleReview(id: string, action: 'confirm' | 'reject'): Promise<void> {
  await eventStore.review(id, { action })
}
</script>

<template>
  <div class="page">
    <!-- 关键指标 -->
    <section class="grid stats">
      <StatCard
        label="监控摄像头"
        icon="▦"
        tone="accent"
        :value="stats?.cameraTotal ?? 0"
        :hint="`在线 ${stats?.cameraOnline ?? 0} 路`"
      />
      <StatCard
        label="24h 检测次数"
        icon="◈"
        :value="stats?.detectionTotal ?? 0"
        hint="累计入库检测记录"
      />
      <StatCard
        label="待复核事件"
        icon="⚠"
        :tone="(stats?.eventPending ?? 0) > 0 ? 'danger' : 'ok'"
        :value="stats?.eventPending ?? 0"
        :hint="`事件总数 ${stats?.eventTotal ?? 0}`"
      />
      <StatCard
        label="24h 车流量"
        icon="🚗"
        tone="ok"
        :value="(stats?.vehicleTotal ?? 0).toLocaleString()"
        hint="所有点位累计"
      />
      <StatCard
        label="在岗警力"
        icon="◉"
        tone="ok"
        :value="onDutyTotal"
        :hint="`可派 ${officerStats?.onDuty ?? 0} · 出警中 ${officerStats?.busy ?? 0}`"
      />
      <StatCard
        label="定位活跃"
        icon="⌖"
        :value="officerStats?.locationActive ?? 0"
        :hint="`${activeWindowMinutes} 分钟内上报 · ${officerStats?.withLocation ?? 0} 人有坐标`"
      />
    </section>

    <div class="two-col">
      <!-- 最近事件 -->
      <section class="panel">
        <div class="panel-title">
          <span>⚠ 最近事件</span>
          <RouterLink to="/events" class="more">全部 →</RouterLink>
        </div>

        <div class="event-list scroll-y">
          <div v-if="eventStore.loading && !eventStore.events.length" class="empty">
            <div class="spinner" />
          </div>
          <div v-else-if="!eventStore.events.length" class="empty">
            <div>暂无事件</div>
            <small class="text-faint">系统会在检测到异常时自动生成告警</small>
          </div>
          <EventCard
            v-for="item in eventStore.events"
            :key="item.id"
            :event="item"
            compact
            @review="handleReview"
          />
        </div>
      </section>

      <!-- 系统与数据 -->
      <div class="right-col">
        <section class="panel">
          <div class="panel-title">◎ 依赖状态</div>
          <div class="kv-list">
            <div class="kv">
              <span>后端服务</span>
              <span class="badge" :class="health?.status === 'ok' ? 'badge-ok' : 'badge-warn'">
                {{ health?.status ?? '未连接' }}
              </span>
            </div>
            <div class="kv">
              <span>MongoDB</span>
              <span class="badge" :class="health?.mongo ? 'badge-ok' : 'badge-danger'">
                {{ health?.mongo ? '已连接' : '未连接' }}
              </span>
            </div>
            <div class="kv">
              <span>Redis</span>
              <span class="badge" :class="health?.redis ? 'badge-ok' : 'badge-warn'">
                {{ health?.redis ? '已连接' : '降级运行' }}
              </span>
            </div>
            <div class="kv">
              <span>YOLO 推理</span>
              <span class="badge" :class="health?.yolo ? 'badge-ok' : 'badge-warn'">
                {{ health?.yolo ? '已就绪' : '未安装' }}
              </span>
            </div>
            <div class="kv">
              <span>视频源</span>
              <span class="badge" :class="sourceBadge.cls">{{ sourceBadge.text }}</span>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-title">⇄ 上海路网</div>
          <div class="kv-list">
            <div class="kv">
              <span>路口节点</span>
              <span class="mono">{{ roadSummary?.intersectionCount ?? '—' }}</span>
            </div>
            <div class="kv">
              <span>路段数</span>
              <span class="mono">{{ roadSummary?.roadCount ?? '—' }}</span>
            </div>
            <div class="kv">
              <span>总里程</span>
              <span class="mono">{{ roadSummary?.totalLengthKm ?? '—' }} km</span>
            </div>
            <div class="kv">
              <span>实时监控点位</span>
              <span class="mono">{{ videoStore.cameras.length }}</span>
            </div>
          </div>
          <div class="footnote text-faint">
            视频源位于河南高速，与上海路网分区域展示
          </div>
        </section>
      </div>
    </div>

    <!-- 实时警力位置 -->
    <section class="panel officer-panel">
      <div class="panel-title">
        <span>⌖ 实时警力位置</span>
        <span class="live-tag">● 实时</span>
      </div>

      <div v-if="officerError" class="empty">{{ officerError }}</div>
      <div v-else-if="!locatedOfficers.length" class="empty">
        <div>暂无警员位置</div>
        <small class="text-faint">在「警员管理」中录入坐标或使用当前位置上报</small>
      </div>
      <div v-else class="officer-grid">
        <article v-for="item in locatedOfficers" :key="item.officerId" class="officer-card">
          <div class="officer-head">
            <strong>{{ item.name || item.officerId }}</strong>
            <span class="badge" :style="{ color: OFFICER_STATUS_COLOR[item.status] }">
              ● {{ OFFICER_STATUS_LABEL[item.status] }}
            </span>
          </div>
          <div class="officer-meta">{{ item.unit || '未分配单位' }} · {{ item.officerId }}</div>
          <div class="officer-coords mono">
            {{ item.latitude.toFixed(5) }}, {{ item.longitude.toFixed(5) }}
          </div>
          <div class="officer-foot">
            <span>{{ lastSeenText(item.lastSeen) }}</span>
            <span>{{ accuracyText(item) }}</span>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.stats {
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
}

.two-col {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

@media (max-width: 1100px) {
  .two-col {
    grid-template-columns: 1fr;
  }
}

.event-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  max-height: 560px;
}

.right-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.kv-list {
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.kv {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 0;
  border-bottom: 1px dashed var(--border);
  font-size: 13px;
}
.kv:last-child {
  border-bottom: none;
}

.footnote {
  padding: 0 14px 12px;
  font-size: 11px;
}

.more {
  margin-left: auto;
  color: var(--accent);
  font-size: 12px;
  text-decoration: none;
}

.live-tag {
  margin-left: auto;
  color: var(--ok);
  font-size: 12px;
  font-weight: 600;
}

.officer-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 10px;
  padding: 12px;
}

.officer-card {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 11px 12px;
  background: var(--bg-panel-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.officer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
}

.officer-meta {
  color: var(--text-dim);
  font-size: 11px;
}

.officer-coords {
  color: var(--accent);
  font-size: 12px;
}

.officer-foot {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  color: var(--text-faint);
  font-size: 11px;
}
</style>
