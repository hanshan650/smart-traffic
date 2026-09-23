<!--
  审计记录
  ========
  车主信息查询的留痕。这一页是给 `auditor` 角色用的 —— 它是四档里最窄的角色，
  只能看这一页（后端也只为主放行了 `/surveillance/audits`）。

  为什么审计员不该看到业务画面：核查"谁在什么时候查了谁的车牌"是合规要求，
  和监控大屏、警情调度是两件事。给多了反而扩大了能接触个人信息的范围。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { surveillanceApi } from '@/api'
import { describeError } from '@/api/client'
import { formatServerTime } from '@/utils/time'
import type { LookupAuditEntry } from '@/types/api'

const items = ref<LookupAuditEntry[]>([])
const stats = ref<{ total: number; byOutcome: Record<string, number> } | null>(null)
const plate = ref('')
const loading = ref(false)
const error = ref('')

/** 结果 → 中文。后端只给英文枚举，展示层自己映射 */
const OUTCOME_LABEL: Record<string, string> = {
  success: '已返回',
  not_found: '未找到',
  unavailable: '通道不可用',
  denied: '已拒绝',
  error: '查询失败',
}

const outcomeRows = computed(() =>
  Object.entries(stats.value?.byOutcome ?? {}).map(([key, count]) => ({
    key,
    label: OUTCOME_LABEL[key] ?? key,
    count,
  })),
)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const result = await surveillanceApi.audits({ limit: 100, plate: plate.value.trim() })
    items.value = result.items
    stats.value = result.stats
  } catch (err) {
    error.value = describeError(err).message
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="audit">
    <section class="panel">
      <div class="panel-title">
        ⧉ 车主信息查询审计
        <span class="panel-sub">每一次查询都留痕，含操作人、事由与结果</span>
      </div>

      <div class="toolbar">
        <input
          v-model="plate"
          class="input"
          placeholder="按车牌筛选（可留空）"
          spellcheck="false"
          @keyup.enter="load()"
        />
        <button class="btn" :disabled="loading" @click="load()">
          {{ loading ? '查询中…' : '刷新' }}
        </button>
      </div>

      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="stats" class="stats">
        <div class="stat">
          <span class="stat-label">记录总数</span>
          <strong>{{ stats.total }}</strong>
        </div>
        <div v-for="row in outcomeRows" :key="row.key" class="stat">
          <span class="stat-label">{{ row.label }}</span>
          <strong>{{ row.count }}</strong>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-title">留痕明细</div>

      <p v-if="!loading && !items.length" class="empty">
        暂无审计记录。车主查询未启用（`SURVEILLANCE_OWNER_PROVIDER=none`）时不会有留痕。
      </p>

      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>时间</th>
              <th>操作人</th>
              <th>车牌</th>
              <th>事由</th>
              <th>通道</th>
              <th>结果</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, index) in items" :key="index">
              <td class="mono nowrap">{{ formatServerTime(item.at) }}</td>
              <td>{{ item.operator || '—' }}</td>
              <td class="mono">{{ item.plate || '—' }}</td>
              <td class="reason">{{ item.reason || '—' }}</td>
              <td>{{ item.source || '—' }}</td>
              <td>
                <span class="outcome" :class="item.outcome === 'success' ? 'ok' : 'warn'">
                  {{ OUTCOME_LABEL[item.outcome] ?? item.outcome }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.audit {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.panel {
  padding: 14px 16px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 12px;
}
.panel-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  font-size: 14px;
  font-weight: 600;
}
.panel-sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-faint);
}

.toolbar {
  display: flex;
  gap: 8px;
}
.input {
  flex: 1;
  min-width: 0;
  max-width: 300px;
  padding: 7px 11px;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  background: var(--bg-panel-2);
  color: var(--text);
  font-size: 14px;
  box-sizing: border-box;
}
.input:focus {
  outline: none;
  border-color: var(--accent-dim);
}
.btn {
  padding: 7px 16px;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  background: var(--bg-panel-2);
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.error {
  margin: 10px 0 0;
  font-size: 13px;
  color: #f87171;
}

.stats {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 12px;
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 92px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--bg-panel-2);
}
.stat-label {
  font-size: 11px;
  color: var(--text-faint);
}
.stat strong {
  font-size: 17px;
}

.empty {
  margin: 0;
  padding: 18px 0;
  font-size: 13px;
  color: var(--text-faint);
  line-height: 1.7;
}

.table-wrap {
  overflow-x: auto;
}
.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.table th {
  padding: 7px 10px;
  text-align: left;
  font-weight: 600;
  font-size: 12px;
  color: var(--text-faint);
  border-bottom: 1px solid var(--border-strong);
  white-space: nowrap;
}
.table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
.table tbody tr:hover {
  background: var(--bg-panel-2);
}
.mono {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12px;
}
.nowrap {
  white-space: nowrap;
}
.reason {
  max-width: 260px;
  color: var(--text-dim);
}
.outcome {
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.outcome.ok {
  background: rgba(74, 222, 128, 0.16);
  color: #4ade80;
}
.outcome.warn {
  background: rgba(251, 191, 36, 0.16);
  color: #fbbf24;
}
</style>
