<!--
  警情调度
  ========
  把「AI 识别到事故」到「警员到场办结」的整条链路放在一屏里：

    警情上报  →  外部平台回执  →  智能派警（多判据评分）  →  处置流转

  三个刻意的设计取向：

  1. **上报与派警解耦**：外部警情平台不可用时只标记 ``reportStatus=failed``，
     本地派警照常进行。对外通道断了不等于没人出警。
  2. **评分可解释**：每名候选的最终得分都能拆成四条判据的贡献值之和，
     并直接显示在界面上 —— 与事故识别页同一套「证据链」表达。
  3. **排除也可见**：不参与派警的警员（下班 / 请假）仍会列出**并给出原因**，
     这样「为什么没派他」是可回答的，而不是一片沉默。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { incidentApi } from '@/api'
import { describeError } from '@/api/client'
import EvidenceBar from '@/components/EvidenceBar.vue'
import {
  DISPATCH_STRATEGY_LABEL,
  EVENT_LEVEL_LABEL,
  EVENT_LEVEL_COLOR,
  EVENT_TYPE_LABEL,
  INCIDENT_STATUS_COLOR,
  INCIDENT_STATUS_LABEL,
  OFFICER_STATUS_LABEL,
  REPORT_STATUS_LABEL,
} from '@/types/api'
import type {
  DispatchAlgorithmInfo,
  DispatchCandidate,
  DispatchStrategy,
  EventLevel,
  EventType,
  Incident,
  IncidentStats,
} from '@/types/api'

const STRATEGIES: DispatchStrategy[] = ['nearest', 'balanced', 'skill']

const incidents = ref<Incident[]>([])
const stats = ref<IncidentStats | null>(null)
const algorithm = ref<DispatchAlgorithmInfo | null>(null)
const current = ref<Incident | null>(null)
const loading = ref(false)
const busy = ref(false)
const message = ref('')
const messageTone = ref<'ok' | 'warn' | 'danger'>('ok')

const filterStatus = ref('')
const filterLevel = ref('')

// 派警面板
const strategy = ref<DispatchStrategy>('nearest')
const topN = ref(5)
const manualOfficer = ref('')
const expanded = ref<Record<string, boolean>>({})
const showAlgorithm = ref(false)

// 新建测试警情
const showCreate = ref(false)
const createForm = ref({
  type: 'collision' as EventType,
  level: 'critical' as EventLevel,
  address: '京港澳高速 K712 许昌段',
  description: '检测到车辆碰撞，疑似事故',
  latitude: 34.7466,
  longitude: 113.6254,
  score: 0.8333,
  autoReport: true,
})

const EVENT_TYPES: EventType[] = [
  'collision',
  'abnormal_stop',
  'congestion',
  'wrong_way',
  'debris',
  'pedestrian',
]
const EVENT_LEVELS: EventLevel[] = ['info', 'warning', 'critical']

const summaryCards = computed(() => {
  const data = stats.value
  return [
    { label: '警情总数', value: data?.total ?? 0, color: 'var(--accent)' },
    { label: '待派警', value: data?.unassigned ?? 0, color: '#facc15' },
    { label: '上报失败', value: data?.reportFailed ?? 0, color: '#ef4444' },
    { label: '平均派警(分)', value: (data?.avgDispatchMinutes ?? 0).toFixed(1), color: '#22d3ee' },
  ]
})

const candidates = computed<DispatchCandidate[]>(() => current.value?.candidates ?? [])

/** 候选内部四条判据的贡献上限，用于统一条形长度基准 */
const maxContribution = computed(() => {
  let max = 0
  for (const item of candidates.value) {
    for (const ev of item.evidences) max = Math.max(max, ev.contribution)
  }
  return max || 1
})

/** 当前状态可执行的流转动作 */
const availableActions = computed<{ action: string; label: string; primary?: boolean }[]>(() => {
  const status = current.value?.status
  if (status === 'reported') {
    return [
      { action: 'accept', label: '直接接警' },
      { action: 'arrive', label: '登记到场', primary: true },
    ]
  }
  if (status === 'dispatched') {
    return [
      { action: 'accept', label: '接警', primary: true },
      { action: 'arrive', label: '登记到场' },
    ]
  }
  if (status === 'accepted') return [{ action: 'arrive', label: '登记到场', primary: true }]
  if (status === 'arrived') return [{ action: 'close', label: '办结', primary: true }]
  if (status === 'failed') {
    return [
      { action: 'accept', label: '人工受理' },
      { action: 'close', label: '关闭', primary: true },
    ]
  }
  return []
})

