<!--
  路况首页（行进视角）
  ====================
  民众打开这一页通常只想知道一件事：**我现在这条路上，前面有没有事**。

  所以默认进的是**地图视图**，并且地图是主体 —— 当前位置、所在路段状态、
  前方事件都在地图里，不用去别处对照。列表视图保留给两个场景：
  在地铁上不方便看地图、以及想把所有事件从上到下扫一遍。

  刻意不做的几件事：
    · 不显示摄像头编号、不显示 AI 检测快照 —— 后端不会返回，这里也没有
    · 不显示算法置信度 —— 对出行决策没有意义，反而容易被误读
    · **不假装成真导航**：没有路网几何，算不出沿路里程与转向指引，
      所以只标"约多少公里"，不喊"前方 300 米右转"
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { publicApi } from '@/api/citizen'
import { describeError } from '@/api/client'
import RouteMap from '@/components/citizen/RouteMap.vue'
import { useGeolocation } from '@/composables/useGeolocation'
import { CITIZEN_CONGESTION_COLOR, CITIZEN_LEVEL_COLOR } from '@/types/citizen'
import { formatDistance } from '@/utils/geo'
import { buildRouteContext, roadOptions } from '@/utils/route'
import { relativeTime } from '@/utils/time'
import type { CongestionRankItem, PublicEvent, PublicOverview } from '@/types/citizen'

/** 视图模式。默认地图 —— 这一页的主要用途是"看前面有什么" */
type ViewMode = 'map' | 'list'

const overview = ref<PublicOverview | null>(null)
const events = ref<PublicEvent[]>([])
const rank = ref<CongestionRankItem[]>([])
const loading = ref(true)
const error = ref('')
const viewMode = ref<ViewMode>('map')

/**
 * 用户手动选定的路段。
 *
 * 存在的意义是"自动判定错了时能纠正"。没有定位、或用户根本不在
 * 被监测的路段上时，这是他唯一能主动做的操作。
 */
const manualRoad = ref('')

const { state: geoState, fix: geoFix, message: geoMessage, start: startGeo } =
  useGeolocation()

const roads = computed(() => roadOptions(events.value))

/** 行进路段上下文。地图与列表共用这一份，避免两处各算一遍导致不一致 */
const route = computed(() =>
  buildRouteContext({
    events: events.value,
    fix: geoFix.value
      ? { lat: geoFix.value.lat, lng: geoFix.value.lng, heading: geoFix.value.heading }
      : null,
    manualRoad: manualRoad.value || undefined,
  }),
)

/**
 * 本路段前方事件、已在身后的、以及远处的。
 *
 * 三个分区由 `utils/route.ts` 算好（含 50 公里上限），这里只消费结果 ——
 * 分段规则如果散在模板里，地图与列表两份就很容易不一致。
 */
const aheadEvents = computed(() => route.value.ahead)
const passedEvents = computed(() => route.value.passed)
const farEvents = computed(() => route.value.far)

/** 其他路段的展示条数上限，避免列表无限长 */
const offRouteShown = computed(() => route.value.offRoute.slice(0, 15))

/** 定位精度不可信时给个说明，免得用户把"路段判错了"当成系统故障 */
const accuracyWarning = computed(() => {
  const fix = geoFix.value
  if (!fix) return ''
  if (fix.accuracy > 500) return `定位精度约 ${Math.round(fix.accuracy)} 米，路段判断可能不准`
  return ''
})

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

const currentStatus = computed(() => {
  if (!route.value.currentRoad) return null
  return rank.value.find((item) => item.roadName === route.value.currentRoad) ?? null
})

const statusColor = computed(
  () => CITIZEN_CONGESTION_COLOR[currentStatus.value?.congestionLevel ?? ''] ?? '#64748b',
)

let timer: number | undefined

