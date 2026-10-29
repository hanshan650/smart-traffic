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

import PwaPrompt from '@/components/PwaPrompt.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'

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

        <div class="c-header-right">
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

          <ThemeToggle class="c-theme-in-header" />
        </div>
      </div>
    </header>

    <!--
      手机端顶栏是隐藏的（见样式表末尾），但主题切换仍要能点到，
      所以单独浮一个按钮在右上角，避开内容区标题。
    -->
    <ThemeToggle class="c-theme-floating" />

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

    <!--
      PWA 提示：安装引导 + 新版本 + 离线。
      只有民众端带安装引导 —— 警务端是内部系统，值班员用书签就够了，
      而且 manifest 的 start_url 指向民众端，装出来的图标本就是民众端。
    -->
    <PwaPrompt citizen />
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
  /* 主色块上的文字色：白天配白字，黑夜主色更亮，改用深色字 */
  --c-on-primary: #ffffff;
  --c-danger: #dc2626;
  --c-warn: #ea580c;
  --c-ok: #16a34a;
  /*
    浮在地图上的卡片底色。白天是接近纯白的半透明，黑夜里必须换成深色，
    否则白色卡片会在夜间地图上刺眼；文字色由 --c-text 系列自动反转。
  */
  --c-overlay: rgba(255, 255, 255, 0.95);
  /* 红色 / 橙色提示卡底色，黑夜下有单独的深色版本 */
  --c-danger-soft: #fef2f2;
  --c-danger-border: #fecaca;
  --c-danger-text: #7f1d1d;
  --c-warn-soft: #fff7ed;
  --c-warn-border: #fed7aa;
  --c-ok-soft: #f0fdf4;
  --c-primary-border: #bfdbfe;
  --c-radius: 14px;
  --c-shadow: 0 1px 3px rgba(15, 23, 42, 0.06), 0 6px 18px rgba(15, 23, 42, 0.05);

  /*
    字号阶梯：随视口**连续缩放**，不用断点跳档
    ==========================================
    民众端从手机到桌面要都能用，而固定 px 会在宽屏上显得很小；
    换成一档档的断点又会在大屏边界处突变（跨过 768px 时全页文字突然
    跳一截），而中问尺寸（平板竖屏、分屏窗口）又只能二选一。

    锚点：390px 视口应得手机尺寸，1440px 应得桌面尺寸，
    中间线性插值，两端用 clamp 夹住不上不下。
    斜率用 vw，所以浏览器缩放、设备旋转都会自动跟上。
  */
  --fs-xs: clamp(10px, 0.3vw + 8.9px, 13px); /* 口径说明、图注 */
  --fs-sm: clamp(11px, 0.35vw + 9.7px, 14px); /* 次要信息：时间、元数据 */
  --fs-base: clamp(12px, 0.4vw + 10.5px, 16px); /* 正文 */
  --fs-md: clamp(14px, 0.5vw + 12px, 18px); /* 小标题、重点值 */
  --fs-lg: clamp(16px, 0.6vw + 13.6px, 21px); /* 主标题 */
  --fs-xl: clamp(22px, 1.1vw + 18px, 34px); /* 大号数字 */
  /*
    输入框专用。**任何视口下都不得低于 16px**：
    iOS Safari 在字号小于 16px 的输入框获得焦点时会自动放大整个页面，
    且不会自动缩回。所以它不能跟着 --fs-md 走（那在窄屏只有 14px）。
    宁可输入框比其他文字大一点，也不能让用户每次点输入框都得双指缩回来。
  */
  --fs-input: clamp(16px, 0.3vw + 14.9px, 18px);

  /*
    地图标记尺寸跟着字号走。
    必须单独给变量：标记的 HTML 是高德插入的，只能走内联样式，
    用不了 var() —— 由 RouteMap 在绘制时读取这两个值再拼进 HTML。
  */
  --pin-size: clamp(26px, 0.8vw + 22.8px, 32px);
  --pin-font: calc(var(--pin-size) / 2);

  min-height: 100vh;
  background: var(--c-bg);
  color: var(--c-text);
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, 'Segoe UI', 'PingFang SC',
    'Microsoft YaHei', sans-serif;
  font-size: var(--fs-base);
  /* 移动端底部 Tab 会盖住内容，留出安全间距 */
  padding-bottom: 64px;
  transition: background-color 0.25s ease, color 0.25s ease;
}

