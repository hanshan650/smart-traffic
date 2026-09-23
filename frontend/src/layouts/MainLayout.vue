<!--
  后台主布局
  ==========
  左侧导航 + 顶部状态栏 + 内容区，并承载全局告警弹窗。

  告警弹窗实现「AI 建议 + 人工一键确认」：新事件到达时置顶显示，
  值班员可一键确认或标记误报，支持键盘快捷键（空格确认 / Esc 关闭）。
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { useEventStore } from '@/stores/event'
import { useSystemStore } from '@/stores/system'
import { useVideoStore } from '@/stores/video'
import { useWebSocket } from '@/composables/useWebSocket'
import PwaPrompt from '@/components/PwaPrompt.vue'
import {
  CONGESTION_COLOR,
  CONGESTION_LABEL,
  EVENT_LEVEL_LABEL,
  EVENT_TYPE_LABEL,
} from '@/types/api'

const route = useRoute()
const systemStore = useSystemStore()
const videoStore = useVideoStore()
const eventStore = useEventStore()
const { connected } = useWebSocket()

const toast = ref('')

const navItems = [
  { name: 'dashboard', path: '/dashboard', label: '运行概览', icon: '◈' },
  { name: 'wall', path: '/wall', label: '实时监控', icon: '▦' },
  { name: 'map', path: '/map', label: '地图态势', icon: '◉' },
  { name: 'accident', path: '/accident', label: '事故识别', icon: '⚡' },
  { name: 'dispatch', path: '/dispatch', label: '警情调度', icon: '⚑' },
  { name: 'surveillance', path: '/surveillance', label: '违停监控', icon: '⊙' },
  { name: 'reports', path: '/reports', label: '民众上报', icon: '✉' },
  { name: 'officers', path: '/officers', label: '警员管理', icon: '☰' },
  { name: 'events', path: '/events', label: '告警中心', icon: '⚠' },
  { name: 'roads', path: '/roads', label: '上海路网', icon: '⇄' },
  { name: 'system', path: '/system', label: '系统状态', icon: '◎' },
]

const healthBadge = computed(() => {
  const health = systemStore.health
  if (!health) return { text: '未连接', short: '未连', cls: 'badge-muted' }
  if (health.status === 'ok') return { text: '服务正常', short: '正常', cls: 'badge-ok' }
  return { text: '部分降级', short: '降级', cls: 'badge-warn' }
})

const pendingCount = computed(() => eventStore.pendingEvents.length)

// ---------------------------------------------------------------- 一键确认

async function confirmLatest(): Promise<void> {
  const event = eventStore.latestPending
  if (!event) return
  const result = await eventStore.review(event.id, { action: 'confirm' })
  toast.value = result.message
}

async function rejectLatest(): Promise<void> {
  const event = eventStore.latestPending
  if (!event) return
  const result = await eventStore.review(event.id, { action: 'reject' })
  toast.value = result.message
}

function handleKeydown(e: KeyboardEvent): void {
  if (!eventStore.latestPending) return
  if (e.code === 'Space') {
    e.preventDefault()
    void confirmLatest()
  } else if (e.code === 'Escape') {
    eventStore.dismissLatest()
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
  if (!videoStore.cameras.length) {
    void videoStore.loadCameras(8, false)
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown)
})

// 提示 3 秒后自动消失
watch(toast, (value) => {
  if (!value) return
  setTimeout(() => {
    toast.value = ''
  }, 3000)
})

// 每 15 秒刷新一次统计与健康
let refreshTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  refreshTimer = setInterval(() => {
    void systemStore.refresh()
  }, 15_000)
})
onBeforeUnmount(() => {
  if (refreshTimer !== null) clearInterval(refreshTimer)
})

/**
 * 移动端抽屉开合状态。
 *
 * 窄屏下侧栏改为抽屉：固定 251px 的侧栏在手机上会把内容挤到没法看，
 * 而警务端 12 个导航项又不可能压成底部 Tab（那样每项只剩 30px 宽）。
 *
 * 点击导航项后**必须自动关闭** —— 否则用户点完菜单还得再手动关一次，
 * 而且新页面被抽屉盖着看不见。
 */
const drawerOpen = ref(false)

watch(
  () => route.fullPath,
  () => { drawerOpen.value = false },
)
</script>

