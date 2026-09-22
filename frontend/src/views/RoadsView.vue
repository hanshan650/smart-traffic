<!--
  上海路网
  ========
  两件事：

  1. **路网台账** —— 路段清单（长度 / 限速 / 车道 / 当前拥堵）
  2. **路线规划** —— 通过后端代理调用高德驾车规划（地名 → 坐标 → 路线）

  注意：本页与视频源无关，展示的是**上海**路网数据。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { roadApi } from '@/api'
import { describeError } from '@/api/client'
import { CONGESTION_COLOR, CONGESTION_LABEL } from '@/types/api'
import type { AmapRoute, RoadNetwork } from '@/types/api'

const network = ref<RoadNetwork | null>(null)
const loading = ref(true)
const error = ref('')

// ------------------------------------------------------------------ 路线规划

const origin = ref('五角场')
const destination = ref('上海南站')
const planning = ref(false)
const planError = ref('')
const route = ref<AmapRoute | null>(null)
const routeMeta = ref<{ distance: number; duration: number } | null>(null)

const filteredKeyword = ref('')

const roads = computed(() => {
  const list = network.value?.roads ?? []
  const keyword = filteredKeyword.value.trim()
  if (!keyword) return list
  return list.filter(
    (road) => road.name.includes(keyword) || road.id.toLowerCase().includes(keyword.toLowerCase()),
  )
})

const congestionStats = computed(() => {
  const stats: Record<string, number> = { normal: 0, light: 0, moderate: 0, heavy: 0 }
  for (const road of network.value?.roads ?? []) {
    stats[road.congestionLevel] = (stats[road.congestionLevel] ?? 0) + 1
  }
  return stats
})

async function planRoute(): Promise<void> {
  planError.value = ''
  route.value = null
  routeMeta.value = null
  planning.value = true

  try {
    const [from, to] = await Promise.all([
      roadApi.geocode(origin.value),
      roadApi.geocode(destination.value),
    ])

    const result = await roadApi.route(
      `${from.lng},${from.lat}`,
      `${to.lng},${to.lat}`,
    )

    route.value = result
    routeMeta.value = { distance: result.distance, duration: result.duration }
  } catch (err) {
    const info = describeError(err)
    planError.value = info.hint ? `${info.message} — ${info.hint}` : info.message
  } finally {
    planning.value = false
  }
}

function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟`
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`
}

onMounted(async () => {
  try {
    network.value = await roadApi.network()
  } catch (err) {
    error.value = describeError(err).message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="page">
    <!-- 摘要 -->
    <section class="grid summary">
      <div class="panel mini">
        <span class="mini-label">路口节点</span>
        <span class="mini-value mono">{{ network?.summary.intersectionCount ?? '—' }}</span>
      </div>
      <div class="panel mini">
        <span class="mini-label">路段总数</span>
        <span class="mini-value mono">{{ network?.summary.roadCount ?? '—' }}</span>
      </div>
      <div class="panel mini">
        <span class="mini-label">总里程</span>
        <span class="mini-value mono">{{ network?.summary.totalLengthKm ?? '—' }} km</span>
      </div>
      <div class="panel mini">
        <span class="mini-label">拥堵路段</span>
        <span class="mini-value mono" :style="{ color: congestionStats.heavy ? 'var(--danger)' : 'var(--ok)' }">
          {{ congestionStats.moderate + congestionStats.heavy }}
        </span>
      </div>
    </section>

    <div class="two-col">
      <!-- 路段台账 -->
      <section class="panel">
        <div class="panel-title">
          <span>⇄ 路段台账</span>
          <input
            v-model="filteredKeyword"
            class="input search"
            placeholder="按名称或编号筛选"
          />
        </div>

        <div v-if="loading" class="empty">
          <div class="spinner" />
        </div>
        <div v-else-if="error" class="empty">
          <div style="color: var(--danger)">⚠ {{ error }}</div>
        </div>
        <div v-else class="table-wrap scroll-y">
          <table class="table">
            <thead>
              <tr>
                <th>编号</th>
                <th>路段</th>
                <th>长度</th>
                <th>限速</th>
                <th>车道</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="road in roads" :key="road.id">
                <td class="mono">{{ road.id }}</td>
                <td>{{ road.name }}</td>
                <td class="mono">{{ (road.lengthM / 1000).toFixed(1) }} km</td>
                <td class="mono">{{ road.speedLimit }}</td>
                <td class="mono">{{ road.lanes }}</td>
                <td>
                  <span class="badge" :style="{ color: CONGESTION_COLOR[road.congestionLevel] }">
                    ● {{ CONGESTION_LABEL[road.congestionLevel] }}
                  </span>
                </td>
              </tr>
              <tr v-if="!roads.length">
                <td colspan="6" class="text-faint">没有匹配的路段</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- 路线规划 -->
      <section class="panel">
        <div class="panel-title">➤ 驾车路线规划</div>

        <div class="planner">
          <label class="field">
            <span>起点</span>
            <input v-model="origin" class="input" placeholder="如：五角场" />
          </label>
          <label class="field">
            <span>终点</span>
            <input v-model="destination" class="input" placeholder="如：上海南站" />
          </label>
          <button class="btn btn-primary" :disabled="planning" @click="planRoute">
            {{ planning ? '规划中…' : '开始规划' }}
          </button>

          <div v-if="planError" class="plan-error">{{ planError }}</div>
        </div>

        <div v-if="routeMeta" class="route-result">
          <div class="route-head">
            <div>
              <span class="route-value mono">{{ (routeMeta.distance / 1000).toFixed(1) }}</span>
              <span class="route-unit">km</span>
            </div>
            <div>
              <span class="route-value mono">{{ formatDuration(routeMeta.duration) }}</span>
            </div>
          </div>

          <ol class="steps scroll-y">
            <li v-for="(step, index) in route?.steps ?? []" :key="index">
              <span class="step-instruction">{{ step.instruction }}</span>
              <span class="step-meta mono text-faint">{{ step.distance }} m</span>
            </li>
          </ol>
        </div>

        <div v-else class="plan-hint text-faint">
          需在 <code>backend/.env</code> 配置 <code>AMAP_WEB_KEY</code>（高德「Web服务」类型 Key）
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.summary {
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
}
.mini {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
}
.mini-label {
  color: var(--text-dim);
  font-size: 12px;
}
.mini-value {
  font-size: 20px;
  font-weight: 700;
}

.two-col {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
  gap: 14px;
  align-items: start;
}

.search {
  margin-left: auto;
  width: 200px;
  font-size: 12px;
}

.table-wrap {
  max-height: 520px;
}

.planner {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-dim);
}

.plan-error {
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: rgba(239, 68, 68, 0.12);
  color: var(--danger);
  font-size: 12px;
}
.plan-hint {
  padding: 0 14px 16px;
  font-size: 12px;
}

.route-result {
  border-top: 1px solid var(--border);
  padding: 12px 14px 16px;
}
.route-head {
  display: flex;
  gap: 28px;
  margin-bottom: 10px;
}
.route-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--accent);
}
.route-unit {
  margin-left: 3px;
  font-size: 12px;
  color: var(--text-dim);
}

.steps {
  margin: 0;
  padding-left: 18px;
  max-height: 260px;
  font-size: 12px;
}
.steps li {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 4px 0;
  border-bottom: 1px dashed var(--border);
  color: var(--text-dim);
}
.step-instruction {
  flex: 1;
  min-width: 0;
}
.step-meta {
  white-space: nowrap;
}
</style>
