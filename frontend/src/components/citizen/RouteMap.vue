<!--
  行进视角路况地图
  ================
  民众端的主视图。回答一个问题：**我在哪、前面有什么**。

  与警务端 `MapView` 的差别不只是配色：

  |              | 警务端 MapView          | 本组件                       |
  |--------------|-------------------------|------------------------------|
  | 视角         | 俯瞰全局，看所有点位      | 以**用户位置**为中心的街道级 |
  | 图层         | 路网+摄像头+事件+路况     | 我的位置+本路段事件+路段状态  |
  | 数据来源     | 摄像头列表反查坐标        | **事件/快照自带坐标**         |
  | 信息组织     | 按类别分栏               | 按"前方/其他"分层             |

  ---------------------------------------------------------------------------
  两条不能含糊的原则
  ---------------------------------------------------------------------------
  1. **距离是直线估算**，不是沿路里程（没有路网几何，详见 `utils/route.ts`）。
     所以界面上写"约 X 公里"，不写"前方 X 公里"。
  2. **没有定位也要能用**。定位失败是常态（用户不点允许、非 HTTPS、
     室内无信号），这时退回"看全省路况"，而不是把这一页变成错误页。
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { AMAP_STYLE_LIGHT, useAmap } from '@/composables/useAmap'
import { useSystemStore } from '@/stores/system'
import { CITIZEN_CONGESTION_COLOR, CITIZEN_LEVEL_COLOR } from '@/types/citizen'
import { formatDistance } from '@/utils/geo'
import { relativeTime } from '@/utils/time'
import type { GeoFix, GeoState } from '@/composables/useGeolocation'
import type { PublicEvent, CongestionRankItem } from '@/types/citizen'
import type { RouteContext } from '@/utils/route'

const props = defineProps<{
  /** 用户位置（GCJ-02，已转换）。null = 尚未拿到 */
  fix: GeoFix | null
  geoState: GeoState
  geoMessage: string
  route: RouteContext
  rank: CongestionRankItem[]
  /** 可手动选择的路段名 */
  roads: string[]
}>()

const emit = defineEmits<{
  locate: []
  'select-road': [road: string]
}>()

const systemStore = useSystemStore()

const { create, destroy, map, AMap, loading, error } = useAmap()

const containerId = 'citizen-route-map'
const amap = map
const overlays = ref<any[]>([])

/**
 * 是否跟随用户位置。
 *
 * 默认开启，但**用户一旦手动拖动地图就关掉** —— 一边看某条路一边被
 * 拽回当前位置是最招人烦的交互。点定位按钮可以重新开启。
 */
const following = ref(true)

/** 详情卡（点击某个事件点后弹出） */
const selected = ref<{ title: string; lines: string[]; level: string } | null>(null)

/** 底部列表是否展开。移动端占屏幕，给个折叠能力 */
const listOpen = ref(true)

/** 路段选择器是否展开 */
const pickerOpen = ref(false)

const hasFix = computed(() => props.fix !== null)

/**
 * 当前路段的通行状况。
 *
 * 从拥堵排行里按路名找 —— 路况快照和事件是两套数据源，
 * 用路名关联而不是靠坐标就近匹配：同一条高速上可能有好几个监测点，
 * 就近匹配容易把相邻路段的状态算进来。
 */
const currentStatus = computed(() => {
  if (!props.route.currentRoad) return null
  return props.rank.find((item) => item.roadName === props.route.currentRoad) ?? null
})

const statusColor = computed(() => {
  const level = currentStatus.value?.congestionLevel
  return level ? CITIZEN_CONGESTION_COLOR[level] ?? '#64748b' : '#64748b'
})

/** 本路段前方事件。分组规则集中在 utils/route.ts，这里不再过滤一遍 */
const aheadList = computed(() => props.route.ahead)

/** 无坐标事件的数量，用于给出"有几条没显示在地图上"的说明 */
const unlocatedCount = computed(() => props.route.unlocated.length)

