<!--
  违停监控（持续检测）
  ====================
  把「一直盯着看」这件事的完整链路放在一屏：

    启动巡检  →  逐轮抓帧分析  →  静止判定（多判据证据链）  →  告警  →  核查 / 查车主

  三个刻意的展示取向：

  1. **把"没报警"也显示出来**。逐目标判定表会列出画面里每条轨迹停了多久、
     得分多少、为什么没到告警线。只显示告警会让人无法判断"系统是不是在工作"
     —— 一片空白既可能是没有违停，也可能是巡检挂了。

  2. **车牌识别不可用时，把原因贴在脸上**。车牌通道没接入时，界面直接显示
     "字符高度只有 X px，需接入高分辨率卡口相机"，而不是留一个空白格。
     技术限制要让人看见，不能藏在一个空值里。

  3. **车主查询强制填事由**。查询人、事由是必填项，且界面明确提示"本次查询
     将被审计留痕"。这不是形式主义 —— 机动车所有人信息属于受保护的
     个人信息，界面必须让操作者意识到这一点。
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { surveillanceApi } from '@/api'
import { describeError } from '@/api/client'
import EvidenceBar from '@/components/EvidenceBar.vue'
import {
  ALERT_STATUS_COLOR,
  ALERT_STATUS_LABEL,
  READABILITY_COLOR,
  READABILITY_LABEL,
} from '@/types/api'
import type {
  CameraWatchState,
  LookupAuditEntry,
  StallAlert,
  SurveillanceAlgorithm,
  SurveillanceStatus,
} from '@/types/api'

const status = ref<SurveillanceStatus | null>(null)
const algorithm = ref<SurveillanceAlgorithm | null>(null)
const alerts = ref<StallAlert[]>([])
const audits = ref<LookupAuditEntry[]>([])
const alertStats = ref<{ total: number; withPlate: number; byStatus: Record<string, number> }>({
  total: 0,
  withPlate: 0,
  byStatus: {},
})

const busy = ref(false)
const message = ref('')
const messageTone = ref<'ok' | 'warn' | 'danger'>('ok')
const expanded = ref<Record<string, boolean>>({})
const showAlgorithm = ref(false)
const activeTab = ref<'verdicts' | 'alerts' | 'owner' | 'audits'>('verdicts')

// 车主查询表单
const lookupForm = ref({
  plate: '',
  operator: '',
  reason: '',
  provider: 'mock',
  alertId: '',
})
const lookupResult = ref<StallAlert | null>(null)
const ownerResult = ref<Awaited<ReturnType<typeof surveillanceApi.lookupOwner>> | null>(null)

const cameraIdInput = ref('')

let timer: number | undefined

const running = computed(() => status.value?.running ?? false)

const summaryCards = computed(() => [
  { label: '巡检状态', value: running.value ? '运行中' : '已停止', color: running.value ? '#10b981' : '#64748b' },
  { label: '巡检路数', value: status.value?.totals.cameras ?? 0, color: 'var(--accent)' },
  { label: '累计轮次', value: status.value?.totals.rounds ?? 0, color: '#22d3ee' },
  { label: '分析帧数', value: status.value?.totals.framesAnalyzed ?? 0, color: '#22d3ee' },
  { label: '违停告警', value: alertStats.value.total, color: '#ef4444' },
])

/** 当前所有路里最近一轮的判定，按静止时长降序 */
const allVerdicts = computed(() => {
  const rows: { camera: CameraWatchState; verdict: CameraWatchState['verdicts'][number] }[] = []
  for (const camera of status.value?.cameras ?? []) {
    for (const verdict of camera.verdicts) {
      rows.push({ camera, verdict })
    }
  }
  return rows.sort((a, b) => b.verdict.stationarySeconds - a.verdict.stationarySeconds)
})

function notify(text: string, tone: 'ok' | 'warn' | 'danger' = 'ok'): void {
  message.value = text
  messageTone.value = tone
  window.setTimeout(() => {
    if (message.value === text) message.value = ''
  }, 4500)
}