<template>
  <div class="layout" :class="{ 'drawer-open': drawerOpen }">
    <!-- 抽屉遮罩（仅移动端出现） -->
    <div v-if="drawerOpen" class="drawer-mask" @click="drawerOpen = false" />

    <!-- 侧边导航（窄屏下变为抽屉） -->
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">ST</div>
        <div class="brand-text">
          <strong>智能交通监测</strong>
          <small>与事故预警平台</small>
        </div>
        <!-- 抽屉里的关闭按钮，只在移动端显示 -->
        <button class="drawer-close" title="关闭菜单" @click="drawerOpen = false">✕</button>
      </div>

      <nav class="nav">
        <RouterLink
          v-for="item in navItems"
          :key="item.name"
          :to="item.path"
          class="nav-item"
          :class="{ active: route.name === item.name }"
        >
          <span class="nav-icon">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
          <span v-if="item.name === 'events' && pendingCount" class="nav-badge">
            {{ pendingCount }}
          </span>
        </RouterLink>
      </nav>

      <a href="/screen" target="_blank" class="screen-link">⛶ 打开指挥大屏</a>

      <div class="sidebar-footer">
        <div class="footer-row">
          <span>视频源</span>
          <span class="text-dim">{{ videoStore.sourceName || '—' }}</span>
        </div>
        <div class="footer-row">
          <span>路网</span>
          <span class="text-dim">上海市</span>
        </div>
      </div>
    </aside>

    <!-- 主区域 -->
    <div class="main">
      <header class="topbar">
        <!-- 汉堡按钮，仅移动端显示 -->
        <button class="hamburger" title="打开菜单" @click="drawerOpen = true">
          <span />
          <span />
          <span />
        </button>

        <h1 class="page-title">{{ route.meta.title ?? '' }}</h1>

        <div class="topbar-right">
          <!--
            窄屏下状态文字收起，只留圆点；但**断线时必须保留文字** ——
            红点本身不说明问题，值班员需要一眼看出"连不上了"。
          -->
          <span class="badge" :class="connected ? 'badge-ok' : 'badge-danger'">
            <span :class="{ pulse: !connected }">●</span>
            <span v-if="connected" class="badge-label">实时连接</span>
            <span v-else>连接断开</span>
          </span>

          <!-- 健康状态：窄屏换成短词（降级 / 正常），而不是整个隐掉 -->
          <span class="badge" :class="healthBadge.cls">
            <span class="badge-label">{{ healthBadge.text }}</span>
            <span class="badge-short">{{ healthBadge.short }}</span>
          </span>

          <span class="badge badge-info">
            <span class="badge-label">待复核</span>
            {{ pendingCount }}
          </span>
        </div>
      </header>

      <main class="content">
        <RouterView />
      </main>
    </div>

    <!-- 新版本与离线提示（警务端不显示安装引导） -->
    <PwaPrompt />

    <!-- 全局告警弹窗（AI 建议 + 一键确认） -->
    <Transition name="slide-up">
      <div v-if="eventStore.latestPending" class="alert-dialog">
        <div class="alert-head">
          <span class="alert-dot pulse">●</span>
          <strong>{{ eventStore.latestPending.title }}</strong>
          <span
            class="badge"
            :class="`badge-${eventStore.latestPending.level === 'critical' ? 'danger' : 'warn'}`"
          >
            {{ EVENT_LEVEL_LABEL[eventStore.latestPending.level] }}
          </span>
          <span class="badge badge-muted">
            置信度 {{ (eventStore.latestPending.confidence * 100).toFixed(0) }}%
          </span>
          <button class="alert-close" @click="eventStore.dismissLatest()">✕</button>
        </div>

        <div class="alert-body">
          <img
            v-if="eventStore.latestPending.snapshotUrl"
            :src="eventStore.latestPending.snapshotUrl"
            class="alert-thumb"
            alt="事件快照"
          />
          <div class="alert-meta">
            <div>
              <span class="text-faint">类型</span>
              {{ EVENT_TYPE_LABEL[eventStore.latestPending.eventType] }}
            </div>
            <div>
              <span class="text-faint">点位</span>
              {{ eventStore.latestPending.cameraName || eventStore.latestPending.cameraId }}
            </div>
            <div>
              <span class="text-faint">车辆数</span>
              {{ eventStore.latestPending.vehicleCount }}
            </div>
            <div>
              <span class="text-faint">拥堵</span>
              <span
                :style="{ color: CONGESTION_COLOR[eventStore.latestPending.congestionLevel] }"
              >
                {{ CONGESTION_LABEL[eventStore.latestPending.congestionLevel] }}
              </span>
            </div>
          </div>
        </div>

        <div class="alert-actions">
          <button class="btn btn-primary" @click="confirmLatest">
            ✓ 确认并上报 <kbd>空格</kbd>
          </button>
          <button class="btn btn-danger" @click="rejectLatest">✗ 误报</button>
          <button class="btn" @click="eventStore.dismissLatest()">稍后处理</button>
        </div>
      </div>
    </Transition>

    <Transition name="fade">
      <div v-if="toast" class="toast">{{ toast }}</div>
    </Transition>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100%;
}

