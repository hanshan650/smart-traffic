/**
 * 导航与页面权限
 * ==============
 * 侧栏展示与路由拦截共用同一份定义。
 *
 * 为什么必须抽出来：之前这份表只存在于 `MainLayout.vue`，导致路由层
 * 不知道"谁能看哪一页"。后果是审计员登录后落在 `/dashboard`，页面接口
 * 全被后端 403，界面上是一堆空卡片 —— 用户会以为是系统坏了，而不是
 * "你的角色不该来这里"。
 *
 * 这层仍然只是体验保障：真正的权限在后端中间件。前端这层的价值在于
 * 不把用户送到注定失败的页面，而不是阻止攻击。
 */

/** 与后端 `auth_service.Role` 保持一致 */
export type RoleName = 'admin' | 'dispatcher' | 'viewer' | 'auditor'

export interface NavItem {
  name: string
  path: string
  label: string
  icon: string
  /** 可访问的角色。不写表示所有已登录角色都能看 */
  roles?: RoleName[]
  /** 不在侧栏展示，但仍参与权限判断（例如全屏指挥大屏） */
  hidden?: boolean
}

/**
 * 角色组合。
 *
 * 这里**刻意显式列举，不留"默认所有角色可见"**：后端对审计员是
 * "只放行 audits"（`permissions.AUDITOR_ALLOWED_PREFIXES`），若前端默认
 * 可见，审计员点进去只会看到满屏 403。两边的规则必须能一眼对上。
 *
 * 值班业务：能读能写。
 */
const OPERATIONAL: RoleName[] = ['admin', 'dispatcher']
/** 加上只读：能看但不能改。 */
const READABLE: RoleName[] = ['admin', 'dispatcher', 'viewer']

export const NAV_ITEMS: NavItem[] = [
  { name: 'dashboard', path: '/dashboard', label: '运行概览', icon: '◈', roles: READABLE },
  { name: 'wall', path: '/wall', label: '实时监控', icon: '▦', roles: READABLE },
  { name: 'map', path: '/map', label: '地图态势', icon: '◉', roles: READABLE },
  { name: 'accident', path: '/accident', label: '事故识别', icon: '⚡', roles: READABLE },
  { name: 'dispatch', path: '/dispatch', label: '警情调度', icon: '⚑', roles: READABLE },
  { name: 'surveillance', path: '/surveillance', label: '违停监控', icon: '⊙', roles: READABLE },
  { name: 'reports', path: '/reports', label: '民众上报', icon: '✉', roles: READABLE },
  // 警员档案含岗位与勤务安排，不给只读与审计角色
  { name: 'officers', path: '/officers', label: '警员管理', icon: '☰', roles: OPERATIONAL },
  { name: 'events', path: '/events', label: '告警中心', icon: '⚠', roles: READABLE },
  { name: 'roads', path: '/roads', label: '上海路网', icon: '⇄', roles: READABLE },
  // 审计员唯一能进的页（后端也只为它放行了 audits 接口）
  { name: 'audit', path: '/audit', label: '审计记录', icon: '⧉', roles: ['admin', 'auditor'] },
  { name: 'system', path: '/system', label: '系统状态', icon: '◎', roles: READABLE },
  // 指挥大屏是独立布局，不在侧栏，但同样受角色限制
  { name: 'screen', path: '/screen', label: '指挥大屏', icon: '⛶', hidden: true, roles: READABLE },
]

/** 某个角色能否看这一项 */
export function canSee(item: NavItem, role: string | null | undefined): boolean {
  return !item.roles || (!!role && item.roles.includes(role as RoleName))
}

/** 侧栏该显示的项（已按角色过滤，并剔除 hidden） */
export function sidebarItems(role: string | null | undefined): NavItem[] {
  return NAV_ITEMS.filter((item) => !item.hidden && canSee(item, role))
}

/** 路径 → 导航项。用于路由守卫判断目标页是否受导航表管辖 */
export function navItemForPath(path: string): NavItem | undefined {
  return NAV_ITEMS.find((item) => item.path === path)
}

/**
 * 该角色登录后应该落在哪一页。
 *
 * 取"侧栏第一项"而不是写死 `/dashboard`：审计员的侧栏只有「审计记录」，
 * 写死会让它一进来就撞 403。
 */
export function landingPathFor(role: string | null | undefined): string {
  return sidebarItems(role)[0]?.path ?? '/login'
}
