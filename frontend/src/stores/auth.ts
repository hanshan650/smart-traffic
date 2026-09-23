/**
 * 登录态
 * ======
 *
 * **前端不存会话值**。会话走 HttpOnly Cookie，JS 读不到（这是选它的理由之一：
 * XSS 拿不走）。这里只存"当前是谁"，用于渲染与按角色控制界面。
 *
 * 因此刷新页面后必须问一次后端（`restore`），不能像 JWT 方案那样从
 * localStorage 恢复。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { authApi } from '@/api'
import type { AuthUser } from '@/types/api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null)

  /**
   * 是否已经问过后端"我是谁"。
   *
   * 路由守卫必须等这个为 true 才能判断要不要跳登录 ——
   * 否则刷新页面的瞬间会先跳登录、再被 `restore` 的结果拽回来，
   * 用户看到一次明显的闪烁。
   */
  const ready = ref(false)
  const loading = ref(false)

  const isLoggedIn = computed(() => user.value !== null)
  const role = computed(() => user.value?.role ?? '')
  const displayName = computed(() => user.value?.name || user.value?.officerId || '')

  /** 幂等：守卫每个路由都会调，但只有第一次真正发请求 */
  async function restore(): Promise<void> {
    if (ready.value || loading.value) return
    loading.value = true
    try {
      const result = await authApi.me()
      user.value = result.user
    } catch {
      // 401 是正常情况（还没登录），不是错误。交给守卫决定跳不跳登录页
      user.value = null
    } finally {
      loading.value = false
      ready.value = true
    }
  }

  async function login(officerId: string, password: string): Promise<void> {
    const result = await authApi.login(officerId, password)
    user.value = result.user
    ready.value = true
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } finally {
      // 即使请求失败也要清掉本地状态：否则界面还显示着已登录，
      // 而后续每个请求都在 401，比直接跳登录页更难排查
      user.value = null
    }
  }

  /** 角色判断。界面按它隐藏按钮 —— 但**真正的拦截在后端**，这里只是体验 */
  function hasRole(...roles: string[]): boolean {
    return !!user.value && roles.includes(user.value.role)
  }

  return {
    user, ready, loading,
    isLoggedIn, role, displayName,
    restore, login, logout, hasRole,
  }
})
