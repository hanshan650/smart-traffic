<!--
  判据证据条
  ==========
  一条判据的「原值 → 归一化得分 → 加权贡献」，用于把一次评分的
  计算过程摊开给操作员看。事故识别与智能派警共用同一套表达方式：
  任何结论都要能追溯到它的每一分是怎么来的。

  ``maxContribution`` 由父组件传入（通常取全部判据中的最大贡献），
  这样同一名候选内部的四条判据可以横向比较长度。
-->
<script setup lang="ts">
const props = defineProps<{
  label: string
  score: number
  weight: number
  contribution: number
  detail?: string
  maxContribution: number
}>()

const width = (): string => {
  if (props.maxContribution <= 0) return '0%'
  const ratio = props.contribution / props.maxContribution
  return `${Math.max(2, Math.min(100, ratio * 100)).toFixed(1)}%`
}
</script>

<template>
  <div class="ev">
    <div class="ev-head">
      <span class="ev-label">{{ label }}</span>
      <span class="ev-num">
        <span class="mono">{{ (score * 100).toFixed(0) }}</span>
        <span class="ev-dim">分 × </span>
        <span class="mono">{{ weight.toFixed(2) }}</span>
        <span class="ev-dim"> = </span>
        <span class="mono ev-contrib">{{ contribution.toFixed(3) }}</span>
      </span>
    </div>
    <div class="ev-track">
      <div class="ev-fill" :style="{ width: width() }" />
    </div>
    <div v-if="detail" class="ev-detail">{{ detail }}</div>
  </div>
</template>

<style scoped>
.ev + .ev {
  margin-top: 6px;
}
.ev-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}
.ev-label {
  color: var(--text-dim);
}
.ev-num {
  font-size: 11px;
  color: var(--text);
  white-space: nowrap;
}
.ev-dim {
  color: var(--text-faint);
}
.ev-contrib {
  color: var(--accent-2, #22d3ee);
}
.ev-track {
  height: 4px;
  margin-top: 3px;
  border-radius: 2px;
  background: var(--bg-hover);
  overflow: hidden;
}
.ev-fill {
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(90deg, var(--accent), var(--accent-2, #22d3ee));
  transition: width 0.3s ease;
}
.ev-detail {
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-faint);
}
</style>
