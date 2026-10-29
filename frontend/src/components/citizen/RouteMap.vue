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
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import RoutePlanPanel from '@/components/citizen/RoutePlanPanel.vue'
import { mapStyleFor, useAmap } from '@/composables/useAmap'
import { useSystemStore } from '@/stores/system'
import { useThemeStore } from '@/stores/theme'
import { CITIZEN_CONGESTION_COLOR, CITIZEN_LEVEL_COLOR } from '@/types/citizen'
import { formatDistance } from '@/utils/geo'
import { hasPoint } from '@/utils/route'
import {
  eventsAlongRoute,
  formatDuration,
  projectOntoPath,
  travelModeMeta,
} from '@/utils/routePlan'
import { relativeTime } from '@/utils/time'
import type { GeoFix, GeoState } from '@/composables/useGeolocation'
import type { PublicEvent, CongestionRankItem } from '@/types/citizen'
import type { RouteContext } from '@/utils/route'
import type { RoutePlan } from '@/utils/routePlan'

const props = defineProps<{
  /** 用户位置（GCJ-02，已转换）。null = 尚未拿到 */
  fix: GeoFix | null
  geoState: GeoState
  geoMessage: string
  route: RouteContext
  rank: CongestionRankItem[]
  /** 全部事件。规划出路线后要在这里面找"沿途"的那些 */
  events: PublicEvent[]
  /** 可手动选择的路段名 */
  roads: string[]
  /** 数据是否来自演示源。true 时顶部常驻提示条 */
  demo?: boolean
  /**
   * 3D 视图开关。由页面持有而不是组件内部：
   * 它决定了地图的渲染模式，列表视图切回来时不该被重置。
   */
  threeD?: boolean
}>()

const emit = defineEmits<{
  locate: []
  'select-road': [road: string]
  /** 开关已打开但设备实际切不过去（WebGL 被禁用等），页面据此把开关复原 */
  'three-d-unavailable': []
}>()

const systemStore = useSystemStore()
const themeStore = useThemeStore()

const {
  create,
  destroy,
  setMapStyle,
  map,
  AMap,
  loading,
  error,
  viewSwitching,
  switchViewMode,
  setPitch,
  setRotation,
} = useAmap()

const containerId = 'citizen-route-map'
const amap = map
const overlays = ref<any[]>([])

/** 路线规划面板是否展开 */
const plannerOpen = ref(false)

/**
 * 当前路线。`null` 表示没规划 —— 于是整页回到"我在哪条路上"的语义，
 * 底部列表、编号标记、距离口径都跟着切回去。
 *
 * 用 `shallowRef` 而非 `ref`：一条跨城路线有上万个坐标点，`ref` 会为
 * 每个点建一层响应式代理，纯属浪费 —— 这里只会在整体替换时更新。
 */
const plan = shallowRef<RoutePlan | null>(null)

/**
 * 路线图层单独存放。
 *
 * 事件图层每 30 秒随数据重画一次，路线跟着一起重画会闪，
 * 而且每次重画都得重新 `setFitView`，用户刚缩放的视角会被抢走。
 */
let routeOverlays: any[] = []

/** 3D 默认俯仰角：没有路线时按"开车看路"的视角给一个中间值 */
const DEFAULT_3D_PITCH = 52

/**
 * 是否跟随用户位置。
 *
 * 默认开启，但**用户一旦手动拖动地图就关掉** —— 一边看某条路一边被
 * 拽回当前位置是最招人烦的交互。点定位按钮可以重新开启。
 */
const following = ref(true)

/** 详情卡（点击某个事件点后弹出） */
const selected = ref<{ title: string; lines: string[]; level: string } | null>(null)

/** 底部列表是否展开。手机端默认收起成一行摘要 —— 这一页的主体是地图 */
const isNarrow = typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
const listOpen = ref(!isNarrow)

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

/**
 * 底部列表 / 编号标记的数据源。
 *
 * 规划出路线后换成"沿途事件"，距离口径从**直线距离**变成**沿路里程** ——
 * 这是路线规划带来的最大变化，不能只画一条线却继续显示直线距离。
 * 归一成同一种形状，模板里就不用写两套判断。
 */
interface SheetEvent {
  event: PublicEvent
  distanceKm: number
}

