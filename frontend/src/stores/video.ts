/**
 * 视频源 Store
 * ============
 * 管理视频源列表、摄像头清单与实时快照。
 *
 * 设计要点
 * --------
 * · 视频源通过 key 切换，当前默认 ``henan``；
 *   ``shanghai`` 会返回明确的不可用原因，前端据此展示提示而非报错
 * · 摄像头列表支持「追加加载」（传 exclude 排除已添加项）
 * · 快照由 WebSocket ``snapshot`` 频道实时更新，也支持一次性 REST 拉取
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { videoApi } from '@/api'
import { describeError } from '@/api/client'
import type { CameraInfo, SnapshotPayload, VideoSourceInfo } from '@/types/api'

export const useVideoStore = defineStore('video', () => {
  const sources = ref<VideoSourceInfo[]>([])
  const currentSourceKey = ref('henan')
  const sourceName = ref('')
  const cameras = ref<CameraInfo[]>([])
  const totalAvailable = ref(0)
  const totalAll = ref(0)

  /** cameraNum → 最新快照 */
  const snapshots = ref<Record<string, SnapshotPayload>>({})

  const loading = ref(false)
  const error = ref('')
  const hint = ref('')

  const currentSource = computed<VideoSourceInfo | undefined>(() =>
    sources.value.find((item) => item.key === currentSourceKey.value),
  )

  const activeCameras = computed(() => cameras.value.filter((cam) => cam.online === 1))

  // ------------------------------------------------------------------ 视频源

  async function loadSources(): Promise<void> {
    try {
      const data = await videoApi.sources()
      sources.value = data.sources
      currentSourceKey.value = data.current
      sourceName.value = data.sources.find((s) => s.key === data.current)?.displayName ?? ''
    } catch (err) {
      const info = describeError(err)
      error.value = info.message
      hint.value = info.hint
    }
  }

  // ---------------------------------------------------------------- 摄像头

  /**
   * 加载摄像头列表。
   * @param append ``true`` 时追加到现有列表（排除已有项），``false`` 时整体替换
   */
  async function loadCameras(count = 8, append = false): Promise<number> {
    loading.value = true
    error.value = ''
    hint.value = ''

    try {
      const exclude = append ? cameras.value.map((cam) => cam.cameraNum).join(',') : ''
      const data = await videoApi.cameras({
        source: currentSourceKey.value,
        count,
        exclude,
      })

      const incoming = data.cameras.filter(
        (cam) => !cameras.value.some((existing) => existing.cameraNum === cam.cameraNum),
      )

      cameras.value = append ? [...cameras.value, ...incoming] : data.cameras
      sourceName.value = data.sourceName
      totalAvailable.value = data.totalAvailable
      totalAll.value = data.totalAll

      return incoming.length
    } catch (err) {
      const info = describeError(err)
      error.value = info.message
      hint.value = info.hint
      return 0
    } finally {
      loading.value = false
    }
  }

  function clearCameras(): void {
    cameras.value = []
    snapshots.value = {}
  }

  // ---------------------------------------------------------------- 流地址

  /**
   * 获取**可播放**的流地址。
   *
   * 返回的是后端同源代理地址（``/api/v1/video/hls/...``）：
   * 上游 m3u8 与 ts 分片均校验 Referer，浏览器直连会被拒且存在跨域限制，
   * 因此统一走后端代理。若旧版后端未返回代理地址，则回退到上游地址。
   *
   * 注意：上游 token 有时效，请勿长期缓存返回值。
   */
  async function getStreamUrl(cameraNum: string): Promise<string> {
    const data = await videoApi.streamUrl({
      source: currentSourceKey.value,
      cameraNum,
    })
    return data.hlsProxyUrl || data.streamUrl
  }

  // ---------------------------------------------------------------- 快照

  /** 应用来自 WebSocket 的实时快照 */
  function applySnapshot(payload: SnapshotPayload): void {
    snapshots.value = { ...snapshots.value, [payload.cameraId]: payload }
  }

  function getSnapshot(cameraId: string): SnapshotPayload | undefined {
    return snapshots.value[cameraId]
  }

  return {
    sources,
    currentSourceKey,
    currentSource,
    sourceName,
    cameras,
    activeCameras,
    totalAvailable,
    totalAll,
    snapshots,
    loading,
    error,
    hint,
    loadSources,
    loadCameras,
    clearCameras,
    getStreamUrl,
    applySnapshot,
    getSnapshot,
  }
})