/*
  黑夜主题（18:00–06:00 自动，或手动选择）
  =========================================
  民众端原本只有亮色，这里把同一套 --c-* 变量换成深色值，页面结构不动。
  用 html[data-theme='dark'] 作为祖先选择器，scoped 编译后仍能命中本容器，
  且不会外泄到警务端。
*/
html[data-theme='dark'] .citizen-shell {
  --c-bg: #070d18;
  --c-surface: #101a2b;
  --c-surface-2: #16223a;
  --c-border: #23324a;
  --c-border-strong: #34465f;
  --c-text: #e6edf6;
  --c-text-dim: #a7b6c9;
  --c-text-faint: #7f8fa6;
  --c-primary: #38bdf8;
  --c-primary-soft: rgba(56, 189, 248, 0.14);
  --c-on-primary: #06121f;
  --c-danger: #f87171;
  --c-warn: #fb923c;
  --c-ok: #34d399;
  --c-overlay: rgba(16, 26, 43, 0.94);
  --c-danger-soft: rgba(248, 113, 113, 0.12);
  --c-danger-border: rgba(248, 113, 113, 0.4);
  --c-danger-text: #fecaca;
  --c-warn-soft: rgba(251, 146, 60, 0.12);
  --c-warn-border: rgba(251, 146, 60, 0.4);
  --c-ok-soft: rgba(52, 211, 153, 0.12);
  --c-primary-border: rgba(56, 189, 248, 0.35);
  --c-shadow: 0 1px 3px rgba(0, 0, 0, 0.5), 0 6px 18px rgba(0, 0, 0, 0.35);
}

/*
  手机端：锁定视口高度，滚动交给内容区
  ======================================
  原先整页随内容高度增长，手机上一律产生纵向滚动 —— 导航类界面里
  这很难用：想拖地图却把页面拖走了，顶部路段条与底部 Tab 也跟着跑。

  改成 App 式布局：外壳固定一屏高，只有 .c-main 自己滚。
  地图页里 .c-main 的内容正好撑满，所以连它也不滚。

  用 dvh 而非 vh：移动端地址栏收起时 vh 不更新，底部会露一截。
*/
@media (max-width: 767px) {
  .citizen-shell {
    height: 100vh;
    height: 100dvh;
    min-height: 0;
    overflow: hidden;
    /*
      给底部 Tab 留出高度，不能设成 0。
      Tab 是 fixed 浮在页面上，设 0 会让 .c-main 延伸到它下面 ——
      而地图的事件栏就在地图底部，会被 Tab 正好盖住。
      实测：设 0 时地图 727px 高、底边 834px，与 Tab 的 794~844 重叠 40px。
      Tab 自身另有 safe-area 内边距，这里同步加上。
    */
    padding-bottom: calc(50px + env(safe-area-inset-bottom, 0px));
  }
}