async function load(): Promise<void> {
  try {
    const [o, t, r] = await Promise.all([
      publicApi.overview(),
      // 取 100 条而不是 20：要按距离排序就得有足够样本，
      // 只取 20 条时远处那条"约 30 公里"的事故可能根本不在结果里
      publicApi.traffic({ limit: 100 }),
      publicApi.rank(10),
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
  // 定位与数据并行，不必等路况回来才请求权限
  startGeo()
  await load()
  timer = window.setInterval(load, 30000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})

function selectRoad(road: string): void {
  manualRoad.value = road
}

/** 回到自动判定 */
function clearManual(): void {
  manualRoad.value = ''
}
</script>

<template>
  <div class="home" :class="{ fill: viewMode === 'map' }">
    <div v-if="loading" class="c-card placeholder">正在获取路况…</div>

    <div v-else-if="error" class="c-card error-box">
      <strong>暂时无法获取路况</strong>
      <p>{{ error }}</p>
      <button class="c-btn" @click="load">重试</button>
    </div>

    <template v-else>
      <!--
        视图切换。
        放在地图上方而不是浮在地图上：地图内已经有顶栏（路段状态）
        和底栏（前方事件），再往里面塞按钮会互相抢位置。
      -->
      <div class="view-switch">
        <button
          class="switch-btn"
          :class="{ active: viewMode === 'map' }"
          @click="viewMode = 'map'"
        >
          ◎ 行进
        </button>
        <button
          class="switch-btn"
          :class="{ active: viewMode === 'list' }"
          @click="viewMode = 'list'"
        >
          ☰ 列表
        </button>
      </div>

      <!-- ============================ 地图视图 ============================ -->
      <RouteMap
        v-if="viewMode === 'map'"
        :fix="geoFix"
        :geo-state="geoState"
        :geo-message="geoMessage"
        :route="route"
        :rank="rank"
        :roads="roads"
        @locate="startGeo"
        @select-road="selectRoad"
      />

      <!-- ============================ 列表视图 ============================ -->
      <template v-else>
        <section
          class="c-card status-card"
          :style="{ background: tone.bg, borderColor: tone.color + '33' }"
        >
          <span class="status-icon" :style="{ background: tone.color }">{{ tone.icon }}</span>
          <div>
            <h2 :style="{ color: tone.color }">{{ overview?.overallLabel }}</h2>
            <p class="status-sub">
              共 {{ overview?.eventTotal ?? 0 }} 条路况事件 · 更新于
              {{ relativeTime(overview?.generatedAt) }}
            </p>
          </div>
        </section>

        <!-- 我所在的路段：列表视图里最高优先级 -->
        <section v-if="route.currentRoad" class="c-card myroad">
          <div class="myroad-head">
            <span class="myroad-dot" :style="{ background: statusColor }" />
            <strong>{{ route.currentRoad }}</strong>
            <span v-if="currentStatus" class="myroad-status" :style="{ color: statusColor }">
              {{ currentStatus.congestionLabel }}
            </span>
            <button
              v-if="route.source === 'manual'"
              class="myroad-reset"
              @click="clearManual"
            >
              恢复自动
            </button>
          </div>
          <p class="myroad-sub">
            <template v-if="aheadEvents.length">
              50 公里内 {{ aheadEvents.length }} 个事件<template v-if="aheadEvents[0]"
                >，最近 {{ formatDistance(aheadEvents[0].distanceKm) }}</template
              >
            </template>
            <template v-else>50 公里内暂无事件</template>
          </p>
          <p v-if="accuracyWarning" class="myroad-warn">{{ accuracyWarning }}</p>
        </section>

        <!-- 定位引导：没定位时不空着，给一个明确的动作 -->
        <section v-else class="c-card geo-card">
          <strong>开启定位可查看前方路况</strong>
          <p>{{ geoMessage || '授权后可自动判断你在哪条路段，并按距离列出前方事件' }}</p>
          <div class="geo-actions">
            <button class="c-btn" @click="startGeo">开启定位</button>
            <button v-if="roads.length" class="c-btn" @click="viewMode = 'map'">
              在地图上选择路段
            </button>
          </div>
        </section>

        <!-- 本路段前方事件 -->
        <section v-if="route.currentRoad" class="c-card">
          <h3 class="c-section-title">本路段前方（50 公里内）</h3>

          <div v-if="!aheadEvents.length" class="empty-inline">当前没有需要提醒的事件</div>

          <ul v-else class="event-list">
            <li v-for="(item, index) in aheadEvents" :key="index" class="event-item">
              <span
                class="event-mark"
                :style="{ background: CITIZEN_LEVEL_COLOR[item.event.level] ?? '#64748b' }"
              />
              <div class="event-body">
                <div class="event-head">
                  <span class="event-type">{{ item.event.eventTypeLabel }}</span>
                  <span class="event-dist">{{ formatDistance(item.distanceKm) }}</span>
                </div>
                <p v-if="item.event.description" class="event-desc">
                  {{ item.event.description }}
                </p>
              </div>
            </li>
          </ul>

          <!-- 已驶过的：不隐藏，掉头回来时有用 -->
          <div v-if="passedEvents.length" class="passed-block">
            <div class="passed-title">已在身后</div>
            <ul class="event-list">
              <li v-for="(item, index) in passedEvents" :key="index" class="event-item passed">
                <span
                  class="event-mark"
                  :style="{ background: CITIZEN_LEVEL_COLOR[item.event.level] ?? '#64748b' }"
                />
                <div class="event-body">
                  <div class="event-head">
                    <span class="event-type">{{ item.event.eventTypeLabel }}</span>
                    <span class="event-dist muted">{{ formatDistance(item.distanceKm) }}</span>
                  </div>
                  <p v-if="item.event.description" class="event-desc">
                    {{ item.event.description }}
                  </p>
                </div>
              </li>
            </ul>
          </div>

          <!--
            同路段但 50 公里以外。单独分区而不是混进"前方"：
            一条高速绵延数百公里，把 200 公里外的事件写成"前方"
            对驾驶决策是误导。
          -->
          <div v-if="farEvents.length" class="passed-block">
            <div class="passed-title">同路段更远处（50 公里以外）</div>
            <ul class="event-list">
              <li v-for="(item, index) in farEvents" :key="index" class="event-item passed">
                <span
                  class="event-mark"
                  :style="{ background: CITIZEN_LEVEL_COLOR[item.event.level] ?? '#64748b' }"
                />
                <div class="event-body">
                  <div class="event-head">
                    <span class="event-type">{{ item.event.eventTypeLabel }}</span>
                    <span class="event-dist muted">{{ formatDistance(item.distanceKm) }}</span>
                  </div>
                  <p v-if="item.event.description" class="event-desc">
                    {{ item.event.description }}
                  </p>
                </div>
              </li>
            </ul>
          </div>
        </section>

        <!--
          其他路段。
          分两个分支而不是在一个循环里做联合类型判断：有定位时元素是
          `EventFix`（带距离），无定位时是裸 `PublicEvent`（只有时间）。
          混在一个列表里写，模板会变成一堆 `'event' in item` 的丑陋判断，
          而且类型保护也失效。
        -->
        <section class="c-card">
          <h3 class="c-section-title">
            {{ route.currentRoad ? '附近其他路段' : '全部路况事件' }}
          </h3>

          <div v-if="!events.length" class="empty-inline">当前没有需要提醒的路况事件</div>

          <ul v-else-if="route.hasFix" class="event-list">
            <li v-for="(item, index) in offRouteShown" :key="index" class="event-item">
              <span
                class="event-mark"
                :style="{ background: CITIZEN_LEVEL_COLOR[item.event.level] ?? '#64748b' }"
              />
              <div class="event-body">
                <div class="event-head">
                  <span class="event-type">{{ item.event.eventTypeLabel }}</span>
                  <span class="event-dist muted">{{ formatDistance(item.distanceKm) }}</span>
                </div>
                <p class="event-road">{{ item.event.roadName }}</p>
              </div>
            </li>
          </ul>

          <ul v-else class="event-list">
            <li v-for="(item, index) in events.slice(0, 15)" :key="index" class="event-item">
              <span
                class="event-mark"
                :style="{ background: CITIZEN_LEVEL_COLOR[item.level] ?? '#64748b' }"
              />
              <div class="event-body">
                <div class="event-head">
                  <span class="event-type">{{ item.eventTypeLabel }}</span>
                  <span class="event-time">{{ relativeTime(item.createdAt) }}</span>
                </div>
                <p class="event-road">{{ item.roadName }}</p>
              </div>
            </li>
          </ul>

          <p v-if="route.unlocated.length" class="unlocated-note">
            另有 {{ route.unlocated.length }} 条事件缺少坐标，无法参与距离排序
          </p>
        </section>

        <section class="stat-row">
          <div v-for="item in levelCards" :key="item.label" class="c-card stat">
            <span class="stat-value" :style="{ color: item.color }">{{ item.value }}</span>
            <span class="stat-label">{{ item.label }}</span>
          </div>
        </section>

        <section class="c-card">
          <h3 class="c-section-title">路段通行状况</h3>
          <div class="congestion-row">
            <div v-for="item in congestionCards" :key="item.label" class="congestion-item">
              <span class="congestion-dot" :style="{ background: item.color }" />
              <span class="congestion-value">{{ item.value }}</span>
              <span class="congestion-label">{{ item.label }}</span>
            </div>
          </div>
        </section>

        <section v-if="rank.length" class="c-card">
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
                  background:
                    (CITIZEN_CONGESTION_COLOR[item.congestionLevel] ?? '#64748b') + '18',
                }"
              >
                {{ item.congestionLabel }}
              </span>
            </li>
          </ul>
        </section>
      </template>

      <!-- 免责声明只在列表视图显示：地图视图里同一条口径说明已经写在
       底部事件栏中，两处都放会重复占用手机屏幕 -->
      <p v-if="viewMode === 'list'" class="disclaimer">
        以上信息来自高速监测点自动采集，可能存在延迟或误差。距离为直线估算，非沿路里程。出行请以实际路况与交管部门发布为准。
      </p>
    </template>
  </div>
</template>

<style scoped>
.home {
  display: flex;
  flex-direction: column;
  gap: clamp(8px, 0.5vw + 6px, 12px);
}

/*
  地图模式：占满 .c-main 的可用高度。

  手机上这一页就是一张地图，页面不该滚动 —— 想拖动地图却把整页
  拖走了是最影响使用的问题。外壳已经锁定一屏高（见 CitizenLayout），
  这里让地图自己撑满剩下的空间，于是连 .c-main 也不会溢出。

  只在窄屏做：宽屏下 .citizen-shell 是 min-height 而非固定高，
  height: 100% 拿不到可用高度，地图会塌成 0。
*/
@media (max-width: 767px) {
  .home.fill {
    height: 100%;
    gap: 8px;
    /* 地图撑满时不应再把外层撑出滚动条 */
    min-height: 0;
  }
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
  font-size: var(--fs-base);
  padding: 32px 16px;
}

.error-box strong {
  display: block;
  margin-bottom: 4px;
  color: var(--c-danger);
}
.error-box p {
  margin: 0 0 10px;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
}

.c-section-title {
  margin: 0 0 10px;
  font-size: var(--fs-md);
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
  font-size: var(--fs-lg);
  font-weight: 700;
}
.status-card h2 {
  margin: 0;
  font-size: var(--fs-lg);
  line-height: 1.3;
}
.status-sub {
  margin: 3px 0 0;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
}

/* ------------------------------------------------------------ 我的路段卡 */

.myroad {
  border-left: 3px solid var(--c-primary);
}
.myroad-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.myroad-dot {
  flex: 0 0 auto;
  width: 9px;
  height: 9px;
  border-radius: 50%;
}
.myroad-head strong {
  font-size: var(--fs-md);
  color: var(--c-text);
}
.myroad-status {
  font-size: var(--fs-sm);
  font-weight: 600;
}
.myroad-reset {
  margin-left: auto;
  padding: 3px 9px;
  border: 1px solid var(--c-border-strong);
  border-radius: 8px;
  background: none;
  color: var(--c-text-dim);
  font-size: var(--fs-xs);
  cursor: pointer;
}
.myroad-sub {
  margin: 5px 0 0;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
}
.myroad-warn {
  margin: 5px 0 0;
  font-size: var(--fs-xs);
  color: var(--c-warn);
}

/* ---------------------------------------------------------------- 定位卡 */

.geo-card strong {
  display: block;
  font-size: var(--fs-md);
  color: var(--c-text);
}
.geo-card p {
  margin: 4px 0 10px;
  font-size: var(--fs-sm);
  line-height: 1.6;
  color: var(--c-text-dim);
}
.geo-actions {
  display: flex;
  gap: 8px;
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
  font-size: var(--fs-xl);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.stat-label {
  font-size: var(--fs-sm);
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
  font-size: var(--fs-sm);
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
  font-size: var(--fs-sm);
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
  font-size: var(--fs-base);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rank-meta {
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}
.rank-badge {
  flex: 0 0 auto;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: var(--fs-xs);
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
/* 已驶过的降不透明度，但仍可读 —— 不是禁用状态 */
.event-item.passed {
  opacity: 0.62;
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
  font-size: var(--fs-base);
  font-weight: 600;
}
.event-time {
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
  white-space: nowrap;
}
.event-dist {
  flex: 0 0 auto;
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--c-primary);
  white-space: nowrap;
}
.event-dist.muted {
  color: var(--c-text-dim);
  font-weight: 400;
}
.event-road {
  margin: 2px 0 0;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
}
.event-desc {
  margin: 2px 0 0;
  font-size: var(--fs-sm);
  color: var(--c-text-faint);
  line-height: 1.5;
}

.passed-block {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--c-border-strong);
}
.passed-title {
  margin-bottom: 2px;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}

.empty-inline {
  padding: 18px 0;
  text-align: center;
  font-size: var(--fs-base);
  color: var(--c-text-faint);
}

.unlocated-note {
  margin: 10px 0 0;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}

.disclaimer {
  margin: 2px 0 0;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
  line-height: 1.6;
  text-align: center;
}

/* -------------------------------------------------------- 视图切换 */

.view-switch {
  display: flex;
  gap: 4px;
  padding: clamp(2px, 0.25vw + 1.5px, 4px);
  border-radius: clamp(9px, 0.4vw + 7.4px, 12px);
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  box-shadow: var(--c-shadow);
}
.switch-btn {
  flex: 1;
  padding: clamp(5px, 0.3vw + 3.8px, 8px);
  border: none;
  border-radius: 8px;
  background: none;
  color: var(--c-text-dim);
  font-size: var(--fs-base);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  /* 手机够矮、桌面手点得中，中间连续过渡 */
  min-height: clamp(30px, 1vw + 26px, 42px);
}
.switch-btn.active {
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 600;
}

.c-btn {
  padding: clamp(6px, 0.3vw + 4.8px, 9px) clamp(13px, 0.8vw + 10px, 18px);
  border: 1px solid var(--c-border-strong);
  border-radius: 9px;
  background: var(--c-surface);
  color: var(--c-text);
  font-size: var(--fs-base);
  cursor: pointer;
  min-height: clamp(32px, 0.6vw + 29.6px, 38px);
}

@media (min-width: 640px) {
  .congestion-row {
    grid-template-columns: repeat(4, 1fr);
  }
}
</style>
