/**
 * API 端点封装
 * ============
 * 按业务域分组，供 store / view 直接调用。
 * 所有函数返回已解包的数据（``response.data``）。
 */
import { http } from './client'
import type {
  AlgorithmConfig,
  AlgorithmInfo,
  AmapRoute,
  AnalyzeResult,
  CameraListResponse,
  CameraWatchState,
  ClientConfig,
  DetectionResult,
  DispatchAlgorithmInfo,
  DispatchStrategy,
  EventListResponse,
  EventReviewPayload,
  FlowResult,
  HealthStatus,
  Incident,
  IncidentListResponse,
  IncidentStats,
  LookupAuditEntry,
  ModelStatus,
  Officer,
  OfficerListResponse,
  OfficerStats,
  OwnerLookupResult,
  RoadNetwork,
  RuntimeConfigSaveResult,
  RuntimeConfigState,
  ScenarioInfo,
  SimulationResult,
  SourceListResponse,
  StallAlert,
  StatsOverview,
  StreamUrlResponse,
  SurveillanceAlgorithm,
  SurveillanceStatus,
  TrafficEvent,
} from '@/types/api'

// ==========================================================================
// 系统
// ==========================================================================

export const systemApi = {
  health: () => http.get<HealthStatus>('/health').then((r) => r.data),

  stats: () => http.get<StatsOverview>('/stats').then((r) => r.data),

  config: () => http.get<ClientConfig>('/config').then((r) => r.data),

  /** 列出可在网页上修改的配置项（默认关闭，见 .env 的 RUNTIME_CONFIG_ENABLED） */
  runtimeConfig: () =>
    http.get<RuntimeConfigState>('/runtime-config').then((r) => r.data),

  /**
   * 保存配置到 backend/.env（无需重启，服务端会热更新内存配置）。
   *
   * 需要口令，且服务端按白名单校验 —— 名单外的键会被拒绝，
   * 拒绝原因在返回的 ``rejected`` 里。
   */
  saveRuntimeConfig: (payload: { token: string; values: Record<string, string> }) =>
    http.post<RuntimeConfigSaveResult>('/runtime-config', payload).then((r) => r.data),
}

// ==========================================================================
// 视频源
// ==========================================================================

export const videoApi = {
  /** 列出全部视频源及可用状态 */
  sources: () => http.get<SourceListResponse>('/video/sources').then((r) => r.data),

  /** 检索摄像头列表 */
  cameras: (params: { source?: string; count?: number; exclude?: string } = {}) =>
    http.get<CameraListResponse>('/video/cameras', { params }).then((r) => r.data),

  /** 获取直播流地址（带时效 token，勿缓存） */
  streamUrl: (params: { source?: string; cameraNum: string }) =>
    http.get<StreamUrlResponse>('/video/stream-url', { params }).then((r) => r.data),
}

// ==========================================================================
// 检测
// ==========================================================================

export const detectionApi = {
  /** 模型状态与降级原因 */
  model: () => http.get<ModelStatus>('/detection/model').then((r) => r.data),

  /** 拥堵阈值 */
  thresholds: () =>
    http
      .get<{ light: number; moderate: number; heavy: number; confidence: number }>(
        '/detection/thresholds',
      )
      .then((r) => r.data),

  /**
   * 从视频源抓帧检测。
   *
   * `latitude` / `longitude` 建议传入：调用方从摄像头列表里已经拿到了
   * 坐标，顺手上传能让生成的告警带上位置，地图上才能标出该事件。
   * 服务端并不具备「按编号查摄像头」的能力（上游检索接口是按视野
   * 随机抽样的），所以坐标只能由调用方提供。
   */
  snapshot: (params: {
    source?: string
    cameraNum: string
    roadId?: string
    latitude?: number
    longitude?: number
  }) =>
    http
      .post<DetectionResult>('/detection/snapshot', null, {
        params,
        // 抓帧需下载 HLS 分片并本地解码，再叠加一次推理，
        // 低码率点位实测可达十几秒 —— 全局 20s 超时会误触发取消
        timeout: 120000,
      })
      .then((r) => r.data),

  /** 上传图片检测 */
  upload: (file: File, cameraId = 'CAM_UPLOAD', roadId = 'R001') => {
    const form = new FormData()
    form.append('file', file)
    form.append('camera_id', cameraId)
    form.append('road_id', roadId)
    return http
      .post<DetectionResult>('/detection/image', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 60000,
      })
      .then((r) => r.data)
  },

  /** 多帧流量分析（时序聚合）。逐帧检测，耗时约为单帧的数倍 */
  flow: (params: {
    source?: string
    cameraNum: string
    frameCount?: number
    roadId?: string
  }) =>
    http
      .post<FlowResult>('/detection/flow', null, { params, timeout: 300000 })
      .then((r) => r.data),

  /** 检测历史 */
  history: (params: { limit?: number; roadId?: string } = {}) =>
    http
      .get<{ success: boolean; total: number; items: Record<string, unknown>[] }>(
        '/detection/history',
        { params },
      )
      .then((r) => r.data),
}