const sheetEvents = computed<SheetEvent[]>(() => {
  const current = plan.value
  if (!current) {
    return props.route.ahead.map((item) => ({ event: item.event, distanceKm: item.distanceKm }))
  }

  const meta = travelModeMeta(current.mode)
  const along = eventsAlongRoute(props.events, current.path, meta.corridorM)

  // 没有定位（或定位还没回来）时只能按"距路线起点"的里程显示
  if (!props.fix) {
    return along.map((item) => ({ event: item.event, distanceKm: item.alongKm }))
  }

  /*
    距离必须从**当前所在位置**算，而不是从规划时的起点算。
    否则车开出去十公里，列表还写着"约 12 公里"，越走越不对 ——
    这是导航类界面最容易被立刻发现的一种错误。

    先用用户位置在路线上的投影算出"我已经走了多远"，再相减。
    用户离路线很远（还没上路、或已经绕开）时退回按起点算：
    那种情况下投影点会被夹到路线端点，算出来的相对里程没有意义。
  */
  const mine = projectOntoPath(current.path, props.fix.lng, props.fix.lat)
  if (!Number.isFinite(mine.offsetM) || mine.offsetM > meta.corridorM * 2) {
    return along.map((item) => ({ event: item.event, distanceKm: item.alongKm }))
  }

  return along
    .map((item) => ({ event: item.event, distanceKm: item.alongKm - mine.alongKm }))
    // 已驶过的（留 50 米容差，避免刚经过就被抹掉）不再占"前方"的位置
    .filter((item) => item.distanceKm >= -0.05)
})

/** 无坐标事件的数量，用于给出"有几条没显示在地图上"的说明 */
const unlocatedCount = computed(() => props.route.unlocated.length)

/**
 * 街道级缩放。既是初始视野，也是地图缩放的**下限**。
 *
 * 为什么需要一个下滑：初始化后的视野会直接呈现在用户眼前，从省级（7）起步
 * 即便随后会被 `setFitView` 调整，中间那几帧"一大片无关区域"
 * 也足以让人以为地图加载错了地方。
 *
 * 为什么它同时是下限：`setFitView` 的目标是"容纳全部点位"，
 * 远端点位一多就会退到市区级（实测 6 个跨 4 km 的点位会退到 14，
 * 加入"同路段更远处"的点位会退到 12）—— 地图上只剩几条主干道，
 * 恰好把用户最需要的那部分细节丢掉。缩得比街道级更远时拉回来：
 * 屏幕外的远端点位由下方列表承担，不拿地图细节去换"看得全"。
 *
 * 注意这与警务端相反：那边要"看全所有点位"，所以不能设下限。
 */
const STREET_ZOOM = 15

/**
 * 有定位时的缩放级别：**导航级**。
 *
 * 比街道级再近一档：本页回答的是"我在哪条路、前方哪个路口有情况"。
 * 16 下一屏约 600~800 m，能看到路口与前后路段 —— 这才是行进视角。
 */
const TRACKING_ZOOM = 16

/** 中位数。用中位数而非平均值：平均值会被少数极远的点位拉偏 */
function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

/**
 * 所有带坐标的事件，按"前方 → 已在身后 → 同路段更远处 → 其他路段"排列。
 *
 * `unlocated` 刻意不参与 —— 它们本来就没有坐标。
 */
const pointEvents = computed(() =>
  [
    ...props.route.ahead,
    ...props.route.passed,
    ...props.route.far,
    ...props.route.offRoute,
  ]
    .map((item) => item.event)
    .filter(hasPoint),
)

/**
 * 视野中心：所有点位坐标的中位数。
 *
 * 这里曾直接传河南（视频源所在地）—— 而民众端的路况数据在上海，
 * 结果地图会先在河南渲染一帧，再飞到上海，明显一跳。
 * 没有点位时返回 undefined，交给 `useAmap` 用运行时配置里的上海中心兜底。
 */
const dataCenter = computed<[number, number] | undefined>(() => {
  const points = pointEvents.value
  if (!points.length) return undefined
  return [
    median(points.map((item) => item.longitude)),
    median(points.map((item) => item.latitude)),
  ]
})

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
 *
 * 尺寸必须**从 CSS 变量读**而不是写死：标记的大小得跟界面字号一起变，
 * 否则宽屏下字变大了、圆圈还是手机尺寸，比例就很怪。
 * 内联样式用不了 `var()`（它不在我们的样式上下文里），所以只能由 JS 取出来拼进去。
 *
 * 底部加小尖角并把整体上移，让尖角指向真实坐标：事件往往就在用户脚下
 * （"不足 50 米"是常态），若标记压在真实坐标上，会被 zIndex 更高的
 * "我的位置"蓝点整个盖住 —— 地图上看着什么都没有，而列表里却列着一条事件。
 */