async function refresh(): Promise<void> {
  try {
    const [state, alertList, stats] = await Promise.all([
      surveillanceApi.status(),
      surveillanceApi.alerts({ limit: 30 }),
      surveillanceApi.alertStats(),
    ])
    status.value = state
    alerts.value = alertList.items
    alertStats.value = stats
  } catch (error) {
    // 轮询失败不弹提示，避免巡检停止时刷屏
  }
}

async function start(): Promise<void> {
  busy.value = true
  try {
    // 输入格式：编号，或「编号:点位名称」。名称里带「服务区」「收费站」
    // 等关键词时该点位会被判定为可停车场所，不产生违停告警 ——
    // 这是避免误报的关键信息，所以放在输入框里让部署者显式声明。
    const ids: string[] = []
    const hints: Record<string, string> = {}
    for (const token of cameraIdInput.value.split(/[,\s]+/)) {
      const text = token.trim()
      if (!text) continue
      const separator = text.indexOf(':')
      if (separator > 0) {
        const id = text.slice(0, separator).trim()
        ids.push(id)
        hints[id] = text.slice(separator + 1).trim()
      } else {
        ids.push(text)
      }
    }
    const result = await surveillanceApi.start(ids, hints)
    notify(result.message, 'ok')
    await refresh()
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    busy.value = false
  }
}

async function stop(): Promise<void> {
  busy.value = true
  try {
    const result = await surveillanceApi.stop()
    notify(result.message, 'ok')
    await refresh()
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    busy.value = false
  }
}

async function toggle(camera: CameraWatchState): Promise<void> {
  try {
    await surveillanceApi.toggleCamera(camera.cameraId, !camera.enabled)
    notify(`已${camera.enabled ? '暂停' : '恢复'} ${camera.cameraId}`, 'ok')
    await refresh()
  } catch (error) {
    notify(describeError(error).message, 'danger')
  }
}

async function updateAlert(alert: StallAlert, next: string): Promise<void> {
  try {
    await surveillanceApi.updateAlert(alert.id, next)
    notify(`已标记为${ALERT_STATUS_LABEL[next as keyof typeof ALERT_STATUS_LABEL]}`, 'ok')
    await refresh()
  } catch (error) {
    notify(describeError(error).message, 'danger')
  }
}

function openLookup(alert: StallAlert): void {
  activeTab.value = 'owner'
  lookupForm.value.plate = alert.plate
  lookupForm.value.alertId = alert.id
  lookupResult.value = alert
  if (!alert.plate) {
    notify(
      '该告警没有可用车牌（识别通道未接入或分辨率不足），可手动输入车牌查询',
      'warn',
    )
  }
}

async function submitLookup(): Promise<void> {
  if (!lookupForm.value.plate.trim()) {
    notify('请输入车牌号', 'warn')
    return
  }
  if (!lookupForm.value.operator.trim()) {
    notify('查询人必填：个人信息查询必须留痕', 'warn')
    return
  }
  if (!lookupForm.value.reason.trim()) {
    notify('查询事由必填：无正当理由不得查询个人信息', 'warn')
    return
  }

  busy.value = true
  try {
    ownerResult.value = await surveillanceApi.lookupOwner({
      plate: lookupForm.value.plate.trim(),
      operator: lookupForm.value.operator.trim(),
      reason: lookupForm.value.reason.trim(),
      provider: lookupForm.value.provider,
      alertId: lookupForm.value.alertId,
    })
    notify(
      ownerResult.value.owner.found
        ? '查询成功（个人信息已脱敏）'
        : `未查到：${ownerResult.value.owner.message.slice(0, 40)}`,
      ownerResult.value.owner.found ? 'ok' : 'warn',
    )
    await loadAudits()
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    busy.value = false
  }
}

async function loadAudits(): Promise<void> {
  try {
    const result = await surveillanceApi.audits({ limit: 30 })
    audits.value = result.items
  } catch {
    // 审计查询失败不影响主流程
  }
}

function evidenceSum(alert: StallAlert): number {
  return alert.evidences.reduce((total, item) => total + item.contribution, 0)
}

function formatTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

onMounted(async () => {
  await refresh()
  await loadAudits()
  try {
    algorithm.value = await surveillanceApi.algorithm()
  } catch {
    // 说明性接口
  }
  // 巡检会持续推进，用轮询跟上。5 秒足以覆盖默认 15 秒的巡检间隔，
  // 既不会太密也不会让界面显得卡住。
  timer = window.setInterval(refresh, 5000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="page">
    <!-- 概览与控制 -->
    <section class="panel">
      <div class="panel-title">
        <span>◈ 违停监控</span>
        <div class="head-actions">
          <input
            v-model="cameraIdInput"
            class="input wide"
            placeholder="编号，或 编号:点位名称（含“服务区”则豁免）"
          />
          <button v-if="!running" class="btn btn-sm btn-primary" :disabled="busy" @click="start">
            ▶ 开始巡检
          </button>
          <button v-else class="btn btn-sm" :disabled="busy" @click="stop">■ 停止巡检</button>
          <button class="btn btn-sm" @click="showAlgorithm = !showAlgorithm">
            {{ showAlgorithm ? '收起算法' : '⚙ 算法说明' }}
          </button>
        </div>
      </div>

      <div class="cards">
        <div v-for="item in summaryCards" :key="item.label" class="mini-card">
          <span class="mini-label">{{ item.label }}</span>
          <span class="mini-value" :style="{ color: item.color }">{{ item.value }}</span>
        </div>
      </div>

      <p class="hint">
        巡检按轮次持续抓帧分析，<strong>使用真实墙钟时间</strong>判定静止时长 ——
        分批抓帧的轮次空档会被正确计入，30 秒阈值不会因帧率换算而失真。
        <span v-if="status && !running" class="warn-text">
          当前未运行，不会产生新告警。
        </span>
      </p>

      <!-- 算法说明 -->
      <div v-if="showAlgorithm && algorithm" class="algo">
        <div class="algo-col">
          <h4>判定判据（权重）</h4>
          <div v-for="item in algorithm.detection.criterions" :key="item.name" class="algo-row">
            <span class="mono algo-name">{{ item.name }}</span>
            <div>
              <div class="small">
                {{ item.label }}
                <span class="mono text-faint">
                  {{ algorithm.detection.weights[item.name]?.toFixed(2) }}
                </span>
              </div>
              <code>{{ item.formula }}</code>
              <div class="text-faint small">{{ item.rationale }}</div>
            </div>
          </div>
        </div>

        <div class="algo-col">
          <h4>抑制条件</h4>
          <div v-for="item in algorithm.detection.suppressions" :key="item.name" class="algo-row">
            <span class="mono algo-name">{{ item.name }}</span>
            <div>
              <div class="small">{{ item.label }}</div>
              <div class="text-faint small">{{ item.detail }}</div>
            </div>
          </div>

          <h4 class="mt">适配器通道</h4>
          <div v-for="group in [
              { title: '场所识别', data: algorithm.sceneText },
              { title: '车牌识别', data: algorithm.plate },
              { title: '车主查询', data: algorithm.ownerLookup },
            ]" :key="group.title" class="algo-block">
            <div class="small text-dim">
              {{ group.title }}：当前 <span class="mono">{{ group.data.current }}</span>
            </div>
            <div v-for="channel in group.data.channels" :key="channel.key" class="channel">
              <span class="mono">{{ channel.key }}</span>
              <span :class="channel.available ? 'tag-ok' : 'tag-faint'">
                {{ channel.available ? '可用' : '不可用' }}
              </span>
              <span class="text-faint small">{{ channel.reason }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 各路巡检状态 -->
    <section v-if="status?.cameras.length" class="panel">
      <div class="panel-title"><span>▦ 巡检通道</span></div>
      <div class="watch-grid">
        <div v-for="camera in status.cameras" :key="camera.cameraId" class="watch-card">
          <div class="watch-head">
            <span class="mono">{{ camera.cameraId }}</span>
            <span class="badge" :class="camera.enabled ? 'tag-ok' : 'tag-faint'">
              {{ camera.enabled ? '● 巡检中' : '○ 已暂停' }}
            </span>
          </div>

          <img v-if="camera.lastSnapshot" :src="camera.lastSnapshot" class="watch-snap" alt="巡检快照" />
          <div v-else class="watch-snap empty-snap">暂无快照</div>

          <div class="watch-stats">
            <span>轮次 <b>{{ camera.rounds }}</b></span>
            <span>帧数 <b>{{ camera.framesAnalyzed }}</b></span>
            <span>轨迹 <b>{{ camera.trackCount }}</b></span>
            <span>静止 <b>{{ camera.stationaryCount }}</b></span>
            <span>告警 <b class="hot">{{ camera.alertsRaised }}</b></span>
            <span>耗时 <b>{{ camera.lastRoundMs.toFixed(0) }}ms</b></span>
          </div>

          <div class="watch-zone">
            <span class="tag" :class="camera.parkingAllowed ? 'tag-warn' : 'tag-ok'">
              {{ camera.zoneLabel }}{{ camera.parkingAllowed ? '（允许停车）' : '（禁停）' }}
            </span>
            <span v-if="camera.crowded" class="tag tag-warn">
              拥堵排队 {{ (camera.crowdRatio * 100).toFixed(0) }}%
            </span>
          </div>

          <div class="watch-foot">
            <span class="text-faint small">{{ formatTime(camera.lastRoundAt) }}</span>
            <button class="btn btn-sm" @click="toggle(camera)">
              {{ camera.enabled ? '暂停' : '恢复' }}
            </button>
          </div>

          <div v-if="camera.lastError" class="watch-error">⚠ {{ camera.lastError }}</div>
        </div>
      </div>
    </section>

    <!-- 标签页 -->
    <section class="panel">
      <div class="tabs">
        <button
          v-for="item in [
            { key: 'verdicts', label: `实时判定 (${allVerdicts.length})` },
            { key: 'alerts', label: `违停告警 (${alerts.length})` },
            { key: 'owner', label: '车主查询' },
            { key: 'audits', label: `审计留痕 (${audits.length})` },
          ]"
          :key="item.key"
          class="tab"
          :class="{ active: activeTab === item.key }"
          @click="activeTab = item.key as typeof activeTab"
        >
          {{ item.label }}
        </button>
      </div>

      <!-- 实时判定 -->
      <div v-if="activeTab === 'verdicts'">
        <p class="text-faint small">
          列出最近一轮巡检中每一条轨迹的判定。<strong>未告警的也在这里</strong> ——
          这样你能看出系统是否在工作，而不是只看到一片空白。
        </p>

        <div v-if="!running && !allVerdicts.length" class="empty">
          巡检未运行。点击上方「开始巡检」后，这里会显示画面中各目标的静止时长与得分。
        </div>
        <div v-else-if="!allVerdicts.length" class="empty">
          最近一轮未检测到符合条件的车辆
        </div>

        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>摄像头</th>
                <th class="num">轨迹</th>
                <th>类别</th>
                <th class="num">已静止</th>
                <th class="num">得分</th>
                <th>结果</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in allVerdicts" :key="`${row.camera.cameraId}-${row.verdict.trackId}`">
                <td class="mono small">{{ row.camera.cameraId }}</td>
                <td class="num mono">{{ row.verdict.trackId }}</td>
                <td class="text-dim">{{ row.verdict.className }}</td>
                <td class="num mono" :class="{ hot: row.verdict.stationarySeconds >= 30 }">
                  {{ row.verdict.stationarySeconds.toFixed(1) }}s
                </td>
                <td class="num mono">{{ row.verdict.score.toFixed(4) }}</td>
                <td>
                  <span v-if="row.verdict.alert" class="tag tag-danger">告警</span>
                  <span v-else class="tag tag-faint">正常</span>
                </td>
                <td class="small text-faint">{{ row.verdict.suppressReason || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 告警列表 -->
      <div v-else-if="activeTab === 'alerts'">
        <div v-if="!alerts.length" class="empty">
          暂无违停告警。车辆在非可停车地点静止超过
          {{ algorithm?.detection.config.stallSeconds ?? 30 }} 秒时会在此出现。
        </div>

        <div v-else class="alert-list">
          <div v-for="alert in alerts" :key="alert.id" class="alert-card">
            <div class="alert-head" @click="expanded[alert.id] = !expanded[alert.id]">
              <span class="tag" :style="{ color: ALERT_STATUS_COLOR[alert.status] }">
                {{ ALERT_STATUS_LABEL[alert.status] }}
              </span>
              <span class="mono small">{{ alert.cameraId }}</span>
              <span class="text-dim small">轨迹 {{ alert.trackId }} · {{ alert.className }}</span>
              <span class="alert-still mono">已静止 {{ alert.stationarySeconds.toFixed(1) }}s</span>
              <span class="mono small text-dim">得分 {{ alert.score.toFixed(4) }}</span>
              <span class="text-faint small">{{ formatTime(alert.createdAt) }}</span>
              <span class="chev">{{ expanded[alert.id] ? '▾' : '▸' }}</span>
            </div>

            <div v-if="expanded[alert.id]" class="alert-body">
              <div class="alert-cols">
                <div>
                  <h4>判据证据链</h4>
                  <div
                    v-for="ev in alert.evidences"
                    :key="ev.name"
                  >
                    <EvidenceBar
                      :label="ev.label"
                      :score="ev.score"
                      :weight="ev.weight"
                      :contribution="ev.contribution"
                      :detail="ev.detail"
                      :max-contribution="Math.max(...alert.evidences.map((x) => x.contribution), 0.01)"
                    />
                  </div>
                  <div class="cand-sum">
                    判据贡献合计 <span class="mono">{{ evidenceSum(alert).toFixed(4) }}</span>
                    ＝ 最终得分 <span class="mono">{{ alert.score.toFixed(4) }}</span>
                  </div>
                </div>

                <div>
                  <h4>车牌识别</h4>
                  <div class="plate-box">
                    <div v-if="alert.plate" class="plate-value">
                      <span class="mono large">{{ alert.plate }}</span>
                      <span v-if="alert.plateSimulated" class="tag tag-warn">模拟数据</span>
                    </div>
                    <div v-else class="plate-none">未获取到车牌</div>

                    <div class="small">
                      字符高度估算
                      <span class="mono" :style="{ color: READABILITY_COLOR[alert.plateReadability] }">
                        {{ alert.plateCharHeight.toFixed(1) }} px
                      </span>
                      <span :style="{ color: READABILITY_COLOR[alert.plateReadability] }">
                        （{{ READABILITY_LABEL[alert.plateReadability] }}）
                      </span>
                    </div>
                    <div class="text-faint small">{{ alert.plateMessage }}</div>
                  </div>

                  <h4 class="mt">处置</h4>
                  <div class="row-actions">
                    <button class="btn btn-sm danger" @click="updateAlert(alert, 'verified')">
                      确认违停
                    </button>
                    <button class="btn btn-sm" @click="updateAlert(alert, 'dismissed')">误报</button>
                    <button class="btn btn-sm" @click="updateAlert(alert, 'handled')">已处置</button>
                    <button class="btn btn-sm btn-primary" @click="openLookup(alert)">
                      查车主 →
                    </button>
                  </div>

                  <div v-if="alert.snapshotUrl" class="snap-wrap">
                    <img :src="alert.snapshotUrl" class="snap" alt="告警快照" />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 车主查询 -->
      <div v-else-if="activeTab === 'owner'">
        <div class="compliance">
          <strong>⚠ 合规提示</strong>
          机动车所有人信息属于受保护的个人信息。本系统的每次查询都会记录
          <b>查询人、时间、车牌与事由</b>并写入审计台账，返回结果中的姓名、
          证件号、手机号一律脱敏。
        </div>

        <div class="form-grid">
          <label class="field">
            <span>车牌号 *</span>
            <input v-model="lookupForm.plate" class="input" placeholder="如 豫A12345" />
          </label>
          <label class="field">
            <span>查询人（警号）*</span>
            <input v-model="lookupForm.operator" class="input" placeholder="必填，用于留痕" />
          </label>
          <label class="field">
            <span>查询事由 *</span>
            <input v-model="lookupForm.reason" class="input" placeholder="如 违停告警核查" />
          </label>
          <label class="field">
            <span>查询通道</span>
            <select v-model="lookupForm.provider" class="select">
              <option value="none">none（未接入）</option>
              <option value="mock">mock（模拟数据）</option>
              <option value="official">official（正式通道）</option>
            </select>
          </label>
        </div>

        <div class="row-actions">
          <button class="btn btn-sm btn-primary" :disabled="busy" @click="submitLookup">
            {{ busy ? '查询中…' : '执行查询' }}
          </button>
          <span v-if="lookupResult" class="text-faint small">
            关联告警：{{ lookupResult.id }}
          </span>
        </div>

        <div v-if="ownerResult" class="owner-result">
          <div class="panel-title">
            <span>
              ◈ 查询结果
              <span v-if="ownerResult.owner.simulated" class="tag tag-warn">模拟数据</span>
            </span>
            <span class="text-faint small">
              通道 {{ ownerResult.owner.source }} · 审计已留痕
              {{ ownerResult.audit.persisted ? '✓' : '✗' }}
            </span>
          </div>

          <div v-if="!ownerResult.owner.found" class="empty">
            {{ ownerResult.owner.message }}
          </div>

          <div v-else class="kv">
            <div><span>车牌</span><b class="mono">{{ ownerResult.owner.plate }}</b></div>
            <div><span>车主</span><b>{{ ownerResult.owner.ownerName }}</b></div>
            <div><span>证件号</span><b class="mono">{{ ownerResult.owner.ownerIdMasked }}</b></div>
            <div><span>手机</span><b class="mono">{{ ownerResult.owner.ownerPhoneMasked }}</b></div>
            <div><span>品牌</span><b>{{ ownerResult.owner.vehicleBrand }}</b></div>
            <div><span>颜色</span><b>{{ ownerResult.owner.vehicleColor }}</b></div>
            <div><span>车型</span><b>{{ ownerResult.owner.vehicleType }}</b></div>
            <div><span>注册日期</span><b class="mono">{{ ownerResult.owner.registerDate }}</b></div>
          </div>

          <p v-if="ownerResult.owner.simulated" class="hint warn-text">
            以上为<strong>模拟数据</strong>，非真实车主信息。正式查询需通过政务专网
            对接公安交管部门的信息查询服务。
          </p>
        </div>
      </div>

      <!-- 审计 -->
      <div v-else>
        <p class="text-faint small">
          每一次车主信息查询（含失败与不可用）都会留痕。审计记录本身不脱敏车牌 ——
          它的用途正是回答「谁在什么时候查了哪辆车、依据什么」。
        </p>

        <div v-if="!audits.length" class="empty">暂无查询记录</div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>时间</th>
                <th>车牌</th>
                <th>查询人</th>
                <th>事由</th>
                <th>通道</th>
                <th>结果</th>
                <th>落库</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, index) in audits" :key="index">
                <td class="mono small">{{ formatTime(item.at) }}</td>
                <td class="mono">{{ item.plate }}</td>
                <td class="mono">{{ item.operator }}</td>
                <td class="small text-dim">{{ item.reason }}</td>
                <td class="mono small">{{ item.source }}</td>
                <td>
                  <span :class="item.outcome === 'found' ? 'tag-ok' : 'tag-faint'">
                    {{ item.outcome }}
                  </span>
                </td>
                <td>{{ item.persisted ? '✓' : '✗' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

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
  align-items: center;
}
.input.wide {
  width: 260px;
}
.small {
  font-size: 12px;
}
.hint {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--text-faint);
  line-height: 1.6;
}
.warn-text {
  color: var(--warn);
}
.mt {
  margin-top: 14px;
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

/* ---------------------------------------------------------------- 算法说明 */

.algo {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
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
  grid-template-columns: 110px 1fr;
  gap: 8px;
  padding: 5px 0;
}
.algo-name {
  font-size: 12px;
  color: var(--accent-2, #22d3ee);
}
.algo code {
  display: block;
  font-size: 11px;
  color: var(--text-dim);
}
.algo-block {
  margin-top: 8px;
}
.channel {
  display: flex;
  gap: 6px;
  align-items: baseline;
  padding: 3px 0;
  font-size: 11px;
  flex-wrap: wrap;
}

/* ---------------------------------------------------------------- 巡检卡片 */

.watch-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 10px;
}
.watch-card {
  padding: 10px;
  background: var(--bg-panel-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.watch-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
}
.watch-snap {
  width: 100%;
  aspect-ratio: 352 / 288;
  object-fit: cover;
  border-radius: var(--radius-sm);
  background: #000;
}
.empty-snap {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-faint);
  font-size: 12px;
}
.watch-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin-top: 8px;
  font-size: 11px;
  color: var(--text-dim);
}
.watch-stats b {
  font-family: var(--font-mono);
  color: var(--text);
}
.watch-stats b.hot {
  color: var(--warn);
}
.watch-zone {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.watch-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}
.watch-error {
  margin-top: 6px;
  font-size: 11px;
  color: var(--warn);
}

/* ---------------------------------------------------------------- 标签页 */

.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--border);
}
.tab {
  padding: 7px 14px;
  border: none;
  background: none;
  color: var(--text-faint);
  font-size: 13px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.tab:hover {
  color: var(--text);
}
.tab.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

/* ---------------------------------------------------------------- 表格 */

.table-wrap {
  overflow-x: auto;
}
.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.table th,
.table td {
  padding: 7px 9px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.table th {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-faint);
}
.table .num {
  text-align: right;
}
.table td.hot {
  color: var(--warn);
}

/* ---------------------------------------------------------------- 告警卡 */

.alert-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.alert-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-panel-2);
  overflow: hidden;
}
.alert-head {
  display: grid;
  grid-template-columns: 82px minmax(120px, 1.4fr) minmax(110px, 1fr) 120px 110px 150px 14px;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  font-size: 12px;
  cursor: pointer;
}
.alert-head:hover {
  background: var(--bg-hover);
}
.alert-still {
  color: var(--warn);
  font-weight: 600;
}
.chev {
  color: var(--text-faint);
}
.alert-body {
  padding: 10px 12px 12px;
  border-top: 1px solid var(--border);
}
.alert-cols {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 20px;
}
.alert-cols h4 {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-faint);
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