function notify(text: string, tone: 'ok' | 'warn' | 'danger' = 'ok'): void {
  message.value = text
  messageTone.value = tone
  window.setTimeout(() => {
    if (message.value === text) message.value = ''
  }, 4200)
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [list, overview] = await Promise.all([
      incidentApi.list({
        status: filterStatus.value || undefined,
        level: filterLevel.value || undefined,
      }),
      incidentApi.stats(),
    ])
    incidents.value = list.items
    stats.value = overview
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    loading.value = false
  }
}

async function loadAlgorithm(): Promise<void> {
  try {
    algorithm.value = await incidentApi.algorithm()
  } catch {
    // 说明性接口，失败不影响主流程
  }
}

async function select(item: Incident): Promise<void> {
  try {
    current.value = await incidentApi.detail(item.id)
    manualOfficer.value = ''
    expanded.value = {}
  } catch (error) {
    notify(describeError(error).message, 'danger')
  }
}

async function createIncident(): Promise<void> {
  busy.value = true
  try {
    const created = await incidentApi.create({
      ...createForm.value,
      roadId: '',
      cameraId: '',
      cameraName: '',
    })
    notify(
      created.reportStatus === 'acked'
        ? `已生成警情 ${created.incidentNo}，外部平台已受理`
        : `已生成警情 ${created.incidentNo}，上报状态：${REPORT_STATUS_LABEL[created.reportStatus]}`,
      created.reportStatus === 'failed' ? 'warn' : 'ok',
    )
    showCreate.value = false
    await load()
    current.value = created
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    busy.value = false
  }
}

async function dispatch(): Promise<void> {
  if (!current.value) return
  busy.value = true
  try {
    const updated = await incidentApi.dispatch(current.value.id, {
      strategy: strategy.value,
      topN: topN.value,
      officerId: manualOfficer.value.trim() || undefined,
    })
    current.value = updated
    notify(
      updated.assignedOfficerId
        ? `已派警：${updated.assignedOfficerName}（${updated.assignedOfficerId}），距离 ${updated.distanceKm.toFixed(2)} km，约 ${updated.etaMinutes.toFixed(0)} 分钟到达`
        : '派警完成',
      'ok',
    )
    await load()
  } catch (error) {
    const described = describeError(error)
    notify(described.message, 'warn')
  } finally {
    busy.value = false
  }
}

async function changeStatus(action: string, label: string): Promise<void> {
  if (!current.value) return
  busy.value = true
  try {
    current.value = await incidentApi.updateStatus(current.value.id, action)
    notify(`已${label}`, 'ok')
    await load()
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    busy.value = false
  }
}

async function retryReport(): Promise<void> {
  if (!current.value) return
  busy.value = true
  try {
    const updated = await incidentApi.retry(current.value.id)
    current.value = updated
    notify(
      updated.reportStatus === 'acked' ? '补报成功，外部平台已受理' : '补报仍未成功',
      updated.reportStatus === 'acked' ? 'ok' : 'warn',
    )
    await load()
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    busy.value = false
  }
}

function evidenceSum(item: DispatchCandidate): number {
  return item.evidences.reduce((total, ev) => total + ev.contribution, 0)
}

function formatTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

onMounted(async () => {
  await load()
  await loadAlgorithm()
  if (incidents.value.length) await select(incidents.value[0])
})
</script>

