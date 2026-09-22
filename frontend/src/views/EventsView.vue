<!--
  告警中心
  ========
  事件列表 + 筛选 + 人工复核。

  这是「AI 建议 + 人工一键确认」的主战场：待复核事件在此处置，
  误报会回写后端负样本池，形成主动学习闭环。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import EventCard from '@/components/EventCard.vue'
import { useEventStore } from '@/stores/event'
import type { EventLevel, EventStatus } from '@/types/api'

const eventStore = useEventStore()

const statusFilter = ref<EventStatus | ''>('')
const levelFilter = ref<EventLevel | ''>('')
const toast = ref('')

const statusOptions: { value: EventStatus | ''; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待复核' },
  { value: 'confirmed', label: '已确认' },
  { value: 'rejected', label: '误报' },
  { value: 'archived', label: '已归档' },
]

const levelOptions: { value: EventLevel | ''; label: string }[] = [
  { value: '', label: '全部等级' },
  { value: 'critical', label: '严重' },
  { value: 'warning', label: '警告' },
  { value: 'info', label: '提示' },
]

const filtered = computed(() => eventStore.events)

async function reload(): Promise<void> {
  await eventStore.load({
    status: statusFilter.value || undefined,
    level: levelFilter.value || undefined,
    limit: 100,
  })
}

async function handleReview(id: string, action: 'confirm' | 'reject'): Promise<void> {
  const result = await eventStore.review(id, { action })
  toast.value = result.message
  setTimeout(() => {
    toast.value = ''
  }, 2500)
}

onMounted(() => {
  void reload()
})
</script>

<template>
  <div class="page">
    <!-- 筛选 -->
    <section class="panel filters">
      <div class="filter-group">
        <label class="text-faint">状态</label>
        <select v-model="statusFilter" class="select" @change="reload">
          <option v-for="opt in statusOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
      </div>

      <div class="filter-group">
        <label class="text-faint">等级</label>
        <select v-model="levelFilter" class="select" @change="reload">
          <option v-for="opt in levelOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
      </div>

      <button class="btn btn-sm" :disabled="eventStore.loading" @click="reload">
        ↻ 刷新
      </button>

      <div class="filter-summary">
        <span class="badge badge-muted">共 {{ eventStore.total }} 条</span>
        <span
          class="badge"
          :class="eventStore.pendingEvents.length ? 'badge-warn' : 'badge-ok'"
        >
          待复核 {{ eventStore.pendingEvents.length }}
        </span>
      </div>
    </section>

    <Transition name="fade">
      <div v-if="toast" class="toast">{{ toast }}</div>
    </Transition>

    <!-- 列表 -->
    <section class="list">
      <div v-if="eventStore.loading" class="empty">
        <div class="spinner" />
        <div>加载中…</div>
      </div>

      <div v-else-if="eventStore.error" class="empty">
        <div class="error-title">⚠ 无法读取事件</div>
        <div class="text-dim">{{ eventStore.error }}</div>
        <button class="btn" @click="reload">重试</button>
      </div>

      <div v-else-if="!filtered.length" class="empty">
        <div class="empty-icon">✓</div>
        <div>当前筛选条件下没有事件</div>
        <div class="text-faint small">
          系统检测到中度以上拥堵时会自动生成事件
        </div>
      </div>

      <EventCard
        v-for="item in filtered"
        v-else
        :key="item.id"
        :event="item"
        @review="handleReview"
      />
    </section>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.filters {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 10px 14px;
}
.filter-group {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.filter-summary {
  margin-left: auto;
  display: flex;
  gap: 8px;
}

.list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.empty-icon {
  font-size: 34px;
  color: var(--ok);
}
.error-title {
  color: var(--danger);
  font-weight: 600;
}
.small {
  font-size: 12px;
}

.toast {
  position: fixed;
  left: 50%;
  bottom: 32px;
  transform: translateX(-50%);
  padding: 9px 18px;
  border-radius: var(--radius);
  background: var(--bg-panel-2);
  border: 1px solid var(--accent-dim);
  font-size: 13px;
  z-index: 1200;
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
