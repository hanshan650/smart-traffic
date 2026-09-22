<!--
  事故识别算法
  ============
  三级级联（L1 感知 / L2 规则 / L3 时序确认）的**可解释演示台**。

  两条入口
  --------
  · **合成轨迹仿真** —— 无需真实事故视频即可验证算法。
    四种场景分别覆盖正样本与两类典型误报陷阱，
    并完整展开每一条判据的证据链（原始值 / 阈值 / 归一化 / 权重贡献）。
  · **实时片段分析** —— 从在线摄像头抓取 3 秒片段，跑完整流水线。

  这一页是答辩演示的核心：它把「为什么判定为事故」「为什么不判定」
  变成可视化的推理过程，而非一个黑盒分数。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { accidentApi } from '@/api'
import { describeError } from '@/api/client'
import { useVideoStore } from '@/stores/video'
import type {
  AccidentCandidate,
  AlgorithmInfo,
  AnalyzeResult,
  ScenarioInfo,
  SimulationResult,
} from '@/types/api'

const videoStore = useVideoStore()

const tab = ref<'simulate' | 'live'>('simulate')

// ---------------------------------------------------------------- 算法说明

const info = ref<AlgorithmInfo | null>(null)
const scenarios = ref<ScenarioInfo[]>([])

// ---------------------------------------------------------------- 仿真状态

const scenario = ref('collision')
const simulating = ref(false)
const simulation = ref<SimulationResult | null>(null)
const simError = ref('')

// ---------------------------------------------------------------- 实时状态

const cameraNum = ref('')
const frameCount = ref(30)
const analyzing = ref(false)
const analysis = ref<AnalyzeResult | null>(null)
const liveError = ref('')

// ---------------------------------------------------------------- 计算属性

const expected = computed(
  () => scenarios.value.find((item) => item.key === scenario.value)?.expected ?? 'detected',
)

/** 仿真判定是否与场景预期一致 */
const matchOk = computed(() => !!simulation.value && simulation.value.verdict === expected.value)

const simLog = computed(() => simulation.value?.frameLog ?? [])

/** 逐帧柱状图的纵轴上限 */
const simScale = computed(() =>
  Math.max(1, ...simLog.value.map((row) => row.candidates ?? row.detections ?? 0)),
)

const liveLog = computed(() => analysis.value?.frameLog ?? [])

const liveScale = computed(() =>
  Math.max(1, ...liveLog.value.map((row) => row.detections ?? 0)),
)

/** 正样本事件的证据链（含条形图归一化比例） */
const topEvidences = computed(() => withBars(simulation.value?.events[0]))

/** 被抑制的候选样例 */
const suppressSamples = computed(() => simulation.value?.suppressionSamples ?? [])

/** 融合权重（从算法自描述的 config 中提取，避免硬编码） */
const weights = computed<Record<string, number>>(() => {
  const raw = info.value?.algorithm as
    | { config?: { weights?: Record<string, number> } }
    | undefined
  return raw?.config?.weights ?? {}
})

/** 进入 L3 所需的融合评分阈值 */
const scoreThreshold = computed<number>(() => {
  const raw = info.value?.algorithm as { config?: { scoreThreshold?: number } } | undefined
  return raw?.config?.scoreThreshold ?? 0.55
})

/** 最高候选评分（事件为空时退回观察候选） */
const topScore = computed(() => {
  const current = simulation.value
  if (!current) return 0
  if (current.events.length) return current.events[0]?.score ?? 0
  const last = current.rawCandidates[current.rawCandidates.length - 1]
  return last?.score ?? 0
})

function withBars(candidate: AccidentCandidate | undefined) {
  const list = candidate?.evidences ?? []
  const max = Math.max(0.0001, ...list.map((item) => item.contribution))
  return list.map((item) => ({ ...item, ratio: item.contribution / max }))
}

// ---------------------------------------------------------------- 操作

async function runSimulation(): Promise<void> {
  simulating.value = true
  simError.value = ''
  try {
    simulation.value = await accidentApi.simulate(scenario.value)
  } catch (error) {
    simulation.value = null
    simError.value = describeError(error).message
  } finally {
    simulating.value = false
  }
}

