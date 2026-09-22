<!--
  摄像头画面放大查看
  ==================
  点击监控瓦片后弹出，用大尺寸播放同一路流，便于辨认细节
  （车牌、车辆类型、事故现场）。

  设计取舍
  --------
  · 用 ``Teleport`` 挂到 body：父级网格有 ``overflow: hidden``，
    留在原地会被裁掉
  · 重新拉一路流而非移动原 video 元素：DOM 跨组件搬迁容易
    打断解码，且原画面仍应在墙上继续播放
  · 打开时锁 body 滚动，避免背景跟着动
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

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
  }>(),
  {
    congestionLevel: 'normal',
    totalVehicles: 0,
    detecting: false,
  },
)

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'detect', cameraNum: string): void
}>()

const videoEl = ref<HTMLVideoElement | null>(null)
const streamUrl = computed(() => props.streamUrl)
const { status, attach } = useHls(videoEl, streamUrl)

/**
 * 容器宽高比跟随视频真实比例。
 *
 * 上游监控多为 4:3（如 352x288），而这里默认按 16:9 布局，
 * 导致左右各留一条黑边、画面反而更小。读到元数据后立即校正。
 */
const aspectRatio = ref(16 / 9)

function syncAspectRatio(): void {
  const video = videoEl.value
  if (video && video.videoWidth && video.videoHeight) {
    aspectRatio.value = video.videoWidth / video.videoHeight
  }
}

/** 传给 CSS：宽高比同时定高与定宽，避免“宽度 100% + max-height”把画面压扁 */
const stageStyle = computed(() => ({ '--ratio': String(aspectRatio.value) }))

const toneColor = computed(() => CONGESTION_COLOR[props.congestionLevel])

function handleKey(event: KeyboardEvent): void {
  if (event.key === 'Escape') emit('close')
}

onMounted(() => {
  window.addEventListener('keydown', handleKey)
  document.body.style.overflow = 'hidden'
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKey)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <div class="lightbox" @click.self="emit('close')">
      <div class="frame">
        <!-- 工具条 -->
        <div class="bar">
          <span class="title" :title="label">▦ {{ label }}</span>
          <span class="badge" :style="{ color: toneColor }">
            ● {{ CONGESTION_LABEL[congestionLevel] }}
          </span>
          <span class="count mono">
            检测车辆
            <strong>{{ totalVehicles }}</strong>
            辆
          </span>

          <div class="bar-actions">
            <button
              class="btn btn-sm"
              :disabled="detecting"
              @click="emit('detect', cameraNum)"
            >
              {{ detecting ? '检测中…' : '◉ 重新检测' }}
            </button>
            <button class="btn btn-sm" title="关闭（Esc）" @click="emit('close')">✕ 关闭</button>
          </div>
        </div>

        <!-- 画面 -->
        <div class="stage" :style="stageStyle">
          <video
            ref="videoEl"
            class="video"
            autoplay
            playsinline
            muted
            @loadedmetadata="syncAspectRatio"
          />

          <div v-if="status !== 'playing'" class="mask">
            <template v-if="status === 'loading'">
              <div class="spinner" />
              <span>加载中…</span>
            </template>
            <template v-else>
              <div class="mask-icon">⚠</div>
              <span>流加载失败</span>
              <button class="btn btn-sm" @click="attach(streamUrl)">重试</button>
            </template>
          </div>
        </div>

        <div class="foot text-dim">
          摄像头 {{ cameraNum }} · 按 Esc 或点击空白处关闭
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.lightbox {
  position: fixed;
  inset: 0;
  z-index: 1500;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(4, 7, 13, 0.86);
  backdrop-filter: blur(3px);
}

.frame {
  width: min(1600px, 94vw);
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  padding: 10px 12px;
}

.bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 13px;
}
.title {
  font-weight: 600;
  max-width: 46ch;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.count strong {
  color: var(--accent);
  font-size: 16px;
  margin: 0 2px;
}
.bar-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
}

.stage {
  position: relative;
  /* 以高度为准反推宽度：既要填满可用空间，又不能超出视口高度。
     若写成 width:100% + max-height，宽高比会被拉伸变形。 */
  aspect-ratio: var(--ratio, 1.7778);
  height: min(76vh, calc(92vw / var(--ratio, 1.7778)));
  width: auto;
  max-width: 100%;
  margin: 0 auto;
  background: #000;
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.video {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
  background: #000;
}

.mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: rgba(7, 11, 20, 0.85);
  color: var(--text-dim);
  font-size: 13px;
}
.mask-icon {
  font-size: 26px;
  color: var(--warn);
}

.foot {
  font-size: 11px;
  text-align: right;
}
</style>
