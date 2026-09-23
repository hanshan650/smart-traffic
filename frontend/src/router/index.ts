/**
 * 路由表
 * ======
 * · ``/login`` 登录页（独立布局，无导航壳）
 * · ``/screen`` 为独立的全屏指挥大屏，不使用 MainLayout
 * · ``/citizen`` 民众端，**不需要登录**
 * · 其余页面统一挂在 MainLayout 下，**均需登录**
 */
import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

import { canSee, landingPathFor, navItemForPath } from '@/config/nav'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录' },
  },
  {
    path: '/screen',
    name: 'screen',
    component: () => import('@/views/ScreenView.vue'),
    meta: { title: '指挥大屏', fullscreen: true },
  },
  // ------------------------------------------------------------------------
  // 民众端
  // ------------------------------------------------------------------------
  // 与警务端**完全分离的一棵路由树**，使用独立的 CitizenLayout（亮色、
  // 移动优先、底部 Tab）。两者共用同一个构建产物，但视觉与导航各自独立。
  {
    path: '/citizen',
    component: () => import('@/layouts/CitizenLayout.vue'),
    children: [
      {
        path: '',
        name: 'citizen-home',
        component: () => import('@/views/citizen/CitizenHomeView.vue'),
        meta: { title: '路况' },
      },
      {
        path: 'report',
        name: 'citizen-report',
        component: () => import('@/views/citizen/CitizenReportView.vue'),
        meta: { title: '上报' },
      },
      {
        path: 'track',
        name: 'citizen-track',
        component: () => import('@/views/citizen/CitizenTrackView.vue'),
        meta: { title: '进度' },
      },
      {
        path: 'advice',
        name: 'citizen-advice',
        component: () => import('@/views/citizen/CitizenAdviceView.vue'),
        meta: { title: '建议' },
      },
      {
        path: 'help',
        name: 'citizen-help',
        component: () => import('@/views/citizen/CitizenHelpView.vue'),
        meta: { title: '安全' },
      },
    ],
  },
  // ------------------------------------------------------------------------
  // 警务端
  // ------------------------------------------------------------------------
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '运行概览', icon: '◈' },
      },
      {
        path: 'wall',
        name: 'wall',
        component: () => import('@/views/VideoWallView.vue'),
        meta: { title: '实时监控', icon: '▦' },
      },
      {
        path: 'map',
        name: 'map',
        component: () => import('@/views/MapView.vue'),
        meta: { title: '地图态势', icon: '◉' },
      },
      {
        path: 'events',
        name: 'events',
        component: () => import('@/views/EventsView.vue'),
        meta: { title: '告警中心', icon: '⚠' },
      },
      {
        path: 'roads',
        name: 'roads',
        component: () => import('@/views/RoadsView.vue'),
        meta: { title: '上海路网', icon: '⇄' },
      },
      {
        path: 'accident',
        name: 'accident',
        component: () => import('@/views/AccidentView.vue'),
        meta: { title: '事故识别算法', icon: '⚡' },
      },
      {
        path: 'dispatch',
        name: 'dispatch',
        component: () => import('@/views/DispatchView.vue'),
        meta: { title: '警情调度', icon: '⚑' },
      },
      {
        path: 'officers',
        name: 'officers',
        component: () => import('@/views/OfficersView.vue'),
        meta: { title: '警员管理', icon: '☰' },
      },
      {
        path: 'surveillance',
        name: 'surveillance',
        component: () => import('@/views/SurveillanceView.vue'),
        meta: { title: '违停监控', icon: '⊙' },
      },
      {
        path: 'reports',
        name: 'reports',
        component: () => import('@/views/ReportsView.vue'),
        meta: { title: '民众上报', icon: '✉' },
      },
      {
        path: 'audit',
        name: 'audit',
        component: () => import('@/views/AuditView.vue'),
        meta: { title: '审计记录', icon: '⧉' },
      },
      {
        path: 'system',
        name: 'system',
        component: () => import('@/views/SystemView.vue'),
        meta: { title: '系统状态', icon: '◎' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// ---------------------------------------------------------------------------
// 登录守卫
// ---------------------------------------------------------------------------
// 按"**默认需要登录**"写：只有列在这里的路径放行。这样新增警务端页面
// 时不会因为忘了加 meta 而裸奔，与后端的权限中间件（`permissions.is_public`）
// 保持同一套思路。
//
// 注意这只是**体验层**：真正的拦截在后端中间件。前端守卫能被绕过
// （改 JS 、直接调接口），所以不能拿它当安全边界。
const PUBLIC_PATHS = ['/login', '/citizen']

function isPublic(path: string): boolean {
  return PUBLIC_PATHS.some((item) => path === item || path.startsWith(item + '/'))
}

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  // 刷新页面后前端不知道"我是谁"（会话在 HttpOnly Cookie 里，JS 读不到），
  // 所以首个路由要等一次 /auth/me。restore 幂等，之后不会重复请求
  await auth.restore()

  if (!isPublic(to.path) && !auth.isLoggedIn) {
    // 把原本想去的地址带上，登录后能直接回到那里
    return { path: '/login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
  }

  if (to.path === '/login' && auth.isLoggedIn) {
    return { path: landingPathFor(auth.role) }
  }

  // 已登录，但目标页不在这个角色的可见范围（典型：审计员误闯 /dashboard，
  // 页面接口会被后端全部 403，用户看到的是一屏空卡片）。
  // 这里直接把它送回自己的首页，而不是让它进去踩一堆 403。
  //
  // 仅对出现在导航表上的路径生效：/screen、/citizen 这类不在表里的页面
  // 不受影响；它们自己的接口权限仍然后端说了算。
  const item = navItemForPath(to.path)
  if (auth.isLoggedIn && item && !canSee(item, auth.role)) {
    const landing = landingPathFor(auth.role)
    // 比一下再跳，避免出现 A→B→A 的死循环
    if (landing !== to.path) {
      return { path: landing }
    }
  }

  return true
})

/**
 * 按所在端设置页面标题。
 *
 * 民众端与警务端共用一份 `index.html`，标题不能写死在 HTML 里 ——
 * 那样刷新时总会闪一下另一端的名称。这里按路由前缀区分，
 * 顺带把两端的产品名也区分开：普通用户看到的应该是"出行助手"，
 * 而值班员看到的是"监测平台"。
 */
router.afterEach((to) => {
  const pageTitle = (to.meta.title as string) ?? ''
  if (!pageTitle) return

  const isCitizen = to.path.startsWith('/citizen')
  const siteName = isCitizen ? '高速出行助手' : '智能交通监测平台'

  document.title = `${pageTitle} · ${siteName}`
})

export default router