@media (min-width: 768px) {
  .citizen-shell {
    padding-bottom: 0;
  }
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
  max-width: 1080px;
  margin: 0 auto;
  padding: clamp(8px, 0.45vw + 6px, 14px) clamp(12px, 0.9vw + 8px, 24px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: clamp(10px, 1.2vw + 5px, 20px);
}
.c-brand {
  display: flex;
  align-items: center;
  gap: clamp(7px, 0.5vw + 5px, 11px);
}
.c-logo {
  flex: 0 0 auto;
  width: clamp(30px, 1.5vw + 24px, 40px);
  height: clamp(30px, 1.5vw + 24px, 40px);
  display: grid;
  place-items: center;
  border-radius: clamp(8px, 0.5vw + 6px, 12px);
  background: var(--c-primary);
  color: var(--c-on-primary);
  font-weight: 700;
  font-size: var(--fs-lg);
}
.c-brand strong {
  display: block;
  font-size: var(--fs-md);
  line-height: 1.25;
}
.c-tagline {
  font-size: var(--fs-xs);
  color: var(--c-text-faint);
}

.c-header-right {
  display: flex;
  align-items: center;
  gap: clamp(8px, 0.8vw + 5px, 14px);
  min-width: 0;
}

.c-nav-desktop {
  display: none;
  gap: 2px;
}

/* 手机端顶栏整体隐藏，按钮改用 .c-theme-floating 浮在右上角 */
.c-theme-in-header {
  flex: 0 0 auto;
}
/* 用容器前缀提高特异性：ThemeToggle 自身的 display 规则优先级相同，
   只靠书写顺序会在 CSS 分包顺序变化时失效 */
.citizen-shell .c-theme-floating {
  display: none;
}
.c-nav-link {
  padding: 7px 13px;
  border-radius: 9px;
  font-size: var(--fs-base);
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
  /*
    min-height: 0 不能省。flex item 默认 min-height:auto，
    内容超高时它会拒绝收缩，子元素的 flex 撑满就失效了。
  */
  min-height: 0;
  width: 100%;
  max-width: 1080px;
  margin: 0 auto;
  padding: clamp(8px, 0.5vw + 6px, 18px) clamp(10px, 0.7vw + 7px, 20px)
    clamp(10px, 0.7vw + 7px, 24px);
  /* 整页不滚，滚动发生在这里 */
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

@media (max-width: 767px) {
  /*
    手机端收紧内边距。
    地图页里这 16px 加上下留白就是地图与屏幕边缘的距离，
    而地图是这个页面的主体，不该被外壳的排版留白吃掉。
  */
  .c-main {
    padding: 8px 10px 10px;
  }
}

.c-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 10px 16px 18px;
  font-size: var(--fs-xs);
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
  font-size: var(--fs-xs);
  /* 触摸目标不小于 44px，符合移动端可点击区域建议 */
  min-height: 48px;
  justify-content: center;
}
.c-tab.active {
  color: var(--c-primary);
  font-weight: 600;
}
.c-tab-icon {
  font-size: var(--fs-lg);
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
  /* .c-main 的内边距已由 clamp 接管，不再需要断点覆盖 */
}

/*
  窄屏覆盖必须放在样式表**最末尾**
  ==================================
  同特异性的声明只看书写顺序，与是否包在媒体查询里无关。
  这些规则一旦放在被覆盖项前面就会静默失效（已经踩过一次，见下）。
*/
@media (max-width: 767px) {
  /*
    窄屏隐藏顶部品牌条。
    底部 Tab 已经承担了导航，顶部这条只剩品牌标识 —— 却占着 56px。
    在一个以地图为主体的页面上，这 56px 直接换算成地图面积。

    不丢信息：各页面自己都有标题（"路上遇到什么情况？"等），
    PWA 安装后从桌面图标进入，用户也知道这是哪个应用。
  */
  .c-header {
    display: none;
  }

  /*
    顶栏隐藏后主题按钮单独浮起。
    给底色与阴影：它可能压在地图或深色照片上，纯图标会看不清。
  */
  .citizen-shell .c-theme-floating {
    display: inline-flex;
    position: fixed;
    top: max(10px, env(safe-area-inset-top));
    right: 12px;
    z-index: 40;
    background: var(--c-surface);
    box-shadow: var(--c-shadow);
  }
  /*
    窄屏隐藏页脚。
    它处在底部 Tab 的同一区域（实测 footer 795~842、Tab 795~845），
    已被完全盖住，却仍占着 43px 的高度 —— 这部分正是从地图身上抢走的。
    内容"数据来自监测点"在地图的口径说明里已经写过，不必两处都留。

    注意：这条规则曾经写在上面的 .c-footer 定义**之前**，于是被
    `display: flex` 覆盖，页脚照旧占位 —— 本次就是靠测量 clientHeight
    比预期少 43px 才发现的。
  */
  .c-footer {
    display: none;
  }
}
</style>
