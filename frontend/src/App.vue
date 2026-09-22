/**
 * 应用根组件
 * ==========
 * 承担两件全局事务：
 *
 *  1. 启动时加载运行时配置、健康状态，并建立 WebSocket 连接
 *  2. 注册全局实时订阅，把服务端推送写入对应 store
 *
 * 之所以放在这里而不是布局组件中，是因为「指挥大屏」不使用 MainLayout，
 * 但它同样需要接收实时推送。
 */
<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { RouterView } from 'vue-router'

import { connect, disconnect, on } from '@/composables/useWebSocket'
import { useEventStore } from '@/stores/event'
import { useSystemStore } from '@/stores/system'
import { useVideoStore } from '@/stores/video'
import type { SnapshotPayload, TrafficEvent } from '@/types/api'

const systemStore = useSystemStore()
const eventStore = useEventStore()
const videoStore = useVideoStore()

/** 取消订阅函数集合 */
const unsubscribers: Array<() => void> = []

onMounted(() => {
  void systemStore.bootstrap()

  unsubscribers.push(
    on('event:new', (payload) => {
      eventStore.pushNewEvent(payload as TrafficEvent)
    }),
    on('event:update', (payload) => {
      eventStore.applyUpdate(payload as TrafficEvent)
    }),
    on('snapshot', (payload) => {
      videoStore.applySnapshot(payload as SnapshotPayload)
    }),
  )

  connect()
})

onBeforeUnmount(() => {
  unsubscribers.forEach((off) => off())
  unsubscribers.length = 0
  disconnect()
})
</script>

<template>
  <RouterView />
</template>
