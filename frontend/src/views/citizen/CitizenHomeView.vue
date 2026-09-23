<!--
  路况首页
  ========
  民众打开这一页通常只想知道两件事：**现在能不能走**、**哪里有问题**。

  所以信息按这个顺序排：
    1. 一句整体判断（"部分路段拥堵"）—— 一秒内得到答案
    2. 具体堵在哪（拥堵排行）
    3. 有什么事件（事故、障碍物……）

  刻意不做的几件事：
    · 不显示摄像头编号、不显示 AI 检测快照 —— 后端不会返回，这里也没有
    · 不显示算法置信度 —— 对出行决策没有意义，反而容易被误读
-->
<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'

import { publicApi } from '@/api/citizen'
import { describeError } from '@/api/client'
import CongestionMap from '@/components/citizen/CongestionMap.vue'
import { CITIZEN_CONGESTION_COLOR, CITIZEN_LEVEL_COLOR } from '@/types/citizen'
import { relativeTime } from '@/utils/time'
import type { CongestionRankItem, PublicEvent, PublicOverview } from '@/types/citizen'

/** 视图模式。做成切换而非两个 tab：路况信息一套，展示形式两种。 */
type ViewMode = 'list' | 'map'

const overview = ref<PublicOverview | null>(null)
const events = ref<PublicEvent[]>([])
const rank = ref<CongestionRankItem[]>([])
const loading = ref(true)
const error = ref('')
const viewMode = ref<ViewMode>('list')

// 地图与列表共用同一份数据；切到地图时不必重新请求
const mapRank = computed(() => rank.value)

let timer: number | undefined

/** 整体状态 → 视觉基调。绿/橙/红对应"能走 / 注意 / 有问题" */
const tone = computed(() => {
  const key = overview.value?.overall
  if (key === 'attention') return { color: '#dc2626', bg: '#fef2f2', icon: '!' }
  if (key === 'busy') return { color: '#ea580c', bg: '#fff7ed', icon: '~' }
  return { color: '#16a34a', bg: '#f0fdf4', icon: '✓' }
})

const levelCards = computed(() => {
  const counts = overview.value?.eventCounts ?? {}
  return [
    { label: '严重', value: counts.critical ?? 0, color: CITIZEN_LEVEL_COLOR.critical },
    { label: '注意', value: counts.warning ?? 0, color: CITIZEN_LEVEL_COLOR.warning },
    { label: '提示', value: counts.info ?? 0, color: CITIZEN_LEVEL_COLOR.info },
  ]
})

const congestionCards = computed(() => {
  const counts = overview.value?.congestionCounts ?? {}
  return [
    { label: '畅通', value: counts.normal ?? 0, color: CITIZEN_CONGESTION_COLOR.normal },
    { label: '缓行', value: counts.light ?? 0, color: CITIZEN_CONGESTION_COLOR.light },
    { label: '拥堵', value: counts.moderate ?? 0, color: CITIZEN_CONGESTION_COLOR.moderate },
    { label: '严重拥堵', value: counts.heavy ?? 0, color: CITIZEN_CONGESTION_COLOR.heavy },
  ]
})

