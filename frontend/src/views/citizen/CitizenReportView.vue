<!--
  随手上报
  ========
  这一页的成败取决于**报完之后用户会不会再报第二次**。所以有两个重点：

  1. **提交要轻**。必填项只有"类型 + 描述或位置"，联系方式可选。
     要求越多，路面上的司机越不会填 —— 而他们恰恰是最有价值的来源。

  2. **反馈要重**。提交成功后把编号放大显示，并提供"查进度"的直达入口。
     只弹一句"提交成功"就消失，用户记不住编号，也感受不到这件事有人管。

  位置信息优先用浏览器定位，失败时退回手填 —— 隧道里、信号差的地方
  定位经常拿不到，不能因此卡住上报。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { citizenReportApi, publicApi } from '@/api/citizen'
import { describeError } from '@/api/client'
import type { CitizenReportCreateResponse, CitizenReportOptions } from '@/types/citizen'

const router = useRouter()

const options = ref<CitizenReportOptions | null>(null)
const form = ref({
  reportType: '',
  description: '',
  locationText: '',
  roadName: '',
  contact: '',
  latitude: null as number | null,
  longitude: null as number | null,
})
const image = ref<File | null>(null)
const imagePreview = ref('')

const locating = ref(false)
const locationHint = ref('')
const busy = ref(false)
const error = ref('')
const result = ref<CitizenReportCreateResponse | null>(null)

const limits = computed(() => options.value?.limits)

const descriptionLeft = computed(() => {
  const max = limits.value?.maxDescription ?? 500
  return max - form.value.description.length
})

const canSubmit = computed(
  () =>
    !!form.value.reportType &&
    (form.value.description.trim().length > 0 || form.value.locationText.trim().length > 0),
)

onMounted(async () => {
  try {
    options.value = await publicApi.options()
  } catch (err) {
    error.value = describeError(err).message
  }
})

function onFileChange(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  image.value = file
  if (imagePreview.value) URL.revokeObjectURL(imagePreview.value)
  imagePreview.value = file ? URL.createObjectURL(file) : ''
}

function clearImage(): void {
  if (imagePreview.value) URL.revokeObjectURL(imagePreview.value)
  imagePreview.value = ''
  image.value = null
}

function locate(): void {
  if (!navigator.geolocation) {
    locationHint.value = '当前浏览器不支持定位，请手动填写位置'
    return
  }
  locating.value = true
  locationHint.value = '正在定位…'
  navigator.geolocation.getCurrentPosition(
    (position) => {
      form.value.latitude = Number(position.coords.latitude.toFixed(6))
      form.value.longitude = Number(position.coords.longitude.toFixed(6))
      locating.value = false
      locationHint.value = `已定位：${form.value.latitude}, ${form.value.longitude}`
    },
    (err) => {
      locating.value = false
      // 定位失败是常态（隧道、室内、未授权），不该显示成错误
      locationHint.value =
        err.code === err.PERMISSION_DENIED
          ? '未获得定位授权，请手动填写位置'
          : '定位失败，请手动填写位置'
    },
    { timeout: 8000, enableHighAccuracy: false },
  )
}

async function submit(): Promise<void> {
  if (!canSubmit.value) {
    error.value = '请选择上报类型，并至少填写情况描述或具体位置'
    return
  }
  busy.value = true
  error.value = ''
  try {
    result.value = await citizenReportApi.submit({
      reportType: form.value.reportType,
      description: form.value.description,
      locationText: form.value.locationText,
      roadName: form.value.roadName,
      contact: form.value.contact,
      latitude: form.value.latitude,
      longitude: form.value.longitude,
      image: image.value,
    })
  } catch (err) {
    error.value = describeError(err).message
  } finally {
    busy.value = false
  }
}

function reportAgain(): void {
  clearImage()
  result.value = null
  form.value = {
    reportType: '',
    description: '',
    locationText: '',
    roadName: '',
    contact: '',
    latitude: null,
    longitude: null,
  }
}

function goTrack(): void {
  router.push({ path: '/citizen/track', query: { no: result.value?.reportNo ?? '' } })
}
</script>

