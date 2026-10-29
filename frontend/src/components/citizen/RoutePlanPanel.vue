<!--
  路线规划面板
  ============
  地图上的浮层：选终点、选出行方式、出一条路线。

  三件事刻意与"导航软件"划清界限：
    · 不给"开始导航"，只给路线预览 —— 本项目不接管实时引导
    · 不隐藏失败原因：步行/骑行规划高速路段是常态性失败，
      "规划失败"四个字会让人以为系统坏了
    · 切换方式会**重新规划**而不是把旧路线留着：
      界面写着步行、地图上却是驾车线，比没路线更糟
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, toRef } from 'vue'

import { useRoutePlan } from '@/composables/useRoutePlan'
import { formatDistance } from '@/utils/geo'
import {
  TRAVEL_MODES,
  formatDuration,
  travelModeMeta,
} from '@/utils/routePlan'
import type { GeoFix } from '@/composables/useGeolocation'
import type { PlaceSuggestion } from '@/composables/useRoutePlan'
import type { RoutePlan, TravelMode } from '@/utils/routePlan'

const props = defineProps<{
  /** 高德 API 命名空间（useAmap 里的 AMap ref 在模板中已解包） */
  amap: any
  /** 用户位置（GCJ-02）。null 时无法规划 —— 起点必须是"我" */
  fix: GeoFix | null
  /** 当前已呈现的路线，由父组件持有 */
  plan: RoutePlan | null
}>()

const emit = defineEmits<{
  plan: [plan: RoutePlan | null]
  locate: []
  close: []
}>()

const { planning, error, suggestions, search, autocomplete, geocode, clearSuggestions, clearError } =
  useRoutePlan(toRef(props, 'amap'))

const mode = ref<TravelMode>('driving')
const keyword = ref('')
/** 从下拉里选中的地点。手动输入但没选中的不算 —— 那时只有文本，没有坐标 */
const destination = ref<PlaceSuggestion | null>(null)
/** 表单级错误（地址解析不出来），与规划服务的错误分开 */
const formError = ref('')

const activeMeta = computed(() => travelModeMeta(mode.value))
const message = computed(() => formError.value || error.value)

let debounceTimer: number | undefined

function onInput(): void {
  // 输入一变，之前选中的坐标就失效了，必须作废，否则会拿旧终点去规划
  destination.value = null
  formError.value = ''
  clearError()

  if (debounceTimer !== undefined) window.clearTimeout(debounceTimer)
  const text = keyword.value
  debounceTimer = window.setTimeout(() => autocomplete(text), 250)
}

function pick(tip: PlaceSuggestion): void {
  destination.value = tip
  keyword.value = tip.name
  clearSuggestions()
  void start(tip)
}

async function start(target: PlaceSuggestion): Promise<void> {
  if (!props.fix) {
    formError.value = '需要先定位到你的位置才能规划路线'
    return
  }

  formError.value = ''
  const result = await search({
    mode: mode.value,
    origin: [props.fix.lng, props.fix.lat],
    destination: [target.lng, target.lat],
    destinationName: target.name,
  })

  // 失败时也要 emit null：旧路线与当前选中的出行方式已经对不上了
  emit('plan', result)
}

async function submit(): Promise<void> {
  formError.value = ''

  const target = destination.value ?? (await geocode(keyword.value))
  if (!target) {
    formError.value = '没有找到这个地点，换个更具体的名称试试'
    return
  }

  destination.value = target
  keyword.value = target.name
  await start(target)
}

/**
 * 切换出行方式。
 *
 * 已有路线时立刻按新方式重算 —— 这是三种方式之间唯一有意义的差别来源：
 * 路线几何、里程、耗时、沿途事件全都跟着变，只换一个高亮标签等于骗人。
 */
async function changeMode(next: TravelMode): Promise<void> {
  if (next === mode.value) return
  mode.value = next
  if (!destination.value || !props.plan) return
  await start(destination.value)
}

function reset(): void {
  keyword.value = ''
  destination.value = null
  formError.value = ''
  clearSuggestions()
  clearError()
  emit('plan', null)
}

onBeforeUnmount(() => {
  if (debounceTimer !== undefined) window.clearTimeout(debounceTimer)
})
</script>

