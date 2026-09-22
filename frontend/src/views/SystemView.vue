<!--
  系统状态
  ========
  展示依赖健康度、视频源清单、模型状态与运行时配置。

  这一页是排障入口：当检测不可用或视频源报错时，先看这里。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { detectionApi } from '@/api'
import { describeError } from '@/api/client'
import { useSystemStore } from '@/stores/system'
import { useVideoStore } from '@/stores/video'
import type { ModelStatus } from '@/types/api'

const systemStore = useSystemStore()
const videoStore = useVideoStore()

const model = ref<ModelStatus | null>(null)
const thresholds = ref<{ light: number; moderate: number; heavy: number; confidence: number } | null>(
  null,
)

const health = computed(() => systemStore.health)
const config = computed(() => systemStore.config)

interface CheckItem {
  label: string
  ok: boolean
  text: string
  hint: string
}

const checks = computed<CheckItem[]>(() => {
  const h = health.value
  return [
    {
      label: 'MongoDB',
      ok: !!h?.mongo,
      text: h?.mongo ? '已连接' : '未连接',
      hint: h?.mongo ? '检测记录与事件可持久化' : '检测结果不会落库，事件功能不可用',
    },
    {
      label: 'Redis',
      ok: !!h?.redis,
      text: h?.redis ? '已连接' : '降级运行',
      hint: h?.redis ? '实时缓存与告警限流正常' : '缓存与限流降级，告警不会被抑制（可能重复）',
    },
    {
      label: 'YOLO 推理',
      ok: !!h?.yolo,
      text: h?.yolo ? '就绪' : '不可用',
      hint: h?.yolo ? `模型：${model.value?.model ?? '—'}` : (model.value?.reason ?? ''),
    },
    {
      label: `视频源 · ${h?.videoSource ?? '—'}`,
      ok: !!h?.videoSourceAvailable,
      text: h?.videoSourceAvailable ? '可用' : '仅接入位',
      hint: h?.videoSourceAvailable
        ? '可正常拉取摄像头与直播流'
        : '该视频源暂无公开接口，仅保留调用契约',
    },
  ]
})

async function refresh(): Promise<void> {
  await systemStore.bootstrap()
  try {
    model.value = await detectionApi.model()
  } catch {
    model.value = null
  }
  try {
    thresholds.value = await detectionApi.thresholds()
  } catch {
    thresholds.value = null
  }
}

/** 连通性自测：拉取一个摄像头列表 */
const probeResult = ref('')
const probing = ref(false)

async function probe(): Promise<void> {
  probing.value = true
  probeResult.value = ''
  try {
    const count = await videoStore.loadCameras(3, false)
    probeResult.value = count
      ? `✓ 成功获取 ${count} 个摄像头点位`
      : `未返回点位：${videoStore.error || '未知原因'}`
  } catch (error) {
    probeResult.value = describeError(error).message
  } finally {
    probing.value = false
  }
}

onMounted(() => {
  void refresh()
  void videoStore.loadSources()
})
</script>

<template>
  <div class="page">
    <section class="panel">
      <div class="panel-title">
        <span>◎ 依赖健康度</span>
        <button class="btn btn-sm refresh" @click="refresh">↻ 刷新</button>
      </div>

      <div class="checks">
        <div v-for="item in checks" :key="item.label" class="check">
          <span class="check-dot" :class="item.ok ? 'ok' : 'bad'">●</span>
          <div class="check-main">
            <div class="check-head">
              <strong>{{ item.label }}</strong>
              <span class="badge" :class="item.ok ? 'badge-ok' : 'badge-warn'">
                {{ item.text }}
              </span>
            </div>
            <div v-if="item.hint" class="check-hint text-dim">{{ item.hint }}</div>
          </div>
        </div>
      </div>
    </section>

    <div class="two-col">
      <!-- 视频源清单 -->
      <section class="panel">
        <div class="panel-title">▦ 视频源注册表</div>
        <table class="table">
          <thead>
            <tr>
              <th>关键标识</th>
              <th>名称</th>
              <th>区域</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="source in videoStore.sources" :key="source.key">
              <td class="mono">{{ source.key }}</td>
              <td>{{ source.displayName }}</td>
              <td>{{ source.region }}</td>
              <td>
                <span class="badge" :class="source.available ? 'badge-ok' : 'badge-muted'">
                  {{ source.available ? '可用' : '仅接入位' }}
                </span>
              </td>
            </tr>
            <tr v-if="!videoStore.sources.length">
              <td colspan="4" class="text-faint">加载中…</td>
            </tr>
          </tbody>
        </table>

        <div class="probe">
          <button class="btn btn-sm" :disabled="probing" @click="probe">
            {{ probing ? '测试中…' : '连通性自测' }}
          </button>
          <span v-if="probeResult" class="probe-result mono">{{ probeResult }}</span>
        </div>
      </section>

      <!-- 阈值与配置 -->
      <section class="panel">
        <div class="panel-title">⚙ 运行参数</div>
        <div class="kv-list">
          <div class="kv">
            <span>拥堵判定（轻度）</span>
            <span class="mono">≥ {{ thresholds?.light ?? '—' }} 辆</span>
          </div>
          <div class="kv">
            <span>拥堵判定（中度）</span>
            <span class="mono">≥ {{ thresholds?.moderate ?? '—' }} 辆</span>
          </div>
          <div class="kv">
            <span>拥堵判定（严重）</span>
            <span class="mono">≥ {{ thresholds?.heavy ?? '—' }} 辆</span>
          </div>
          <div class="kv">
            <span>检测置信度阈值</span>
            <span class="mono">{{ thresholds?.confidence ?? '—' }}</span>
          </div>
          <div class="kv">
            <span>高德 JS Key</span>
            <span class="badge" :class="config?.amapJsKey ? 'badge-ok' : 'badge-warn'">
              {{ config?.amapJsKey ? '已配置' : '未配置' }}
            </span>
          </div>
          <div class="kv">
            <span>高德安全密钥</span>
            <span
              class="badge"
              :class="config?.amapJsSecurityCode ? 'badge-ok' : 'badge-warn'"
            >
              {{ config?.amapJsSecurityCode ? '已配置' : '未配置（地图会报 SCODE 错误）' }}
            </span>
          </div>
          <div class="kv">
            <span>当前视频源</span>
            <span class="mono">{{ config?.videoSource ?? '—' }}</span>
          </div>
        </div>
      </section>
    </div>

    <!-- 后端原始健康数据 -->
    <section class="panel">
      <div class="panel-title">⌗ 后端原始响应</div>
      <pre class="raw mono">{{ JSON.stringify(health, null, 2) }}</pre>
    </section>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.refresh {
  margin-left: auto;
}

.checks {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 10px;
  padding: 12px;
}
.check {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  background: var(--bg-panel-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.check-dot {
  font-size: 14px;
  line-height: 1.5;
}
.check-dot.ok {
  color: var(--ok);
}
.check-dot.bad {
  color: var(--warn);
}
.check-main {
  flex: 1;
  min-width: 0;
}
.check-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.check-hint {
  margin-top: 3px;
  font-size: 11px;
  word-break: break-all;
}

.two-col {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 14px;
  align-items: start;
}

.kv-list {
  padding: 8px 14px 14px;
}
.kv {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px dashed var(--border);
  font-size: 13px;
}
.kv:last-child {
  border-bottom: none;
}

.probe {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 14px 14px;
}
.probe-result {
  font-size: 12px;
  color: var(--text-dim);
}

.raw {
  margin: 0;
  padding: 12px;
  max-height: 320px;
  overflow: auto;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-dim);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
