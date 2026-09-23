<!--
  警务端登录
  ==========
  只有警号 + 口令。没有"注册"——账号由管理员在警员管理里维护，或由
  `scripts/seed_credentials.py` 批量初始化。

  这一页刻意做得克制：不放演示账号、不放口令提示。演示口令写死在界面上，
  一旦被带到任何非演示环境就是事故。
-->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { authApi } from '@/api'
import { describeError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { RoleOption } from '@/types/api'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const officerId = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
const roles = ref<RoleOption[]>([])

onMounted(async () => {
  try {
    const result = await authApi.roles()
    roles.value = result.items
  } catch {
    // 角色说明只是辅助信息，拿不到不该影响登录
    roles.value = []
  }
})

async function submit(): Promise<void> {
  if (!officerId.value.trim() || !password.value) {
    error.value = '请输入警号与口令'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await auth.login(officerId.value.trim(), password.value)
    // 登录前想去的页面在 query 里，登录后送回去
    const redirect = route.query.redirect
    await router.replace(typeof redirect === 'string' && redirect ? redirect : '/dashboard')
  } catch (err) {
    error.value = describeError(err).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <form class="login-card" @submit.prevent="submit">
      <div class="brand">
        <div class="brand-mark">ST</div>
        <div class="brand-text">
          <strong>智能交通监测</strong>
          <span>与事故预警平台</span>
        </div>
      </div>

      <h1 class="title">警务端登录</h1>

      <label class="field">
        <span class="field-label">警号</span>
        <input
          v-model="officerId"
          class="input"
          autocomplete="username"
          spellcheck="false"
          placeholder="例如 410001"
        />
      </label>

      <label class="field">
        <span class="field-label">口令</span>
        <input
          v-model="password"
          class="input"
          type="password"
          autocomplete="current-password"
          placeholder="请输入口令"
        />
      </label>

      <p v-if="error" class="error">{{ error }}</p>

      <button class="submit" type="submit" :disabled="busy">
        {{ busy ? '登录中…' : '登录' }}
      </button>

      <!--
        角色说明放在折叠里而不是直接铺开：它是"给第一次用的人看的"，
        每天登录的人不需要每次滚过这一大段。
      -->
      <details v-if="roles.length" class="roles">
        <summary>系统有哪几种角色？</summary>
        <ul>
          <li v-for="item in roles" :key="item.value">
            <span class="role-name">{{ item.label }}</span>
            <span class="role-hint">{{ item.hint }}</span>
          </li>
        </ul>
      </details>

      <p class="foot">
        还没有账号？管理员可在「警员管理」中新增，
        或在后端执行 <code>python scripts/seed_credentials.py</code> 批量初始化。
      </p>
    </form>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
  background: var(--bg-base, #0b1220);
  box-sizing: border-box;
}

.login-card {
  width: 100%;
  max-width: 400px;
  padding: 28px 26px;
  background: var(--bg-panel, #0f172a);
  border: 1px solid var(--border, #1e293b);
  border-radius: 14px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.42);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 20px;
}
.brand-mark {
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: var(--accent, #22d3ee);
  color: #05202a;
  font-weight: 700;
  letter-spacing: 0.5px;
}
.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
}
.brand-text strong {
  font-size: 15px;
}
.brand-text span {
  font-size: 12px;
  color: var(--text-dim, #94a3b8);
}

.title {
  margin: 0 0 18px;
  font-size: 19px;
  font-weight: 600;
}

.field {
  display: block;
  margin-bottom: 14px;
}
.field-label {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--text-dim, #94a3b8);
}
.input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-strong, #334155);
  border-radius: 9px;
  background: var(--bg-panel-2, #16213e);
  color: var(--text, #e2e8f0);
  /* ≥16px：更小的字号在 iOS 上聚焦时会把整页放大且不缩回 */
  font-size: 16px;
  box-sizing: border-box;
}
.input:focus {
  outline: none;
  border-color: var(--accent, #22d3ee);
}

.error {
  margin: 0 0 12px;
  padding: 9px 11px;
  border-radius: 8px;
  background: rgba(248, 113, 113, 0.12);
  border-left: 3px solid #f87171;
  color: #fca5a5;
  font-size: 13px;
  line-height: 1.6;
}

.submit {
  width: 100%;
  padding: 11px;
  border: none;
  border-radius: 9px;
  background: var(--accent, #22d3ee);
  color: #05202a;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
}
.submit:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.roles {
  margin-top: 18px;
  font-size: 12px;
  color: var(--text-dim, #94a3b8);
}
.roles summary {
  cursor: pointer;
  color: var(--text-faint, #64748b);
}
.roles ul {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.roles li {
  display: flex;
  gap: 8px;
  line-height: 1.6;
}
.role-name {
  flex: 0 0 52px;
  color: var(--text, #e2e8f0);
  font-weight: 600;
}

.foot {
  margin: 18px 0 0;
  padding-top: 14px;
  border-top: 1px solid var(--border, #1e293b);
  font-size: 11px;
  line-height: 1.7;
  color: var(--text-faint, #64748b);
}
.foot code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--bg-panel-2, #16213e);
  font-size: 10.5px;
}
</style>