async function runAnalysis(): Promise<void> {
  if (!cameraNum.value) return
  analyzing.value = true
  liveError.value = ''
  try {
    analysis.value = await accidentApi.analyze({
      cameraNum: cameraNum.value,
      source: videoStore.currentSourceKey,
      frameCount: frameCount.value,
    })
  } catch (error) {
    analysis.value = null
    liveError.value = describeError(error).message
  } finally {
    analyzing.value = false
  }
}

onMounted(async () => {
  try {
    info.value = await accidentApi.info()
    scenarios.value = (await accidentApi.scenarios()).items
  } catch {
    /* 说明信息加载失败不影响仿真功能 */
  }

  if (!videoStore.cameras.length) {
    await videoStore.loadCameras(8, false)
  }
  cameraNum.value = videoStore.cameras[0]?.cameraNum ?? ''

  await runSimulation()
})
</script>

<template>
  <div class="page">
    <!-- ---------------------------------------------------------- 框架说明 -->
    <section class="panel">
      <div class="panel-title">
        <span>⚡ 事故识别算法 · 三级级联</span>
        <span class="badge badge-info">可解释推理</span>
      </div>

      <div class="framework">
        <div v-for="layer in info?.framework ?? []" :key="layer.layer" class="layer">
          <div class="layer-head">
            <span class="layer-tag">{{ layer.layer }}</span>
            <strong>{{ layer.name }}</strong>
          </div>
          <p class="layer-text">{{ layer.method }}</p>
          <p class="layer-out">→ {{ layer.output }}</p>
          <p class="layer-purpose">{{ layer.purpose }}</p>
        </div>
      </div>
    </section>

    <!-- ------------------------------------------------------------ 标签页 -->
    <div class="tabs">
      <button :class="{ active: tab === 'simulate' }" @click="tab = 'simulate'">
        合成轨迹仿真
      </button>
      <button :class="{ active: tab === 'live' }" @click="tab = 'live'">实时片段分析</button>
    </div>

    <!-- ======================================================== 合成轨迹仿真 -->
    <template v-if="tab === 'simulate'">
      <section class="panel">
        <div class="panel-title"><span>场景选择</span></div>

        <div class="scenarios">
          <button
            v-for="item in scenarios"
            :key="item.key"
            class="scenario"
            :class="{
              active: scenario === item.key,
              positive: item.expected === 'detected',
            }"
            @click="scenario = item.key"
          >
            <span class="scenario-label">{{ item.label }}</span>
            <span class="scenario-expect">
              期望 {{ item.expected === 'detected' ? '检出' : '不检出' }}
            </span>
          </button>
        </div>

        <div class="actions">
          <button class="btn btn-primary" :disabled="simulating" @click="runSimulation">
            {{ simulating ? '分析中…' : '▶ 运行仿真' }}
          </button>
          <span
            v-if="simulation"
            class="badge"
            :class="matchOk ? 'badge-ok' : 'badge-warn'"
          >
            {{ matchOk ? '✓ 判定符合预期' : '⚠ 与预期不符' }}
          </span>
          <span v-if="simulation" class="text-dim small">
            {{ simulation.frames }} 帧 / {{ simulation.fps }} fps /
            {{ simulation.durationSeconds }} 秒
          </span>
        </div>

        <p v-if="simError" class="err">{{ simError }}</p>
      </section>

      <template v-if="simulation">
        <!-- ------------------------------------------------------- 判定结果 -->
        <section class="panel verdict" :class="simulation.verdict">
          <div class="verdict-main">
            <span class="verdict-icon">{{ simulation.verdict === 'detected' ? '⚠' : '✓' }}</span>
            <div>
              <strong class="verdict-text">
                {{ simulation.verdict === 'detected' ? '检出事故' : '未检出事故' }}
              </strong>
              <p class="text-dim small">{{ simulation.label }}</p>
            </div>
          </div>

          <div class="verdict-stats">
            <div class="vstat">
              <span class="vstat-label">L2 原始候选</span>
              <span class="vstat-value">{{ simulation.rawCandidateCount }}</span>
            </div>
            <div class="vstat">
              <span class="vstat-label">L3 确认事件</span>
              <span class="vstat-value">{{ simulation.eventCount }}</span>
            </div>
            <div class="vstat">
              <span class="vstat-label">最高评分</span>
              <span class="vstat-value">{{ topScore.toFixed(4) }}</span>
            </div>
          </div>
        </section>

        <!-- --------------------------------------------------------- 证据链 -->
        <section class="panel">
          <div class="panel-title">
            <span>判定依据 · 证据链</span>
            <span class="text-dim small">S = Σ 归一化得分 × 权重</span>
          </div>

          <template v-if="topEvidences.length">
            <p class="evidence-head">
              目标对
              <span class="mono">[{{ simulation.events[0]?.trackIds.join(', ') }}]</span>
              ·
              {{ simulation.events[0]?.classNames.join(' vs ') }}
              ·
              第 {{ simulation.events[0]?.frameIndex }} 帧
            </p>

            <table class="ev-table">
              <thead>
                <tr>
                  <th>判据</th>
                  <th class="num">原始值</th>
                  <th class="num">阈值</th>
                  <th class="num">归一化</th>
                  <th class="num">权重</th>
                  <th>贡献</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in topEvidences" :key="item.name" :class="{ hit: item.triggered }">
                  <td>
                    <span class="dot" :class="item.triggered ? 'on' : 'off'">●</span>
                    {{ item.label }}
                  </td>
                  <td class="num mono">{{ item.rawValue.toFixed(4) }}</td>
                  <td class="num mono text-dim">{{ item.threshold.toFixed(4) }}</td>
                  <td class="num mono">{{ item.score.toFixed(4) }}</td>
                  <td class="num mono text-dim">{{ item.weight.toFixed(2) }}</td>
                  <td>
                    <div class="bar-wrap">
                      <div class="bar" :style="{ width: `${item.ratio * 100}%` }"></div>
                      <span class="bar-text mono">{{ item.contribution.toFixed(4) }}</span>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>

            <p class="total">
              融合评分
              <strong class="mono">{{ simulation.events[0]?.score.toFixed(4) }}</strong>
              <span class="text-dim">
                （阈值 {{ scoreThreshold }} 以上才进入 L3）
              </span>
            </p>
          </template>

          <p v-else class="empty">
            本场景未产生达到阈值的候选 —— 这正是「不检出」的预期行为。
          </p>
        </section>

        <!-- --------------------------------------------------------- 逐帧曲线 -->
        <section class="panel">
          <div class="panel-title">
            <span>逐帧判定过程</span>
            <span class="text-dim small">
              灰柱 = L2 候选（含被抑制） · 蓝柱 = L3 确认
            </span>
          </div>

          <div class="chart">
            <div v-for="row in simLog" :key="row.frameIndex" class="chart-col">
              <div class="chart-stack">
                <div
                  class="chart-bar raw"
                  :style="{ height: `${((row.candidates ?? 0) / simScale) * 100}%` }"
                  :title="`第 ${row.frameIndex} 帧：候选 ${row.candidates ?? 0}，抑制 ${row.suppressed ?? 0}`"
                ></div>
                <div
                  class="chart-bar hit"
                  :style="{ height: `${(row.confirmed / simScale) * 100}%` }"
                  :title="`第 ${row.frameIndex} 帧：确认 ${row.confirmed}`"
                ></div>
              </div>
              <span class="chart-x">{{ row.frameIndex }}</span>
            </div>
          </div>
        </section>

        <!-- --------------------------------------------------------- 误报抑制 -->
        <section class="panel" v-if="suppressSamples.length">
          <div class="panel-title">
            <span>误报抑制规则生效</span>
            <span class="badge badge-warn">{{ suppressSamples.length }} 例样例</span>
          </div>

          <div class="suppress-list">
            <div v-for="(item, index) in suppressSamples" :key="index" class="suppress">
              <div class="suppress-head">
                <span class="badge badge-warn">已抑制</span>
                <span class="small">{{ item.suppressReason }}</span>
              </div>
              <p class="text-dim small">
                第 {{ item.frameIndex }} 帧 · 目标对
                <span class="mono">[{{ item.trackIds.join(', ') }}]</span>
                · 融合评分
                <span class="mono">{{ item.score.toFixed(4) }}</span>
              </p>
            </div>
          </div>
        </section>

        <!-- ----------------------------------------------------------- 轨迹表 -->
        <section class="panel">
          <div class="panel-title"><span>轨迹摘要</span></div>

          <div class="table-wrap">
            <table class="track-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>类别</th>
                  <th class="num">观测帧</th>
                  <th class="num">末段速度</th>
                  <th class="num">峰值速度</th>
                  <th class="num">最大骤降比</th>
                  <th class="num">航向变化率</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="track in simulation.tracks" :key="track.trackId">
                  <td class="mono">{{ track.trackId }}</td>
                  <td>{{ track.className }}</td>
                  <td class="num mono">{{ track.age }}</td>
                  <td class="num mono">{{ track.speed.toFixed(4) }}</td>
                  <td class="num mono">{{ track.peakSpeed.toFixed(4) }}</td>
                  <td class="num mono" :class="{ hot: track.peakSpeedDropRatio > 0.6 }">
                    {{ track.peakSpeedDropRatio.toFixed(3) }}
                  </td>
                  <td class="num mono">{{ track.headingChangeRate.toFixed(3) }}</td>
                  <td>
                    <span class="badge" :class="track.stationary ? 'badge-warn' : 'badge-ok'">
                      {{ track.stationary ? '静止' : '运动' }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </template>

    <!-- ======================================================== 实时片段分析 -->
    <template v-else>
      <section class="panel">
        <div class="panel-title"><span>分析参数</span></div>

        <div class="controls">
          <div class="field">
            <label>摄像头</label>
            <select v-model="cameraNum" class="select">
              <option v-for="cam in videoStore.cameras" :key="cam.cameraNum" :value="cam.cameraNum">
                {{ cam.cameraNum }} · {{ cam.road || cam.regionName }}
              </option>
            </select>
          </div>

          <div class="field">
            <label>分析帧数（{{ frameCount }} 帧）</label>
            <input v-model.number="frameCount" type="range" min="10" max="120" step="5" />
          </div>

          <button class="btn btn-primary" :disabled="analyzing || !cameraNum" @click="runAnalysis">
            {{ analyzing ? '抓取分析中…' : '▶ 开始分析' }}
          </button>
        </div>

        <p class="text-dim small">
          流程：取流 → 抽帧（ffmpeg，缺省时降级为 HLS 分片离线解码）→ YOLOv8 推理
          → IoU 跟踪 → L2 规则 → L3 确认。系统会先探测流的真实帧率作为时基，
          因此实际分析时长取决于帧数与上游帧率。
        </p>

        <p v-if="liveError" class="err">{{ liveError }}</p>
      </section>

      <template v-if="analysis">
        <section class="panel">
          <div class="panel-title">
            <span>分析结果</span>
            <span class="text-dim small">
              {{ analysis.cameraNum }} · {{ analysis.frameCount }} 帧 ·
              {{ analysis.fps }} fps · {{ analysis.durationSeconds }} 秒 ·
              {{ analysis.trackCount }} 条轨迹
            </span>
          </div>

          <p v-if="!analysis.events.length" class="empty">
            本片段未检出事故。真实道路中事故属于小概率事件，未检出是常态。
          </p>

          <div v-else class="events">
            <div v-for="(item, index) in analysis.events" :key="index" class="event">
              <div class="event-head">
                <span class="badge badge-danger">{{ item.eventType }}</span>
                <span class="mono">评分 {{ item.score.toFixed(4) }}</span>
                <span class="text-dim small">
                  第 {{ item.frameIndex }} 帧 · 目标对
                  <span class="mono">[{{ item.trackIds.join(', ') }}]</span>
                </span>
              </div>

              <img v-if="item.snapshotUrl" :src="item.snapshotUrl" class="event-thumb" alt="事件截图" />

              <table class="ev-table">
                <thead>
                  <tr>
                    <th>判据</th>
                    <th class="num">原始值</th>
                    <th class="num">阈值</th>
                    <th class="num">贡献</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="ev in withBars(item)"
                    :key="ev.name"
                    :class="{ hit: ev.triggered }"
                  >
                    <td>
                      <span class="dot" :class="ev.triggered ? 'on' : 'off'">●</span>
                      {{ ev.label }}
                    </td>
                    <td class="num mono">{{ ev.rawValue.toFixed(4) }}</td>
                    <td class="num mono text-dim">{{ ev.threshold.toFixed(4) }}</td>
                    <td>
                      <div class="bar-wrap">
                        <div class="bar" :style="{ width: `${ev.ratio * 100}%` }"></div>
                        <span class="bar-text mono">{{ ev.contribution.toFixed(4) }}</span>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-title"><span>逐帧统计</span></div>
          <div class="chart">
            <div v-for="row in liveLog" :key="row.frameIndex" class="chart-col">
              <div class="chart-stack">
                <div
                  class="chart-bar raw"
                  :style="{ height: `${((row.detections ?? 0) / liveScale) * 100}%` }"
                  :title="`第 ${row.frameIndex} 帧：检测 ${row.detections ?? 0}`"
                ></div>
                <div
                  class="chart-bar hit"
                  :style="{ height: `${(row.confirmed / liveScale) * 100}%` }"
                  :title="`第 ${row.frameIndex} 帧：确认 ${row.confirmed}`"
                ></div>
              </div>
              <span class="chart-x">{{ row.frameIndex }}</span>
            </div>
          </div>
        </section>
      </template>
    </template>

    <!-- ---------------------------------------------------------- 判据与规则 -->
    <section class="panel" v-if="info">
      <div class="panel-title">
        <span>判据定义与误报抑制</span>
        <span class="text-dim small">设计取舍说明</span>
      </div>

      <div class="two-col">
        <div>
          <h4 class="sub-title">四条判据</h4>
          <div v-for="item in info.criterions" :key="item.name" class="crit">
            <div class="crit-head">
              <strong>{{ item.label }}</strong>
              <span class="mono small text-dim">{{ item.formula }}</span>
            </div>
            <p class="text-dim small">{{ item.rationale }}</p>
          </div>
        </div>

        <div>
          <h4 class="sub-title">误报抑制规则</h4>
          <div v-for="item in info.suppressions" :key="item.name" class="crit">
            <div class="crit-head">
              <strong>{{ item.label }}</strong>
              <span class="mono small text-dim">
                {{ item.factor !== undefined ? `×${item.factor}` : `≥${item.threshold}` }}
              </span>
            </div>
          </div>

          <h4 class="sub-title mt">权重</h4>
          <div class="weights">
            <span class="chip">接触 {{ weights['contact'] ?? '—' }}</span>
            <span class="chip">速度骤降 {{ weights['speedDrop'] ?? '—' }}</span>
            <span class="chip">TTC {{ weights['ttc'] ?? '—' }}</span>
            <span class="chip">轨迹畸变 {{ weights['heading'] ?? '—' }}</span>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 24px;
}