/*
  移动端适配
  ===========
  警员在路面执勤时用手机接警、处置，所以警务端也得能在窄屏上用。
  策略：侧栏（固定 210px）改为抽屉，汉堡按钮唤出。

  断点取 900px 而不是 768px：侧栏 210px + 内容区最小可用宽度（表格、
  分栏）大约需要 690px，加起来接近 900px。只看惯用的 768px 会在
  768–900px 这段把内容挤得比手机还难用（平板竖屏正好落在这里）。
*/
.hamburger {
  display: none;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  width: 40px;
  height: 40px;
  margin-right: 10px;
  padding: 0 10px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  cursor: pointer;
}
.hamburger span {
  display: block;
  height: 2px;
  border-radius: 2px;
  background: var(--text-dim);
}
.hamburger:active {
  background: var(--bg-panel-2);
}

.drawer-mask {
  display: none;
}

.drawer-close {
  display: none;
}


/* ---------------- 侧边栏 ---------------- */
.sidebar {
  width: 210px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-panel);
  border-right: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 14px;
  border-bottom: 1px solid var(--border);
}
.brand-mark {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-sm);
  background: linear-gradient(135deg, var(--accent), var(--accent-dim));
  color: #06283d;
  font-weight: 700;
  font-size: 13px;
}
.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}
.brand-text strong {
  font-size: 13px;
}
.brand-text small {
  color: var(--text-faint);
  font-size: 11px;
}

.nav {
  flex: 1;
  padding: 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  color: var(--text-dim);
  text-decoration: none;
  font-size: 13px;
  transition: all 0.15s;
}
.nav-item:hover {
  background: var(--bg-panel-2);
  color: var(--text);
}
.nav-item.active {
  background: rgba(34, 211, 238, 0.12);
  color: var(--accent);
  font-weight: 600;
}
.nav-icon {
  width: 16px;
  text-align: center;
}
.nav-badge {
  margin-left: auto;
  min-width: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--danger);
  color: #fff;
  font-size: 11px;
  text-align: center;
}

.screen-link {
  margin: 0 14px 10px;
  padding: 8px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-sm);
  color: var(--text-dim);
  font-size: 12px;
  text-align: center;
  text-decoration: none;
}
.screen-link:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.sidebar-footer {
  padding: 10px 14px 14px;
  border-top: 1px solid var(--border);
  font-size: 12px;
}
.footer-row {
  display: flex;
  justify-content: space-between;
  padding: 2px 0;
  color: var(--text-faint);
}

/* ---------------- 主区域 ---------------- */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 52px;
  padding: 0 18px;
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
}
.page-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
/* 宽屏用完整文字，窄屏（见文末媒体查询）换成 .badge-short 的短词 */
.topbar-right .badge-short {
  display: none;
}

.content {
  flex: 1;
  overflow: auto;
  padding: 16px;
}

/* ---------------- 告警弹窗 ---------------- */
.alert-dialog {
  position: fixed;
  right: 20px;
  bottom: 20px;
  width: min(460px, calc(100vw - 40px));
  background: var(--bg-panel);
  border: 1px solid var(--danger);
  border-radius: var(--radius);
  box-shadow: 0 12px 40px rgba(239, 68, 68, 0.22);
  z-index: 1000;
  overflow: hidden;
}
.alert-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: rgba(239, 68, 68, 0.1);
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.alert-dot {
  color: var(--danger);
}
.alert-close {
  margin-left: auto;
  background: none;
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  font-size: 14px;
}
.alert-body {
  display: flex;
  gap: 12px;
  padding: 12px;
}
.alert-thumb {
  width: 120px;
  height: 76px;
  object-fit: cover;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: #000;
}
.alert-meta {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 10px;
  font-size: 12px;
}
.alert-meta .text-faint {
  margin-right: 5px;
}
.alert-actions {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid var(--border);
}
.alert-actions .btn {
  flex: 1;
}
kbd {
  padding: 1px 5px;
  border: 1px solid var(--border-strong);
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.3);
  font-size: 10px;
}