const DEFAULT_CENTER: [number, number] = [113.6, 34.0]
const DEFAULT_ZOOM = 7
/** 有定位时的缩放级别：街道级，能看清路口与前后路段 */
const TRACKING_ZOOM = 14

function clearOverlays(): void {
  if (amap.value && overlays.value.length) {
    amap.value.remove(overlays.value)
  }
  overlays.value = []
}

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

/**
 * 事件编号标记的 HTML。
 *
 * 样式必须**内联**：高德会把这段 HTML 插到自己的容器里，
 * 而组件的 scoped 样式带 data-v 属性，选不中这些节点。
 * 用内联样式比"再写一个全局样式块"更不容易漏。
 *
 * 底部加小尖角并把整体上移 32px：事件往往就在用户脚下
 * （"不足 50 米"是常态），若标记压在真实坐标上，
 * 会被 zIndex 更高的"我的位置"蓝点整个盖住 —— 地图上看着什么都没有，
 * 而列表里却列着一条事件。尖角指向真实位置，标记本体浮在上方。
 */
function pinHtml(index: number, color: string): string {
  return (
    '<div style="width:26px;text-align:center;">' +
    `<div style="width:26px;height:26px;border-radius:50%;background:${color};` +
    'color:#fff;display:flex;align-items:center;justify-content:center;' +
    'font-size:13px;font-weight:700;font-family:system-ui,sans-serif;' +
    'border:2px solid #fff;box-sizing:border-box;' +
    `box-shadow:0 2px 8px rgba(15,23,42,.35);">${index}</div>` +
    '<div style="width:0;height:0;margin:-1px auto 0;' +
    'border-left:5px solid transparent;border-right:5px solid transparent;' +
    `border-top:7px solid ${color};"></div>` +
    '</div>'
  )
}

/** 我的位置标记。外层光晕用全局 keyframes 做呼吸效果（见文件末尾的非 scoped 样式块） */
const MY_POSITION_HTML =
  '<div class="map-me"><span class="map-me-pulse"></span><span class="map-me-dot"></span></div>'

