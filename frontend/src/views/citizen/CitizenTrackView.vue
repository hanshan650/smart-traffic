<!--
  上报进度查询
  ============
  这一页存在的意义是**形成反馈闭环**。

  众包上报最常见的失败不是"没人报"，而是"报了没下文"——
  用户得不到任何回应，下次就不再报了。所以这里做了两件事：

  1. **进度可视化**：把状态映射成三个阶段条，让"在处理"这件事看得见；
  2. **驳回也要说清理由**：`reviewNote` 会被完整展示。即使没被采纳，
     用户也知道组织认真看过 —— 这比一个沉默的"已结束"好得多。

  编号支持通过 URL 查询参数带入（`?no=xxx`），这样从上报成功页
  点"查看进度"可以直达，不必手输。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { citizenReportApi } from '@/api/citizen'
import { describeError } from '@/api/client'
import { REPORT_STATUS_COLOR } from '@/types/citizen'
import { formatServerTime } from '@/utils/time'
import { forgetReport, loadMyReports, updateMyReport } from '@/utils/myReports'
import type { MyReportEntry } from '@/utils/myReports'
import type { CitizenReport } from '@/types/citizen'

const route = useRoute()

const reportNo = ref('')
const report = ref<CitizenReport | null>(null)
const loading = ref(false)
const error = ref('')

/** 三个阶段，用于渲染进度条 */
const STAGES = ['已提交', '核实中', '已完成']

const stageIndex = computed(() => {
  const status = report.value?.status
  if (status === 'pending') return 1
  if (status) return 2
  return 0
})

const statusColor = computed(() =>
  report.value ? (REPORT_STATUS_COLOR[report.value.status] ?? '#64748b') : '#64748b',
)

function statusColorOf(status: string): string {
  return REPORT_STATUS_COLOR[status] ?? '#64748b'
}

// ==========================================================================
// 我的上报（本机记录）
// ==========================================================================

const entries = ref<MyReportEntry[]>([])
const refreshing = ref(false)

/** 进页面时最多刷新几条。更早的记录按需再查，不一次把请求打满 */
const REFRESH_MAX = 10
/** 并发上限：移动网络下同时开太多连接反而更慢 */
const REFRESH_CONCURRENCY = 4

/**
 * 批量刷新本机记录的状态。
 *
 * 用 `allSettled` 而非 `all`：某一条查不到（编号失效、服务端已清理）
 * 不该让整批刷新失败 —— 其余几条的状态仍然是有效的。
 */
async function refreshStatuses(): Promise<void> {
  if (!entries.value.length) return
  refreshing.value = true
  try {
    const targets = entries.value.slice(0, REFRESH_MAX)
    for (let i = 0; i < targets.length; i += REFRESH_CONCURRENCY) {
      const batch = targets.slice(i, i + REFRESH_CONCURRENCY)
      await Promise.allSettled(
        batch.map(async (item) => {
          const result = await citizenReportApi.track(item.reportNo)
          updateMyReport(item.reportNo, {
            status: result.report.status,
            statusLabel: result.report.statusLabel,
            reportTypeLabel: result.report.reportTypeLabel,
            roadName: result.report.roadName,
          })
        }),
      )
      // 每批回来就更新一次，用户能边看边刷新，不用等全部跑完
      entries.value = loadMyReports()
    }
  } finally {
    refreshing.value = false
  }
}

/** 从本机列表移除。只删本地记录，服务端的数据不动 */
function removeEntry(no: string, event: Event): void {
  // 整行是可点的，别让"移除"顺带触发一次查询
  event.stopPropagation()
  forgetReport(no)
  entries.value = loadMyReports()
  if (report.value?.reportNo === no) report.value = null
}

async function search(preset?: string): Promise<void> {
  const value = (preset ?? reportNo.value).trim()
  if (!value) {
    error.value = '请输入上报编号'
    return
  }
  // 从列表点进来时把编号回填到输入框，用户能看清查的是哪一条
  reportNo.value = value
  loading.value = true
  error.value = ''
  report.value = null
  try {
    const result = await citizenReportApi.track(value)
    report.value = result.report
    // 顺手同步到本机列表，免得列表上还挂着旧的提交态
    updateMyReport(value, {
      status: result.report.status,
      statusLabel: result.report.statusLabel,
    })
    entries.value = loadMyReports()
  } catch (err) {
    error.value = describeError(err).message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  entries.value = loadMyReports()
  void refreshStatuses()

  const fromQuery = route.query.no
  if (typeof fromQuery === 'string' && fromQuery) {
    reportNo.value = fromQuery
    void search(fromQuery)
  }
})
</script>

