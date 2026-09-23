/**
 * 系统状态 Store
 * ==============
 * 承载健康状态、运行时配置与统计概览。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { systemApi } from '@/api'
import { describeError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { ClientConfig, HealthStatus, StatsOverview } from '@/types/api'

export const useSystemStore = defineStore('system', () => {
  const health = ref<HealthStatus | null>(null)
  const config = ref<ClientConfig | null>(null)
  const stats = ref<StatsOverview | null>(null)

  const loading = ref(false)
  const error = ref('')

  async function loadHealth(): Promise<void> {
    try {
      health.value = await systemApi.health()
    } catch (err) {
      const { message } = describeError(err)
      error.value = message
      health.value = null
    }
  }

  async function loadConfig(): Promise<void> {
    try {
      config.value = await systemApi.config()
    } catch (err) {
      const { message } = describeError(err)
      error.value = message
    }
  }

  async function loadStats(): Promise<void> {
    // 审计员没有 /stats 的权限（后端只放行了 audits）。不拦一下的话，
    // 每次启动与轮询都会在控制台留一条 403 —— 噪音会掩盖真正的问题，
    // 也会让人以为系统坏了。
    //
    // 在 store 里拦而不是在各个调用点拦：bootstrap 与 refresh 都会走到
    // 这里，放一处就不会漏。
    //
    // 必须等 `ready`：bootstrap 可能在 /auth/me 返回前就跑了，那时角色还是
    // 空的，一刀切会连管理员一起挡掉。角色未定时照常请求 —— cookie 在后端
    // 手上，它会给出正确判断。
    const auth = useAuthStore()
    if (auth.ready && !auth.hasRole('admin', 'dispatcher', 'viewer')) return

    try {
      stats.value = await systemApi.stats()
    } catch {
      // 统计失败不影响主流程，保持上一次的值
    }
  }

  /** 应用启动时调用：并行拉取配置与健康状态 */
  async function bootstrap(): Promise<void> {
    loading.value = true
    error.value = ''
    await Promise.allSettled([loadConfig(), loadHealth(), loadStats()])
    loading.value = false
  }

  /** 定期刷新统计（大屏用） */
  async function refresh(): Promise<void> {
    await Promise.allSettled([loadStats(), loadHealth()])
  }

  return {
    health,
    config,
    stats,
    loading,
    error,
    loadHealth,
    loadConfig,
    loadStats,
    bootstrap,
    refresh,
  }
})
