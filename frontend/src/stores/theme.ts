/**
 * 主题状态（白天 / 黑夜）
 * ======================
 * 三种模式：
 *
 *  · ``auto`` —— 跟随时间，**18:00–次日 06:00 为黑夜**，其余为白天（默认）
 *  · ``light`` / ``dark`` —— 手动固定，用于演示或值班室临时调整
 *
 * 主题通过 ``<html data-theme="light|dark">`` 下发，所有配色都走 CSS 变量
 * （见 ``styles/global.css``）。这样夜间模式不会漏掉某个组件的硬编码颜色，
 * 也不需要给每个页面都传一遍 props。
 *
 * 时间判定放在前端而不是服务端：主题纯属展示层偏好，没必要为它多打一次接口；
 * 用户手动选择的结果写进 localStorage，刷新与跨标签页都保持一致。
 */
import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'

export type ThemeMode = 'auto' | 'light' | 'dark'
export type ThemeTone = 'light' | 'dark'

/** 与 index.html 中的首屏脚本保持一致，改名需两处同步 */
export const THEME_STORAGE_KEY = 'traffic:theme-mode'

/** 黑夜时段起点（含） */
export const NIGHT_START_HOUR = 18
/** 黑夜时段终点（不含） */
export const NIGHT_END_HOUR = 6

/** 按小时判定：18:00–23:59 与 00:00–05:59 属于黑夜 */
export function toneForHour(hour: number): ThemeTone {
  return hour >= NIGHT_START_HOUR || hour < NIGHT_END_HOUR ? 'dark' : 'light'
}

export function toneForDate(date: Date): ThemeTone {
  return toneForHour(date.getHours())
}

/** 读取本地偏好。隐私模式下 localStorage 可能抛异常，按默认 auto 处理 */
export function readThemeMode(): ThemeMode {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    if (stored === 'auto' || stored === 'light' || stored === 'dark') return stored
  } catch {
    // 存储不可用时不影响主题本身
  }
  return 'auto'
}

/**
 * 把主题写到 DOM。
 *
 * 同时设置 ``color-scheme``：原生控件（滚动条、日期选择器、输入框自动填充）
 * 才会跟着变，否则白天的下拉框在黑夜主题里仍是亮色。
 */
export function applyTheme(tone: ThemeTone): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.dataset.theme = tone
  root.style.colorScheme = tone

  // 移动端地址栏 / 状态栏配色
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) {
    meta.setAttribute('content', tone === 'dark' ? '#070b14' : '#eef2f7')
  }
}

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>(readThemeMode())
  /** 每分钟推进一次，用于 auto 模式跨过 18:00 / 06:00 时自动切换 */
  const now = ref(new Date())

  const tone = computed<ThemeTone>(() =>
    mode.value === 'auto' ? toneForDate(now.value) : mode.value,
  )
  const isNight = computed(() => tone.value === 'dark')

  const modeLabel = computed(() => {
    if (mode.value === 'light') return '白天'
    if (mode.value === 'dark') return '黑夜'
    return isNight.value ? '自动 · 夜间' : '自动 · 白天'
  })

  const hint = computed(() => {
    if (mode.value === 'auto') {
      return `主题：跟随时间（当前${isNight.value ? '黑夜' : '白天'}，18:00–06:00 自动切换黑夜）`
    }
    return `主题：${modeLabel.value}（点击切换）`
  })

  const icon = computed(() => {
    if (mode.value === 'light') return '☀'
    if (mode.value === 'dark') return '☾'
    return '◐'
  })

  // 无论 auto 还是手动，只要最终色调变化就同步到 DOM
  watch(tone, (value) => applyTheme(value), { immediate: true })

  let started = false

  function refresh(): void {
    now.value = new Date()
  }

  function setMode(next: ThemeMode): void {
    mode.value = next
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next)
    } catch {
      // 存不下就只在本次会话生效
    }
  }

  /** 手动 → 自动 循环，供顶栏单按钮使用 */
  function cycle(): void {
    if (mode.value === 'auto') setMode('light')
    else if (mode.value === 'light') setMode('dark')
    else setMode('auto')
  }

  /**
   * 启动自动判定。
   *
   * 定时器用 30 秒而不是 1 分钟：整点时最多延迟 30 秒切换，
   * 而代价只是一次极轻量的时间比较。切回标签页时立即重新判定，
   * 避免笔记本合盖一夜后仍停留在旧主题。
   */
  function init(): void {
    if (started) return
    started = true

    refresh()
    // 定时器与页面同生命周期：应用只有一次挂载，不需要在别处清理
    setInterval(refresh, 30_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('focus', refresh)
  }

  function onVisibilityChange(): void {
    if (document.visibilityState === 'visible') refresh()
  }

  return { mode, tone, isNight, modeLabel, hint, icon, init, refresh, setMode, cycle }
})
