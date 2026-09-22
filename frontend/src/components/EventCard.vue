<!--
  事件卡片
  ========
  用于告警中心列表与指挥大屏的事件流。
  待复核状态时展示「确认 / 误报」按钮，实现人机协同。
-->
<script setup lang="ts">
import {
  CONGESTION_COLOR,
  CONGESTION_LABEL,
  EVENT_LEVEL_LABEL,
  EVENT_STATUS_LABEL,
  EVENT_TYPE_LABEL,
} from '@/types/api'
import type { TrafficEvent } from '@/types/api'

withDefaults(
  defineProps<{
    event: TrafficEvent
    compact?: boolean
  }>(),
  { compact: false },
)

const emit = defineEmits<{
  (e: 'review', id: string, action: 'confirm' | 'reject'): void
}>()

const LEVEL_CLASS: Record<string, string> = {
  info: 'badge-info',
  warning: 'badge-warn',
  critical: 'badge-danger',
}

const STATUS_CLASS: Record<string, string> = {
  pending: 'badge-warn',
  confirmed: 'badge-ok',
  rejected: 'badge-muted',
  archived: 'badge-info',
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="event-card" :class="[`level-${event.level}`, { compact }]">
    <div class="card-body">
      <img v-if="event.snapshotUrl" :src="event.snapshotUrl" class="thumb" alt="事件快照" />
      <div v-else class="thumb thumb-empty">无快照</div>

      <div class="info">
        <div class="row-title">
          <span class="badge" :class="LEVEL_CLASS[event.level]">
            {{ EVENT_LEVEL_LABEL[event.level] }}
          </span>
          <span class="badge badge-muted">{{ EVENT_TYPE_LABEL[event.eventType] }}</span>
          <span class="badge" :class="STATUS_CLASS[event.status]">
            {{ EVENT_STATUS_LABEL[event.status] }}
          </span>
          <span class="confidence mono">
            置信度 {{ (event.confidence * 100).toFixed(0) }}%
          </span>
        </div>

        <div class="title">{{ event.title }}</div>

        <div v-if="!compact && event.description" class="desc">{{ event.description }}</div>

        <div class="meta">
          <span>◉ {{ event.cameraName || event.cameraId || '—' }}</span>
          <span>⇄ {{ event.roadName || event.roadId || '—' }}</span>
          <span>🚗 {{ event.vehicleCount }}</span>
          <span :style="{ color: CONGESTION_COLOR[event.congestionLevel] }">
            ● {{ CONGESTION_LABEL[event.congestionLevel] }}
          </span>
          <span class="mono text-faint">{{ formatTime(event.createdAt) }}</span>
        </div>

        <div v-if="event.reviewedBy" class="reviewed">
          由 {{ event.reviewedBy }} 复核 · {{ EVENT_STATUS_LABEL[event.status] }}
        </div>
      </div>
    </div>

    <div v-if="event.status === 'pending'" class="actions">
      <button class="btn btn-sm btn-primary" @click="emit('review', event.id, 'confirm')">
        ✓ 确认并上报
      </button>
      <button class="btn btn-sm btn-danger" @click="emit('review', event.id, 'reject')">
        ✗ 误报
      </button>
    </div>
  </div>
</template>

<style scoped>
.event-card {
  display: flex;
  align-items: stretch;
  gap: 0;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-left: 3px solid var(--border-strong);
  border-radius: var(--radius);
  overflow: hidden;
}
.event-card.level-warning {
  border-left-color: var(--warn);
}
.event-card.level-critical {
  border-left-color: var(--danger);
  background: rgba(239, 68, 68, 0.04);
}
.event-card.level-info {
  border-left-color: var(--accent);
}

.card-body {
  flex: 1;
  display: flex;
  gap: 12px;
  padding: 10px 12px;
  min-width: 0;
}

.thumb {
  width: 132px;
  height: 82px;
  flex-shrink: 0;
  object-fit: cover;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: #000;
}
.thumb-empty {
  display: grid;
  place-items: center;
  color: var(--text-faint);
  font-size: 11px;
}
.compact .thumb {
  width: 96px;
  height: 60px;
}

.info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.row-title {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.confidence {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-dim);
}
.title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.desc {
  font-size: 12px;
  color: var(--text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 11px;
  color: var(--text-dim);
}
.reviewed {
  font-size: 11px;
  color: var(--text-faint);
}

.actions {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
  padding: 10px;
  border-left: 1px solid var(--border);
  background: var(--bg-panel-2);
}
.actions .btn {
  white-space: nowrap;
}
</style>