function pinHtml(index: number, color: string, pin: PinStyle): string {
  const tip = pin.tip
  return (
    `<div style="width:${pin.size}px;text-align:center;">` +
    `<div style="width:${pin.size}px;height:${pin.size}px;border-radius:50%;background:${color};` +
    'color:#fff;display:flex;align-items:center;justify-content:center;' +
    `font-size:${pin.font}px;font-weight:700;font-family:system-ui,sans-serif;` +
    'border:2px solid #fff;box-sizing:border-box;' +
    `box-shadow:0 2px 8px rgba(15,23,42,.35);">${index}</div>` +
    `<div style="width:0;height:0;margin:-1px auto 0;` +
    'border-left:5px solid transparent;border-right:5px solid transparent;' +
    `border-top:${tip}px solid ${color};"></div>` +
    '</div>'
  )
}

interface PinStyle {
  size: number
  font: number
  /** 尖角高度 */
  tip: number
  /** Marker 相对坐标的偏移，使尖角底端落在真实位置上 */
  offsetY: number
}

/**
 * 从 CSS 变量读出标记尺寸。
 *
 * 变量定义在 `CitizenLayout` 的 `.citizen-shell` 上，并且随手机会变宽屏
 * 而整档上调 —— 地图标记必须跟着变，否则宽屏下会是"大字配小圈"。
 */
function readPinStyle(): PinStyle {
  const FALLBACK = { size: 26, font: 13 }
  if (typeof document === 'undefined') {
    return { ...FALLBACK, tip: 7, offsetY: -32 }
  }
  const host = document.querySelector('.citizen-shell')
  const cs = host ? getComputedStyle(host) : null
  const size = parseFloat(cs?.getPropertyValue('--pin-size') ?? '') || FALLBACK.size
  const font = parseFloat(cs?.getPropertyValue('--pin-font') ?? '') || FALLBACK.font
  // 尖角按比例缩放，保证放大的只是整体而不只是圆点
  const tip = Math.max(5, Math.round(size * 0.27))
  return { size, font, tip, offsetY: -(size + tip - 1) }
}

/** 我的位置标记。外层光晕用全局 keyframes 做呼吸效果（见文件末尾的非 scoped 样式块） */
const MY_POSITION_HTML =
  '<div class="map-me"><span class="map-me-pulse"></span><span class="map-me-dot"></span></div>'

/**
 * 起终点圆形标记。
 *
 * 同样必须内联样式：高德会把 content 插到自己的容器里，
 * scoped 样式带 data-v 属性选不中。
 */
function endpointHtml(text: string, color: string): string {
  return (
    '<div style="width:26px;height:26px;border-radius:50%;' +
    `background:${color};color:#fff;display:flex;align-items:center;justify-content:center;` +
    'font-size:13px;font-weight:700;font-family:system-ui,sans-serif;' +
    'border:2px solid #fff;box-sizing:border-box;box-shadow:0 2px 8px rgba(15,23,42,.35);">' +
    `${text}</div>`
  )
}

function clearRoute(): void {
  if (amap.value && routeOverlays.length) {
    amap.value.remove(routeOverlays)
  }
  routeOverlays = []
}

/**
 * 画路线。
 *
 * 两层折线（白色描边 + 彩色主线）而不是一条粗线：3D 下底图有建筑阴影，
 * 单色线压在深色路面上会看不清边界，白色描边能稳定描出路线轮廓。
 *
 * `showDir` 必须开：俯仰之后"哪一头是终点"在视觉上完全看不出来，
 * 方向箭头是这个视角下唯一可靠的前进指示。
 */
function drawRoute(): void {
  if (!amap.value || !AMap.value) return
  clearRoute()

  const current = plan.value
  if (!current) return

  const meta = travelModeMeta(current.mode)
  const path = current.path
  if (path.length < 2) return

  const casing = new AMap.value.Polyline({
    path,
    strokeColor: '#ffffff',
    strokeWeight: 10,
    strokeOpacity: 0.9,
    lineJoin: 'round',
    lineCap: 'round',
    zIndex: 140,
    clickable: false,
  })

  const line = new AMap.value.Polyline({
    path,
    strokeColor: meta.color,
    strokeWeight: 6,
    strokeOpacity: 0.95,
    lineJoin: 'round',
    lineCap: 'round',
    zIndex: 141,
    clickable: false,
    showDir: true,
    // 箭头颜色跟着主线走，否则在彩色线上会出现一圈突兀的白箭头
    dirColor: meta.color,
  })

  const start = new AMap.value.Marker({
    position: path[0],
    content: endpointHtml('起', meta.color),
    offset: new AMap.value.Pixel(-13, -13),
    zIndex: 160,
    clickable: false,
  })

  const end = new AMap.value.Marker({
    position: path[path.length - 1],
    content: endpointHtml('终', '#dc2626'),
    offset: new AMap.value.Pixel(-13, -13),
    zIndex: 160,
    clickable: false,
  })

  routeOverlays = [casing, line, start, end]
  amap.value.add(routeOverlays)
}

