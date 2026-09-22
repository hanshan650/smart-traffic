<!--
  监控画面瓦片
  ============
  负责单路 HLS 播放与状态展示。检测请求由父组件发起，本组件只负责展示
  检测结果与触发事件，避免把网络逻辑散落在组件里。

  注意：上游 m3u8 带时效 token，父组件在 token 失效后应重新拉取并
  更新 ``streamUrl``，本组件会自动重新挂载播放器。
-->
<script setup lang="ts">
import { computed, ref } from 'vue'

import { useHls } from '@/composables/useHls'
import { CONGESTION_COLOR, CONGESTION_LABEL } from '@/types/api'
import type { CongestionLevel } from '@/types/api'

const props = withDefaults(
  defineProps<{
    cameraNum: string
    label: string
    streamUrl: string
    congestionLevel?: CongestionLevel
    totalVehicles?: number
    detecting?: boolean
    snapshotUrl?: string
    /** 常显信息条；缺省时仅在悬停时浮现，以免遮挡画面 */
    showLabels?: boolean
  }>(),
  {
    congestionLevel: 'normal',
    totalVehicles: 0,
    detecting: false,
    snapshotUrl: '',
    showLabels: false,
  },
)

const emit = defineEmits<{
  (e: 'remove', cameraNum: string): void
  (e: 'detect', cameraNum: string): void
  (e: 'expand', cameraNum: string): void
}>()

const videoEl = ref<HTMLVideoElement | null>(null)
const streamUrl = computed(() => props.streamUrl)
const { status, attach } = useHls(videoEl, streamUrl)
</script>

<template>
  <div class="tile" :class="{ 'labels-on': showLabels }">
    <video ref="videoEl" class="video" autoplay playsinline muted />

    <!-- 点击画面放大（按钮区域有自己的处理，不会冒泡到这里） -->
    <button
      class="expand-hit"
      :title="`放大查看 ${label}`"
      @click="emit('expand', cameraNum)"
    ></button>

    <!-- 右上角常驻车辆数。信息条默认隐藏，但这一小块必须一直可见——
         它是在不遮挡画面的前提下“哪路有车”的唯一线索。 -->
    <div
      class="veh-badge"
      :class="{ active: totalVehicles > 0, detecting }"
      :style="totalVehicles > 0 ? { color: CONGESTION_COLOR[congestionLevel], borderColor: CONGESTION_COLOR[congestionLevel] + '66' } : undefined"
      :title="detecting ? '检测中' : `检测车辆数：${totalVehicles}`"
    >
      <span class="veh-num">{{ detecting ? '·' : totalVehicles }}</span>
      <span class="veh-unit">辆</span>
    </div>

    <!-- 离线 / 加载中遮罩 -->
    <div v-if="status !== 'playing'" class="mask">
      <div v-if="status === 'loading'" class="spinner" />
      <template v-else>
        <div class="mask-icon">⚠</div>
        <div class="mask-text">流加载失败</div>
        <button class="btn btn-sm" @click="attach(streamUrl)">重试</button>
      </template>
    </div>

    <!-- 信息条
         默认隐藏：站名可能非常长（如“G1511(日兰高速公路王楼至兰考段)河南开封…”），
         常驻会盖住大片画面。悬停时浮现，或用 showLabels 常显。
         名称强制单行截断，完整名称通过 title 提示。 -->
    <div class="overlay">
      <div class="overlay-top">
        <span class="tile-label" :title="label">▦ {{ label }}</span>
        <button
          class="icon-btn"
          title="移除"
          @click.stop="emit('remove', cameraNum)"
        >
          ✕
        </button>
      </div>

      <div class="overlay-bottom">
        <span class="badge" :style="{ color: CONGESTION_COLOR[congestionLevel] }">
          ● {{ CONGESTION_LABEL[congestionLevel] }}
        </span>
        <span class="veh-count mono">{{ totalVehicles }} 辆</span>
        <button
          class="btn btn-sm detect-btn"
          :disabled="detecting"
          @click.stop="emit('detect', cameraNum)"
        >
          {{ detecting ? '检测中' : '检测' }}
        </button>
      </div>
    </div>

    <!-- 检测着色进度条 -->
    <div
      class="tone-bar"
      :style="{
        width: totalVehicles ? '100%' : '0%',
        backgroundColor: CONGESTION_COLOR[congestionLevel],
      }"
    />
  </div>
</template>

<style scoped>
.tile {
  position: relative;
  aspect-ratio: 16 / 9;
  min-height: 180px;
  background: #000;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.video {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* 覆盖整块的透明点击层，用于放大查看。
   放在最底层（z-index:1），上面的信息条与徽章遮挡它。 */
.expand-hit {
  position: absolute;
  inset: 0;
  z-index: 1;
  padding: 0;
  border: none;
  background: transparent;
  cursor: zoom-in;
  transition: background 0.16s ease;
}
.tile:hover .expand-hit {
  background: rgba(34, 211, 238, 0.05);
}

/* 右上角常驻车辆数。
   信息条默认隐藏，所以这块小徽章是“哪路有车”的唯一持续线索。
   刻意做得小、半透明，且不拦截鼠标（继续传给放大层）。 */
.veh-badge {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 3;
  display: flex;
  align-items: baseline;
  gap: 2px;
  padding: 2px 9px;
  border-radius: 999px;
  background: rgba(7, 11, 20, 0.72);
  border: 1px solid var(--border-strong);
  color: var(--text-faint);
  font-size: 11px;
  line-height: 1.6;
  pointer-events: none;
  transition: color 0.16s ease, border-color 0.16s ease;
}
.veh-num {
  font-family: var(--font-mono);
  font-size: 15px;
  font-weight: 600;
}
.veh-unit {
  opacity: 0.75;
}

.mask {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: rgba(7, 11, 20, 0.82);
  color: var(--text-dim);
  font-size: 12px;
}
.mask-icon {
  font-size: 22px;
  color: var(--warn);
}

.overlay {
  position: absolute;
  inset: auto 0 0 0;
  z-index: 4;
  padding: 8px 10px;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.88));
  /* 默认完全隐藏。用 visibility 而非仅 opacity，
     否则透明的“✕”按钮仍可被点到。 */
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.16s ease;
  pointer-events: none;
}
.tile:hover .overlay,
.tile.labels-on .overlay {
  opacity: 1;
  visibility: visible;
}

.overlay-top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.tile-label {
  /* flex:1 + min-width:0 才能让 text-overflow 生效 */
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.8);
}
.icon-btn {
  flex: none;
  pointer-events: auto;
  background: rgba(0, 0, 0, 0.5);
  border: none;
  color: #cbd5e1;
  width: 20px;
  height: 20px;
  border-radius: 3px;
  cursor: pointer;
  font-size: 11px;
  line-height: 1;
}
.icon-btn:hover {
  background: var(--danger);
  color: #fff;
}

.overlay-bottom {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.veh-count {
  color: #e2e8f0;
  font-weight: 600;
}
.detect-btn {
  pointer-events: auto;
  margin-left: auto;
}

.tone-bar {
  position: absolute;
  left: 0;
  top: 0;
  height: 2px;
  transition: width 0.4s ease, background-color 0.4s;
}
</style>
