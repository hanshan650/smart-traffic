<!--
  实时监控（视频墙）
  ==================
  · 摄像头来自视频源适配层（当前为河南高速云视频）
  · 每格独立检测，检测结果回写瓦片
  · 支持 2/3/4 列切换与手动粘贴 m3u8

  性能提示：每路 HLS 都会占用解码资源，建议不超过 16 路；
  瓦片使用 ``aspect-ratio`` 布局，父容器滚动时不会重排。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import VideoLightbox from '@/components/VideoLightbox.vue'
import VideoTile from '@/components/VideoTile.vue'
import { detectionApi } from '@/api'
import { describeError } from '@/api/client'
import { useVideoStore } from '@/stores/video'
import { buildCameraLabel } from '@/utils/camera'
import type { CongestionLevel } from '@/types/api'

interface WallCamera {
  cameraNum: string
  label: string
  roadId: string
  streamUrl: string
  detecting: boolean
  totalVehicles: number
  congestionLevel: CongestionLevel
}

const videoStore = useVideoStore()

const columns = ref(3)
const wallCameras = ref<WallCamera[]>([])
const manualInput = ref('')
const busy = ref(false)
/** 信息条常显开关。默认关闭：站名很长会盖住画面，需要时才打开 */
const showLabels = ref(false)
/** 当前放大查看的那一路；``null`` 表示未打开 */
const expanded = ref<WallCamera | null>(null)
const message = ref('')
const messageTone = ref<'ok' | 'warn' | 'danger'>('ok')

let messageTimer: ReturnType<typeof setTimeout> | null = null

const occupied = computed(() => wallCameras.value.map((cam) => cam.cameraNum))
const totalVehicles = computed(() =>
  wallCameras.value.reduce((sum, cam) => sum + cam.totalVehicles, 0),
)
const congestedCount = computed(
  () => wallCameras.value.filter((cam) => cam.congestionLevel !== 'normal').length,
)

function notify(text: string, tone: 'ok' | 'warn' | 'danger' = 'ok'): void {
  message.value = text
  messageTone.value = tone
  if (messageTimer !== null) clearTimeout(messageTimer)
  messageTimer = setTimeout(() => {
    message.value = ''
  }, 4000)
}

/** 逐路拉取流地址后追加到墙 */
async function appendCameras(
  targets: { cameraNum: string; label: string; roadId: string }[],
): Promise<void> {
  const fresh = targets.filter(
    (target) => !wallCameras.value.some((cam) => cam.cameraNum === target.cameraNum),
  )
  if (!fresh.length) return

  busy.value = true
  let success = 0
  let failed = 0

  for (const target of fresh) {
    try {
      const streamUrl = await videoStore.getStreamUrl(target.cameraNum)
      wallCameras.value.push({
        ...target,
        streamUrl,
        detecting: false,
        totalVehicles: 0,
        congestionLevel: 'normal',
      })
      success += 1
    } catch {
      failed += 1
    }
  }

  busy.value = false

  if (success === 0) {
    const hint = videoStore.hint || '请确认网络可访问河南高速云平台'
    notify(`流地址获取失败 — ${hint}`, 'danger')
  } else if (failed > 0) {
    notify(`已添加 ${success} 路，${failed} 路失败`, 'warn')
  } else {
    notify(`已添加 ${success} 路监控`, 'ok')
  }
}

/** 随机加载一批摄像头 */
async function loadRandom(count = 6): Promise<void> {
  const added = await videoStore.loadCameras(count, true)
  if (added === 0) {
    notify(videoStore.error || '没有更多可用的摄像头', 'warn')
    return
  }

  const pending = videoStore.cameras
    .filter((cam) => !occupied.value.includes(cam.cameraNum))
    .slice(0, added)
    .map((cam, index) => ({
      cameraNum: cam.cameraNum,
      label: buildCameraLabel(cam),
      roadId: `W${String(wallCameras.value.length + index + 1).padStart(3, '0')}`,
    }))

  await appendCameras(pending)
}