/**
 * 视野框住整条路线。
 *
 * 3D 下先按出行方式设俯仰角再 fit：`setFitView` 会把俯仰因素一起算进可视范围，
 * 顺序反了会得到一条"框住了但被压扁"的路线。
 *
 * `maxZoom` 给 17：短途路线（比如走到街对面）如果按"刚好填满屏幕"来算，
 * 会放大到 19 级，底图只剩楼块轮廓。
 */
function focusPlan(): void {
  const instance = amap.value
  const current = plan.value
  if (!instance || !current || !routeOverlays.length) return

  // 带过渡：切换出行方式时镜头是"抬/落"到新角度，而不是瞬间跳过去
  if (props.threeD) setPitch(travelModeMeta(current.mode).pitch, 520)

  instance.setFitView?.(routeOverlays, false, [70, 40, 150, 40], 17)
}

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
  // 有路线时编号的是"沿途事件"，否则是"本路段前方事件"（见 sheetEvents）
  const pin = readPinStyle()
  const numbered = sheetEvents.value
  numbered.forEach((item, index) => {
    const color = CITIZEN_LEVEL_COLOR[item.event.level] ?? '#64748b'
    const marker = new AMap.value.Marker({
      position: [item.event.longitude as number, item.event.latitude as number],
      content: pinHtml(index + 1, color, pin),
      // 宽居中；纵向让尖角底端落在真实坐标上
      offset: new AMap.value.Pixel(-pin.size / 2, pin.offsetY),
      zIndex: 150,
      cursor: 'pointer',
    })
    marker.on('click', () => selectEvent(item.event, item.distanceKm, index + 1))
    all.push(marker)
  })

  // ---- 4. 其他事件：小空心点 ----
  // 规划出路线后不再画：这一趟要去哪已经确定，把全城事件都堆上来
  // 只会让路线本身难以辨认
  if (!plan.value) {
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
  }

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
  const center = dataCenter.value
  if (!center) return

  // 不用 `setFitView`：它的目标是"容纳全部点位"，必然向"看得全"妥协
  // （实测 6 个跨 4 km 的点位会退到 zoom 14，加入"更远处"的点位会退到 12）。
  // 它也没有"不要缩得比 X 更远"的参数，第四个 maxZoom 管的是**放大上限**。
  //
  // 这一页要的是街道级细节，所以直接定中心 + 固定缩放；
  // 装不下的远端点位交给下方列表，不拿地图细节去换"看得全"。
  amap.value.setZoomAndCenter(STREET_ZOOM, center)
  fittedOnce = true
}

/** 首次拿到定位时自动进入跟随；此后位置更新只挪地图，不重置缩放 */
let enteredTracking = false
function maybeEnterTracking(): void {
  if (enteredTracking || !amap.value || !props.fix) return
  centerOnUser(false)
  // 3D 下把镜头转向行进方向，"往前看"才成立；
  // heading 为 null（静止/不支持）时不要拿 0 顶上，那会把地图拧成正北
  if (props.threeD && props.fix.heading !== null) setRotation(props.fix.heading)
  enteredTracking = true
}

let resizeTimer: number | undefined

/**
 * 视口变化后重绘。
 *
 * 标记尺寸是从 CSS 变量读出来的，而变量会随断点（手机 ↔ 宽屏）整档变化；
 * 不重绘的话，把窗口从窄拖到宽会出现"字变大了、圆圈还是原尺寸"。
 *
 * 做防抖：拖动窗口会连续触发 resize，每次都重绘整张地图没有必要。
 */
function onResize(): void {
  if (resizeTimer !== undefined) window.clearTimeout(resizeTimer)
  resizeTimer = window.setTimeout(() => {
    draw()
    // 用户已经手动拖过地图、或已经跟随过用户位置时，不要抢回视野
    if (!enteredTracking && !props.fix) fitAll()
  }, 200)
}

/** 每次重建都要重挂的事件（地图实例销毁后监听器不会自动保留） */
function bindMapEvents(): void {
  amap.value?.on?.('dragstart', () => {
    following.value = false
  })
}

