<!--
  系统状态
  ========
  展示依赖健康度、视频源清单、模型状态与运行时配置。

  这一页是排障入口：当检测不可用或视频源报错时，先看这里。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { detectionApi, systemApi } from '@/api'
import { describeError } from '@/api/client'
import { useSystemStore } from '@/stores/system'
import { useVideoStore } from '@/stores/video'
import type { ModelStatus, RuntimeConfigSaveResult, RuntimeConfigState } from '@/types/api'

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

// ==========================================================================
// 在线修改配置
// ==========================================================================

const runtime = ref<RuntimeConfigState | null>(null)
const runtimeLoading = ref(false)
/** 编辑中的值：key -> 新值。只放用户真正改过的项 */
const drafts = ref<Record<string, string>>({})
const token = ref('')
const saving = ref(false)
const runtimeError = ref('')
const saveResult = ref<RuntimeConfigSaveResult | null>(null)

/**
 * 口令只记在本会话内，免得每保存一次都要重输。
 *
 * 用 sessionStorage 而非 localStorage：关掉标签页即清除，
 * 不在设备上长期留一份"能改服务器配置"的凭据。
 */
const TOKEN_KEY = 'smart-traffic:runtime-config-token'

async function loadRuntime(): Promise<void> {
  runtimeLoading.value = true
  runtimeError.value = ''
  try {
    runtime.value = await systemApi.runtimeConfig()
  } catch (err) {
    runtimeError.value = describeError(err).message
  } finally {
    runtimeLoading.value = false
  }
}

/**
 * 只提交改动过的项。
 *
 * 必须这样：服务端回显的是打码值（含 `*`），把没改的项一并提交上去，
 * 等于把 `6bd4******45ef` 这种字符串写进 .env。
 */
const dirty = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const item of runtime.value?.items ?? []) {
    const draft = drafts.value[item.key]
    if (draft !== undefined && draft !== item.value) out[item.key] = draft
  }
  return out
})

const dirtyCount = computed(() => Object.keys(dirty.value).length)

function onEdit(key: string, value: string): void {
  drafts.value[key] = value
}

async function save(): Promise<void> {
  if (!dirtyCount.value) {
    runtimeError.value = '没有改动需要保存'
    return
  }
  saving.value = true
  runtimeError.value = ''
  saveResult.value = null
  try {
    const result = await systemApi.saveRuntimeConfig({
      token: token.value,
      values: dirty.value,
    })
    saveResult.value = result
    runtime.value = result.config
    drafts.value = {}
    sessionStorage.setItem(TOKEN_KEY, token.value)
  } catch (err) {
    runtimeError.value = describeError(err).message
  } finally {
    saving.value = false
  }
}

function resetDrafts(): void {
  drafts.value = {}
  saveResult.value = null
  runtimeError.value = ''
}

/**
 * 改过高德相关项后需要刷新页面才生效：
 * 地图实例在挂载时读一次 `systemStore.config`，之后不会再取。
 * 不提示的话，用户会以为保存失败了。
 */
const needReload = computed(
  () => !!saveResult.value?.changed.some((key) => key.startsWith('AMAP_')),
)

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
  token.value = sessionStorage.getItem(TOKEN_KEY) ?? ''
  void refresh()
  void videoStore.loadSources()
  void loadRuntime()
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

      <!-- 在线修改配置（默认关闭，写入服务器配置属于敏感操作） -->
      <section class="panel">
        <div class="panel-title">
          ✎ 在线修改配置
          <span
            v-if="runtime"
            class="title-tag"
            :class="runtime.enabled ? 'tag-ok' : 'tag-off'"
          >
            {{ runtime.enabled ? '已启用' : '默认关闭' }}
          </span>
        </div>

        <div v-if="runtimeLoading" class="cfg-note">读取中…</div>

        <template v-else-if="runtime">
          <!-- 关闭时把"为什么关着、怎么打开"直接写出来，而不是只给一个灰按钮 -->
          <div v-if="!runtime.enabled" class="cfg-note cfg-note-off">
            {{ runtime.reason }}
          </div>

          <template v-else>
            <p class="cfg-hint">
              保存后立即生效，无需重启服务。值写回
              <span class="mono">{{ runtime.envPath }}</span>
              ，原有注释与其他键不受影响。
            </p>

            <div class="cfg-list">
              <label v-for="item in runtime.items" :key="item.key" class="cfg-row">
                <span class="cfg-key mono">{{ item.key }}</span>
                <input
                  class="cfg-input"
                  :value="drafts[item.key] ?? ''"
                  :placeholder="item.value || '未配置'"
                  spellcheck="false"
                  autocomplete="off"
                  @input="onEdit(item.key, ($event.target as HTMLInputElement).value)"
                />
              </label>
            </div>

            <div class="cfg-actions">
              <input
                v-model="token"
                class="cfg-input cfg-token"
                type="password"
                placeholder="保存口令（.env 里的 RUNTIME_CONFIG_TOKEN）"
                autocomplete="off"
              />
              <button class="btn" :disabled="saving || !dirtyCount" @click="save">
                {{ saving ? '保存中…' : `保存${dirtyCount ? ` (${dirtyCount})` : ''}` }}
              </button>
              <button class="btn" :disabled="!dirtyCount" @click="resetDrafts">撤销</button>
            </div>

            <p v-if="runtimeError" class="cfg-msg cfg-msg-error">{{ runtimeError }}</p>
            <p v-if="saveResult" class="cfg-msg cfg-msg-ok">{{ saveResult.message }}</p>
            <p v-if="saveResult?.rejected.length" class="cfg-msg cfg-msg-warn">
              被拒绝：{{ saveResult.rejected.join('；') }}
            </p>
            <p v-if="needReload" class="cfg-msg cfg-msg-warn">
              高德配置已写入，但当前页面用的仍是旧值 —— 刷新后生效。
            </p>
          </template>
        </template>
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

/* -------------------------------------------------- 在线修改配置 */

.title-tag {
  margin-left: 8px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}
.tag-ok {
  background: rgba(74, 222, 128, 0.16);
  color: #4ade80;
}
.tag-off {
  background: var(--bg-panel-2);
  color: var(--text-faint);
}

.cfg-note {
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--bg-panel-2);
  color: var(--text-dim);
  font-size: 13px;
  line-height: 1.7;
}
/* 关闭状态用左侧色条强调：这条信息是"怎么打开"，不是普通说明 */
.cfg-note-off {
  border-left: 3px solid #d97706;
}

.cfg-hint {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-dim);
}

.cfg-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cfg-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.cfg-key {
  flex: 0 0 210px;
  font-size: 12px;
  color: var(--text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cfg-input {
  flex: 1;
  min-width: 0;
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-panel-2);
  color: var(--text);
  /* ≥16px：更小的字号在 iOS 上聚焦时会把整页放大且不缩回 */
  font-size: 16px;
  box-sizing: border-box;
}
.cfg-input:focus {
  outline: none;
  border-color: var(--accent-dim);
}

.cfg-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
}
.cfg-token {
  flex: 1 1 200px;
}

.cfg-msg {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.7;
}
.cfg-msg-ok {
  color: #4ade80;
}
.cfg-msg-error {
  color: #f87171;
}
.cfg-msg-warn {
  color: #fbbf24;
}
</style>
