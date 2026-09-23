/**
 * 路由表
 * ======
 * · ``/screen`` 为独立的全屏指挥大屏，不使用 MainLayout
 * · 其余页面统一挂在 MainLayout 下
 */
import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
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