<template>
  <div class="track">
    <!--
      本机记录放最前面：用户进这一页十次有九次是想看"我报的那条怎么样了"，
      而不是想再输一遍编号。没有记录时整块不出现，界面回到最朴素的样子。
    -->
    <section v-if="entries.length" class="c-card">
      <div class="mine-head">
        <h3 class="mine-title">
          我的上报
          <span class="mine-count">{{ entries.length }}</span>
        </h3>
        <span v-if="refreshing" class="mine-refreshing">更新中…</span>
      </div>

      <ul class="mine-list">
        <li
          v-for="item in entries"
          :key="item.reportNo"
          class="mine-item"
          @click="search(item.reportNo)"
        >
          <div class="mine-row">
            <span class="mine-no">{{ item.reportNo }}</span>
            <span
              class="mine-status"
              :style="{
                color: statusColorOf(item.status),
                background: statusColorOf(item.status) + '18',
              }"
            >
              {{ item.statusLabel || '状态未知' }}
            </span>
          </div>
          <div class="mine-row mine-meta">
            <span>{{ item.reportTypeLabel || '未分类' }}</span>
            <span v-if="item.roadName" class="mine-road">{{ item.roadName }}</span>
            <span class="mine-time">{{ formatServerTime(item.createdAt) }}</span>
          </div>
          <button
            class="mine-remove"
            title="从本机移除（不影响已提交的上报）"
            @click="removeEntry(item.reportNo, $event)"
          >
            ✕
          </button>
        </li>
      </ul>

      <p class="mine-note">
        记录保存在本机浏览器中，清除浏览器数据后会丢失；凭编号仍可随时查询。
      </p>
    </section>

    <section class="c-card">
      <h2 class="c-title">查上报进度</h2>
      <p class="c-sub">输入提交时拿到的上报编号（形如 MZ202609230001）。</p>

      <div class="search-row">
        <input
          v-model="reportNo"
          class="c-input"
          placeholder="MZ..."
          @keyup.enter="search()"
        />
        <button class="c-btn primary" :disabled="loading" @click="search()">
          {{ loading ? '查询中…' : '查询' }}
        </button>
      </div>

      <p v-if="error" class="error-text">{{ error }}</p>
    </section>

    <!-- 结果 -->
    <template v-if="report">
      <section class="c-card">
        <div class="result-head">
          <span class="result-no">{{ report.reportNo }}</span>
          <span class="result-status" :style="{ color: statusColor, background: statusColor + '18' }">
            {{ report.statusLabel }}
          </span>
        </div>

        <!-- 进度条 -->
        <div class="stages">
          <div
            v-for="(stage, index) in STAGES"
            :key="stage"
            class="stage"
            :class="{ done: index <= stageIndex, current: index === stageIndex }"
          >
            <span class="stage-dot" :style="index <= stageIndex ? { background: statusColor, borderColor: statusColor } : {}" />
            <span class="stage-label">{{ stage }}</span>
            <span v-if="index < STAGES.length - 1" class="stage-line" :class="{ done: index < stageIndex }" />
          </div>
        </div>

        <!-- 复核意见 -->
        <div v-if="report.reviewNote" class="review-box" :style="{ borderColor: statusColor + '55' }">
          <span class="review-label">核实意见</span>
          <p class="review-text">{{ report.reviewNote }}</p>
          <span class="review-time">{{ formatServerTime(report.reviewedAt) }}</span>
        </div>
      </section>

      <section class="c-card">
        <h3 class="c-section-title">上报内容</h3>
        <dl class="detail-list">
          <div><dt>类型</dt><dd>{{ report.reportTypeLabel }}</dd></div>
          <div v-if="report.description"><dt>描述</dt><dd>{{ report.description }}</dd></div>
          <div v-if="report.locationText"><dt>位置</dt><dd>{{ report.locationText }}</dd></div>
          <div v-if="report.roadName"><dt>路段</dt><dd>{{ report.roadName }}</dd></div>
          <div v-if="report.latitude != null">
            <dt>坐标</dt>
            <dd class="mono">{{ report.latitude }}, {{ report.longitude }}</dd>
          </div>
          <div><dt>提交时间</dt><dd>{{ formatServerTime(report.createdAt) }}</dd></div>
        </dl>

        <img v-if="report.imageUrl" :src="report.imageUrl" class="report-image" alt="上报照片" />
      </section>
    </template>

    <p class="hint">
      提交过的上报会自动记录在本机，下次进入本页即可看到。
      <br />
      编号区分大小写，需与提交时返回的一致。
    </p>
  </div>
</template>

