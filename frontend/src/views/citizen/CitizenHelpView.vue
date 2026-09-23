<!--
  应急与安全
  ==========
  这一页有个和其他页面不同的约束：**它必须在系统其他部分都不可用时仍然可用**。

  所以内容全部来自 `/public/emergency` 这个静态接口（不读数据库、不依赖
  Redis），而且电话号码直接渲染成可点击的 `tel:` 链接 —— 出事的时候
  没人有耐心复制号码再切到拨号盘。

  电话内容由后端维护（`EMERGENCY_CONTACTS`），前端不硬编码 ——
  号码写错一个的代价太高，只应有一个地方定义它。
-->
<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { publicApi } from '@/api/citizen'
import type { EmergencyInfo } from '@/types/citizen'

const info = ref<EmergencyInfo | null>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    info.value = await publicApi.emergency()
  } catch {
    // 静态信息都取不到时，至少给出最关键的号码，不能是一片空白
    info.value = {
      success: false,
      contacts: [
        { name: '交通事故报警', number: '122', note: '' },
        { name: '高速公路报警救援', number: '12122', note: '' },
      ],
      tips: [],
      note: '应急信息加载失败，请直接拨打上述号码。',
    }
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="help">
    <section class="c-card alert-card">
      <p class="alert-text">
        遇紧急情况，<strong>先确保自身安全，再拨打报警电话</strong>。
        本平台不替代报警服务。
      </p>
    </section>

    <section class="c-card">
      <h2 class="c-title">应急电话</h2>
      <p class="c-sub">点击号码可直接拨打。</p>

      <div v-if="loading" class="placeholder">加载中…</div>
      <ul v-else class="contact-list">
        <li v-for="item in info?.contacts ?? []" :key="item.number" class="contact-item">
          <div class="contact-main">
            <span class="contact-name">{{ item.name }}</span>
            <span v-if="item.note" class="contact-note">{{ item.note }}</span>
          </div>
          <a :href="`tel:${item.number}`" class="contact-number">{{ item.number }}</a>
        </li>
      </ul>
    </section>

    <section v-if="info?.tips.length" class="c-card">
      <h2 class="c-title">安全须知</h2>
      <div class="tips">
        <details v-for="(tip, index) in info.tips" :key="index" class="tip" :open="index === 0">
          <summary class="tip-summary">{{ tip.title }}</summary>
          <ol class="tip-steps">
            <li v-for="(step, stepIndex) in tip.steps" :key="stepIndex">{{ step }}</li>
          </ol>
        </details>
      </div>
    </section>

    <p v-if="info?.note" class="footer-note">{{ info.note }}</p>
  </div>
</template>

<style scoped>
.help {
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

.alert-card {
  background: #fef2f2;
  border-color: #fecaca;
}
.alert-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #7f1d1d;
}
.alert-text strong {
  color: var(--c-danger);
}

.c-title {
  margin: 0 0 4px;
  font-size: 16px;
}
.c-sub {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--c-text-dim);
}

.placeholder {
  padding: 20px;
  text-align: center;
  color: var(--c-text-faint);
  font-size: 13px;
}

/* ---------------------------------------------------------------- 电话列表 */

.contact-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.contact-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 13px;
  background: var(--c-surface-2);
  border-radius: 11px;
}
.contact-main {
  min-width: 0;
}
.contact-name {
  display: block;
  font-size: 14px;
  font-weight: 600;
}
.contact-note {
  font-size: 11px;
  color: var(--c-text-faint);
}
.contact-number {
  flex: 0 0 auto;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 19px;
  font-weight: 700;
  color: var(--c-primary);
  text-decoration: none;
  letter-spacing: 0.5px;
  padding: 4px 10px;
  border-radius: 9px;
  background: var(--c-primary-soft);
}
.contact-number:active {
  transform: scale(0.97);
}

/* ---------------------------------------------------------------- 安全须知 */

.tips {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tip {
  border: 1px solid var(--c-border);
  border-radius: 11px;
  overflow: hidden;
}
.tip-summary {
  padding: 11px 13px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  background: var(--c-surface-2);
  list-style: none;
  /* 自定义展开箭头，去掉浏览器默认三角 */
  position: relative;
}
.tip-summary::-webkit-details-marker {
  display: none;
}
.tip-summary::after {
  content: '▾';
  position: absolute;
  right: 13px;
  color: var(--c-text-faint);
  font-size: 11px;
}
.tip[open] .tip-summary::after {
  content: '▴';
}
.tip-steps {
  margin: 0;
  padding: 11px 13px 11px 30px;
  font-size: 12.5px;
  color: var(--c-text-dim);
  line-height: 1.85;
}
.tip-steps li::marker {
  color: var(--c-primary);
  font-weight: 600;
}

.footer-note {
  margin: 0;
  font-size: 11px;
  color: var(--c-text-faint);
  text-align: center;
  line-height: 1.6;
}
</style>
