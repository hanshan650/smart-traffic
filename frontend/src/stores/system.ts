/**
 * 系统状态 Store
 * ==============
 * 承载健康状态、运行时配置与统计概览。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { systemApi } from '@/api'
import { describeError } from '@/api/client'
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