/** 手动添加单个摄像头 */
async function addManual(): Promise<void> {
  const value = manualInput.value.trim()
  if (!value) return

  const isUrl = value.includes('.m3u8')

  if (isUrl) {
    wallCameras.value.push({
      cameraNum: `manual_${Date.now()}`,
      label: `手动流 ${wallCameras.value.length + 1}`,
      roadId: 'MANUAL',
      streamUrl: value,
      detecting: false,
      totalVehicles: 0,
      congestionLevel: 'normal',
    })
    manualInput.value = ''
    return
  }

  await appendCameras([
    {
      cameraNum: value,
      label: value.slice(-6),
      roadId: `W${String(wallCameras.value.length + 1).padStart(3, '0')}`,
    },
  ])
  manualInput.value = ''
}

/** 单路检测 */
async function detectOne(cameraNum: string): Promise<void> {
  const target = wallCameras.value.find((cam) => cam.cameraNum === cameraNum)
  if (!target || target.detecting) return

  target.detecting = true
  try {
    const result = await detectionApi.snapshot({
      cameraNum,
      roadId: target.roadId,
    })
    target.totalVehicles = result.totalVehicles
    target.congestionLevel = result.congestionLevel
  } catch (error) {
    const info = describeError(error)
    notify(`${info.message}${info.hint ? ` — ${info.hint}` : ''}`, 'danger')
  } finally {
    target.detecting = false
  }
}

/** 全部检测（限并发，兼顾速度与上游压力） */
async function detectAll(): Promise<void> {
  if (busy.value) return
  busy.value = true

  // 每路检测都要下载 HLS 分片并做一次 CPU 推理（单路约 5~15 秒）。
  // 全串行太慢，全并发又会让 CPU 与上游同时饱和反而更慢，
  // 故取 3 路并发作为平衡点。
  const queue = [...wallCameras.value]
  const workerCount = Math.min(3, queue.length)

  async function worker(): Promise<void> {
    for (;;) {
      const camera = queue.shift()
      if (!camera) return
      await detectOne(camera.cameraNum)
    }
  }

  await Promise.all(Array.from({ length: workerCount }, worker))

  busy.value = false
  notify(`已完成 ${wallCameras.value.length} 路检测`, 'ok')
}

function refreshStreams(): void {
  wallCameras.value = []
  notify('已清空监控墙', 'ok')
}

/** 打开某一路的放大视图 */
function expandCamera(cameraNum: string): void {
  expanded.value = wallCameras.value.find((cam) => cam.cameraNum === cameraNum) ?? null
}

/** 移除摄像头时同步关掉它的放大视图，避免看到已删除的画面 */
function removeCamera(cameraNum: string): void {
  wallCameras.value = wallCameras.value.filter((cam) => cam.cameraNum !== cameraNum)
  if (expanded.value?.cameraNum === cameraNum) expanded.value = null
}

onMounted(() => {
  if (!videoStore.sources.length) {
    void videoStore.loadSources()
  }
})

defineExpose({ columns })
</script>