.small {
  font-size: 12px;
}

/* ------------------------------------------------------------------ 框架 */

.framework {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 10px;
}

.layer {
  background: var(--bg-panel-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 10px 12px;
}
.layer-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.layer-tag {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--accent-dim);
  color: #e0f2fe;
}
.layer-text {
  margin: 6px 0 4px;
  font-size: 12px;
  color: var(--text-dim);
}
.layer-out {
  margin: 0;
  font-size: 12px;
  color: var(--accent);
}
.layer-purpose {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-faint);
}

/* ------------------------------------------------------------------ 标签页 */

.tabs {
  display: flex;
  gap: 6px;
}
.tabs button {
  padding: 7px 16px;
  font-size: 13px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text-dim);
  cursor: pointer;
}
.tabs button.active {
  background: var(--accent-dim);
  border-color: var(--accent);
  color: #e0f2fe;
}

/* ------------------------------------------------------------------ 场景 */

.scenarios {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 8px;
}
.scenario {
  display: flex;
  flex-direction: column;
  gap: 3px;
  align-items: flex-start;
  padding: 9px 12px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--bg-panel-2);
  color: var(--text);
  cursor: pointer;
  text-align: left;
}
.scenario:hover {
  border-color: var(--border-strong);
}
.scenario.active {
  border-color: var(--accent);
  background: var(--bg-hover);
}
.scenario.positive .scenario-expect {
  color: var(--danger);
}
.scenario-label {
  font-size: 13px;
}
.scenario-expect {
  font-size: 11px;
  color: var(--ok);
}

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.err {
  margin: 10px 0 0;
  color: var(--danger);
  font-size: 12px;
}
.empty {
  margin: 8px 0 0;
  color: var(--text-faint);
  font-size: 13px;
}