/**
 * 2D / 3D 切换。
 *
 * 重建、遮罩与抬头动画都在 `useAmap.switchViewMode` 里（三个页面共用），
 * 这里只做页面级的两件事：重画覆盖物、重挂 dragstart；
 * 以及把"3D 建不出来"回报给页面，让开关弹回去。
 */
let planFocusTimer: number | undefined

async function applyViewMode(enabled: boolean): Promise<void> {
  const pitch = enabled
    ? plan.value
      ? travelModeMeta(plan.value.mode).pitch
      : DEFAULT_3D_PITCH
    : 0

  const ok = await switchViewMode(enabled ? '3D' : '2D', {
    pitch,
    draw: () => {
      draw()
      drawRoute()
    },
    after: () => {
      bindMapEvents()
      // 3D 下镜头跟着车头转；2D 重建后旋转已归零，无需处理
      if (enabled && props.fix && props.fix.heading !== null) {
        setRotation(props.fix.heading)
      }
    },
  })

  if (enabled && !ok) {
    emit('three-d-unavailable')
    if (plan.value) focusPlan()
    return
  }

  /*
    路线要在抬头动画结束后再框。
    按平视（pitch=0）算出来的可视范围，倾斜后会偏 —— 远端被压缩、
    近端出画面；等 650ms 的抬头动画走完再 setFitView，框出来的才是
    用户最终看到的那一屏。
  */
  if (plan.value) {
    if (enabled) {
      if (planFocusTimer !== undefined) window.clearTimeout(planFocusTimer)
      planFocusTimer = window.setTimeout(() => focusPlan(), 700)
    } else {
      focusPlan()
    }
  }
}

onMounted(async () => {
  if (!systemStore.config) {
    await systemStore.loadConfig()
  }

  await create({
    container: containerId,
    center: dataCenter.value,
    zoom: STREET_ZOOM,
    mapStyle: mapStyleFor(themeStore.tone),
    showTraffic: true,
    // 3D 偏好可能来自上次访问（页面持久化），建图时就要按它来，
    // 否则会先渲染一帧 2D 再跳成 3D
    viewMode: props.threeD ? '3D' : '2D',
    pitch: props.threeD
      ? plan.value
        ? travelModeMeta(plan.value.mode).pitch
        : DEFAULT_3D_PITCH
      : 0,
  })

  // 用户拖动地图就取消跟随
  bindMapEvents()

  // 视口尺寸变化后标记尺寸可能跨档（手机 ↔ 宽屏），需要重绘
  window.addEventListener('resize', onResize)

  draw()
  maybeEnterTracking()
  if (!props.fix) fitAll()
})

watch(
  () => [props.rank, props.route, props.fix, props.events],
  () => {
    draw()
    maybeEnterTracking()
    if (!props.fix && !enteredTracking) fitAll()
  },
  { deep: false },
)

/**
 * 路线变化：重画路线 + 重画事件图层（编号与空心点都换了一套）。
 *
 * 清除路线后主动把视野送回"我的位置"：路线没了还停在一个框着整条
 * 路线的远景上，用户会以为自己掉出了覆盖范围。
 */
watch(plan, (current) => {
  drawRoute()
  draw()
  if (current) {
    focusPlan()
  } else if (props.fix) {
    centerOnUser()
  } else {
    fittedOnce = false
    fitAll()
  }
})

/**
 * 3D 开关：只能重建地图（viewMode 是构造参数，见 `applyViewMode`）。
 *
 * 重建失败时会给页面发 `three-d-unavailable`，让开关自己弹回去 ——
 * 一个"开着但没效果"的开关会让用户反复点，最后认定整页都是坏的。
 */
watch(
  () => props.threeD,
  (enabled) => {
    void applyViewMode(enabled)
  },
)

// 主题切换后底图必须一起换：高德样式与 CSS 变量是两套体系
watch(
  () => themeStore.tone,
  (tone) => setMapStyle(mapStyleFor(tone)),
)

/** 位置持续更新时保持跟随（导航的基本预期） */
watch(
  () => props.fix?.at,
  () => {
    if (following.value && props.fix) {
      amap.value?.setCenter?.([props.fix.lng, props.fix.lat])
      // 3D 下镜头跟着车头转，否则"前方"会落到屏幕侧面
      if (props.threeD && props.fix.heading !== null) setRotation(props.fix.heading)
    }
  },
)

onBeforeUnmount(() => {
  if (resizeTimer !== undefined) window.clearTimeout(resizeTimer)
  if (planFocusTimer !== undefined) window.clearTimeout(planFocusTimer)
  window.removeEventListener('resize', onResize)
  clearRoute()
  destroy()
})