/* ---------------- 提示条 ---------------- */
.toast {
  position: fixed;
  left: 50%;
  bottom: 32px;
  transform: translateX(-50%);
  padding: 10px 20px;
  border-radius: var(--radius);
  background: var(--bg-panel-2);
  border: 1px solid var(--accent-dim);
  color: var(--text);
  font-size: 13px;
  z-index: 1100;
}

/* ---------------- 过渡 ---------------- */
.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.25s ease;
}
.slide-up-enter-from,
.slide-up-leave-to {
  opacity: 0;
  transform: translateY(20px);
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
/*
  移动端适配（必须放在样式表最后）
  ==================================
  这几条规则覆盖 .sidebar / .topbar / .content 的桌面值，而 CSS 对同特异性
  声明只看出现顺序 —— 所以这一块一旦被放到那些规则前面，就会静默失效：
  抽屉宽度回到 210px、顶栏内边距回到桌面值，编译器与浏览器都不报错。
*/
@media (max-width: 900px) {
  .hamburger {
    display: flex;
  }

  /* 侧栏脱离流，避免占位（否则内容区会被挤出屏幕右侧） */
  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    z-index: 1500;
    width: 258px;
    max-width: 82vw;
    transform: translateX(-100%);
    transition: transform 0.24s ease;
    box-shadow: 6px 0 28px rgba(0, 0, 0, 0.45);
    overflow-y: auto;
  }
  .drawer-open .sidebar {
    transform: translateX(0);
  }

  .drawer-mask {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 1400;
    background: rgba(2, 6, 12, 0.6);
  }

  .drawer-close {
    display: block;
    margin-left: auto;
    width: 32px;
    height: 32px;
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--text-dim);
    font-size: 14px;
    cursor: pointer;
  }

  /* 顶栏：标题占满剩余宽度，状态徽章只留圆点与数字，避免换行 */
  .topbar {
    height: auto;
    min-height: 52px;
    gap: 8px;
    padding: 8px 12px;
    /* 横屏时避开刘海 */
    padding-left: max(12px, env(safe-area-inset-left));
    padding-right: max(12px, env(safe-area-inset-right));
  }
  .page-title {
    flex: 1;
    min-width: 0;
    font-size: 14px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .topbar-right {
    gap: 6px;
  }
  .topbar-right .badge {
    padding: 3px 7px;
    font-size: 11px;
  }
  /* 描述性文字收起，改用短词；数字直接可见，不受影响 */
  .topbar-right .badge-label {
    display: none;
  }
  .topbar-right .badge-short {
    display: inline;
  }

  .content {
    padding: 12px;
    padding-bottom: max(12px, env(safe-area-inset-bottom));
  }

  /*
    警务端各页面的表单控件统一提到 16px。
    这一条写在布局里而不是逐个改页面：iOS Safari 在字号 < 16px 的输入框
    获得焦点时会自动放大页面，而各页面自己的 .input 大多是 13–14px。
    逐个页面改既容易漏，又会让同一控件在两端表现不一致。

    排除 checkbox / radio：它们没有文字，放大字号只会撑变形。
  */
  .content :deep(input:not([type='checkbox']):not([type='radio'])),
  .content :deep(textarea),
  .content :deep(select) {
    font-size: 16px;
  }

  /*
    触控目标下限。手指点击精度约 44px，13px 字号的小按钮
    （各页面里的 .btn-sm）在手机上容易点不中，尤其执勤时单手操作。
  */
  .content :deep(.btn) {
    min-height: 36px;
  }

  /*
    宽表格的横向滚动。
    这些表格（警员档案 10 列等）在手机上必然超出屏幕，只能横向滑动。

    刻意**不加**边缘渐隐之类的"可滚动"提示：要正确实现得靠 4 层背景
    配合 background-attachment 判断是否滚到端点，做不完整就会在无法滚动时
    也显示渐变，反而误导。移动端用户对横向滑表格本来就熟悉，不值得为此引入
    一个半对的视觉效果。
  */
  .content :deep(.table-wrap) {
    -webkit-overflow-scrolling: touch;
  }
}

</style>