/* ------------------------------------------------------------------ 判定 */

.verdict {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
  border-left: 3px solid var(--border-strong);
}
.verdict.detected {
  border-left-color: var(--danger);
}
.verdict.not_detected {
  border-left-color: var(--ok);
}
.verdict-main {
  display: flex;
  align-items: center;
  gap: 14px;
}
.verdict-icon {
  font-size: 30px;
}
.verdict.detected .verdict-icon {
  color: var(--danger);
}
.verdict.not_detected .verdict-icon {
  color: var(--ok);
}
.verdict-text {
  font-size: 18px;
}
.verdict-main p {
  margin: 2px 0 0;
}
.verdict-stats {
  display: flex;
  gap: 26px;
}
.vstat {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}
.vstat-label {
  font-size: 11px;
  color: var(--text-faint);
}
.vstat-value {
  font-family: var(--font-mono);
  font-size: 18px;
  color: var(--accent);
}

/* ------------------------------------------------------------------ 证据 */

.evidence-head {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--text-dim);
}

.ev-table,
.track-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.ev-table th,
.ev-table td,
.track-table th,
.track-table td {
  padding: 6px 8px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
.ev-table th,
.track-table th {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-faint);
  white-space: nowrap;
}
.ev-table .num,
.track-table .num {
  text-align: right;
}
.ev-table tr.hit td {
  background: rgba(239, 68, 68, 0.06);
}
.track-table td.hot {
  color: var(--danger);
}