<template>
  <div class="report">
    <!-- 提交成功 -->
    <section v-if="result" class="c-card success-card">
      <div class="success-icon">✓</div>
      <h2>上报已提交</h2>
      <p class="success-sub">{{ result.message }}</p>

      <div class="code-box">
        <span class="code-label">上报编号</span>
        <strong class="code-value">{{ result.reportNo }}</strong>
      </div>

      <p class="success-tip">
        请记录该编号。可凭它在「进度」页查看核实结果 ——
        值班人员会尽快核实，若情况紧急请同时拨打 12122。
      </p>

      <div class="success-actions">
        <button class="c-btn primary" @click="goTrack">查看处理进度</button>
        <button class="c-btn" @click="reportAgain">再报一条</button>
      </div>
    </section>

    <!-- 表单 -->
    <template v-else>
      <section class="c-card">
        <h2 class="c-title">路上遇到什么情况？</h2>
        <p class="c-sub">
          您的上报会进入待核实队列，由值班人员确认后转入正式处置流程。
        </p>

        <div class="type-grid">
          <button
            v-for="item in options?.reportTypes ?? []"
            :key="item.key"
            class="type-btn"
            :class="{ active: form.reportType === item.key }"
            @click="form.reportType = item.key"
          >
            {{ item.label }}
          </button>
        </div>
      </section>

      <section class="c-card">
        <label class="field">
          <span class="field-label">
            情况描述
            <em v-if="limits" :class="{ warn: descriptionLeft < 50 }">
              还可输入 {{ descriptionLeft }} 字
            </em>
          </span>
          <textarea
            v-model="form.description"
            class="c-input"
            rows="3"
            :maxlength="limits?.maxDescription ?? 500"
            placeholder="例如：行车道上有掉落的纸箱，已有多辆车急刹"
          />
        </label>

        <label class="field">
          <span class="field-label">所在位置</span>
          <input
            v-model="form.locationText"
            class="c-input"
            placeholder="例如：京港澳高速 K712 附近，往南方向"
          />
        </label>

        <div class="locate-row">
          <button class="c-btn small" :disabled="locating" @click="locate">
            {{ locating ? '定位中…' : '⌖ 使用当前位置' }}
          </button>
          <span v-if="locationHint" class="locate-hint">{{ locationHint }}</span>
        </div>

        <label class="field">
          <span class="field-label">
            路段名称
            <em>选填</em>
          </span>
          <input v-model="form.roadName" class="c-input" placeholder="例如：京港澳高速" />
        </label>
      </section>

      <section class="c-card">
        <h3 class="c-section-title">现场照片</h3>
        <p class="c-sub small">选填。有照片能显著提高核实效率。</p>

        <div v-if="imagePreview" class="preview-wrap">
          <img :src="imagePreview" class="preview" alt="上报照片预览" />
          <button class="c-btn small" @click="clearImage">移除照片</button>
        </div>

        <label v-else class="upload-box">
          <input type="file" accept="image/*" class="file-input" @change="onFileChange" />
          <span class="upload-icon">＋</span>
          <span class="upload-text">拍照或从相册选择</span>
        </label>
      </section>

      <section class="c-card">
        <label class="field">
          <span class="field-label">
            联系方式
            <em>选填，仅供值班人员核实使用</em>
          </span>
          <input
            v-model="form.contact"
            class="c-input"
            :maxlength="limits?.maxContact ?? 100"
            placeholder="手机号或邮箱"
          />
        </label>
        <p class="privacy-note">
          联系方式不会公开显示，仅在核实需要时由值班人员查看。
        </p>
      </section>

      <div v-if="error" class="c-card error-box">{{ error }}</div>

      <button class="submit-btn" :disabled="busy || !canSubmit" @click="submit">
        {{ busy ? '提交中…' : '提交上报' }}
      </button>

      <p class="limit-note">
        为防滥用，同一设备 {{ limits?.windowHours ?? 1 }} 小时内最多上报
        {{ limits?.rateLimit ?? 10 }} 条。紧急情况请直接拨打 12122。
      </p>
    </template>
  </div>
</template>

<style scoped>
.report {
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
.c-sub.small {
  font-size: var(--fs-xs);
  margin-bottom: 8px;
}
.c-section-title {
  margin: 0 0 4px;
  font-size: var(--fs-base);
  font-weight: 600;
  color: var(--c-text-dim);
}

/* ---------------------------------------------------------------- 类型选择 */

.type-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(104px, 1fr));
  gap: 8px;
}
.type-btn {
  padding: 11px 8px;
  border: 1px solid var(--c-border);
  border-radius: 11px;
  background: var(--c-surface-2);
  color: var(--c-text);
  font-size: var(--fs-base);
  cursor: pointer;
  transition: all 0.15s;
  /* 移动端点按目标高度 */
  min-height: 46px;
}
.type-btn:active {
  transform: scale(0.98);
}
.type-btn.active {
  border-color: var(--c-primary);
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 600;
}

