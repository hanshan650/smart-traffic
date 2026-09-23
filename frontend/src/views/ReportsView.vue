<!--
  民众上报复核
  ============
  这是民众参与进入正式流程的**唯一闸门**。

  设计上的取舍：

  1. **默认只看待复核的**。值班员打开这页是来处理积压的，把已处理的
     混进来只会稀释注意力。要看历史可以切状态筛选。

  2. **联系方式在这里不脱敏**。值班员需要打电话核实 —— 这也是这一页
     属于警务端而不是民众端的原因。旁边同时显示脱敏形式，便于对外
     沟通时引用。

  3. **「通过」会真的生成事件**。它不是一次单纯的标记，而是把民间线索
     转成正式告警。所以按钮文案写明「通过并生成事件」，避免误点。

  4. **驳回必须填意见**。理由会展示给上报人（进度页可见）。不写理由
     的驳回等于告诉用户"以后别报了"。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { reviewApi } from '@/api/citizen'
import { describeError } from '@/api/client'
import { relativeTime } from '@/utils/time'
import type { InternalCitizenReport } from '@/types/citizen'

const STATUS_OPTIONS = [
  { key: 'pending', label: '待复核' },
  { key: 'approved', label: '已通过' },
  { key: 'rejected', label: '已驳回' },
  { key: 'duplicate', label: '重复' },
]

const reports = ref<InternalCitizenReport[]>([])
const stats = ref<{ total: number; pending: number; byStatus: Record<string, number>; byType: Record<string, number> } | null>(null)
const filterStatus = ref('pending')
const current = ref<InternalCitizenReport | null>(null)
const loading = ref(false)
const busy = ref(false)
const message = ref('')
const tone = ref<'ok' | 'warn' | 'danger'>('ok')

// 复核表单
const reviewer = ref('')
const note = ref('')
const level = ref('')

const REVIEWER_KEY = 'police.reviewer'

const summaryCards = computed(() => [
  { label: '上报总数', value: stats.value?.total ?? 0, color: 'var(--accent)' },
  { label: '待复核', value: stats.value?.pending ?? 0, color: '#facc15' },
  { label: '已通过', value: stats.value?.byStatus?.approved ?? 0, color: '#10b981' },
  { label: '已驳回', value: stats.value?.byStatus?.rejected ?? 0, color: '#64748b' },
])

function notify(text: string, next: 'ok' | 'warn' | 'danger' = 'ok'): void {
  message.value = text
  tone.value = next
  window.setTimeout(() => {
    if (message.value === text) message.value = ''
  }, 4200)
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [list, overview] = await Promise.all([
      reviewApi.list({ status: filterStatus.value, limit: 50 }),
      reviewApi.stats(),
    ])
    reports.value = list.items
    stats.value = overview
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    loading.value = false
  }
}

function select(item: InternalCitizenReport): void {
  current.value = item
  note.value = ''
  level.value = item.level || ''
}

async function review(action: string, label: string): Promise<void> {
  if (!current.value) return
  if (!reviewer.value.trim()) {
    notify('请先填写复核人（警号），复核需要留痕', 'warn')
    return
  }
  // 驳回必须给理由：它会展示给上报人，不写理由等于让用户不再上报
  if (action !== 'approve' && !note.value.trim()) {
    notify(`请填写${label}的理由，该理由会展示给上报人`, 'warn')
    return
  }

  busy.value = true
  try {
    const updated = await reviewApi.review(current.value.reportNo, {
      action,
      reviewer: reviewer.value.trim(),
      note: note.value.trim(),
      level: action === 'approve' ? level.value : '',
    })
    notify(
      action === 'approve'
        ? `已通过并生成事件${updated.eventId ? `（${updated.eventId.slice(-6)}）` : ''}`
        : `已${label}`,
      'ok',
    )
    current.value = null
    await load()
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  // 复核人从本地记忆读取：值班员整天反复填同一个警号没有意义
  reviewer.value = window.localStorage.getItem(REVIEWER_KEY) ?? ''
  await load()
})

function rememberReviewer(): void {
  if (reviewer.value.trim()) {
    window.localStorage.setItem(REVIEWER_KEY, reviewer.value.trim())
  }
}
</script>