function draw(): void {
  if (!amap.value || !AMap.value) return
  clearOverlays()

  const all: any[] = []

  // ---- 1. 路段状态线：同路名的点位连起来 ----
  // 只有同路名 ≥2 个点才连，且相邻点不能太远 —— 否则会画出一条
  // 横跨几百公里的"假路段"，比不画更有误导性
  const byRoad = new Map<string, CongestionRankItem[]>()
  props.rank.forEach((item) => {
    if (!isValidPoint(item.latitude, item.longitude)) return
    const list = byRoad.get(item.roadName) ?? []
    list.push(item)
    byRoad.set(item.roadName, list)
  })

  byRoad.forEach((points) => {
    if (points.length < 2) return
    const path = points.map((p) => [p.longitude as number, p.latitude as number])
    const worst = points.reduce((acc, p) => {
      const order = ['normal', 'light', 'moderate', 'heavy']
      return order.indexOf(p.congestionLevel) > order.indexOf(acc) ? p.congestionLevel : acc
    }, 'normal')
    const color = CITIZEN_CONGESTION_COLOR[worst] ?? '#64748b'
    all.push(
      new AMap.value.Polyline({
        path,
        strokeColor: color,
        strokeWeight: 5,
        strokeOpacity: 0.5,
        lineJoin: 'round',
        zIndex: 100,
        // 路段线不可点：它只是状态底色，点击交给上面的点标记
        clickable: false,
      }),
    )
  })

  // ---- 2. 其他路段的拥堵点：小色点，不抢主体 ----
  props.rank.forEach((item) => {
    if (!isValidPoint(item.latitude, item.longitude)) return
    const color = CITIZEN_CONGESTION_COLOR[item.congestionLevel] ?? '#64748b'
    const marker = new AMap.value.CircleMarker({
      center: [item.longitude, item.latitude],
      radius: 9,
      strokeColor: color,
      strokeWeight: 2,
      fillColor: color,
      fillOpacity: 0.4,
      zIndex: 110,
      cursor: 'pointer',
    })
    marker.on('click', () => selectRank(item))
    all.push(marker)
  })

  // ---- 3. 本路段事件：带编号的大标记，编号对应底部列表 ----
  const numbered = aheadList.value
  numbered.forEach((item, index) => {
    const color = CITIZEN_LEVEL_COLOR[item.event.level] ?? '#64748b'
    const marker = new AMap.value.Marker({
      position: [item.event.longitude as number, item.event.latitude as number],
      content: pinHtml(index + 1, color),
      // 宽 26 居中；高 32（圆 26 + 尖 7 - 叠 1）让尖角底端落在真实坐标上
      offset: new AMap.value.Pixel(-13, -32),
      zIndex: 150,
      cursor: 'pointer',
    })
    marker.on('click', () => selectEvent(item.event, item.distanceKm, index + 1))
    all.push(marker)
  })

  // ---- 4. 其他事件：小空心点 ----
  props.route.offRoute.forEach((item) => {
    if (!isValidPoint(item.event.latitude, item.event.longitude)) return
    const color = CITIZEN_LEVEL_COLOR[item.event.level] ?? '#64748b'
    const marker = new AMap.value.CircleMarker({
      center: [item.event.longitude as number, item.event.latitude as number],
      radius: 8,
      strokeColor: color,
      strokeWeight: 2,
      fillColor: '#ffffff',
      fillOpacity: 0.85,
      zIndex: 130,
      cursor: 'pointer',
    })
    marker.on('click', () => selectEvent(item.event, item.distanceKm, null))
    all.push(marker)
  })

  // ---- 5. 我的位置：精度圈 + 光晕 + 实心点 ----
  if (props.fix) {
    const { lat, lng, accuracy } = props.fix

    // 精度圈：半径单位是米，高德会随缩放自动换算。
    // 只在精度差到值得注意时才画 —— 精度 20 米时那个圈小得像杂质
    if (accuracy > 80) {
      all.push(
        new AMap.value.Circle({
          center: [lng, lat],
          radius: Math.min(accuracy, 2000),
          strokeColor: '#1d4ed8',
          strokeWeight: 1,
          strokeOpacity: 0.5,
          fillColor: '#3b82f6',
          fillOpacity: 0.12,
          zIndex: 190,
          clickable: false,
        }),
      )
    }

    all.push(
      new AMap.value.Marker({
        position: [lng, lat],
        content: MY_POSITION_HTML,
        offset: new AMap.value.Pixel(-14, -14),
        zIndex: 200,
        clickable: false,
      }),
    )
  }

  overlays.value = all
  if (all.length) {
    amap.value.add(all)
  }
}

function selectRank(item: CongestionRankItem): void {
  selected.value = {
    title: item.roadName,
    level: CITIZEN_CONGESTION_COLOR[item.congestionLevel] ?? '#64748b',
    lines: [`通行状况：${item.congestionLabel}`, `监测到 ${item.vehicleCount} 辆车`],
  }
}

function selectEvent(event: PublicEvent, distanceKm: number, index: number | null): void {
  selected.value = {
    title: `${index ? `${index}. ` : ''}${event.eventTypeLabel}${event.roadName ? ` · ${event.roadName}` : ''}`,
    level: CITIZEN_LEVEL_COLOR[event.level] ?? '#64748b',
    lines: [
      formatDistance(distanceKm),
      event.description || '（无补充描述）',
      `${event.levelLabel} · ${relativeTime(event.createdAt)}`,
    ],
  }
}

/** 居中到用户位置并放大到街道级 */
function centerOnUser(animate = true): void {
  if (!amap.value || !props.fix) return
  const [lng, lat] = [props.fix.lng, props.fix.lat]
  if (animate) {
    amap.value.setZoomAndCenter(TRACKING_ZOOM, [lng, lat])
  } else {
    amap.value.setZoomAndCenter(TRACKING_ZOOM, [lng, lat], false)
  }
  following.value = true
}

