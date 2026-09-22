<!--
  运行概览
  ========
  大屏之外的一页式总览：关键指标 + 最近事件 + 依赖状态。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import EventCard from '@/components/EventCard.vue'
import StatCard from '@/components/StatCard.vue'
import { roadApi } from '@/api'
import { useEventStore } from '@/stores/event'
import { useSystemStore } from '@/stores/system'
import { useVideoStore } from '@/stores/video'

const systemStore = useSystemStore()
const eventStore = useEventStore()
const videoStore = useVideoStore()

const roadSummary = ref<{ intersectionCount: number; roadCount: number; totalLengthKm: number } | null>(
  null,
)

const stats = computed(() => systemStore.stats)
const health = computed(() => systemStore.health)

const sourceBadge = computed(() => {
  const health = systemStore.health
  if (!health) return { text: '未知', cls: 'badge-muted' }
  return health.videoSourceAvailable
    ? { text: `可用 · ${health.videoSource}`, cls: 'badge-ok' }
    : { text: `仅接入位 · ${health.videoSource}`, cls: 'badge-warn' }
})

onMounted(async () => {
  void eventStore.load({ limit: 6 })
  try {
    roadSummary.value = await roadApi.summary()
  } catch {
    roadSummary.value = null
  }
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
</style>