<template>
  <div class="planner">
    <div class="planner-head">
      <strong>路线规划</strong>
      <button class="planner-close" title="收起" @click="emit('close')">✕</button>
    </div>

    <!-- 没有定位就没有起点。不拿地图中心凑数：那会规划出一条"从随便某个地方出发"的路线 -->
    <div v-if="!fix" class="planner-empty">
      <p>需要先定位到你的位置，才能规划从这里出发的路线</p>
      <button class="planner-btn" @click="emit('locate')">开启定位</button>
    </div>

    <template v-else>
      <div class="planner-field">
        <input
          v-model="keyword"
          class="planner-input"
          type="text"
          inputmode="search"
          autocomplete="off"
          placeholder="输入终点，如 上海虹桥站"
          aria-label="终点"
          @input="onInput"
          @keyup.enter="submit"
        />
        <button v-if="keyword" class="field-clear" title="清空" @click="reset">✕</button>
      </div>

      <ul v-if="suggestions.length" class="suggest">
        <li v-for="(tip, index) in suggestions" :key="`${tip.name}-${index}`">
          <button class="suggest-item" @click="pick(tip)">
            <span class="suggest-name">{{ tip.name }}</span>
            <span v-if="tip.address" class="suggest-addr">{{ tip.address }}</span>
          </button>
        </li>
      </ul>

      <!-- 出行方式：三种规划服务的路线几何本来就不同，所以做成并列的一级选择 -->
      <div class="modes">
        <button
          v-for="item in TRAVEL_MODES"
          :key="item.key"
          class="mode-btn"
          :class="{ active: mode === item.key }"
          :style="mode === item.key ? { borderColor: item.color, color: item.color } : undefined"
          @click="changeMode(item.key)"
        >
          {{ item.label }}
        </button>
      </div>

      <p class="mode-hint">{{ activeMeta.hint }}</p>

      <button class="planner-btn primary" :disabled="planning || !keyword.trim()" @click="submit">
        {{ planning ? '规划中…' : '规划路线' }}
      </button>

      <p v-if="message" class="planner-error">{{ message }}</p>

      <!-- 结果摘要放在面板里，地图上的底部事件栏只负责"沿途有什么" -->
      <div v-if="plan" class="summary" :style="{ borderLeftColor: activeMeta.color }">
        <div class="summary-head">
          <span class="summary-mode">{{ travelModeMeta(plan.mode).label }}</span>
          <span class="summary-dest">{{ plan.destinationName }}</span>
        </div>
        <p class="summary-line">
          <strong>{{ formatDistance(plan.distanceM / 1000) }}</strong>
          <span>·</span>
          <strong>{{ formatDuration(plan.durationS) }}</strong>
        </p>
        <p class="summary-note">沿路线 {{ travelModeMeta(plan.mode).corridorM }} 米内的事件会列在下方</p>
        <button class="summary-clear" @click="reset">清除路线</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.planner {
  position: absolute;
  top: 70px;
  left: 10px;
  right: 10px;
  z-index: 16;
  width: min(340px, calc(100% - 20px));
  max-height: calc(100% - 90px);
  padding: 10px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  border-radius: 12px;
  background: var(--c-overlay);
  backdrop-filter: blur(8px);
  box-shadow: 0 4px 18px rgba(15, 23, 42, 0.22);
}

.planner-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--fs-md);
  color: var(--c-text);
}
.planner-close {
  border: none;
  background: none;
  color: var(--c-text-faint);
  font-size: var(--fs-base);
  cursor: pointer;
}

.planner-empty p {
  margin: 0 0 8px;
  font-size: var(--fs-sm);
  line-height: 1.5;
  color: var(--c-text-dim);
}

.planner-field {
  position: relative;
}
.planner-input {
  width: 100%;
  padding: 9px 30px 9px 10px;
  border: 1px solid var(--c-border-strong);
  border-radius: 9px;
  background: var(--c-surface);
  color: var(--c-text);
  /* iOS 上输入框字号小于 16px 会触发自动放大，整个页面跟着跳一下 */
  font-size: max(16px, var(--fs-base));
  box-sizing: border-box;
}
.planner-input:focus {
  outline: none;
  border-color: var(--c-primary);
}
.field-clear {
  position: absolute;
  top: 50%;
  right: 8px;
  transform: translateY(-50%);
  border: none;
  background: none;
  color: var(--c-text-faint);
  font-size: var(--fs-sm);
  cursor: pointer;
}

.suggest {
  margin: 0;
  padding: 3px;
  list-style: none;
  max-height: 180px;
  overflow-y: auto;
  border: 1px solid var(--c-border);
  border-radius: 9px;
  background: var(--c-surface);
}
.suggest-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  width: 100%;
  padding: 7px 8px;
  border: none;
  border-radius: 7px;
  background: none;
  text-align: left;
  cursor: pointer;
}
.suggest-item:hover {
  background: var(--c-surface-2);
}
.suggest-name {
  font-size: var(--fs-base);
  color: var(--c-text);
}
.suggest-addr {
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modes {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}
.mode-btn {
  padding: 7px 0;
  border: 1px solid var(--c-border-strong);
  border-radius: 9px;
  background: var(--c-surface);
  color: var(--c-text-dim);
  font-size: var(--fs-base);
  cursor: pointer;
}
.mode-btn.active {
  background: var(--c-surface-2);
  font-weight: 600;
}

.mode-hint {
  margin: 0;
  font-size: var(--fs-xs);
  line-height: 1.5;
  color: var(--c-text-faint);
}

.planner-btn {
  padding: 8px 12px;
  border: 1px solid var(--c-border-strong);
  border-radius: 9px;
  background: var(--c-surface);
  color: var(--c-text);
  font-size: var(--fs-base);
  cursor: pointer;
}
.planner-btn.primary {
  border-color: var(--c-primary);
  background: var(--c-primary);
  color: var(--c-on-primary);
  font-weight: 600;
}
.planner-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.planner-error {
  margin: 0;
  padding: 6px 8px;
  border-radius: 8px;
  background: var(--c-danger-soft);
  color: var(--c-danger);
  font-size: var(--fs-xs);
  line-height: 1.5;
}

.summary {
  padding: 7px 9px;
  border-left: 3px solid var(--c-primary);
  border-radius: 8px;
  background: var(--c-surface-2);
}
.summary-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}
.summary-mode {
  flex: 0 0 auto;
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}
.summary-dest {
  font-size: var(--fs-sm);
  color: var(--c-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.summary-line {
  display: flex;
  gap: 6px;
  margin: 3px 0 0;
  font-size: var(--fs-md);
  color: var(--c-text);
}
.summary-note {
  margin: 4px 0 0;
  font-size: var(--fs-xs);
  line-height: 1.5;
  color: var(--c-text-faint);
}
.summary-clear {
  margin-top: 6px;
  padding: 0;
  border: none;
  background: none;
  color: var(--c-text-dim);
  font-size: var(--fs-xs);
  text-decoration: underline;
  cursor: pointer;
}
</style>