<template>
  <div class="page">
    <!-- 工具栏 -->
    <section class="panel toolbar">
      <div class="tool-group">
        <span class="tool-label">布局</span>
        <button
          v-for="n in [2, 3, 4]"
          :key="n"
          class="btn btn-sm"
          :class="{ 'btn-primary': columns === n }"
          @click="columns = n"
        >
          {{ n }} 列
        </button>
      </div>

      <div class="tool-group">
        <button class="btn btn-sm btn-primary" :disabled="busy" @click="loadRandom(6)">
          + 加载摄像头
        </button>
        <button class="btn btn-sm" :disabled="busy || !wallCameras.length" @click="detectAll">
          ◉ 全部检测
        </button>
        <button class="btn btn-sm" :disabled="!wallCameras.length" @click="refreshStreams">
          ✕ 清空
        </button>
        <button
          class="btn btn-sm"
          :class="{ 'btn-primary': showLabels }"
          :disabled="!wallCameras.length"
          :title="showLabels ? '隐藏信息条（悬停时才显示）' : '常显摄像头名称与检测结果'"
          @click="showLabels = !showLabels"
        >
          ☰ 标签
        </button>
      </div>

      <div class="tool-group grow">
        <input
          v-model="manualInput"
          class="input grow"
          placeholder="摄像头编号，或粘贴 .m3u8 直链"
          @keyup.enter="addManual"
        />
        <button class="btn btn-sm" :disabled="busy || !manualInput.trim()" @click="addManual">
          添加
        </button>
      </div>

      <div class="tool-group stats">
        <span class="badge badge-info">▦ {{ wallCameras.length }} 路</span>
        <span class="badge badge-muted">🚗 {{ totalVehicles }} 辆</span>
        <span class="badge" :class="congestedCount ? 'badge-warn' : 'badge-ok'">
          ▲ {{ congestedCount }} 拥堵
        </span>
        <span class="badge badge-muted">{{ videoStore.sourceName || '—' }}</span>
      </div>
    </section>

    <Transition name="fade">
      <div v-if="message" class="notice" :class="`notice-${messageTone}`">{{ message }}</div>
    </Transition>

    <!-- 视频网格 -->
    <section v-if="wallCameras.length" class="wall" :style="{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }">
      <VideoTile
        v-for="cam in wallCameras"
        :key="cam.cameraNum"
        :camera-num="cam.cameraNum"
        :label="cam.label"
        :stream-url="cam.streamUrl"
        :detecting="cam.detecting"
        :total-vehicles="cam.totalVehicles"
        :congestion-level="cam.congestionLevel"
        :show-labels="showLabels"
        @remove="removeCamera"
        @detect="detectOne"
        @expand="expandCamera"
      />
    </section>

    <!-- 放大查看 -->
    <VideoLightbox
      v-if="expanded"
      :camera-num="expanded.cameraNum"
      :label="expanded.label"
      :stream-url="expanded.streamUrl"
      :detecting="expanded.detecting"
      :total-vehicles="expanded.totalVehicles"
      :congestion-level="expanded.congestionLevel"
      @detect="detectOne"
      @close="expanded = null"
    />

    <!-- 空状态 -->
    <section v-else class="panel empty-state">
      <div class="empty-icon">▦</div>
      <div class="empty-title">监控墙为空</div>
      <p class="text-dim">
        点击「加载摄像头」从<strong>{{ videoStore.sourceName || '当前视频源' }}</strong
        >随机获取点位
      </p>
      <div class="empty-actions">
        <button class="btn btn-primary" :disabled="busy" @click="loadRandom(6)">
          加载 6 路摄像头
        </button>
        <RouterLink to="/map" class="btn">去地图查看点位 →</RouterLink>
      </div>
      <p v-if="videoStore.error" class="text-dim small">
        {{ videoStore.error }}
        <br />
        {{ videoStore.hint }}
      </p>
    </section>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 10px 12px;
}
.tool-group {
  display: flex;
  align-items: center;
  gap: 6px;
}
.tool-group.grow {
  flex: 1;
  min-width: 240px;
}
.tool-label {
  color: var(--text-faint);
  font-size: 12px;
}
.grow {
  flex: 1;
}

.wall {
  display: grid;
  gap: 10px;
}

.notice {
  padding: 8px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.notice-ok {
  background: rgba(16, 185, 129, 0.12);
  color: var(--ok);
}
.notice-warn {
  background: rgba(250, 204, 21, 0.12);
  color: var(--warn);
}
.notice-danger {
  background: rgba(239, 68, 68, 0.12);
  color: var(--danger);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 64px 24px;
  text-align: center;
}
.empty-icon {
  font-size: 42px;
  color: var(--text-faint);
}
.empty-title {
  font-size: 16px;
  font-weight: 600;
}
.empty-actions {
  display: flex;
  gap: 10px;
  margin-top: 6px;
}
.small {
  font-size: 12px;
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