.plate-box {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 9px 11px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.plate-value {
  display: flex;
  align-items: center;
  gap: 8px;
}
.plate-value .large {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: 1px;
}
.plate-none {
  font-size: 13px;
  color: var(--text-faint);
}
.snap-wrap {
  margin-top: 10px;
}
.snap {
  width: 100%;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
}

/* ---------------------------------------------------------------- 车主查询 */

.compliance {
  padding: 9px 12px;
  margin-bottom: 14px;
  border-radius: var(--radius-sm);
  background: rgba(250, 204, 21, 0.08);
  border: 1px solid rgba(250, 204, 21, 0.3);
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-dim);
}
.compliance strong {
  color: var(--warn);
  margin-right: 6px;
}
.compliance b {
  color: var(--text);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-dim);
}

.owner-result {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
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
  min-width: 58px;
}
.kv b {
  font-weight: 500;
  color: var(--text);
}

.row-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
  align-items: center;
}
.btn.danger {
  color: var(--danger);
  border-color: rgba(239, 68, 68, 0.4);
}

.tag {
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--bg-hover);
  font-size: 11px;
}
.tag-ok {
  color: var(--ok);
}
.tag-warn {
  color: var(--warn);
}
.tag-danger {
  color: var(--danger);
}
.tag-faint {
  color: var(--text-faint);
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