// ==========================================================================
// 事件
// ==========================================================================

export const eventApi = {
  list: (params: { status?: string; level?: string; limit?: number; skip?: number } = {}) =>
    http.get<EventListResponse>('/events', { params }).then((r) => r.data),

  detail: (eventId: string) => http.get<TrafficEvent>(`/events/${eventId}`).then((r) => r.data),

  /** 一键确认 / 误报 */
  review: (eventId: string, payload: EventReviewPayload) =>
    http.post<TrafficEvent>(`/events/${eventId}/review`, payload).then((r) => r.data),

  stats: () =>
    http
      .get<{
        success: boolean
        byStatus: Record<string, number>
        byLevel: Record<string, number>
        total: number
        pending: number
      }>('/events/stats')
      .then((r) => r.data),

  rank: (limit = 10) =>
    http
      .get<{ success: boolean; items: { member: string; score: number }[] }>('/events/rank', {
        params: { limit },
      })
      .then((r) => r.data),
}

// ==========================================================================
// 上海路网
// ==========================================================================

export const roadApi = {
  network: () => http.get<RoadNetwork>('/roads/network').then((r) => r.data),

  summary: () =>
    http
      .get<{ success: boolean; intersectionCount: number; roadCount: number; totalLengthKm: number; avgLengthKm: number }>(
        '/roads/summary',
      )
      .then((r) => r.data),

  geocode: (address: string, city = '上海') =>
    http
      .get<{ success: boolean; lng: number; lat: number; formattedAddress: string }>(
        '/roads/amap/geocode',
        { params: { address, city } },
      )
      .then((r) => r.data),

  route: (origin: string, destination: string) =>
    http
      .get<AmapRoute>('/roads/amap/route', { params: { origin, destination } })
      .then((r) => r.data),
}

// ==========================================================================
// 事故识别算法
// ==========================================================================

export const accidentApi = {
  /** 三级级联框架、判据与误报抑制规则说明 */
  info: () => http.get<AlgorithmInfo>('/accident/algorithm').then((r) => r.data),

  /** 仿真场景清单 */
  scenarios: () =>
    http
      .get<{ success: boolean; items: ScenarioInfo[] }>('/accident/scenarios')
      .then((r) => r.data),

  /** 运行合成轨迹仿真（无需真实事故视频） */
  simulate: (scenarioKey: string) =>
    http
      .post<SimulationResult>('/accident/simulate', null, { params: { scenario: scenarioKey } })
      .then((r) => r.data),

  /** 抓取实时片段并分析（同步阻塞，耗时数秒到数十秒） */
  analyze: (params: {
    cameraNum: string
    source?: string
    frameCount?: number
    roadId?: string
    latitude?: number
    longitude?: number
  }) =>
    http
      .post<AnalyzeResult>('/accident/analyze', null, { params, timeout: 180000 })
      .then((r) => r.data),

  /** 当前算法参数 */
  config: () =>
    http
      .get<{ success: boolean; config: AlgorithmConfig }>('/accident/config')
      .then((r) => r.data),
}

// ==========================================================================
// 警员
// ==========================================================================

export const officerApi = {
  /** 警员列表。`status` 可选 on_duty / busy / off_duty / leave */
  list: (params: { status?: string; unit?: string; keyword?: string; limit?: number } = {}) =>
    http.get<OfficerListResponse>('/officers', { params }).then((r) => r.data),

  /** 勤务概览（各状态人数与单位分布） */
  stats: () => http.get<OfficerStats>('/officers/stats').then((r) => r.data),

  /** 详情。注意入参是**警号**而非数据库 id */
  detail: (officerId: string) =>
    http.get<Officer>(`/officers/${officerId}`).then((r) => r.data),

  create: (payload: Record<string, unknown>) =>
    http.post<Officer>('/officers', payload).then((r) => r.data),

  update: (officerId: string, payload: Record<string, unknown>) =>
    http.put<Officer>(`/officers/${officerId}`, payload).then((r) => r.data),

  remove: (officerId: string) =>
    http.delete<{ success: boolean; message: string }>(`/officers/${officerId}`).then((r) => r.data),

  /** 导入示例警员（幂等）。reset=true 会先清空 */
  seed: (reset = false) =>
    http
      .post<{ success: boolean; created: number; message: string }>('/officers/seed', null, {
        params: { reset },
      })
      .then((r) => r.data),
}

// ==========================================================================
// 警情与派警
// ==========================================================================

