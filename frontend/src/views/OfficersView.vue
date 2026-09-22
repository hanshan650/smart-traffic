<!--
  警员管理
  ========
  警员档案的录入与勤务状态维护。这一页是智能派警的**数据基础** ——
  派警算法只认识数据库里的警员，档案不全（尤其是坐标与专长）会直接
  导致派警结果失准，因此本页刻意把「坐标」与「专长」与姓名同等对待。

  交互约定
  --------
  · 新增与编辑共用同一个表单弹窗（``editing`` 为 null 即新增）
  · 警号是业务主键，编辑时不可修改（后端也按警号定位）
  · 删除需二次确认：警员是主数据，误删会影响派警可用性
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { officerApi } from '@/api'
import { describeError } from '@/api/client'
import {
  OFFICER_SKILL_LABEL,
  OFFICER_STATUS_COLOR,
  OFFICER_STATUS_LABEL,
} from '@/types/api'
import type { Officer, OfficerSkill, OfficerStats, OfficerStatus } from '@/types/api'

const STATUS_OPTIONS: OfficerStatus[] = ['on_duty', 'busy', 'off_duty', 'leave']
const SKILL_OPTIONS: OfficerSkill[] = [
  'accident',
  'traffic',
  'first_aid',
  'hazmat',
  'investigation',
]

const officers = ref<Officer[]>([])
const stats = ref<OfficerStats | null>(null)
const loading = ref(false)
const busy = ref(false)
const message = ref('')
const messageTone = ref<'ok' | 'warn' | 'danger'>('ok')

// 筛选
const filterStatus = ref('')
const keyword = ref('')

// 表单
const showForm = ref(false)
const editing = ref<Officer | null>(null)
const form = ref({
  officerId: '',
  name: '',
  rank: '',
  unit: '',
  phone: '',
  status: 'off_duty' as OfficerStatus,
  skills: [] as OfficerSkill[],
  latitude: 34.7466,
  longitude: 113.6254,
  region: '',
  activeCases: 0,
  avgResponseMinutes: 0,
  note: '',
})

const summaryCards = computed(() => {
  const data = stats.value
  return [
    { label: '警员总数', value: data?.total ?? 0, color: 'var(--accent)' },
    { label: '在岗可派', value: data?.onDuty ?? 0, color: '#10b981' },
    { label: '出警中', value: data?.busy ?? 0, color: '#facc15' },
    { label: '下班', value: data?.offDuty ?? 0, color: '#64748b' },
    { label: '请假', value: data?.leave ?? 0, color: '#f97316' },
  ]
})

function notify(text: string, tone: 'ok' | 'warn' | 'danger' = 'ok'): void {
  message.value = text
  messageTone.value = tone
  window.setTimeout(() => {
    if (message.value === text) message.value = ''
  }, 4000)
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [list, overview] = await Promise.all([
      officerApi.list({ status: filterStatus.value || undefined, keyword: keyword.value || undefined }),
      officerApi.stats(),
    ])
    officers.value = list.items
    stats.value = overview
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  form.value = {
    officerId: '',
    name: '',
    rank: '',
    unit: '',
    phone: '',
    status: 'off_duty',
    skills: [],
    latitude: 34.7466,
    longitude: 113.6254,
    region: '',
    activeCases: 0,
    avgResponseMinutes: 0,
    note: '',
  }
  showForm.value = true
}

function openEdit(officer: Officer): void {
  editing.value = officer
  form.value = {
    officerId: officer.officerId,
    name: officer.name,
    rank: officer.rank,
    unit: officer.unit,
    phone: officer.phone,
    status: officer.status,
    skills: [...officer.skills],
    latitude: officer.latitude,
    longitude: officer.longitude,
    region: officer.region,
    activeCases: officer.activeCases,
    avgResponseMinutes: officer.avgResponseMinutes,
    note: officer.note,
  }
  showForm.value = true
}

function toggleSkill(skill: OfficerSkill): void {
  const index = form.value.skills.indexOf(skill)
  if (index >= 0) form.value.skills.splice(index, 1)
  else form.value.skills.push(skill)
}