async function load(): Promise<void> {
  try {
    const [o, t, r] = await Promise.all([
      publicApi.overview(),
      publicApi.traffic({ limit: 20 }),
      publicApi.rank(6),
    ])
    overview.value = o
    events.value = t.items
    rank.value = r.items
    error.value = ''
  } catch (err) {
    error.value = describeError(err).message
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  // 路况变化以分钟计，30 秒刷新足够；再密只是徒增请求
  timer = window.setInterval(load, 30000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="home">
    <div v-if="loading" class="c-card placeholder">正在获取路况…</div>

    <div v-else-if="error" class="c-card error-box">
      <strong>暂时无法获取路况</strong>
      <p>{{ error }}</p>
      <button class="c-btn" @click="load">重试</button>
    </div>

    <template v-else>
      <!-- 整体状态 -->
      <section
        class="c-card status-card"
        :style="{ background: tone.bg, borderColor: tone.color + '33' }"
      >
        <span class="status-icon" :style="{ background: tone.color }">{{ tone.icon }}</span>
        <div>
          <h2 :style="{ color: tone.color }">{{ overview?.overallLabel }}</h2>
          <p class="status-sub">
            共 {{ overview?.eventTotal ?? 0 }} 条路况事件 ·
            更新于 {{ relativeTime(overview?.generatedAt) }}
          </p>
        </div>
      </section>

      <!-- 事件级别。地图模式下隐藏：这些数字已经在地图内的浮层里了，
           同时在两处展示只会让人怀疑哪一份才是最新的 -->
      <section v-if="viewMode === 'list'" class="stat-row">
        <div v-for="item in levelCards" :key="item.label" class="c-card stat">
          <span class="stat-value" :style="{ color: item.color }">{{ item.value }}</span>
          <span class="stat-label">{{ item.label }}</span>
        </div>
      </section>

      <!-- 拥堵分布（仅列表模式） -->
      <section v-if="viewMode === 'list'" class="c-card">
        <h3 class="c-section-title">路段通行状况</h3>
        <div class="congestion-row">
          <div v-for="item in congestionCards" :key="item.label" class="congestion-item">
            <span class="congestion-dot" :style="{ background: item.color }" />
            <span class="congestion-value">{{ item.value }}</span>
            <span class="congestion-label">{{ item.label }}</span>
          </div>
        </div>
      </section>

      <!-- 视图切换 -->
      <div class="view-switch">
        <button
          class="switch-btn"
          :class="{ active: viewMode === 'list' }"
          @click="viewMode = 'list'"
        >
          ☰ 列表
        </button>
        <button
          class="switch-btn"
          :class="{ active: viewMode === 'map' }"
          @click="viewMode = 'map'"
        >
          ◉ 地图
        </button>
      </div>

      <!--
        地图视图。

        通行状况与事件列表都作为**地图内的浮层**呈现（见 CongestionMap），
        所以这里不再重复渲染外部卡片 —— 同一批数据在两处展示，
        用户还得自己对照“地图上那个红点是列表里哪一条”。
      -->
      <CongestionMap
        v-if="viewMode === 'map'"
        :rank="mapRank"
        :events="events"
        :overview="overview"
      />

      <!-- 拥堵排行 -->
      <section v-if="viewMode === 'list' && rank.length" class="c-card">
        <h3 class="c-section-title">车流最集中的路段</h3>
        <ul class="rank-list">
          <li v-for="(item, index) in rank" :key="index" class="rank-item">
            <span class="rank-no" :class="{ top: index < 3 }">{{ index + 1 }}</span>
            <div class="rank-main">
              <span class="rank-name">{{ item.roadName }}</span>
              <span class="rank-meta">{{ item.vehicleCount }} 辆车</span>
            </div>
            <span
              class="rank-badge"
              :style="{
                color: CITIZEN_CONGESTION_COLOR[item.congestionLevel] ?? '#64748b',
                background: (CITIZEN_CONGESTION_COLOR[item.congestionLevel] ?? '#64748b') + '18',
              }"
            >
              {{ item.congestionLabel }}
            </span>
          </li>
        </ul>
      </section>

      <!-- 事件列表（仅列表模式；地图模式已在浮层内展示） -->
      <section v-if="viewMode === 'list'" class="c-card">
        <h3 class="c-section-title">最新路况事件</h3>

        <div v-if="!events.length" class="empty-inline">
          当前没有需要提醒的路况事件
        </div>

        <ul v-else class="event-list">
          <li v-for="(item, index) in events" :key="index" class="event-item">
            <span
              class="event-mark"
              :style="{ background: CITIZEN_LEVEL_COLOR[item.level] ?? '#64748b' }"
            />
            <div class="event-body">
              <div class="event-head">
                <span class="event-type">{{ item.eventTypeLabel }}</span>
                <span class="event-time">{{ relativeTime(item.createdAt) }}</span>
              </div>
              <p v-if="item.roadName" class="event-road">{{ item.roadName }}</p>
              <p v-if="item.description" class="event-desc">{{ item.description }}</p>
            </div>
          </li>
        </ul>
      </section>

      <!-- 免责声明在两种视图下都保留：它是合规要求，不随展示形式变化 -->
      <p class="disclaimer">
        以上信息来自高速监测点自动采集，可能存在延迟或误差。出行请以实际路况与交管部门发布为准。
      </p>
    </template>
  </div>
</template>

<style scoped>
.home {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.c-card {
  padding: 14px 16px;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--c-radius);
  box-shadow: var(--c-shadow);
}

.placeholder {
  text-align: center;
  color: var(--c-text-faint);
  font-size: 13px;
  padding: 32px 16px;
}

.error-box strong {
  display: block;
  margin-bottom: 4px;
  color: var(--c-danger);
}
.error-box p {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--c-text-dim);
}

.c-section-title {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text-dim);
}

