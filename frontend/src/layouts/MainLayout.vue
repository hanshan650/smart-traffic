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
  { name: 'events', path: '/events', label: '告警中心', icon: '⚠' },
  { name: 'roads', path: '/roads', label: '上海路网', icon: '⇄' },
  { name: 'system', path: '/system', label: '系统状态', icon: '◎' },
]

const healthBadge = computed(() => {
  const health = systemStore.health
  if (!health) return { text: '未连接', cls: 'badge-muted' }
  if (health.status === 'ok') return { text: '服务正常', cls: 'badge-ok' }
  return { text: '部分降级', cls: 'badge-warn' }
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
</script>

<template>
  <div class="layout">
    <!-- 侧边导航 -->
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">ST</div>
        <div class="brand-text">
          <strong>智能交通监测</strong>
          <small>与事故预警平台</small>
        </div>
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
        <h1 class="page-title">{{ route.meta.title ?? '' }}</h1>

        <div class="topbar-right">
          <span class="badge" :class="connected ? 'badge-ok' : 'badge-danger'">
            <span :class="{ pulse: !connected }">●</span>
            {{ connected ? '实时连接' : '连接断开' }}
          </span>
          <span class="badge" :class="healthBadge.cls">{{ healthBadge.text }}</span>
          <span class="badge badge-info">
            待复核 {{ pendingCount }}
          </span>
        </div>
      </header>

      <main class="content">
        <RouterView />
      </main>
    </div>

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
</style>
