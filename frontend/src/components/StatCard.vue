<!-- 统计卡片 -->
<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    value: string | number
    hint?: string
    tone?: 'default' | 'ok' | 'warn' | 'danger' | 'accent'
    icon?: string
  }>(),
  { tone: 'default', hint: '', icon: '' },
)
</script>

<template>
  <div class="stat-card panel" :class="`tone-${tone}`">
    <div class="stat-head">
      <span v-if="icon" class="stat-icon">{{ icon }}</span>
      <span class="stat-label">{{ label }}</span>
    </div>
    <div class="stat-value mono">{{ value }}</div>
    <div v-if="hint" class="stat-hint">{{ hint }}</div>
  </div>
</template>

<style scoped>
.stat-card {
  padding: 12px 14px;
  border-left: 3px solid var(--border-strong);
}
.stat-head {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-dim);
  font-size: 12px;
}
.stat-icon {
  font-size: 13px;
}
.stat-value {
  margin-top: 6px;
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--text);
}
.stat-hint {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-faint);
}

.tone-ok {
  border-left-color: var(--ok);
}
.tone-ok .stat-value {
  color: var(--ok);
}
.tone-warn {
  border-left-color: var(--warn);
}
.tone-warn .stat-value {
  color: var(--warn);
}
.tone-danger {
  border-left-color: var(--danger);
}
.tone-danger .stat-value {
  color: var(--danger);
}
.tone-accent {
  border-left-color: var(--accent);
}
.tone-accent .stat-value {
  color: var(--accent);
}
</style>