/** 定位按钮：重新定位并恢复跟随 */
function onLocate(): void {
  emit('locate')
  centerOnUser()
}

function pickRoad(road: string): void {
  pickerOpen.value = false
  // 手动选路段意味着"我不走这条规划路线了"，两条语义不能同时成立
  plan.value = null
  emit('select-road', road)
}

function togglePlanner(): void {
  plannerOpen.value = !plannerOpen.value
  if (plannerOpen.value) pickerOpen.value = false
}

function togglePicker(): void {
  pickerOpen.value = !pickerOpen.value
  if (pickerOpen.value) plannerOpen.value = false
}

function onPlan(next: RoutePlan | null): void {
  plan.value = next
}
</script>

<template>
  <div class="map-wrap" :class="{ 'has-demo': demo }">
    <div :id="containerId" class="map-canvas" />

    <!--
      2D / 3D 重建时的过场遮罩。
      地图实例必须先销毁再新建，中间有一小段容器是空的；
      用一层底色淡入淡出把它盖住，看起来就是"视角转过去了"。
    -->
    <Transition name="map-fade">
      <div v-if="viewSwitching" class="map-fade" />
    </Transition>

    <!--
      演示数据提示。
      不能只做成一个小标签：这些坐标与描述都是编的，
      一旦和真实路况长得一样，看的人没有任何依据分辨 ——
      拿一个编的位置去规划出行，比看不到内容严重得多。
      所以做成通栏色条，占满宽度、用警告色。
    -->
    <div v-if="demo" class="demo-banner">
      演示数据 · 非真实路况
    </div>

    <!-- 切换 2D/3D 时由 map-fade 负责过场，不要再弹"加载地图" -->
    <div v-if="loading && !viewSwitching" class="map-mask">地图加载中…</div>
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
        <!--
          副标题拆成两个独立 flex item 而不是一个长句子。
          窄屏下卡片只有 180px 左右，长句子会在"…50 米以 / 内"中间
          断掉；拆开后换行发生在两段之间，读起来是完整的。
        -->
        <div class="road-sub">
          <span v-if="aheadList.length">前方 {{ aheadList.length }} 个事件</span>
          <span v-if="aheadList.length">最近 {{ formatDistance(aheadList[0].distanceKm) }}</span>
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
          class="icon-btn"
          :class="{ active: plannerOpen }"
          title="规划路线"
          @click="togglePlanner"
        >
          ⇢
        </button>
        <button
          v-if="roads.length"
          class="icon-btn"
          :class="{ active: pickerOpen }"
          title="选择路段"
          @click="togglePicker"
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

    <!--
      路线规划面板。
      与路段选择器互斥展开：两者都是"我要看哪条路"的入口，
      叠在一起会互相遮挡，而且同时存在时语义是冲突的。
    -->
    <RoutePlanPanel
      v-if="plannerOpen"
      :amap="AMap"
      :fix="fix"
      :plan="plan"
      @plan="onPlan"
      @locate="emit('locate')"
      @close="plannerOpen = false"
    />

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
          {{ plan ? '沿途路况' : '前方路况' }}
          <span v-if="sheetEvents.length" class="sheet-count">{{ sheetEvents.length }}</span>
        </span>
        <!--
          收起时直接给出最关键的一条，而不是只留一个标题。
          只看标题的话用户必须先展开才知道有没有事 —— 而"前面有没有事"
          正是这一页要回答的第一个问题。
        -->
        <span v-if="!listOpen && sheetEvents.length" class="sheet-summary">
          {{ sheetEvents[0].event.eventTypeLabel }} ·
          {{ formatDistance(sheetEvents[0].distanceKm) }}
        </span>
        <span v-else-if="!listOpen" class="sheet-summary muted">暂无事件</span>
        <span class="sheet-chevron">{{ listOpen ? '▾' : '▴' }}</span>
      </button>

      <div v-show="listOpen" class="sheet-body">
        <!--
          路线摘要。
          地图上已经画了线，这里只补"多远、多久、哪种方式" ——
          路线本身不重复叙述，底部列表留给"沿途有什么"。
        -->
        <div
          v-if="plan"
          class="plan-bar"
          :style="{ borderLeftColor: travelModeMeta(plan.mode).color }"
        >
          <span class="plan-mode" :style="{ color: travelModeMeta(plan.mode).color }">
            {{ travelModeMeta(plan.mode).label }}
          </span>
          <span class="plan-dest">{{ plan.destinationName }}</span>
          <span class="plan-meta">
            {{ formatDistance(plan.distanceM / 1000) }} · {{ formatDuration(plan.durationS) }}
          </span>
        </div>

        <div v-if="!sheetEvents.length" class="sheet-empty">
          <span v-if="plan">这条路线沿途暂无事件</span>
          <span v-else-if="!hasFix">开启定位后显示你前方的事件</span>
          <span v-else-if="!route.currentRoad">请先选择要查看的路段</span>
          <span v-else>当前路段前方暂无事件</span>
        </div>

        <ul v-else class="event-list">
          <li
            v-for="(item, index) in sheetEvents"
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

        <!-- 其他路段的最近事件：给个参考，不抢主体。有路线时这一趟已经确定，不再罗列 -->
        <div v-if="!plan && route.offRoute.length" class="other-block">
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
          距离口径必须写清楚。没有路线时是直线距离，有路线时是沿路投影 ——
          两种数字看起来一样，含义完全不同，混着写等于什么都没说。
        -->
        <p class="sheet-note">
          <template v-if="plan">
            距离为沿规划路线投影估算，横向
            {{ travelModeMeta(plan.mode).corridorM }} 米内视为沿途；未计入实时拥堵绕行
          </template>
          <template v-else>
            距离为两点间直线估算，非沿路里程；点位精度约百米
          </template>
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
  /*
    提示条高度参与顶部浮层的定位。
    用变量而不是给 .topbar 写两条规则：后者要在两个地方各维护一份边距，
    改一处忘一处就会出现提示条压住路段卡。
  */
  --banner-h: 0px;
  /*
    地图是主视图，占满视口剩余空间。
    用 dvh 而非 vh：移动端浏览器地址栏收起时 vh 不会更新，
    底部会露出一截被遮住的地图。

    减去的外壳高度也随视口缩放（头部内边距是 clamp 的），
    写死 232 会在中间尺寸上要么留白、要么溢出。
  */
  height: calc(100dvh - clamp(190px, 9vw + 155px, 232px));
  min-height: clamp(420px, 30vh + 180px, 520px);
  border-radius: var(--c-radius);
  overflow: hidden;
  border: 1px solid var(--c-border);
  background: var(--c-surface);
}