export const incidentApi = {
  list: (params: { status?: string; level?: string; limit?: number } = {}) =>
    http.get<IncidentListResponse>('/incidents', { params }).then((r) => r.data),

  stats: () => http.get<IncidentStats>('/incidents/stats').then((r) => r.data),

  /** 派警判据、策略权重、上报适配器与状态机说明 */
  algorithm: () =>
    http.get<DispatchAlgorithmInfo>('/incidents/algorithm').then((r) => r.data),

  detail: (incidentId: string) =>
    http.get<Incident>(`/incidents/${incidentId}`).then((r) => r.data),

  create: (payload: Record<string, unknown>) =>
    http.post<Incident>('/incidents', payload).then((r) => r.data),

  /** 向外部平台上报警情 */
  report: (incidentId: string, reporter = '') =>
    http
      .post<Incident>(`/incidents/${incidentId}/report`, null, { params: { reporter } })
      .then((r) => r.data),

  /** 重试上报 */
  retry: (incidentId: string) =>
    http.post<Incident>(`/incidents/${incidentId}/retry`, null).then((r) => r.data),

  /** 智能派警。无人可派时后端返回 409 */
  dispatch: (
    incidentId: string,
    payload: { strategy?: DispatchStrategy; officerId?: string; topN?: number; note?: string },
  ) =>
    http
      .post<Incident>(`/incidents/${incidentId}/dispatch`, payload, { timeout: 60000 })
      .then((r) => r.data),

  /** 处置流转：accept 接警 / arrive 到场 / close 办结 */
  updateStatus: (incidentId: string, action: string, note = '') =>
    http
      .post<Incident>(`/incidents/${incidentId}/status`, null, { params: { action, note } })
      .then((r) => r.data),
}

// ==========================================================================
// 持续检测（违停监控）
// ==========================================================================

export const surveillanceApi = {
  /** 巡检运行状态与各路统计 */
  status: () => http.get<SurveillanceStatus>('/surveillance/status').then((r) => r.data),

  /**
   * 开始巡检。
   *
   * `hints` 用于声明点位名称（如「京港澳高速许昌服务区入口」）——
   * 名称含「服务区」「收费站」等关键词时该点位按可停车场所处理，不报违停。
   */
  start: (cameraIds: string[] = [], hints: Record<string, string> = {}) =>
    http
      .post<{ success: boolean; started: boolean; cameras: string[]; message: string }>(
        '/surveillance/start',
        { cameraIds, hints },
        { timeout: 60000 },
      )
      .then((r) => r.data),

  stop: () =>
    http
      .post<{ success: boolean; stopped: boolean; message: string }>('/surveillance/stop', {}, {
        timeout: 60000,
      })
      .then((r) => r.data),

  /** 暂停 / 恢复单路巡检 */
  toggleCamera: (cameraId: string, enabled: boolean) =>
    http
      .post<{ success: boolean; camera: CameraWatchState }>(
        `/surveillance/cameras/${cameraId}/toggle`,
        null,
        { params: { enabled } },
      )
      .then((r) => r.data),

  /** 违停告警列表 */
  alerts: (params: { limit?: number; cameraId?: string; status?: string } = {}) =>
    http
      .get<{ success: boolean; total: number; items: StallAlert[] }>('/surveillance/alerts', {
        params,
      })
      .then((r) => r.data),

  alertStats: () =>
    http
      .get<{ success: boolean; total: number; byStatus: Record<string, number>; withPlate: number }>(
        '/surveillance/alerts/stats',
      )
      .then((r) => r.data),

  alertDetail: (alertId: string) =>
    http.get<StallAlert & { success: boolean }>(`/surveillance/alerts/${alertId}`).then((r) => r.data),

  /** 告警处置（核验 / 误报 / 已处置） */
  updateAlert: (alertId: string, status: string, note = '') =>
    http
      .post<StallAlert>(`/surveillance/alerts/${alertId}/status`, { status, note })
      .then((r) => r.data),

  /** 算法说明与三个适配器通道的可用状态 */
  algorithm: () =>
    http.get<SurveillanceAlgorithm>('/surveillance/algorithm').then((r) => r.data),

  /**
   * 车主信息查询。
   *
   * `operator` 与 `reason` 是**必填**的 —— 后端会直接拒绝缺失的请求。
   * 返回的个人信息字段均已脱敏。
   */
  lookupOwner: (payload: {
    plate: string
    operator: string
    reason: string
    purpose?: string
    provider?: string
    alertId?: string
    cameraId?: string
  }) => http.post<OwnerLookupResult>('/surveillance/owner-lookup', payload).then((r) => r.data),

  /** 车主查询审计留痕 */
  audits: (params: { limit?: number; plate?: string } = {}) =>
    http
      .get<{
        success: boolean
        total: number
        items: LookupAuditEntry[]
        stats: { total: number; byOutcome: Record<string, number>; bySource: Record<string, number> }
      }>('/surveillance/audits', { params })
      .then((r) => r.data),
}
