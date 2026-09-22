/**
 * HLS 播放器托管
 * ==============
 * 单路 HLS 的挂载 / 重挂 / 销毁与状态机。
 *
 * 抽成 composable 的原因：监控瓦片与放大视图都要播同一路流，
 * 若各写一遍，Hls.js 的实例生命周期（尤其是 ``destroy`` 时机）
 * 很容易在其中一处漏掉，导致内存与网络连接泄漏。
 */
import Hls from 'hls.js'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Ref } from 'vue'

export type HlsStatus = 'loading' | 'playing' | 'error'

export function useHls(videoEl: Ref<HTMLVideoElement | null>, url: Ref<string>) {
  const status = ref<HlsStatus>('loading')
  let hls: Hls | null = null

  function destroy(): void {
    if (hls) {
      hls.destroy()
      hls = null
    }
    const video = videoEl.value
    if (video) {
      video.removeAttribute('src')
      video.load()
    }
  }

  /**
   * 挂载播放器。
   *
   * ``maxBufferLength`` 压得较小是刻意的：监控墙同时播多路，
   * 缓冲过大会让延迟持续累积，反而看不到"实时"画面。
   */
  function attach(target: string): void {
    destroy()

    const video = videoEl.value
    if (!video || !target) {
      status.value = 'error'
      return
    }

    status.value = 'loading'

    if (Hls.isSupported()) {
      hls = new Hls({ maxBufferLength: 5, enableWorker: false, lowLatencyMode: true })
      hls.loadSource(target)
      hls.attachMedia(video)
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        void video.play().catch(() => undefined)
        status.value = 'playing'
      })
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) status.value = 'error'
      })
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = target
      void video.play().catch(() => undefined)
      status.value = 'playing'
    } else {
      status.value = 'error'
    }
  }

  onMounted(() => attach(url.value))
  watch(url, (next) => attach(next))
  onBeforeUnmount(destroy)

  return { status, attach, destroy }
}