.dot {
  font-size: 10px;
  margin-right: 4px;
}
.dot.on {
  color: var(--danger);
}
.dot.off {
  color: var(--text-faint);
}

.bar-wrap {
  position: relative;
  height: 16px;
  min-width: 110px;
  background: rgba(148, 163, 184, 0.08);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.bar {
  height: 100%;
  background: linear-gradient(90deg, var(--accent-dim), var(--accent));
}
.bar-text {
  position: absolute;
  right: 6px;
  top: 0;
  font-size: 11px;
  line-height: 16px;
}

.total {
  margin: 10px 0 0;
  font-size: 13px;
}
.total strong {
  color: var(--accent);
  font-size: 15px;
}

/* ------------------------------------------------------------------ 图表 */

.chart {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 110px;
  overflow-x: auto;
  padding-bottom: 2px;
}
.chart-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 0 0 auto;
  height: 100%;
}
.chart-stack {
  position: relative;
  flex: 1;
  width: 18px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.chart-bar {
  position: absolute;
  bottom: 0;
  width: 100%;
  border-radius: 2px 2px 0 0;
  min-height: 1px;
}
.chart-bar.raw {
  background: rgba(148, 163, 184, 0.35);
}
.chart-bar.hit {
  background: var(--accent);
  width: 60%;
}
.chart-x {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-faint);
  margin-top: 3px;
}