<style scoped>
.track {
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
  margin: 0 0 12px;
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

.search-row {
  display: flex;
  gap: 8px;
}
.c-input {
  flex: 1;
  padding: 10px 12px;
  border: 1px solid var(--c-border-strong);
  border-radius: 10px;
  background: var(--c-surface);
  color: var(--c-text);
  /* ≥ 16px，避免 iOS 聚焦时自动放大页面 */
  font-size: var(--fs-input);
  font-family: var(--font-mono, ui-monospace, monospace);
  letter-spacing: 1px;
  box-sizing: border-box;
}
.c-input:focus {
  outline: none;
  border-color: var(--c-primary);
  box-shadow: 0 0 0 3px rgba(29, 78, 216, 0.12);
}

.error-text {
  margin: 10px 0 0;
  font-size: var(--fs-sm);
  color: var(--c-danger);
}

/* ---------------------------------------------------------------- 结果 */

.result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.result-no {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--fs-md);
  font-weight: 600;
  letter-spacing: 0.5px;
}
.result-status {
  padding: 3px 11px;
  border-radius: 999px;
  font-size: var(--fs-sm);
  font-weight: 600;
}

/* ---------------------------------------------------------------- 进度条 */

.stages {
  display: flex;
  align-items: flex-start;
  gap: 0;
}
.stage {
  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
}
.stage-dot {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid var(--c-border-strong);
  background: var(--c-surface);
  z-index: 1;
}
.stage-label {
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}
.stage.done .stage-label {
  color: var(--c-text-dim);
}
.stage.current .stage-label {
  color: var(--c-text);
  font-weight: 600;
}
.stage-line {
  position: absolute;
  top: 6px;
  left: 50%;
  width: 100%;
  height: 2px;
  background: var(--c-border);
}
.stage-line.done {
  background: var(--c-border-strong);
}

/* ---------------------------------------------------------------- 复核意见 */

.review-box {
  margin-top: 16px;
  padding: 11px 13px;
  border-left: 3px solid;
  border-radius: 0 10px 10px 0;
  background: var(--c-surface-2);
}
.review-label {
  display: block;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
  margin-bottom: 3px;
}
.review-text {
  margin: 0;
  font-size: var(--fs-base);
  line-height: 1.6;
}
.review-time {
  display: block;
  margin-top: 5px;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}

/* ---------------------------------------------------------------- 详情 */

.detail-list {
  margin: 0;
}
.detail-list > div {
  display: flex;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid var(--c-border);
  font-size: var(--fs-base);
}
.detail-list > div:last-child {
  border-bottom: none;
}
.detail-list dt {
  flex: 0 0 68px;
  color: var(--c-text-faint);
  font-size: var(--fs-sm);
}
.detail-list dd {
  margin: 0;
  flex: 1;
  min-width: 0;
  line-height: 1.6;
  word-break: break-word;
}
.mono {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--fs-sm);
}

.report-image {
  width: 100%;
  margin-top: 12px;
  border-radius: 10px;
  border: 1px solid var(--c-border);
}

.hint {
  margin: 0;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
  text-align: center;
  line-height: 1.6;
}

/* ---------------------------------------------------------------- 我的上报 */

.mine-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}
.mine-title {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0;
  font-size: var(--fs-md);
  font-weight: 600;
}
.mine-count {
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--c-surface-2);
  border: 1px solid var(--c-border);
  font-size: var(--fs-xs);
  font-weight: 600;
  color: var(--c-text-dim);
}
.mine-refreshing {
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}

.mine-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mine-item {
  position: relative;
  /* 右边让出位置，避免编号/徒章钻到右上角的"移除"下面 */
  padding: 10px 40px 10px 12px;
  border: 1px solid var(--c-border);
  border-radius: 10px;
  background: var(--c-surface-2);
  cursor: pointer;
  min-height: 44px;
  box-sizing: border-box;
}
.mine-item:active {
  border-color: var(--c-border-strong);
}
.mine-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.mine-no {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--fs-sm);
  font-weight: 600;
  letter-spacing: 0.5px;
}
.mine-status {
  padding: 2px 9px;
  border-radius: 999px;
  font-size: var(--fs-xs);
  font-weight: 600;
}
.mine-meta {
  margin-top: 5px;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}
.mine-road {
  color: var(--c-text-dim);
}
.mine-time {
  margin-left: auto;
}
.mine-remove {
  position: absolute;
  top: 6px;
  right: 6px;
  /* 视觉 16px、热区 32px：用盒子撑开而不把图标画大 */
  width: 32px;
  height: 32px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--c-text-faint);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}
.mine-remove:hover {
  background: var(--c-surface);
  color: var(--c-danger);
}

.mine-note {
  margin: 11px 0 0;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
  line-height: 1.6;
}

.c-btn {
  padding: 10px 20px;
  border: 1px solid var(--c-border-strong);
  border-radius: 10px;
  background: var(--c-surface);
  color: var(--c-text);
  font-size: var(--fs-md);
  cursor: pointer;
  white-space: nowrap;
}
.c-btn.primary {
  background: var(--c-primary);
  border-color: var(--c-primary);
  color: #fff;
  font-weight: 600;
}
.c-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
