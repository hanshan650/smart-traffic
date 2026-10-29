<!--
  主题切换按钮
  ============
  按「自动 → 白天 → 黑夜」循环，默认自动（18:00–06:00 黑夜）。
  单按钮而不是三段开关：顶栏本来就有三个状态徽章，再放一个分段控件
  在 1024px 宽度下会把页面标题挤掉。当前模式由图标与文字共同表达，
  完整说明放在 title 里，鼠标悬停可见。
-->
<script setup lang="ts">
import { useThemeStore } from '@/stores/theme'

const theme = useThemeStore()
</script>

<template>
  <button
    class="theme-toggle"
    type="button"
    :title="theme.hint"
    :aria-label="theme.hint"
    @click="theme.cycle()"
  >
    <span class="theme-icon">{{ theme.icon }}</span>
    <span class="theme-label">{{ theme.modeLabel }}</span>
  </button>
</template>

<style scoped>
.theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  background: var(--bg-panel-2);
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.6;
  white-space: nowrap;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.theme-toggle:hover {
  border-color: var(--accent);
  color: var(--text);
}
.theme-toggle:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.theme-icon {
  font-size: 13px;
  line-height: 1;
  color: var(--accent);
}

/* 窄屏只留图标：顶栏此时已经隐藏了状态文字，主题按钮也不该再占宽度 */
@media (max-width: 1180px) {
  .theme-label {
    display: none;
  }
  .theme-toggle {
    padding: 4px 8px;
  }
}
</style>