/* ------------------------------------------------------------------ 抑制 */

.suppress-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.suppress {
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-panel-2);
  border-left: 2px solid var(--warn);
}
.suppress-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.suppress p {
  margin: 4px 0 0;
}

/* ------------------------------------------------------------------ 控件 */

.controls {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 12px;
  color: var(--text-dim);
}
.field input[type='range'] {
  width: 190px;
  accent-color: var(--accent);
}

/* ------------------------------------------------------------------ 事件 */

.events {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.event {
  padding: 10px;
  border-radius: var(--radius);
  background: var(--bg-panel-2);
  border-left: 2px solid var(--danger);
}
.event-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.event-thumb {
  max-width: 320px;
  border-radius: var(--radius-sm);
  margin-bottom: 8px;
  display: block;
}

/* ------------------------------------------------------------------ 说明 */

.two-col {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 18px;
}
.sub-title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--accent);
}
.sub-title.mt {
  margin-top: 16px;
}
.crit {
  padding: 7px 0;
  border-bottom: 1px dashed var(--border);
}
.crit-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.crit p {
  margin: 3px 0 0;
}
.weights {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.chip {
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--bg-panel-2);
  border: 1px solid var(--border);
  font-size: 12px;
  color: var(--text-dim);
}

.table-wrap {
  overflow-x: auto;
}
</style>