@media (max-width: 767px) {
  /*
    手机端改为“撑满剩余空间”而不是算一个固定高度。
    外壳已经把高度锁成一屏（见 CitizenLayout），这里 flex 撑开即可，
    于是页面上不会出现滚动条 —— 拖地图时不会把整页拖走。
    min-height 必须归零，否则 flex item 会拒绝收缩到内容高度以下。
  */
  .map-wrap {
    height: auto;
    flex: 1;
    min-height: 0;
  }
}

.map-wrap.has-demo {
  --banner-h: 24px;
}

.demo-banner {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 14;
  padding: 4px 10px;
  background: #f59e0b;
  color: #fff;
  font-size: var(--fs-xs);
  font-weight: 600;
  text-align: center;
  letter-spacing: 0.3px;
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

/* 2D/3D 重建的过场遮罩：盖住地图，但压在顶部卡与底部列表之下 */
.map-fade {
  position: absolute;
  inset: 0;
  z-index: 6;
  background: var(--c-surface);
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
  gap: 6px;
  background: var(--c-surface-2);
  color: var(--c-text-faint);
  font-size: var(--fs-base);
  text-align: center;
  padding: 20px;
}
.mask-title {
  font-size: var(--fs-md);
  color: var(--c-text-dim);
}
.mask-detail {
  font-size: var(--fs-xs);
  line-height: 1.6;
  max-width: 300px;
}
.mask-hint {
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}

/* --------------------------------------------------------------- 顶部条 */

.topbar {
  position: absolute;
  top: calc(var(--banner-h) + clamp(8px, 0.4vw + 6.4px, 10px));
  left: clamp(8px, 0.4vw + 6.4px, 10px);
  right: clamp(8px, 0.4vw + 6.4px, 10px);
  display: flex;
  align-items: flex-start;
  gap: clamp(6px, 0.3vw + 5px, 8px);
  z-index: 12;
}

.road-card {
  flex: 1;
  min-width: 0;
  /* 内外边距都随视口连续变，不再随断点跳变 */
  padding: clamp(6px, 0.4vw + 4.4px, 10px) clamp(9px, 0.5vw + 7px, 13px);
  border-radius: clamp(9px, 0.4vw + 7.4px, 12px);
  background: var(--c-overlay);
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
  font-size: var(--fs-md);
  color: var(--c-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.road-status {
  flex: 0 0 auto;
  font-size: var(--fs-sm);
  font-weight: 600;
}
.road-status.muted {
  color: var(--c-text-faint);
  font-weight: 400;
}
.road-sub {
  display: flex;
  align-items: center;
  /*
    间距兼作分隔符。
    不用“·”是因为它也是个 flex item，可能被单独挤到一行去。
    允许整体换行而不是在 span 内部断字。
  */
  gap: 3px 8px;
  margin-top: 2px;
  flex-wrap: wrap;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
}
.road-tag {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-size: var(--fs-xs);
}

.topbar-actions {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.icon-btn {
  width: clamp(34px, 1.2vw + 29px, 42px);
  height: clamp(34px, 1.2vw + 29px, 42px);
  display: grid;
  place-items: center;
  border: none;
  border-radius: clamp(9px, 0.4vw + 7.4px, 12px);
  background: var(--c-overlay);
  backdrop-filter: blur(6px);
  box-shadow: 0 2px 14px rgba(15, 23, 42, 0.18);
  color: var(--c-text-dim);
  font-size: var(--fs-md);
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
  background: var(--c-overlay);
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
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
}
.picker-close {
  border: none;
  background: none;
  color: var(--c-text-faint);
  cursor: pointer;
  font-size: var(--fs-sm);
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
  font-size: var(--fs-base);
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
  background: var(--c-overlay);
  backdrop-filter: blur(6px);
  box-shadow: 0 4px 18px rgba(15, 23, 42, 0.22);
  z-index: 15;
}
.info-head {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: var(--fs-md);
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
  font-size: var(--fs-base);
}
.info-line {
  margin: 5px 0 0;
  font-size: var(--fs-sm);
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
  background: var(--c-overlay);
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
  gap: 8px;
  width: 100%;
  padding: 9px 14px;
  border: none;
  background: none;
  cursor: pointer;
  /* 移动端点按目标 */
  min-height: 40px;
}
.sheet-title {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-base);
  font-weight: 600;
  color: var(--c-text);
}
/* 收起时右侧的一行摘要，占满剩余宽度并允许省略 */
.sheet-summary {
  flex: 1;
  min-width: 0;
  text-align: right;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sheet-summary.muted {
  color: var(--c-text-faint);
}
.sheet-count {
  min-width: 19px;
  padding: 1px 6px;
  border-radius: 9px;
  background: var(--c-danger);
  color: #fff;
  font-size: var(--fs-xs);
  font-weight: 600;
  text-align: center;
}
.sheet-chevron {
  color: var(--c-text-faint);
  font-size: var(--fs-sm);
}

.sheet-body {
  padding: 0 14px 12px;
  overflow-y: auto;
}

/*
  路线摘要条。
  左侧色条与地图上的线同色，用户不必读文字就能确认"这条摘要对应那条线"。
*/
.plan-bar {
  display: flex;
  align-items: baseline;
  gap: 7px;
  margin: 0 0 6px;
  padding: 6px 8px;
  border-left: 3px solid var(--c-primary);
  border-radius: 7px;
  background: var(--c-surface-2);
  font-size: var(--fs-sm);
}
.plan-mode {
  flex: 0 0 auto;
  font-weight: 600;
}
.plan-dest {
  flex: 1;
  min-width: 0;
  color: var(--c-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.plan-meta {
  flex: 0 0 auto;
  color: var(--c-text-dim);
  font-variant-numeric: tabular-nums;
}

.sheet-empty {
  padding: 8px 0 10px;
  font-size: var(--fs-sm);
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
  font-size: var(--fs-sm);
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
  font-size: var(--fs-base);
  font-weight: 600;
  color: var(--c-text);
}
.event-dist {
  flex: 0 0 auto;
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--c-primary);
}
.event-desc {
  margin: 2px 0 0;
  font-size: var(--fs-sm);
  line-height: 1.5;
  color: var(--c-text-dim);
}

.other-block {
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px dashed var(--c-border-strong);
}
.other-title {
  font-size: var(--fs-xs);
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
  font-size: var(--fs-sm);
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
  font-size: var(--fs-xs);
  line-height: 1.5;
  color: var(--c-text-faint);
}

/* 窄屏：地图铺满宽度，去侧边框拿回几像素视觉空间 */
@media (max-width: 767px) {
  .sheet-body {
    padding: 0 12px 10px;
  }
  .info-card {
    left: 8px;
    bottom: 8px;
    width: min(280px, calc(100% - 16px));
  }
}
</style>