/**
 * 把视野调整到所有点位（无定位时的兜底）。
 *
 * `setFitView` 第一个参数要的是**覆盖物数组**，不是 `LngLat` 数组 ——
 * 传后者不报错但算不出正确范围，表现为视野只覆盖一部分点位。
 */
let fittedOnce = false
function fitAll(): void {
  if (!amap.value || fittedOnce) return
  if (!overlays.value.length) return
  amap.value.setFitView(overlays.value, false, [70, 70, 70, 70])
  fittedOnce = true
}

/** 首次拿到定位时自动进入跟随；此后位置更新只挪地图，不重置缩放 */
let enteredTracking = false
function maybeEnterTracking(): void {
  if (enteredTracking || !amap.value || !props.fix) return
  centerOnUser(false)
  enteredTracking = true
}

onMounted(async () => {
  if (!systemStore.config) {
    await systemStore.loadConfig()
  }

  await create({
    container: containerId,
    center: DEFAULT_CENTER,
    zoom: DEFAULT_ZOOM,
    mapStyle: AMAP_STYLE_LIGHT,
    showTraffic: true,
  })

  // 用户拖动地图就取消跟随
  amap.value?.on?.('dragstart', () => {
    following.value = false
  })

  draw()
  maybeEnterTracking()
  if (!props.fix) fitAll()
})

watch(
  () => [props.rank, props.route, props.fix],
  () => {
    draw()
    maybeEnterTracking()
    if (!props.fix && !enteredTracking) fitAll()
  },
  { deep: false },
)

/** 位置持续更新时保持跟随（导航的基本预期） */
watch(
  () => props.fix?.at,
  () => {
    if (following.value && props.fix) {
      amap.value?.setCenter?.([props.fix.lng, props.fix.lat])
    }
  },
)

onBeforeUnmount(destroy)

/** 定位按钮：重新定位并恢复跟随 */
function onLocate(): void {
  emit('locate')
  centerOnUser()
}

function pickRoad(road: string): void {
  pickerOpen.value = false
  emit('select-road', road)
}
</script>