<template>
  <div class="page">
    <section class="panel">
      <div class="panel-title">
        <span>◈ 民众上报复核</span>
        <span class="text-faint small">
          民众上报不直接生成告警，需核实后方可转入正式流程
        </span>
      </div>

      <div class="cards">
        <div v-for="item in summaryCards" :key="item.label" class="mini-card">
          <span class="mini-label">{{ item.label }}</span>
          <span class="mini-value" :style="{ color: item.color }">{{ item.value }}</span>
        </div>
      </div>

      <div class="reviewer-row">
        <label class="small text-dim">复核人（警号）</label>
        <input
          v-model="reviewer"
          class="input"
          placeholder="必填，用于留痕"
          @blur="rememberReviewer"
        />
      </div>
    </section>

    <div class="split">
      <!-- 队列 -->
      <section class="panel list-panel">
        <div class="panel-title">
          <span>▤ 上报队列</span>
          <button class="btn btn-sm" :disabled="loading" @click="load">↻</button>
        </div>

        <div class="tabs">
          <button
            v-for="item in STATUS_OPTIONS"
            :key="item.key"
            class="tab"
            :class="{ active: filterStatus === item.key }"
            @click="filterStatus = item.key; load()"
          >
            {{ item.label }}
          </button>
        </div>

        <div v-if="loading" class="empty">加载中…</div>
        <div v-else-if="!reports.length" class="empty">
          {{ filterStatus === 'pending' ? '当前没有待复核的上报' : '该状态下暂无记录' }}
        </div>

        <ul v-else class="report-list">
          <li
            v-for="item in reports"
            :key="item.reportNo"
            class="report-item"
            :class="{ active: current?.reportNo === item.reportNo }"
            @click="select(item)"
          >
            <div class="report-top">
              <span class="mono small">{{ item.reportNo }}</span>
              <span class="text-faint small">{{ relativeTime(item.createdAt) }}</span>
            </div>
            <div class="report-type">{{ item.reportTypeLabel }}</div>
            <div class="report-desc text-dim small">{{ item.description || item.locationText }}</div>
          </li>
        </ul>
      </section>

      <!-- 详情 -->
      <section class="panel detail-panel">
        <div v-if="!current" class="empty">请选择左侧上报查看详情</div>

        <template v-else>
          <div class="panel-title">
            <span>◈ {{ current.reportNo }}</span>
            <span
              class="badge"
              :class="current.status === 'pending' ? 'tag-warn' : 'tag-ok'"
            >
              {{ current.status === 'pending' ? '待复核' : current.status }}
            </span>
          </div>

          <div class="kv">
            <div><span>类型</span><b>{{ current.reportTypeLabel }}</b></div>
            <div><span>默认级别</span><b>{{ current.level }}</b></div>
            <div><span>位置</span><b>{{ current.locationText || '—' }}</b></div>
            <div><span>路段</span><b>{{ current.roadName || '—' }}</b></div>
            <div>
              <span>坐标</span>
              <b class="mono">
                {{ current.latitude != null ? `${current.latitude}, ${current.longitude}` : '—' }}
              </b>
            </div>
            <div><span>提交时间</span><b>{{ relativeTime(current.createdAt) }}</b></div>
          </div>

          <div class="block-head"><span>情况描述</span></div>
          <p class="desc-text">{{ current.description || '（未填写描述）' }}</p>

          <img v-if="current.imageUrl" :src="current.imageUrl" class="snapshot" alt="上报照片" />

          <!-- 联系方式：警务端不脱敏，这是打电话核实所必需的 -->
          <div class="block-head">
            <span>联系方式</span>
            <span class="text-faint small">仅警务端可见，对外沟通请使用脱敏形式</span>
          </div>
          <div class="contact-box">
            <div v-if="current.contact">
              <span class="mono contact-raw">{{ current.contact }}</span>
              <span class="text-faint small">（对外显示：{{ current.contactMasked }}）</span>
            </div>
            <div v-else class="text-faint small">上报人未留联系方式</div>
          </div>

          <!-- 复核 -->
          <template v-if="current.status === 'pending'">
            <div class="block-head"><span>复核</span></div>

            <label class="field">
              <span>紧急级别（通过时生效）</span>
              <select v-model="level" class="select">
                <option value="info">提示</option>
                <option value="warning">注意</option>
                <option value="critical">严重</option>
              </select>
            </label>

            <label class="field">
              <span>复核意见（会展示给上报人，「通过」时为选填）</span>
              <textarea
                v-model="note"
                class="input textarea"
                rows="2"
                placeholder="例如：已联系路政清理 / 该处为施工路段，非突发情况"
              />
            </label>

            <div class="row-actions">
              <button class="btn btn-sm btn-primary" :disabled="busy" @click="review('approve', '通过')">
                通过并生成事件
              </button>
              <button class="btn btn-sm" :disabled="busy" @click="review('reject', '驳回')">
                驳回
              </button>
              <button class="btn btn-sm" :disabled="busy" @click="review('duplicate', '判为重复')">
                判为重复
              </button>
            </div>
          </template>

          <template v-else>
            <div class="block-head"><span>复核结果</span></div>
            <div class="kv">
              <div><span>复核人</span><b class="mono">{{ current.reviewedBy || '—' }}</b></div>
              <div><span>复核时间</span><b>{{ relativeTime(current.reviewedAt) }}</b></div>
              <div>
                <span>生成事件</span>
                <b class="mono">{{ current.eventId ? current.eventId.slice(-8) : '—' }}</b>
              </div>
            </div>
            <p v-if="current.reviewNote" class="desc-text">{{ current.reviewNote }}</p>
          </template>
        </template>
      </section>
    </div>

    <Transition name="fade">
      <div v-if="message" class="toast" :class="`toast-${tone}`">{{ message }}</div>
    </Transition>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.small {
  font-size: 12px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 10px;
}
.mini-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  background: var(--bg-panel-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.mini-label {
  font-size: 11px;
  color: var(--text-faint);
}
.mini-value {
  font-family: var(--font-mono);
  font-size: 22px;
  font-weight: 600;
}

.reviewer-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}
.reviewer-row .input {
  width: 220px;
}