async function submit(): Promise<void> {
  if (!form.value.officerId.trim()) {
    notify('警号必填', 'warn')
    return
  }

  busy.value = true
  try {
    if (editing.value) {
      // 警号是主键，不参与更新
      const { officerId, ...payload } = form.value
      await officerApi.update(officerId, payload as unknown as Record<string, unknown>)
      notify(`已更新 ${form.value.name || officerId}`, 'ok')
    } else {
      await officerApi.create(form.value as unknown as Record<string, unknown>)
      notify(`已新增 ${form.value.name || form.value.officerId}`, 'ok')
    }
    showForm.value = false
    await load()
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    busy.value = false
  }
}

async function remove(officer: Officer): Promise<void> {
  if (!window.confirm(`确认删除警员 ${officer.name}（${officer.officerId}）？\n历史警情记录不受影响。`)) {
    return
  }
  try {
    await officerApi.remove(officer.officerId)
    notify(`已删除 ${officer.name}`, 'ok')
    await load()
  } catch (error) {
    notify(describeError(error).message, 'danger')
  }
}

async function seed(): Promise<void> {
  if (!window.confirm('导入示例警员？\n将清空现有警员数据并写入 12 名河南各大队示例警员。')) return
  busy.value = true
  try {
    const result = await officerApi.seed(true)
    notify(result.message, 'ok')
    await load()
  } catch (error) {
    notify(describeError(error).message, 'danger')
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <!-- 勤务概览 -->
    <section class="panel">
      <div class="panel-title">
        <span>◈ 勤务概览</span>
        <div class="head-actions">
          <button class="btn btn-sm" :disabled="busy" @click="seed">⤓ 导入示例警员</button>
          <button class="btn btn-sm btn-primary" @click="openCreate">＋ 新增警员</button>
        </div>
      </div>

      <div class="cards">
        <div v-for="item in summaryCards" :key="item.label" class="mini-card">
          <span class="mini-label">{{ item.label }}</span>
          <span class="mini-value" :style="{ color: item.color }">{{ item.value }}</span>
        </div>
      </div>

      <p class="hint">
        只有「在岗」与「出警中」的警员参与智能派警；坐标与专长会直接影响派警评分，
        请如实录入。
      </p>
    </section>

    <!-- 筛选 -->
    <section class="panel filters">
      <div class="filter-group">
        <label>状态</label>
        <select v-model="filterStatus" class="select" @change="load">
          <option value="">全部</option>
          <option v-for="item in STATUS_OPTIONS" :key="item" :value="item">
            {{ OFFICER_STATUS_LABEL[item] }}
          </option>
        </select>
      </div>

      <div class="filter-group">
        <label>搜索</label>
        <input
          v-model="keyword"
          class="input"
          placeholder="警号或姓名"
          @keyup.enter="load"
        />
      </div>

      <button class="btn btn-sm" :disabled="loading" @click="load">↻ 查询</button>
      <span class="grow" />
      <span class="text-dim small">共 {{ officers.length }} 条</span>
    </section>

    <!-- 列表 -->
    <section class="panel">
      <div class="panel-title"><span>▤ 警员档案</span></div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="!officers.length" class="empty">暂无警员，可点击「导入示例警员」快速填充</div>

      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>警号</th>
              <th>姓名</th>
              <th>警衔</th>
              <th>单位</th>
              <th>状态</th>
              <th>专长</th>
              <th class="num">在办</th>
              <th class="num">平均响应</th>
              <th>坐标</th>
              <th class="num">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in officers" :key="item.officerId">
              <td class="mono">{{ item.officerId }}</td>
              <td>{{ item.name }}</td>
              <td class="text-dim">{{ item.rank }}</td>
              <td class="text-dim">{{ item.unit }}</td>
              <td>
                <span class="badge" :style="{ color: OFFICER_STATUS_COLOR[item.status] }">
                  ● {{ OFFICER_STATUS_LABEL[item.status] }}
                </span>
              </td>
              <td>
                <span v-if="!item.skills.length" class="text-faint">—</span>
                <span v-for="skill in item.skills" :key="skill" class="chip">
                  {{ OFFICER_SKILL_LABEL[skill] }}
                </span>
              </td>
              <td class="num mono" :class="{ hot: item.activeCases > 0 }">{{ item.activeCases }}</td>
              <td class="num mono">{{ item.avgResponseMinutes }}m</td>
              <td class="mono text-faint small">
                {{ item.latitude.toFixed(3) }}, {{ item.longitude.toFixed(3) }}
              </td>
              <td class="num">
                <button class="btn btn-sm" @click="openEdit(item)">编辑</button>
                <button class="btn btn-sm danger" @click="remove(item)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 表单弹窗 -->
    <div v-if="showForm" class="modal" @click.self="showForm = false">
      <div class="form-card">
        <div class="form-head">
          <strong>{{ editing ? `编辑警员 ${form.officerId}` : '新增警员' }}</strong>
          <button class="btn btn-sm" @click="showForm = false">✕</button>
        </div>

        <div class="form-grid">
          <label class="field">
            <span>警号 *</span>
            <input v-model="form.officerId" class="input" :disabled="!!editing" placeholder="如 410013" />
          </label>
          <label class="field">
            <span>姓名</span>
            <input v-model="form.name" class="input" placeholder="如 李明" />
          </label>
          <label class="field">
            <span>警衔</span>
            <input v-model="form.rank" class="input" placeholder="如 一级警司" />
          </label>
          <label class="field">
            <span>所属单位</span>
            <input v-model="form.unit" class="input" placeholder="如 郑州交警一大队" />
          </label>
          <label class="field">
            <span>联系电话</span>
            <input v-model="form.phone" class="input" />
          </label>
          <label class="field">
            <span>勤务状态</span>
            <select v-model="form.status" class="select">
              <option v-for="item in STATUS_OPTIONS" :key="item" :value="item">
                {{ OFFICER_STATUS_LABEL[item] }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>纬度</span>
            <input v-model.number="form.latitude" type="number" step="0.0001" class="input" />
          </label>
          <label class="field">
            <span>经度</span>
            <input v-model.number="form.longitude" type="number" step="0.0001" class="input" />
          </label>
          <label class="field">
            <span>在办案件数</span>
            <input v-model.number="form.activeCases" type="number" min="0" class="input" />
          </label>
          <label class="field">
            <span>平均响应（分钟）</span>
            <input v-model.number="form.avgResponseMinutes" type="number" min="0" step="0.5" class="input" />
          </label>
        </div>

        <div class="field block">
          <span>专长（决定技能匹配得分）</span>
          <div class="skills">
            <label v-for="skill in SKILL_OPTIONS" :key="skill" class="skill-item">
              <input
                type="checkbox"
                :checked="form.skills.includes(skill)"
                @change="toggleSkill(skill)"
              />
              {{ OFFICER_SKILL_LABEL[skill] }}
            </label>
          </div>
        </div>

        <label class="field block">
          <span>备注</span>
          <input v-model="form.note" class="input" />
        </label>

        <div class="form-foot">
          <span class="text-dim small">坐标用于计算派警距离与 ETA，请填写实际位置</span>
          <div>
            <button class="btn btn-sm" @click="showForm = false">取消</button>
            <button class="btn btn-sm btn-primary" :disabled="busy" @click="submit">
              {{ busy ? '保存中…' : '保存' }}
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
.grow {
  flex: 1;
}
.small {
  font-size: 12px;
}
.hint {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--text-faint);
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

.filters {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 10px 14px;
}
.filter-group {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-dim);
}

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
.table .btn {
  margin-left: 4px;
}
.btn.danger {
  color: var(--danger);
  border-color: rgba(239, 68, 68, 0.4);
}

.chip {
  display: inline-block;
  padding: 1px 7px;
  margin-right: 4px;
  border-radius: 999px;
  background: var(--bg-hover);
  font-size: 11px;
  color: var(--text-dim);
}

.empty {
  padding: 24px;
  text-align: center;
  color: var(--text-faint);
  font-size: 13px;
}

/* ------------------------------------------------------------------ 弹窗 */

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
  width: min(760px, 94vw);
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
  margin-bottom: 14px;
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
.field.block {
  margin-top: 12px;
}
.skills {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 4px;
}
.skill-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text);
  cursor: pointer;
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
