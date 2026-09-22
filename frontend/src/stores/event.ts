/**
 * 事件 Store
 * ==========
 * 管理交通事件列表与人工复核（一键确认 / 误报）。
 *
 * 人机协同流程
 * ------------
 * AI 检出 → 事件进入 PENDING → 大屏弹窗置顶 → 值班员点「确认」或「误报」。
 * 误报会回写后端负样本池（``hard_negative``），形成主动学习闭环。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { eventApi } from '@/api'
import { describeError } from '@/api/client'
import type { EventReviewPayload, EventStatus, TrafficEvent } from '@/types/api'

export const useEventStore = defineStore('event', () => {
  const events = ref<TrafficEvent[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref('')

  /** 最新一条未处理事件 —— 大屏据此弹窗置顶 */
  const latestPending = ref<TrafficEvent | null>(null)

  const pendingEvents = computed(() => events.value.filter((e) => e.status === 'pending'))

  const pendingCount = computed(
    () => events.value.filter((e) => e.status === 'pending').length,
  )

  // ------------------------------------------------------------------ 查询

  async function load(params: { status?: string; level?: string; limit?: number } = {}): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      const data = await eventApi.list(params)
      events.value = data.items
      total.value = data.total
    } catch (err) {
      const { message } = describeError(err)
      error.value = message
    } finally {
      loading.value = false
    }
  }

  // ------------------------------------------------------------------ 复核

  /** 一键确认或误报。返回是否成功。 */
  async function review(eventId: string, payload: EventReviewPayload): Promise<{ ok: boolean; message: string }> {
    try {
      const updated = await eventApi.review(eventId, payload)
      applyUpdate(updated)
      if (latestPending.value?.id === eventId) {
        latestPending.value = null
      }
      return { ok: true, message: payload.action === 'confirm' ? '已确认并上报' : '已标记为误报' }
    } catch (err) {
      const { message } = describeError(err)
      return { ok: false, message }
    }
  }

  // ---------------------------------------------------------------- 实时更新

  /** WebSocket ``event:new`` */
  function pushNewEvent(event: TrafficEvent): void {
    events.value = [event, ...events.value].slice(0, 200)
    total.value += 1
    if (event.status === 'pending') {
      latestPending.value = event
    }
  }

  /** WebSocket ``event:update`` */
  function applyUpdate(event: TrafficEvent): void {
    const index = events.value.findIndex((item) => item.id === event.id)
    if (index >= 0) {
      const next = [...events.value]
      next[index] = event
      events.value = next
    } else {
      events.value = [event, ...events.value]
    }
  }

  function dismissLatest(): void {
    latestPending.value = null
  }

  function setStatusFilter(status: EventStatus | ''): void {
    void load(status ? { status } : {})
  }

  return {
    events,
    total,
    loading,
    error,
    latestPending,
    pendingEvents,
    pendingCount,
    load,
    review,
    pushNewEvent,
    applyUpdate,
    dismissLatest,
    setStatusFilter,
  }
})