<template>
  <div class="map-wrap">
    <div :id="containerId" class="map-canvas" />

    <div v-if="loading" class="map-mask">地图加载中…</div>
    <div v-else-if="error" class="map-mask">
      <div class="mask-title">地图暂不可用</div>
      <div class="mask-detail">{{ error }}</div>
      <div class="mask-hint">路况信息仍可在列表视图查看</div>
    </div>

    <!-- ==================== 顶部：当前路段状态 ==================== -->
    <div class="topbar">
      <div v-if="route.currentRoad" class="road-card">
        <div class="road-line">
          <span class="road-dot" :style="{ background: statusColor }" />
          <strong class="road-name">{{ route.currentRoad }}</strong>
          <span v-if="currentStatus" class="road-status" :style="{ color: statusColor }">
            {{ currentStatus.congestionLabel }}
          </span>
        </div>
        <div class="road-sub">
          <span v-if="aheadList.length">
            前方 {{ aheadList.length }} 个事件 · 最近 {{ formatDistance(aheadList[0].distanceKm) }}
          </span>
          <span v-else>前方暂无事件</span>
          <span v-if="route.source === 'manual'" class="road-tag">手动选择</span>
        </div>
      </div>

      <!-- 没匹配到路段：不猜，引导用户自己选 -->
      <div v-else class="road-card road-empty">
        <div class="road-line">
          <strong>未定位到已覆盖路段</strong>
        </div>
        <div class="road-sub">
          <span v-if="!hasFix">
            {{ geoMessage || '开启定位后可查看你所在路段的前方路况' }}
          </span>
          <span v-else>附近没有监测点，可选择一条路段查看</span>
        </div>
      </div>

      <div class="topbar-actions">
        <button
          v-if="roads.length"
          class="icon-btn"
          :class="{ active: pickerOpen }"
          title="选择路段"
          @click="pickerOpen = !pickerOpen"
        >
          ⇅
        </button>
        <button
          class="icon-btn"
          :class="{ active: following && hasFix, locating: geoState === 'locating' }"
          title="回到我的位置"
          @click="onLocate"
        >
          ◎
        </button>
      </div>
    </div>

    <!-- 路段选择器 -->
    <div v-if="pickerOpen" class="picker">
      <div class="picker-head">
        <span>选择要查看的路段</span>
        <button class="picker-close" @click="pickerOpen = false">✕</button>
      </div>
      <ul class="picker-list">
        <li v-for="road in roads" :key="road">
          <button
            class="picker-item"
            :class="{ active: road === route.currentRoad }"
            @click="pickRoad(road)"
          >
            {{ road }}
          </button>
        </li>
      </ul>
    </div>

    <!-- ==================== 点位详情 ==================== -->
    <div v-if="selected" class="info-card">
      <div class="info-head">
        <span class="info-dot" :style="{ background: selected.level }" />
        <strong>{{ selected.title }}</strong>
        <button class="info-close" @click="selected = null">✕</button>
      </div>
      <p v-for="(line, index) in selected.lines" :key="index" class="info-line">{{ line }}</p>
    </div>

    <!-- ==================== 底部：前方事件 ==================== -->
    <div class="bottom-sheet" :class="{ collapsed: !listOpen }">
      <button class="sheet-head" @click="listOpen = !listOpen">
        <span class="sheet-title">
          前方路况
          <span v-if="aheadList.length" class="sheet-count">{{ aheadList.length }}</span>
        </span>
        <span class="sheet-chevron">{{ listOpen ? '▾' : '▴' }}</span>
      </button>

      <div v-show="listOpen" class="sheet-body">
        <div v-if="!aheadList.length" class="sheet-empty">
          <span v-if="!hasFix">开启定位后显示你前方的事件</span>
          <span v-else-if="!route.currentRoad">请先选择要查看的路段</span>
          <span v-else>当前路段前方暂无事件</span>
        </div>

        <ul v-else class="event-list">
          <li
            v-for="(item, index) in aheadList"
            :key="index"
            class="event-row"
            @click="selectEvent(item.event, item.distanceKm, index + 1)"
          >
            <span
              class="event-no"
              :style="{ background: CITIZEN_LEVEL_COLOR[item.event.level] ?? '#64748b' }"
            >
              {{ index + 1 }}
            </span>
            <div class="event-main">
              <div class="event-row-head">
                <span class="event-type">{{ item.event.eventTypeLabel }}</span>
                <span class="event-dist">{{ formatDistance(item.distanceKm) }}</span>
              </div>
              <p class="event-desc">{{ item.event.description || '（无补充描述）' }}</p>
            </div>
          </li>
        </ul>

        <!-- 其他路段的最近事件：给个参考，不抢主体 -->
        <div v-if="route.offRoute.length" class="other-block">
          <div class="other-title">附近其他路段</div>
          <ul class="other-list">
            <li
              v-for="(item, index) in route.offRoute.slice(0, 3)"
              :key="index"
              class="other-row"
              @click="selectEvent(item.event, item.distanceKm, null)"
            >
              <span
                class="other-dot"
                :style="{ background: CITIZEN_LEVEL_COLOR[item.event.level] ?? '#64748b' }"
              />
              <span class="other-name">{{ item.event.roadName || '位置未知' }}</span>
              <span class="other-meta">{{ item.event.eventTypeLabel }}</span>
              <!-- 没有定位时算不出距离，就不要显示“距离未知”这种残次文案 -->
              <span v-if="route.hasFix" class="other-dist">
                {{ formatDistance(item.distanceKm) }}
              </span>
            </li>
          </ul>
        </div>

        <p v-if="unlocatedCount" class="sheet-note">
          另有 {{ unlocatedCount }} 条事件缺少坐标，未显示在地图上
        </p>

        <!--
          距离口径必须写清楚。写成"前方 3.2 公里"会让人以为是导航软件
          那种沿路里程，而这里只有直线距离可用（没有路网几何）。
        -->
        <p class="sheet-note">
          距离为两点间直线估算，非沿路里程；点位精度约百米
        </p>
      </div>
    </div>
  </div>