.split {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 12px;
  align-items: start;
}
.list-panel {
  position: sticky;
  top: 12px;
}
@media (max-width: 920px) {
  .split {
    grid-template-columns: 1fr;
  }
  .list-panel {
    position: static;
  }
  .report-list {
    max-height: 300px;
  }
}

.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.tab {
  padding: 5px 11px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: none;
  color: var(--text-faint);
  font-size: 12px;
  cursor: pointer;
}
.tab.active {
  border-color: var(--accent);
  color: var(--accent);
}

.report-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 62vh;
  overflow-y: auto;
}
.report-item {
  padding: 9px 10px;
  margin-bottom: 6px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-panel-2);
  cursor: pointer;
}
.report-item:hover {
  background: var(--bg-hover);
}
.report-item.active {
  border-color: var(--accent);
}
.report-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.report-type {
  margin-top: 3px;
  font-size: 13px;
}
.report-desc {
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kv {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 8px 16px;
  padding: 8px 0;
}
.kv > div {
  display: flex;
  gap: 8px;
  font-size: 12px;
}
.kv span {
  color: var(--text-faint);
  min-width: 54px;
}
.kv b {
  font-weight: 500;
  color: var(--text);
}

.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 14px 0 8px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--text-dim);
}

.desc-text {
  margin: 0;
  padding: 10px 12px;
  background: var(--bg-panel-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: 1.7;
}

.snapshot {
  width: 100%;
  max-height: 320px;
  object-fit: contain;
  margin-top: 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: #000;
}

.contact-box {
  padding: 10px 12px;
  background: var(--bg-panel-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 12px;
}
.contact-raw {
  font-size: 15px;
  font-weight: 600;
  margin-right: 8px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 10px;
  font-size: 12px;
  color: var(--text-dim);
}
.input.textarea {
  resize: vertical;
  font-family: inherit;
}

.row-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.tag-warn {
  color: var(--warn);
}
.tag-ok {
  color: var(--ok);
}

.empty {
  padding: 24px;
  text-align: center;
  color: var(--text-faint);
  font-size: 13px;
  line-height: 1.8;
}

.toast {
  position: fixed;
  left: 50%;
  bottom: 32px;
  transform: translateX(-50%);
  max-width: 76vw;
  padding: 9px 18px;
  border-radius: var(--radius);
  background: var(--bg-panel-2);
  border: 1px solid var(--border-strong);
  font-size: 13px;
  z-index: 1500;
}
.toast-ok {
  border-color: var(--ok);
}
.toast-warn {
  border-color: var(--warn);
}
.toast-danger {
  border-color: var(--danger);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