/* ---------------------------------------------------------------- 整体状态 */

.status-card {
  display: flex;
  align-items: center;
  gap: 14px;
  border-width: 1px;
}
.status-icon {
  flex: 0 0 auto;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  color: #fff;
  font-size: 20px;
  font-weight: 700;
}
.status-card h2 {
  margin: 0;
  font-size: 17px;
  line-height: 1.3;
}
.status-sub {
  margin: 3px 0 0;
  font-size: 12px;
  color: var(--c-text-dim);
}

/* ---------------------------------------------------------------- 数字统计 */

.stat-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.stat {
  padding: 12px 8px;
  text-align: center;
}
.stat-value {
  display: block;
  font-size: 24px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.stat-label {
  font-size: 12px;
  color: var(--c-text-faint);
}

.congestion-row {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}
.congestion-item {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
}
.congestion-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex: 0 0 auto;
}
.congestion-value {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.congestion-label {
  color: var(--c-text-dim);
}

/* ---------------------------------------------------------------- 拥堵排行 */

.rank-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.rank-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--c-border);
}
.rank-item:last-child {
  border-bottom: none;
}
.rank-no {
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 7px;
  background: var(--c-surface-2);
  color: var(--c-text-faint);
  font-size: 12px;
  font-weight: 600;
}
.rank-no.top {
  background: var(--c-primary);
  color: #fff;
}
.rank-main {
  flex: 1;
  min-width: 0;
}
.rank-name {
  display: block;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rank-meta {
  font-size: 11px;
  color: var(--c-text-faint);
}
.rank-badge {
  flex: 0 0 auto;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}

/* ---------------------------------------------------------------- 事件列表 */

.event-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.event-item {
  display: flex;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px solid var(--c-border);
}
.event-item:last-child {
  border-bottom: none;
}
.event-mark {
  flex: 0 0 auto;
  width: 3px;
  border-radius: 2px;
}
.event-body {
  flex: 1;
  min-width: 0;
}
.event-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}
.event-type {
  font-size: 13px;
  font-weight: 600;
}
.event-time {
  font-size: 11px;
  color: var(--c-text-faint);
  white-space: nowrap;
}
.event-road {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--c-text-dim);
}
.event-desc {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--c-text-faint);
  line-height: 1.5;
}

.empty-inline {
  padding: 18px 0;
  text-align: center;
  font-size: 13px;
  color: var(--c-text-faint);
}

.disclaimer {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--c-text-faint);
  line-height: 1.6;
  text-align: center;
}

/* -------------------------------------------------------- 列表/地图切换 */

.view-switch {
  display: flex;
  gap: 4px;
  padding: 3px;
  border-radius: 11px;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  box-shadow: var(--c-shadow);
}
.switch-btn {
  flex: 1;
  padding: 8px;
  border: none;
  border-radius: 8px;
  background: none;
  color: var(--c-text-dim);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  /* 移动端点按目标高度 */
  min-height: 38px;
}
.switch-btn.active {
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 600;
}

.c-btn {
  padding: 7px 16px;
  border: 1px solid var(--c-border-strong);
  border-radius: 9px;
  background: var(--c-surface);
  color: var(--c-text);
  font-size: 13px;
  cursor: pointer;
}

@media (min-width: 640px) {
  .congestion-row {
    grid-template-columns: repeat(4, 1fr);
  }
}
</style>
