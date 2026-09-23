<!--
  出行建议
  ========
  这一页最容易滑向"假装是导航"。

  它做的其实是：**把当前车流最集中的几个路段列出来，建议避开**。
  不做路径规划 —— 那需要路网拓扑与逐路段实时速度，超出本系统的数据范围。

  所以页面顶部就明确写着「不是导航」。给一个看起来像导航、实际不靠谱的
  结果，比坦白说明"这只是参考"危害更大：用户可能真的照着走。

  免责声明由后端下发（`/public/advice` 的 `disclaimer` 字段），
  前端不另写一份 —— 口径只应有一个地方定义。
-->
<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { publicApi } from '@/api/citizen'
import { describeError } from '@/api/client'
import { CITIZEN_CONGESTION_COLOR } from '@/types/citizen'
import type { PublicAdvice } from '@/types/citizen'

const advice = ref<PublicAdvice | null>(null)
const loading = ref(true)
const error = ref('')

async function load(): Promise<void> {
  loading.value = true
  try {
    advice.value = await publicApi.advice(6)
    error.value = ''
  } catch (err) {
    error.value = describeError(err).message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="advice">
    <section class="c-card intro-card">
      <h2 class="c-title">出行参考</h2>
      <p class="c-sub">
        基于监测点的实时车流，提示当前车流集中的路段。
      </p>
      <!-- 免责声明来自后端，保证前后端口径一致 -->
      <p class="disclaimer">{{ advice?.disclaimer }}</p>
    </section>

    <div v-if="loading" class="c-card placeholder">正在获取…</div>

    <div v-else-if="error" class="c-card error-box">{{ error }}</div>

    <template v-else>
      <section v-if="!advice?.avoid.length" class="c-card empty-box">
        <span class="empty-icon">✓</span>
        <strong>当前没有明显拥堵路段</strong>
        <p>监测范围内路况正常，可正常通行。</p>
      </section>

      <section v-else class="c-card">
        <h3 class="c-section-title">建议关注的路段</h3>
        <ul class="avoid-list">
          <li v-for="(item, index) in advice.avoid" :key="index" class="avoid-item">
            <span
              class="avoid-bar"
              :style="{ background: CITIZEN_CONGESTION_COLOR[item.congestionLevel] ?? '#ea580c' }"
            />
            <div class="avoid-body">
              <div class="avoid-head">
                <span class="avoid-name">{{ item.roadName }}</span>
                <span
                  class="avoid-badge"
                  :style="{
                    color: CITIZEN_CONGESTION_COLOR[item.congestionLevel] ?? '#ea580c',
                  }"
                >
                  {{ item.congestionLabel }}
                </span>
              </div>
              <p class="avoid-suggestion">{{ item.suggestion }}</p>
              <p class="avoid-meta">监测到约 {{ item.vehicleCount }} 辆车</p>
            </div>
          </li>
        </ul>
      </section>

      <section class="c-card">
        <h3 class="c-section-title">出行前建议</h3>
        <ul class="tips">
          <li>出发前查看路况，尽量避开上表中标为「严重拥堵」的路段</li>
          <li>高速上遇拥堵请勿占用应急车道，那会阻断救援通道</li>
          <li>长途出行预留额外时间，拥堵时的通行时间可能成倍增加</li>
          <li>遇恶劣天气主动降速，必要时就近驶入服务区等待</li>
        </ul>
      </section>
    </template>
  </div>
</template>

<style scoped>
.advice {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.c-card {
  padding: 14px 16px;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--c-radius);
  box-shadow: var(--c-shadow);
}

.c-title {
  margin: 0 0 4px;
  font-size: var(--fs-lg);
}
.c-sub {
  margin: 0 0 10px;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
  line-height: 1.6;
}
.c-section-title {
  margin: 0 0 10px;
  font-size: var(--fs-base);
  font-weight: 600;
  color: var(--c-text-dim);
}

.intro-card {
  background: var(--c-primary-soft);
  border-color: #bfdbfe;
}
.disclaimer {
  margin: 0;
  font-size: var(--fs-xs);
  color: var(--c-text-dim);
  line-height: 1.7;
  padding-top: 9px;
  border-top: 1px dashed #bfdbfe;
}

.placeholder,
.empty-box {
  text-align: center;
  color: var(--c-text-faint);
  font-size: var(--fs-base);
}
.placeholder {
  padding: 32px 16px;
}
.empty-box {
  padding: 26px 16px;
}
.empty-icon {
  display: block;
  font-size: var(--fs-xl);
  color: var(--c-ok);
  margin-bottom: 6px;
}
.empty-box strong {
  display: block;
  margin-bottom: 4px;
  color: var(--c-text);
}
.empty-box p {
  margin: 0;
  font-size: var(--fs-sm);
}

.error-box {
  color: var(--c-danger);
  font-size: var(--fs-base);
  background: #fef2f2;
  border-color: #fecaca;
}

/* ---------------------------------------------------------------- 列表 */

.avoid-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.avoid-item {
  display: flex;
  gap: 11px;
  padding: 11px 12px;
  background: var(--c-surface-2);
  border-radius: 11px;
}
.avoid-bar {
  flex: 0 0 auto;
  width: 3px;
  border-radius: 2px;
}
.avoid-body {
  flex: 1;
  min-width: 0;
}
.avoid-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.avoid-name {
  font-size: var(--fs-md);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.avoid-badge {
  flex: 0 0 auto;
  font-size: var(--fs-sm);
  font-weight: 600;
}
.avoid-suggestion {
  margin: 4px 0 0;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
}
.avoid-meta {
  margin: 2px 0 0;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}

.tips {
  margin: 0;
  padding-left: 18px;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
  line-height: 1.9;
}
.tips li::marker {
  color: var(--c-primary);
}
</style>
