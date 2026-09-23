/**
 * 民众端 API 封装
 * ================
 *
 * 与 `api/index.ts`（警务端）分开，指向 `/public` 与 `/reports` 两组路由。
 *
 * 为什么不在同一个文件里：警务端的接口几乎都要求内部视图，而民众端
 * 必须走 `/public` 前缀（后端在路由层做数据隔离）。放在一起容易在
 * 写新页面时**顺手 import 了警务端的 API**，把内部数据拉到公开页面。
 * 物理分开后，这种错误需要显式跨文件导入才能犯，更不容易。
 */
import { http } from '@/api/client'
import type {
  CitizenReport,
  CitizenReportCreateResponse,
  CitizenReportOptions,
  EmergencyInfo,
  InternalCitizenReport,
  PublicAdvice,
  PublicOverview,
  PublicTrafficResponse,
  CongestionRankItem,
} from '@/types/citizen'

// ==========================================================================
// 路况（公开）
// ==========================================================================

export const publicApi = {
  /** 实时路况事件 */
  traffic: (params: { limit?: number; level?: string } = {}) =>
    http.get<PublicTrafficResponse>('/public/traffic', { params }).then((r) => r.data),

  /** 拥堵路段排行 */
  rank: (limit = 10) =>
    http
      .get<{ success: boolean; total: number; items: CongestionRankItem[] }>(
        '/public/traffic/rank',
        { params: { limit } },
      )
      .then((r) => r.data),

  /** 路况概览 */
  overview: () => http.get<PublicOverview>('/public/overview').then((r) => r.data),

  /** 出行建议（非导航） */
  advice: (limit = 5) =>
    http.get<PublicAdvice>('/public/advice', { params: { limit } }).then((r) => r.data),

  /** 上报类型等选项 */
  options: () => http.get<CitizenReportOptions>('/public/options').then((r) => r.data),

  /** 应急联系方式与安全须知 */
  emergency: () => http.get<EmergencyInfo>('/public/emergency').then((r) => r.data),
}

// ==========================================================================
// 上报（公开提交 + 凭编号查询）
// ==========================================================================

export const citizenReportApi = {
  /**
   * 提交上报。
   *
   * 用 `FormData` 而非 JSON：需要同时携带图片文件与表单字段，
   * 这是 multipart 的标准做法。注意**不要**手动设置
   * `Content-Type: multipart/form-data` —— 浏览器需要自己填 boundary，
   * 手动设置会导致后端解析失败。
   */
  submit: (payload: {
    reportType: string
    description: string
    locationText: string
    roadName?: string
    contact?: string
    latitude?: number | null
    longitude?: number | null
    image?: File | null
  }) => {
    const form = new FormData()
    form.append('report_type', payload.reportType)
    form.append('description', payload.description)
    form.append('location_text', payload.locationText)
    form.append('road_name', payload.roadName ?? '')
    form.append('contact', payload.contact ?? '')
    if (payload.latitude != null) form.append('latitude', String(payload.latitude))
    if (payload.longitude != null) form.append('longitude', String(payload.longitude))
    if (payload.image) form.append('image', payload.image)

    return http
      .post<CitizenReportCreateResponse>('/public/reports', form, {
        // **必须显式覆盖全局的 application/json**。
        //
        // 共享的 axios 实例设了 `Content-Type: application/json`，
        // 不覆盖的话 FormData 会被按 JSON 序列化成 `{}`，后端收不到任何
        // 表单字段，直接返回 422。
        //
        // 这里给 `undefined` 而不是 `'multipart/form-data'`：后者缺少
        // boundary，需要 axios 自己补；给 undefined 则完全交给浏览器与
        // axios 的默认逻辑处理，最不容易出错。
        headers: { 'Content-Type': undefined },
        // 上传图片 + 写库，给足时间
        timeout: 60000,
      })
      .then((r) => r.data)
  },

  /** 凭编号查询进度 */
  track: (reportNo: string) =>
    http
      .get<{ success: boolean; report: CitizenReport }>(`/public/reports/${reportNo}`)
      .then((r) => r.data),
}

// ==========================================================================
// 上报复核（警务端）
// ==========================================================================

export const reviewApi = {
  list: (params: { status?: string; limit?: number; skip?: number } = {}) =>
    http
      .get<{ success: boolean; total: number; items: InternalCitizenReport[] }>('/reports', {
        params,
      })
      .then((r) => r.data),

  stats: () =>
    http
      .get<{
        success: boolean
        total: number
        pending: number
        byStatus: Record<string, number>
        byType: Record<string, number>
      }>('/reports/stats')
      .then((r) => r.data),

  detail: (reportNo: string) =>
    http
      .get<InternalCitizenReport & { success: boolean }>(`/reports/${reportNo}`)
      .then((r) => r.data),

  /** 复核：approve 会转为正式事件 */
  review: (
    reportNo: string,
    payload: { action: string; reviewer: string; note?: string; level?: string },
  ) =>
    http
      .post<InternalCitizenReport>(`/reports/${reportNo}/review`, payload, { timeout: 60000 })
      .then((r) => r.data),
}