</template>

<!--
  非 scoped 块：高德会把 Marker 的 content 插入它自己的容器，
  这些节点不在组件的作用域内，scoped 样式选不中。
  只有这里需要穿透，其余样式仍走 scoped。
-->
<style>
.map-me {
  position: relative;
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
}
.map-me-dot {
  position: relative;
  z-index: 2;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #1d4ed8;
  border: 3px solid #fff;
  box-shadow: 0 1px 6px rgba(29, 78, 216, 0.6);
}
.map-me-pulse {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: rgba(59, 130, 246, 0.45);
  animation: map-me-pulse 1.8s ease-out infinite;
}
@keyframes map-me-pulse {
  0% {
    transform: scale(0.5);
    opacity: 0.7;
  }
  100% {
    transform: scale(1.9);
    opacity: 0;
  }
}
/* 尊重系统的"减少动态效果"设置：前庭功能敏感的用户会因脉冲不适 */
@media (prefers-reduced-motion: reduce) {
  .map-me-pulse {
    animation: none;
  }
}
</style>

<style scoped>
.map-wrap {
  position: relative;
  width: 100%;
  /* 地图是主视图，占满视口剩余空间。
     用 dvh 而非 vh：移动端浏览器地址栏收起时 vh 不会更新，
     底部会露出一截被遮住的地图 */
  height: calc(100dvh - 232px);
  min-height: 460px;
  border-radius: var(--c-radius);
  overflow: hidden;
  border: 1px solid var(--c-border);
  background: var(--c-surface);
}
.map-canvas {
  width: 100%;
  height: 100%;
  /*
    建立独立的层叠上下文。
    高德把自己的 logo ／ 版权控件放在容器内部，并且给了很高的 z-index；
    不隔离的话它会盖到本组件的浮层上面 —— 实测把“晋新高速”那一行
    压得根本读不出来，而 logo 又不是我们的元素，改不动它的层级。
  */
  position: relative;
  z-index: 0;
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
.mask-hint {
  font-size: 11px;
  color: var(--c-text-faint);
}

/* --------------------------------------------------------------- 顶部条 */

.topbar {
  position: absolute;
  top: 10px;
  left: 10px;
  right: 10px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  z-index: 12;
}

.road-card {
  flex: 1;
  min-width: 0;
  padding: 9px 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(6px);
  box-shadow: 0 2px 14px rgba(15, 23, 42, 0.18);
}
.road-empty {
  border-left: 3px solid var(--c-text-faint);
}
.road-line {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.road-dot {
  flex: 0 0 auto;
  width: 9px;
  height: 9px;
  border-radius: 50%;
}
.road-name {
  font-size: 15px;
  color: var(--c-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.road-status {
  flex: 0 0 auto;
  font-size: 12px;
  font-weight: 600;
}
.road-status.muted {
  color: var(--c-text-faint);
  font-weight: 400;
}
.road-sub {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 3px;
  font-size: 11px;
  color: var(--c-text-dim);
}
.road-tag {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-size: 10px;
}

.topbar-actions {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.icon-btn {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(6px);
  box-shadow: 0 2px 14px rgba(15, 23, 42, 0.18);
  color: var(--c-text-dim);
  font-size: 18px;
  cursor: pointer;
}
.icon-btn.active {
  color: var(--c-primary);
}
.icon-btn.locating {
  color: var(--c-primary);
  animation: blink 1s ease-in-out infinite;
}
@keyframes blink {
  50% {
    opacity: 0.45;
  }
}

/* ------------------------------------------------------------- 路段选择 */

.picker {
  position: absolute;
  top: 70px;
  right: 10px;
  width: 200px;
  max-height: 260px;
  display: flex;
  flex-direction: column;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.97);
  backdrop-filter: blur(6px);
  box-shadow: 0 4px 18px rgba(15, 23, 42, 0.22);
  overflow: hidden;
  z-index: 14;
}
.picker-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 12px;
  border-bottom: 1px solid var(--c-border);
  font-size: 12px;
  color: var(--c-text-dim);
}
.picker-close {
  border: none;
  background: none;
  color: var(--c-text-faint);
  cursor: pointer;
  font-size: 12px;
}
.picker-list {
  margin: 0;
  padding: 5px;
  list-style: none;
  overflow-y: auto;
}
.picker-item {
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: none;
  color: var(--c-text);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}
.picker-item:hover {
  background: var(--c-surface-2);
}
.picker-item.active {
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 600;
}

/* ------------------------------------------------------------- 详情卡 */

.info-card {
  position: absolute;
  left: 10px;
  bottom: 10px;
  width: min(300px, calc(100% - 20px));
  padding: 11px 13px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.97);
  backdrop-filter: blur(6px);
  box-shadow: 0 4px 18px rgba(15, 23, 42, 0.22);
  z-index: 15;
}
.info-head {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 14px;
}
.info-head strong {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.info-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.info-close {
  flex: 0 0 auto;
  border: none;
  background: none;
  color: var(--c-text-faint);
  cursor: pointer;
  font-size: 13px;
}
.info-line {
  margin: 5px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--c-text-dim);
}

/* --------------------------------------------------------- 底部事件列表 */

.bottom-sheet {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  /* 46% 是折中：再高就把地图压成一条缝，而“地图尽量放大”是这一页的主诉求 */
  max-height: 46%;
  display: flex;
  flex-direction: column;
  border-radius: 16px 16px 0 0;
  background: rgba(255, 255, 255, 0.97);
  backdrop-filter: blur(8px);
  box-shadow: 0 -3px 20px rgba(15, 23, 42, 0.2);
  z-index: 13;
}
.bottom-sheet.collapsed {
  max-height: none;
}

.sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 11px 14px;
  border: none;
  background: none;
  cursor: pointer;
  /* 移动端点按目标 */
  min-height: 42px;
}
.sheet-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text);
}
.sheet-count {
  min-width: 19px;
  padding: 1px 6px;
  border-radius: 9px;
  background: var(--c-danger);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  text-align: center;
}
.sheet-chevron {
  color: var(--c-text-faint);
  font-size: 12px;
}