<template>
  <div class="page">
    <!-- 概览 -->
    <section class="panel">
      <div class="panel-title">
        <span>◈ 警情概览</span>
        <div class="head-actions">
          <button class="btn btn-sm" @click="showAlgorithm = !showAlgorithm">
            {{ showAlgorithm ? '收起算法说明' : '⚙ 算法说明' }}
          </button>
          <button class="btn btn-sm btn-primary" @click="showCreate = true">＋ 生成测试警情</button>
        </div>
      </div>

      <div class="cards">
        <div v-for="item in summaryCards" :key="item.label" class="mini-card">
          <span class="mini-label">{{ item.label }}</span>
          <span class="mini-value" :style="{ color: item.color }">{{ item.value }}</span>
        </div>
        <div class="mini-card">
          <span class="mini-label">各状态</span>
          <span class="status-line">
            <span
              v-for="(count, key) in stats?.byStatus ?? {}"
              :key="key"
              class="small"
              :style="{ color: INCIDENT_STATUS_COLOR[key as keyof typeof INCIDENT_STATUS_COLOR] }"
            >
              {{ INCIDENT_STATUS_LABEL[key as keyof typeof INCIDENT_STATUS_LABEL] }} {{ count }}
            </span>
          </span>
        </div>
      </div>

      <!-- 算法说明 -->
      <div v-if="showAlgorithm && algorithm" class="algo">
        <div class="algo-col">
          <h4>派警判据</h4>
          <div v-for="item in algorithm.dispatch.criterions" :key="item.name" class="algo-row">
            <span class="mono algo-name">{{ item.name }}</span>
            <div>
              <div class="small">{{ item.label }} <code>{{ item.formula }}</code></div>
              <div class="text-faint small">{{ item.rationale }}</div>
            </div>
          </div>
        </div>
        <div class="algo-col">
          <h4>策略权重</h4>
          <div v-for="item in algorithm.dispatch.strategies" :key="item.key" class="algo-row">
            <span class="mono algo-name">{{ DISPATCH_STRATEGY_LABEL[item.key as DispatchStrategy] }}</span>
            <div class="small">
              <span v-for="(w, name) in item.weights" :key="name" class="chip">
                {{ name }} {{ w.toFixed(2) }}
              </span>
            </div>
          </div>

          <h4 class="mt">上报通道</h4>
          <div v-for="item in algorithm.reporters" :key="item.key" class="algo-row">
            <span class="mono algo-name">{{ item.key }}</span>
            <div>
              <div class="small">
                {{ item.displayName }}
                <span :class="item.available ? 'tag-ok' : 'tag-faint'">
                  {{ item.available ? '可用' : '不可用' }}
                </span>
              </div>
              <div class="text-faint small">{{ item.reason }}</div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <div class="split">
      <!-- 左：警情列表 -->
      <section class="panel list-panel">
        <div class="panel-title">
          <span>▤ 警情列表</span>
          <button class="btn btn-sm" :disabled="loading" @click="load">↻</button>
        </div>

        <div class="filters">
          <select v-model="filterStatus" class="select" @change="load">
            <option value="">全部状态</option>
            <option v-for="(label, key) in INCIDENT_STATUS_LABEL" :key="key" :value="key">
              {{ label }}
            </option>
          </select>
          <select v-model="filterLevel" class="select" @change="load">
            <option value="">全部级别</option>
            <option v-for="item in EVENT_LEVELS" :key="item" :value="item">
              {{ EVENT_LEVEL_LABEL[item] }}
            </option>
          </select>
        </div>

        <div v-if="loading" class="empty">加载中…</div>
        <div v-else-if="!incidents.length" class="empty">暂无警情，可点击右上角生成测试警情</div>

        <ul v-else class="inc-list">
          <li
            v-for="item in incidents"
            :key="item.id"
            class="inc-item"
            :class="{ active: current?.id === item.id }"
            @click="select(item)"
          >
            <div class="inc-top">
              <span class="mono">{{ item.incidentNo }}</span>
              <span class="badge" :style="{ color: EVENT_LEVEL_COLOR[item.level] }">
                {{ EVENT_LEVEL_LABEL[item.level] }}
              </span>
            </div>
            <div class="inc-mid small">
              {{ EVENT_TYPE_LABEL[item.type] }} · {{ item.address || '位置未知' }}
            </div>
            <div class="inc-bot">
              <span class="badge" :style="{ color: INCIDENT_STATUS_COLOR[item.status] }">
                {{ INCIDENT_STATUS_LABEL[item.status] }}
              </span>
              <span class="text-faint small">{{ formatTime(item.createdAt) }}</span>
            </div>
          </li>
        </ul>
      </section>

      <!-- 右：详情 -->
      <section class="panel detail-panel">
        <div v-if="!current" class="empty">请选择左侧警情查看详情</div>

        <template v-else>
          <div class="panel-title">
            <span>◈ {{ current.incidentNo }} 详情</span>
            <span class="badge" :style="{ color: INCIDENT_STATUS_COLOR[current.status] }">
              {{ INCIDENT_STATUS_LABEL[current.status] }}
            </span>
          </div>

          <!-- 基本信息 -->
          <div class="kv">
            <div><span>类型</span><b>{{ EVENT_TYPE_LABEL[current.type] }}</b></div>
            <div><span>级别</span><b :style="{ color: EVENT_LEVEL_COLOR[current.level] }">{{ EVENT_LEVEL_LABEL[current.level] }}</b></div>
            <div><span>位置</span><b>{{ current.address || '—' }}</b></div>
            <div><span>坐标</span><b class="mono">{{ current.latitude.toFixed(4) }}, {{ current.longitude.toFixed(4) }}</b></div>
            <div><span>识别评分</span><b class="mono">{{ current.score.toFixed(3) }}</b></div>
            <div><span>关联事件</span><b class="mono">{{ current.eventId || '—' }}</b></div>
          </div>

          <!-- 上报状态 -->
          <div class="block-head">
            <span>外部平台上报</span>
            <span class="small">
              <span :class="current.reportStatus === 'acked' ? 'tag-ok' : current.reportStatus === 'failed' ? 'tag-danger' : 'tag-faint'">
                {{ REPORT_STATUS_LABEL[current.reportStatus] }}
              </span>
              <span class="text-faint"> · 通道 {{ current.reporter }}</span>
            </span>
          </div>
          <div class="report-box">
            <div class="small">
              <span class="text-dim">外部编号</span>
              <span class="mono">{{ current.externalId || '—' }}</span>
            </div>
            <div class="small text-faint">{{ current.reportMessage || '—' }}</div>
            <div v-if="current.reportStatus === 'failed'" class="report-tip">
              上报失败<strong>不影响本地派警</strong>，可在恢复后补报。
            </div>
          </div>
          <div class="row-actions">
            <button
              v-if="current.reportStatus !== 'acked'"
              class="btn btn-sm"
              :disabled="busy"
              @click="retryReport"
            >
              ⟳ 补报 / 重试
            </button>
          </div>

          <!-- 派警面板 -->
          <div class="block-head mt">
            <span>智能派警</span>
            <span v-if="current.assignedOfficerId" class="small">
              已派 <b>{{ current.assignedOfficerName }}</b>
              <span class="mono text-dim">({{ current.assignedOfficerId }})</span>
              · {{ current.distanceKm.toFixed(2) }} km · 约 {{ current.etaMinutes.toFixed(0) }} 分钟
            </span>
          </div>

          <div class="dispatch-bar">
            <div class="filter-group">
              <label>策略</label>
              <select v-model="strategy" class="select">
                <option v-for="item in STRATEGIES" :key="item" :value="item">
                  {{ DISPATCH_STRATEGY_LABEL[item] }}
                </option>
              </select>
            </div>
            <div class="filter-group">
              <label>候选数</label>
              <input v-model.number="topN" type="number" min="1" max="20" class="input narrow" />
            </div>
            <div class="filter-group">
              <label>指定警号</label>
              <input v-model="manualOfficer" class="input narrow" placeholder="留空即算法选择" />
            </div>
            <button class="btn btn-sm btn-primary" :disabled="busy" @click="dispatch">
              {{ busy ? '计算中…' : '⚑ 执行派警' }}
            </button>
          </div>

          <!-- 候选评分 -->
          <div v-if="candidates.length" class="cands">
            <div
              v-for="item in candidates"
              :key="item.officerId"
              class="cand"
              :class="{ selected: item.selected, excluded: item.excluded }"
            >
              <div class="cand-head" @click="expanded[item.officerId] = !expanded[item.officerId]">
                <span class="cand-rank">
                  <span v-if="item.selected" class="star">★</span>
                  <span v-else-if="item.excluded" class="excl">✕</span>
                  <span v-else class="dot">·</span>
                </span>
                <span class="cand-name">
                  {{ item.name }}
                  <span class="mono text-faint small">{{ item.officerId }}</span>
                </span>
                <span class="cand-unit text-faint small">{{ item.unit }}</span>
                <span class="text-dim small">{{ OFFICER_STATUS_LABEL[item.status as keyof typeof OFFICER_STATUS_LABEL] }}</span>
                <span class="mono small">{{ item.distanceKm.toFixed(2) }}km</span>
                <span class="mono small text-dim">≈{{ item.etaMinutes.toFixed(0) }}min</span>
                <span class="mono small text-dim">在办 {{ item.activeCases }}</span>
                <span class="mono cand-score" :class="{ dim: item.excluded }">{{ item.score.toFixed(4) }}</span>
                <span class="chev">{{ expanded[item.officerId] ? '▾' : '▸' }}</span>
              </div>

              <div v-if="item.excludeReason" class="cand-reason">ⓘ {{ item.excludeReason }}</div>

              <div v-if="expanded[item.officerId]" class="cand-body">
                <div v-for="ev in item.evidences" :key="ev.name">
                  <EvidenceBar
                    :label="ev.label"
                    :score="ev.score"
                    :weight="ev.weight"
                    :contribution="ev.contribution"
                    :detail="ev.detail"
                    :max-contribution="maxContribution"
                  />
                </div>
                <div class="cand-sum">
                  判据贡献合计
                  <span class="mono">{{ evidenceSum(item).toFixed(4) }}</span>
                  ＝ 最终得分
                  <span class="mono">{{ item.score.toFixed(4) }}</span>
                </div>
              </div>
            </div>
          </div>
          <div v-else-if="!showAlgorithm" class="hint-line">
            尚无评分记录，点击「执行派警」查看各候选警员的判据评分
          </div>

          <!-- 处置流转 -->
          <div v-if="availableActions.length" class="block-head mt">
            <span>处置流转</span>
          </div>
          <div class="row-actions">
            <button
              v-for="item in availableActions"
              :key="item.action"
              class="btn btn-sm"
              :class="{ 'btn-primary': item.primary }"
              :disabled="busy"
              @click="changeStatus(item.action, item.label)"
            >
              {{ item.label }}
            </button>
          </div>

          <!-- 时间线 -->
          <div class="block-head mt"><span>处置时间线</span></div>
          <ul class="timeline">
            <li v-for="(entry, index) in [...current.timeline].reverse()" :key="index">
              <span class="tl-dot" />
              <div>
                <div class="small">{{ entry.note }}</div>
                <div class="text-faint small">
                  {{ formatTime(entry.at) }} · <span class="mono">{{ entry.action }}</span>
                </div>
              </div>
            </li>
            <li v-if="!current.timeline.length" class="text-faint small">暂无记录</li>
          </ul>
        </template>
      </section>
    </div>

    <!-- 新建警情 -->
    <div v-if="showCreate" class="modal" @click.self="showCreate = false">
      <div class="form-card">
        <div class="form-head">
          <strong>生成测试警情</strong>
          <button class="btn btn-sm" @click="showCreate = false">✕</button>
        </div>
        <p class="text-faint small">
          实际系统中警情由 AI 事故识别自动创建；此处用于演示与联调。
        </p>

        <div class="form-grid">
          <label class="field">
            <span>事件类型</span>
            <select v-model="createForm.type" class="select">
              <option v-for="item in EVENT_TYPES" :key="item" :value="item">
                {{ EVENT_TYPE_LABEL[item] }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>紧急级别</span>
            <select v-model="createForm.level" class="select">
              <option v-for="item in EVENT_LEVELS" :key="item" :value="item">
                {{ EVENT_LEVEL_LABEL[item] }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>地点描述</span>
            <input v-model="createForm.address" class="input" />
          </label>
          <label class="field">
            <span>识别评分</span>
            <input v-model.number="createForm.score" type="number" step="0.01" min="0" max="1" class="input" />
          </label>
          <label class="field">
            <span>纬度</span>
            <input v-model.number="createForm.latitude" type="number" step="0.0001" class="input" />
          </label>
          <label class="field">
            <span>经度</span>
            <input v-model.number="createForm.longitude" type="number" step="0.0001" class="input" />
          </label>
        </div>

        <label class="field block">
          <span>描述</span>
          <input v-model="createForm.description" class="input" />
        </label>

        <label class="field block checkbox">
          <input v-model="createForm.autoReport" type="checkbox" />
          <span>创建后自动上报外部警情平台</span>
        </label>

        <div class="form-foot">
          <span class="text-dim small">上报失败不会阻止派警</span>
          <div>
            <button class="btn btn-sm" @click="showCreate = false">取消</button>
            <button class="btn btn-sm btn-primary" :disabled="busy" @click="createIncident">
              {{ busy ? '提交中…' : '生成' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <Transition name="fade">
      <div v-if="message" class="toast" :class="`toast-${messageTone}`">{{ message }}</div>
    </Transition>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.head-actions {
  display: flex;
  gap: 8px;
}
.small {
  font-size: 12px;
}
.mt {
  margin-top: 16px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
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
.status-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
}

/* ---------------------------------------------------------------- 算法说明 */

.algo {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 18px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.algo h4 {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-faint);
}
.algo-row {
  display: grid;
  grid-template-columns: 92px 1fr;
  gap: 8px;
  padding: 5px 0;
}
.algo-name {
  font-size: 12px;
  color: var(--accent-2, #22d3ee);
}
.algo code {
  font-size: 11px;
  color: var(--text-dim);
}
.chip {
  display: inline-block;
  padding: 1px 7px;
  margin: 2px 4px 2px 0;
  border-radius: 999px;
  background: var(--bg-hover);
  font-size: 11px;
  color: var(--text-dim);
}

/* -------------------------------------------------------------- 左右分栏 */

.split {
  display: grid;
  grid-template-columns: 340px 1fr;
  gap: 12px;
  align-items: start;
}
.list-panel {
  position: sticky;
  top: 12px;
}

/* 媒体查询必须排在基础规则之后：两者优先级相同，靠书写顺序决定胜负 */
@media (max-width: 920px) {
  .split {
    grid-template-columns: 1fr;
  }
  .list-panel {
    position: static;
  }
  .inc-list {
    max-height: 300px;
  }
}

.filters {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
.filters .select {
  flex: 1;
}

.inc-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 62vh;
  overflow-y: auto;
}
.inc-item {
  padding: 9px 10px;
  margin-bottom: 6px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-panel-2);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.inc-item:hover {
  background: var(--bg-hover);
}
.inc-item.active {
  border-color: var(--accent);
}
.inc-top,
.inc-bot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.inc-mid {
  margin: 3px 0;
  color: var(--text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------------------------------------------------------------- 详情区 */

.kv {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 8px 16px;
  padding: 10px 0;
}
.kv > div {
  display: flex;
  gap: 8px;
  font-size: 12px;
}
.kv span {
  color: var(--text-faint);
  min-width: 52px;
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

.report-box {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 9px 11px;
  background: var(--bg-panel-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.report-tip {
  margin-top: 4px;
  padding-top: 6px;
  border-top: 1px dashed var(--border-strong);
  font-size: 11px;
  color: var(--warn);
}

.dispatch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 9px 11px;
  background: var(--bg-panel-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.filter-group {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-dim);
}
.input.narrow {
  width: 96px;
}

/* -------------------------------------------------------------- 候选评分 */

.cands {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 10px;
}
.cand {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-panel-2);
  overflow: hidden;
}
.cand.selected {
  border-color: var(--ok);
  box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.25);
}
.cand.excluded {
  opacity: 0.72;
}
.cand-head {
  display: grid;
  grid-template-columns: 20px minmax(120px, 1.4fr) minmax(100px, 1.4fr) 62px 78px 70px 70px 82px 14px;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  font-size: 12px;
  cursor: pointer;
}
.cand-head:hover {
  background: var(--bg-hover);
}
.cand-rank {
  text-align: center;
}
.star {
  color: var(--ok);
}
.excl {
  color: var(--danger);
}
.dot {
  color: var(--text-faint);
}
.cand-name {
  color: var(--text);
}
.cand-unit {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cand-score {
  text-align: right;
  font-weight: 600;
  color: var(--accent-2, #22d3ee);
}
.cand-score.dim {
  color: var(--text-faint);
}
.chev {
  color: var(--text-faint);
}
.cand-reason {
  padding: 0 10px 7px 38px;
  font-size: 11px;
  color: var(--warn);
}
.cand-body {
  padding: 8px 12px 10px 38px;
  border-top: 1px solid var(--border);
}
.cand-sum {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed var(--border-strong);
  font-size: 11px;
  color: var(--text-dim);
}
.cand-sum .mono {
  color: var(--text);
}

.row-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.hint-line {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-faint);
}

/* ---------------------------------------------------------------- 时间线 */

.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
}
.timeline li {
  position: relative;
  display: flex;
  gap: 10px;
  padding: 4px 0 4px 4px;
}
.tl-dot {
  flex: 0 0 auto;
  width: 7px;
  height: 7px;
  margin-top: 5px;
  border-radius: 50%;
  background: var(--accent);
}

.tag-ok {
  color: var(--ok);
}
.tag-danger {
  color: var(--danger);
}
.tag-faint {
  color: var(--text-faint);
}

.empty {
  padding: 22px;
  text-align: center;
  color: var(--text-faint);
  font-size: 13px;
}

/* ------------------------------------------------------------ 弹窗/提示 */

.modal {
  position: fixed;
  inset: 0;
  z-index: 1400;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(4, 7, 13, 0.82);
}
.form-card {
  width: min(640px, 94vw);
  max-height: 90vh;
  overflow-y: auto;
  padding: 14px 16px;
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
}
.form-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 10px;
  margin-top: 10px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-dim);
}
.field.block {
  margin-top: 12px;
}
.field.checkbox {
  flex-direction: row;
  align-items: center;
  gap: 6px;
}
.form-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 16px;
  flex-wrap: wrap;
}
.form-foot > div {
  display: flex;
  gap: 8px;
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
