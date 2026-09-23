<!--
  PWA 运行时提示
  ==============
  三件事，都是"装在手机上"之后才会遇到的：

  1. **有新版本** —— Service Worker 更新了就绪。不能自动刷新：用户可能正在
     填上报表单，刷新会把内容清空。给一个明确的按钮让他自己决定时机。

  2. **可添加到桌面** —— 浏览器触发 `beforeinstallprompt` 时提示。
     **只在民众端显示**：警务端是内部系统，值班员用浏览器书签就够了，
     而且 manifest 的 start_url 指向民众端，装出来的图标本来就该是民众端。

  3. **离线了** —— 明确告诉用户当前看的是缓存数据。这比让页面静静显示旧路况
     要好得多：出行决策依赖"现在堵不堵"，把十分钟前的数据当实时的看，
     可能直接开进堵点。

  三块提示统一在底部 Tab 之上，互不拥挤：同一时刻只显示优先级最高的那个。
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRegisterSW } from 'virtual:pwa-register/vue'

/** 是否民众端 —— 安装引导只对它显示 */
const props = defineProps<{ citizen?: boolean }>()

const { needRefresh, updateServiceWorker } = useRegisterSW({
  onRegisteredSW(_url: string, registration: any) {
    // 每小时检查一次更新。不设更密的间隔：更新检查会打网络，
    // 而这类应用的更新频率本来就不高。
    if (registration) {
      setInterval(() => registration.update?.(), 60 * 60 * 1000)
    }
  },
})

const offline = ref(!navigator.onLine)
const installEvent = ref<any>(null)
const installDismissed = ref(false)

/** 当前该显示哪条提示。同一时刻只显示一条，避免堆叠。 */
const active = computed(() => {
  if (offline.value) return 'offline'
  if (needRefresh.value) return 'update'
  if (props.citizen && installEvent.value && !installDismissed.value) return 'install'
  return ''
})

function onOnline(): void {
  offline.value = false
}

function onOffline(): void {
  offline.value = true
}

function onInstallPrompt(event: Event): void {
  // 必须拦截默认行为，否则浏览器会自己弹一个样式不可控的横幅；
  // 拦截后由我们自己决定何时、以什么样子展示。
  event.preventDefault()
  installEvent.value = event
}

async function install(): Promise<void> {
  const event = installEvent.value
  if (!event) return
  event.prompt()
  const choice = await event.userChoice
  installEvent.value = null
  if (choice?.outcome === 'dismissed') {
    installDismissed.value = true
  }
}

function dismiss(): void {
  installDismissed.value = true
}

async function refresh(): Promise<void> {
  // updateServiceWorker(true) 会先激活新 SW 再刷新页面
  await updateServiceWorker(true)
}

onMounted(() => {
  window.addEventListener('online', onOnline)
  window.addEventListener('offline', onOffline)
  window.addEventListener('beforeinstallprompt', onInstallPrompt)
})

onBeforeUnmount(() => {
  window.removeEventListener('online', onOnline)
  window.removeEventListener('offline', onOffline)
  window.removeEventListener('beforeinstallprompt', onInstallPrompt)
})
</script>

<template>
  <Transition name="pwa-slide">
    <div v-if="active" class="pwa-bar" :class="`pwa-${active}`">
      <!-- 离线 -->
      <template v-if="active === 'offline'">
        <span class="pwa-icon">⚡</span>
        <div class="pwa-text">
          <strong>当前处于离线状态</strong>
          <span>显示的是最近一次缓存的路况，可能已过期</span>
        </div>
      </template>

      <!-- 有新版本 -->
      <template v-else-if="active === 'update'">
        <span class="pwa-icon">↑</span>
        <div class="pwa-text">
          <strong>有新版本可用</strong>
          <span>刷新后生效</span>
        </div>
        <button class="pwa-btn" @click="refresh">立即刷新</button>
      </template>

      <!-- 可安装 -->
      <template v-else>
        <span class="pwa-icon">＋</span>
        <div class="pwa-text">
          <strong>添加到桌面</strong>
          <span>像 App 一样打开，弱网时也能看路况</span>
        </div>
        <button class="pwa-btn" @click="install">添加</button>
        <button class="pwa-close" title="暂不添加" @click="dismiss">✕</button>
      </template>
    </div>
  </Transition>
</template>

<style scoped>
/*
  字号变量带 fallback。
  本组件在民众端（有 --fs-* 阶梯）与警务端（无）都会挂载，
  警务端拿不到变量，用 fallback 保持原尺寸即可。
*/
.pwa-bar {
  position: fixed;
  left: 50%;
  transform: translateX(-50%);
  /*
    贴在底部 Tab 上方。
    58 = Tab 高 50 + 8 的间距。原先是 66，那是按旧布局（Tab 更矮）估的，
    实测会压到地图页的底部事件条上 —— 提示条出现在用户正看的事件上很别扭。
  */
  bottom: calc(58px + env(safe-area-inset-bottom, 0px));
  z-index: 1600;
  display: flex;
  align-items: center;
  gap: 10px;
  width: min(440px, calc(100vw - 20px));
  padding: 9px 12px;
  border-radius: 12px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  box-shadow: 0 4px 18px rgba(15, 23, 42, 0.16);
  font-size: var(--fs-sm, 12px);
}

/* 离线与更新用不同的强调色，避免"有新版本"和"断网了"被看成一回事 */
.pwa-offline {
  border-color: #fbbf24;
  background: #fffbeb;
}
.pwa-update {
  border-color: #93c5fd;
  background: #eff6ff;
}

.pwa-icon {
  flex: 0 0 auto;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: #1d4ed8;
  color: #fff;
  font-size: var(--fs-md, 14px);
  font-weight: 700;
}
.pwa-offline .pwa-icon {
  background: #d97706;
}
.pwa-update .pwa-icon {
  background: #2563eb;
}

.pwa-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.pwa-text strong {
  font-size: var(--fs-sm, 12.5px);
  color: #0f172a;
  font-weight: 600;
}
.pwa-text span {
  font-size: var(--fs-xs, 11px);
  color: #64748b;
  line-height: 1.4;
}

.pwa-btn {
  flex: 0 0 auto;
  padding: 6px 13px;
  border: none;
  border-radius: 9px;
  background: #1d4ed8;
  color: #fff;
  font-size: var(--fs-sm, 12px);
  font-weight: 600;
  cursor: pointer;
  /* 移动端点按目标不小于 36px，避免手指够不准 */
  min-height: 36px;
}
.pwa-btn:active {
  transform: scale(0.97);
}

/*
  关闭按钮的视觉大小保持小巧，但点击区域必须够大。
  原先只有 padding:4px，实测盒子 19×21px —— 手指根本点不准，
  尤其这条提示还出现在用户正在看路况的时候。
  用固定 36px 盒子 + 负外边距：视觉位置不变，热区扩大一倍多。
*/
.pwa-close {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  margin-right: -8px;
  border: none;
  border-radius: 8px;
  background: none;
  color: #94a3b8;
  font-size: var(--fs-base, 13px);
  line-height: 1;
  cursor: pointer;
}
.pwa-close:active {
  background: rgba(15, 23, 42, 0.06);
}

/* 桌面端把提示抬高一点，避开浏览器底栏 */
@media (min-width: 768px) {
  .pwa-bar {
    bottom: 16px;
  }
}

.pwa-slide-enter-active,
.pwa-slide-leave-active {
  transition: opacity 0.22s ease, transform 0.22s ease;
}
.pwa-slide-enter-from,
.pwa-slide-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(12px);
}
</style>