.sheet-body {
  padding: 0 14px 12px;
  overflow-y: auto;
}
.sheet-empty {
  padding: 8px 0 10px;
  font-size: 12px;
  color: var(--c-text-faint);
}

.event-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.event-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 9px 0;
  border-top: 1px solid var(--c-border);
  cursor: pointer;
}
.event-no {
  flex: 0 0 auto;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
}
.event-main {
  flex: 1;
  min-width: 0;
}
.event-row-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.event-type {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text);
}
.event-dist {
  flex: 0 0 auto;
  font-size: 12px;
  font-weight: 600;
  color: var(--c-primary);
}
.event-desc {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--c-text-dim);
}

.other-block {
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px dashed var(--c-border-strong);
}
.other-title {
  font-size: 11px;
  color: var(--c-text-faint);
  margin-bottom: 5px;
}
.other-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.other-row {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 5px 0;
  font-size: 12px;
  cursor: pointer;
}
.other-dot {
  flex: 0 0 auto;
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.other-name {
  flex: 0 1 auto;
  min-width: 0;
  color: var(--c-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.other-meta {
  flex: 0 1 auto;
  min-width: 0;
  color: var(--c-text-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.other-dist {
  margin-left: auto;
  flex: 0 0 auto;
  color: var(--c-text-dim);
}

.sheet-note {
  margin: 8px 0 0;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--c-text-faint);
}

/* 窄屏：地图铺满宽度，去侧边框拿回几像素视觉空间 */
@media (max-width: 767px) {
  .map-wrap {
    height: calc(100dvh - 250px);
    min-height: 420px;
    border-radius: 0;
    border-left: none;
    border-right: none;
  }
  .road-card {
    padding: 8px 10px;
  }
  .road-name {
    font-size: 14px;
  }
}
</style>