/* ---------------------------------------------------------------- 表单字段 */

.field {
  display: block;
  margin-bottom: 12px;
}
.field:last-child {
  margin-bottom: 0;
}
.field-label {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 5px;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
}
.field-label em {
  font-style: normal;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}
.field-label em.warn {
  color: var(--c-warn);
}

.c-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--c-border-strong);
  border-radius: 10px;
  background: var(--c-surface);
  color: var(--c-text);
  /*
    必须 ≥ 16px：iOS Safari 在字号小于 16px 的输入框获得焦点时，
    会自动放大整个页面且不会自动缩回，用户得手动双指缩回来。
    不能改用 user-scalable=no 规避 —— 那会挡住需要放大文字的用户。
  */
  font-size: var(--fs-input);
  font-family: inherit;
  resize: vertical;
  box-sizing: border-box;
}
.c-input:focus {
  outline: none;
  border-color: var(--c-primary);
  box-shadow: 0 0 0 3px rgba(29, 78, 216, 0.12);
}

.locate-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.locate-hint {
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}

/* ---------------------------------------------------------------- 上传 */

.upload-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 22px;
  border: 1.5px dashed var(--c-border-strong);
  border-radius: 11px;
  background: var(--c-surface-2);
  cursor: pointer;
}
.upload-icon {
  font-size: var(--fs-xl);
  color: var(--c-text-faint);
  line-height: 1;
}
.upload-text {
  font-size: var(--fs-sm);
  color: var(--c-text-faint);
}
.file-input {
  display: none;
}

.preview-wrap {
  display: flex;
  flex-direction: column;
  gap: 9px;
  align-items: flex-start;
}
.preview {
  width: 100%;
  max-height: 260px;
  object-fit: cover;
  border-radius: 11px;
  border: 1px solid var(--c-border);
}

/* ---------------------------------------------------------------- 提交 */

.submit-btn {
  padding: 14px;
  border: none;
  border-radius: 12px;
  background: var(--c-primary);
  color: #fff;
  font-size: var(--fs-md);
  font-weight: 600;
  cursor: pointer;
  box-shadow: var(--c-shadow);
}
.submit-btn:disabled {
  background: var(--c-border-strong);
  color: #fff;
  cursor: not-allowed;
}
.submit-btn:not(:disabled):active {
  transform: scale(0.99);
}

.limit-note,
.privacy-note {
  margin: 0;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
  line-height: 1.6;
  text-align: center;
}
.privacy-note {
  text-align: left;
}

.error-box {
  color: var(--c-danger);
  font-size: var(--fs-base);
  background: #fef2f2;
  border-color: #fecaca;
}

.c-btn {
  padding: 8px 15px;
  border: 1px solid var(--c-border-strong);
  border-radius: 10px;
  background: var(--c-surface);
  color: var(--c-text);
  font-size: var(--fs-base);
  cursor: pointer;
}
.c-btn.small {
  padding: 6px 12px;
  font-size: var(--fs-sm);
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

/* ---------------------------------------------------------------- 成功态 */

.success-card {
  text-align: center;
  padding: 24px 18px;
}
.success-icon {
  width: 52px;
  height: 52px;
  margin: 0 auto 12px;
  border-radius: 50%;
  background: var(--c-ok);
  color: #fff;
  font-size: var(--fs-xl);
  display: grid;
  place-items: center;
}
.success-card h2 {
  margin: 0 0 5px;
  font-size: var(--fs-lg);
}
.success-sub {
  margin: 0 0 16px;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
  line-height: 1.6;
}

.code-box {
  padding: 14px;
  border-radius: 12px;
  background: var(--c-primary-soft);
  border: 1px dashed var(--c-primary);
  margin-bottom: 14px;
}
.code-label {
  display: block;
  font-size: var(--fs-xs);
  color: var(--c-text-dim);
  margin-bottom: 3px;
}
.code-value {
  font-size: var(--fs-lg);
  font-family: var(--font-mono, ui-monospace, monospace);
  letter-spacing: 1px;
  color: var(--c-primary);
}

.success-tip {
  margin: 0 0 16px;
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
  line-height: 1.7;
  text-align: left;
}
.success-actions {
  display: flex;
  gap: 9px;
  justify-content: center;
  flex-wrap: wrap;
}
</style>
