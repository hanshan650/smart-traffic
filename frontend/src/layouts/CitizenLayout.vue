<!--
  民众端布局
  ==========
  与警务端**刻意做成两套视觉语言**，不只是"换个颜色"：

  |          | 警务端                  | 民众端                     |
  |----------|-------------------------|----------------------------|
  | 场景     | 监控室大屏，长时间值守   | 手机上看一眼，或路上临时打开 |
  | 底色     | 深色（降低暗环境眩光）   | 亮色（白天户外可读）        |
  | 信息密度 | 高，一屏尽可能多          | 低，一眼看到重点            |
  | 术语     | 专业（判据、证据链）     | 生活化（哪儿堵了、怎么绕）   |
  | 导航     | 左侧固定菜单             | 底部 Tab（拇指可及）        |

  主题色通过**容器级 CSS 变量**下发而非 `:root` —— 后者会污染整个应用，
  导致警务端的深色界面被民众端样式渗透。变量写在根容器上，
  子组件通过继承拿到，切换路由即自动隔离。
-->
<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const navItems = [
  { name: 'citizen-home', path: '/citizen', label: '路况', icon: '◉' },
  { name: 'citizen-report', path: '/citizen/report', label: '上报', icon: '✎' },
  { name: 'citizen-track', path: '/citizen/track', label: '进度', icon: '⌕' },
  { name: 'citizen-advice', path: '/citizen/advice', label: '建议', icon: '⇢' },
  { name: 'citizen-help', path: '/citizen/help', label: '安全', icon: '☎' },
]

const activeName = computed(() => route.name as string)
</script>

<template>
  <div class="citizen-shell">
    <!-- 顶部品牌条 -->
    <header class="c-header">
      <div class="c-header-inner">
        <div class="c-brand">
          <span class="c-logo">路</span>
          <div>
            <strong>高速出行助手</strong>
            <span class="c-tagline">实时路况 · 随手上报</span>
          </div>
        </div>

        <!-- 桌面端把导航放顶部 -->
        <nav class="c-nav-desktop">
          <RouterLink
            v-for="item in navItems"
            :key="item.name"
            :to="item.path"
            class="c-nav-link"
            :class="{ active: activeName === item.name }"
          >
            <span class="c-nav-icon">{{ item.icon }}</span>{{ item.label }}
          </RouterLink>
        </nav>
      </div>
    </header>

    <!-- 内容区 -->
    <main class="c-main">
      <RouterView />
    </main>

    <footer class="c-footer">
      <span>数据来自高速公路监测点，仅供出行参考</span>
      <RouterLink to="/dashboard" class="c-admin-link">管理入口</RouterLink>
    </footer>

    <!-- 移动端底部 Tab -->
    <nav class="c-tabbar">
      <RouterLink
        v-for="item in navItems"
        :key="item.name"
        :to="item.path"
        class="c-tab"
        :class="{ active: activeName === item.name }"
      >
        <span class="c-tab-icon">{{ item.icon }}</span>
        <span class="c-tab-label">{{ item.label }}</span>
      </RouterLink>
    </nav>
  </div>
</template>

<style scoped>
/*
  主题变量写在根容器上。所有子组件通过继承拿到这些值，
  不必在每个页面里重复定义，也不会外泄到警务端。
*/
.citizen-shell {
  --c-bg: #f2f5f9;
  --c-surface: #ffffff;
  --c-surface-2: #f8fafc;
  --c-border: #e2e8f0;
  --c-border-strong: #cbd5e1;
  --c-text: #0f172a;
  --c-text-dim: #475569;
  --c-text-faint: #94a3b8;
  --c-primary: #1d4ed8;
  --c-primary-soft: #eff6ff;
  --c-danger: #dc2626;
  --c-warn: #ea580c;
  --c-ok: #16a34a;
  --c-radius: 14px;
  --c-shadow: 0 1px 3px rgba(15, 23, 42, 0.06), 0 6px 18px rgba(15, 23, 42, 0.05);

  min-height: 100vh;
  background: var(--c-bg);
  color: var(--c-text);
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, 'Segoe UI', 'PingFang SC',
    'Microsoft YaHei', sans-serif;
  /* 移动端底部 Tab 会盖住内容，留出安全间距 */
  padding-bottom: 64px;
}

/* ------------------------------------------------------------------ 顶部 */

.c-header {
  position: sticky;
  top: 0;
  z-index: 20;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-border);
}
.c-header-inner {
  max-width: 980px;
  margin: 0 auto;
  padding: 10px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.c-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}
.c-logo {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  background: var(--c-primary);
  color: #fff;
  font-weight: 700;
  font-size: 17px;
}
.c-brand strong {
  display: block;
  font-size: 15px;
  line-height: 1.25;
}
.c-tagline {
  font-size: 11px;
  color: var(--c-text-faint);
}

.c-nav-desktop {
  display: none;
  gap: 2px;
}
.c-nav-link {
  padding: 7px 13px;
  border-radius: 9px;
  font-size: 13px;
  color: var(--c-text-dim);
  text-decoration: none;
  transition: background 0.15s, color 0.15s;
}
.c-nav-link:hover {
  background: var(--c-surface-2);
}
.c-nav-link.active {
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 600;
}
.c-nav-icon {
  margin-right: 5px;
}

/* ------------------------------------------------------------------ 内容 */

.c-main {
  flex: 1;
  width: 100%;
  max-width: 980px;
  margin: 0 auto;
  padding: 14px 16px 24px;
}

.c-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 10px 16px 18px;
  font-size: 11px;
  color: var(--c-text-faint);
  flex-wrap: wrap;
}
.c-admin-link {
  color: var(--c-text-faint);
  text-decoration: none;
  border-bottom: 1px dashed var(--c-border-strong);
}
.c-admin-link:hover {
  color: var(--c-primary);
}

/* -------------------------------------------------------------- 底部 Tab */

.c-tabbar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 30;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  background: var(--c-surface);
  border-top: 1px solid var(--c-border);
  /* 适配全面屏手势条 */
  padding-bottom: env(safe-area-inset-bottom, 0);
}
.c-tab {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 7px 2px 6px;
  text-decoration: none;
  color: var(--c-text-faint);
  font-size: 11px;
  /* 触摸目标不小于 44px，符合移动端可点击区域建议 */
  min-height: 48px;
  justify-content: center;
}
.c-tab.active {
  color: var(--c-primary);
  font-weight: 600;
}
.c-tab-icon {
  font-size: 17px;
  line-height: 1.1;
}

/* ------------------------------------------------------------------ 桌面 */

@media (min-width: 768px) {
  .citizen-shell {
    padding-bottom: 0;
  }
  .c-nav-desktop {
    display: flex;
  }
  .c-tabbar {
    display: none;
  }
  .c-main {
    padding: 22px 16px 32px;
  }
}
</style>
