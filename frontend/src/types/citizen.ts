/**
 * 民众端类型定义
 * ==============
 *
 * 与 `api.ts`（警务端）分开是有意的，不是重复劳动。
 *
 * 两边**本来就不是同一套数据**：
 *   · 警务端拿到 `cameraId`、`confidence`、`reviewedBy`、`snapshotUrl`
 *   · 民众端只拿到脱敏后的公开字段，坐标还降过精度
 *
 * 如果强行共用一个类型，就得把警务端字段标成可选，结果两边都失去类型保护
 * —— 前端会以为"这个字段可能不存在"而写一堆无用的空值判断，真正缺字段时
 * 反而查不出来。因此这里按**各自的契约**分别定义。
 *
 * 后端对应实现：`backend/app/api/v1/public.py` 的 `_public_event`。
 */

// ==========================================================================
// 路况
// ==========================================================================

export interface PublicEvent {
  eventType: string
  eventTypeLabel: string
  level: string
  levelLabel: string
  title: string
  description: string
  roadName: string
  congestionLevel: string
  congestionLabel: string
  createdAt?: string | null
}

export interface PublicTrafficResponse {
  success: boolean
  total: number
  items: PublicEvent[]
  generatedAt: string
}

export interface CongestionRankItem {
  roadName: string
  vehicleCount: number
  congestionLevel: string
  congestionLabel: string
  updatedAt?: string | null
}

export interface PublicOverview {
  success: boolean
  overall: string
  overallLabel: string
  eventCounts: Record<string, number>
  eventTotal: number
  congestionCounts: Record<string, number>
  generatedAt: string
}

export interface PublicAdvice {
  success: boolean
  disclaimer: string
  avoid: {
    roadName: string
    congestionLevel: string
    congestionLabel: string
    vehicleCount: number
    suggestion: string
  }[]
  generatedAt: string
}

// ==========================================================================
// 上报
// ==========================================================================

export interface CitizenReportType {
  key: string
  label: string
  defaultLevel: string
}

export interface CitizenReportOptions {
  success: boolean
  reportTypes: CitizenReportType[]
  statuses: Record<string, string>
  transitions: Record<string, string[]>
  limits: {
    maxDescription: number
    maxContact: number
    rateLimit: number
    windowHours: number
  }
}

/**
 * 民众可见的上报记录。
 *
 * 注意**没有 contact 字段** —— 后端不会返回，这里也就没有。
 * 前端拿不到的东西不该出现在类型里，否则会有人写出
 * `report.contact` 然后困惑为什么是 undefined。
 */
export interface CitizenReport {
  reportNo: string
  reportType: string
  reportTypeLabel: string
  description: string
  locationText: string
  roadName: string
  latitude?: number | null
  longitude?: number | null
  imageUrl: string
  status: string
  statusLabel: string
  reviewNote: string
  createdAt?: string | null
  reviewedAt?: string | null
}

/** 警务端的上报记录：多出联系方式等内部字段 */
export interface InternalCitizenReport extends CitizenReport {
  id: string
  contact: string
  contactMasked: string
  level: string
  reviewedBy: string
  eventId: string
}

export interface CitizenReportCreateResponse {
  success: boolean
  reportNo: string
  message: string
  report: CitizenReport
}

// ==========================================================================
// 应急信息
// ==========================================================================

export interface EmergencyContact {
  name: string
  number: string
  note: string
}

export interface SafetyTip {
  title: string
  steps: string[]
}

export interface EmergencyInfo {
  success: boolean
  contacts: EmergencyContact[]
  tips: SafetyTip[]
  note: string
}

// ==========================================================================
// 展示辅助
// ==========================================================================

/** 拥堵等级 → 民众端配色。刻意用交通信号式的语义：绿通畅、红拥堵。 */
export const CITIZEN_CONGESTION_COLOR: Record<string, string> = {
  normal: '#16a34a',
  light: '#65a30d',
  moderate: '#ea580c',
  heavy: '#dc2626',
}

/** 紧急级别 → 民众端配色 */
export const CITIZEN_LEVEL_COLOR: Record<string, string> = {
  info: '#0284c7',
  warning: '#ea580c',
  critical: '#dc2626',
}

/** 上报状态 → 进度条上所处的阶段（1-based）。用于给民众一个直观的进度感。 */
export const REPORT_STATUS_STAGE: Record<string, number> = {
  pending: 2,
  approved: 3,
  rejected: 3,
  duplicate: 3,
}

export const REPORT_STATUS_COLOR: Record<string, string> = {
  pending: '#ea580c',
  approved: '#16a34a',
  rejected: '#64748b',
  duplicate: '#64748b',
}
