/**
 * 本机的上报记录
 * ==============
 *
 * 民众端没有登录，所以"我报过什么"只能记在设备上。
 *
 * **为什么不按 IP 反查**（后端其实已经有 `client_key`）：同一出口 IP 后面
 * 可能有很多人 —— 家庭宽带、运营商 NAT、单位网络都是如此。按 IP 查会把
 * 别人报的东西当成自己的展示出来，那是隐私事故，不是功能缺陷。
 *
 * 因此这里只存**编号与元信息**，不存描述、位置文本与联系方式：
 * 描述里常带车牌号、门牌号这类内容，而 localStorage 在共享设备
 * （家里的平板、公司电脑）上是可读的。要看详情就凭编号去查 ——
 * 那一次请求是明确发起的。
 *
 * 代价是它**跟着浏览器走**：清缓存、换设备就没了。所以界面上必须写明
 * 是"本机记录"，并始终保留手输编号的入口，不把本地列表当成唯一途径。
 */
import type { CitizenReport } from '@/types/citizen'

const STORAGE_KEY = 'smart-traffic:my-reports'

/** 最多保留多少条。localStorage 有容量上限，且更早的记录用户也不会去翻 */
export const MY_REPORTS_LIMIT = 50

export interface MyReportEntry {
  reportNo: string
  reportTypeLabel: string
  roadName: string
  createdAt: string
  status: string
  statusLabel: string
}

/** 只信 reportNo 是必须的，其余字段缺了也能显示（可能是旧版本存下的） */
function isEntry(value: unknown): value is MyReportEntry {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return typeof item.reportNo === 'string' && item.reportNo.length > 0
}

function readRaw(): MyReportEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter(isEntry) : []
  } catch {
    // 内容坏了（手工改过、旧版本格式）就当没有 ——
    // 不能让一条脏数据把整页卡住，手输编号的入口仍然可用
    return []
  }
}

function write(list: MyReportEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, MY_REPORTS_LIMIT)))
  } catch {
    // 隐私模式或配额用尽。静默失败：记不记本地只是便利性，不影响上报本身
  }
}

/** 按提交时间倒序（新的在前） */
export function loadMyReports(): MyReportEntry[] {
  return readRaw().sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
}

/** 记一条。同编号覆盖而非追加 —— 一个编号只应出现一次 */
export function rememberReport(report: CitizenReport): void {
  const entry: MyReportEntry = {
    reportNo: report.reportNo,
    reportTypeLabel: report.reportTypeLabel,
    roadName: report.roadName,
    createdAt: report.createdAt ?? new Date().toISOString(),
    status: report.status,
    statusLabel: report.statusLabel,
  }
  write([entry, ...readRaw().filter((item) => item.reportNo !== entry.reportNo)])
}

/** 查到最新状态后回写，下次进页面先看到上次的结果而不是旧的提交态 */
export function updateMyReport(reportNo: string, patch: Partial<MyReportEntry>): void {
  write(readRaw().map((item) => (item.reportNo === reportNo ? { ...item, ...patch } : item)))
}

/** 从本机列表里移除。只删本地记录，不影响服务端的数据 */
export function forgetReport(reportNo: string): void {
  write(readRaw().filter((item) => item.reportNo !== reportNo))
}
