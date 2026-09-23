/**
 * API 类型定义
 * ============
 * 与后端 `backend/app/models/schemas.py` 一一对应，修改时请同步更新两侧。
 *
 * 后端通过 Pydantic 的 alias_generator 自动把 snake_case 序列化为 camelCase，
 * 因此这里统一使用 camelCase。
 */

// ==========================================================================
// 枚举
// ==========================================================================

export type CongestionLevel = 'normal' | 'light' | 'moderate' | 'heavy'

export type EventType =
  | 'collision'
  | 'abnormal_stop'
  | 'congestion'
  | 'wrong_way'
  | 'debris'
  | 'pedestrian'

export type EventLevel = 'info' | 'warning' | 'critical'

export type EventStatus = 'pending' | 'confirmed' | 'rejected' | 'archived'

// ==========================================================================
// 视频源
// ==========================================================================

export interface VideoSourceInfo {
  key: string
  displayName: string
  region: string
  available: boolean
  reason: string
  defaultCamera: string
}

export interface SourceListResponse {
  success: boolean
  current: string
  sources: VideoSourceInfo[]
}

export interface CameraInfo {
  cameraNum: string
  cameraName: string
  road: string
  pileNum: string
  regionName: string
  latitude: number
  longitude: number
  online: number
}

export interface CameraListResponse {
  success: boolean
  source: string
  sourceName: string
  cameras: CameraInfo[]
  totalAvailable: number
  totalAll: number
}

export interface StreamUrlResponse {
  success: boolean
  source: string
  sourceName: string
  cameraNum: string
  /**
   * 上游原始 m3u8 地址（带时效 token，仅后端抓帧使用）。
   *
   * 注意：后端字段名刻意避开 `m3u8_url` —— Pydantic 的 camelCase 转换
   * 会把它变成 `m3U8Url`（大写 U），与惯用的 `m3u8Url` 不一致。
   */
  streamUrl: string
  /** 同源代理地址，前端播放应用此地址 */
  hlsProxyUrl: string
}

// ==========================================================================
// 检测
// ==========================================================================

export interface BBox {
  x: number
  y: number
  w: number
  h: number
}

export interface DetectedObject {
  classId: number
  className: string
  confidence: number
  bbox: BBox
}

export interface DetectionResult {
  cameraId: string
  roadId: string
  totalVehicles: number
  vehicleCounts: Record<string, number>
  congestionLevel: CongestionLevel
  objects: DetectedObject[]
  snapshotUrl: string
  sourceUrl: string
  detectionId: string | null
  timestamp: string
  durationMs: number
}

// ==========================================================================
// 事件
// ==========================================================================

export interface TrafficEvent {
  id: string
  eventType: EventType
  level: EventLevel
  status: EventStatus
  confidence: number
  title: string
  description: string
  cameraId: string
  cameraName: string
  roadId: string
  roadName: string
  sourceKey: string
  snapshotUrl: string
  vehicleCount: number
  congestionLevel: CongestionLevel
  /** 事件坐标。早期事件可能没有该字段（坐标是后加进数据模型的）。 */
  latitude?: number | null
  longitude?: number | null
  createdAt: string
  reviewedAt: string | null
  reviewedBy: string | null
}

export interface EventListResponse {
  success: boolean
  total: number
  items: TrafficEvent[]
}

export interface EventReviewPayload {
  action: 'confirm' | 'reject'
  reviewer?: string
  remark?: string
}

// ==========================================================================
// 上海路网
// ==========================================================================

export interface Intersection {
  id: string
  name: string
  lng: number
  lat: number
}

export interface RoadSegment {
  id: string
  name: string
  fromId: string
  toId: string
  lengthM: number
  speedLimit: number
  lanes: number
  congestionLevel: CongestionLevel
  polyline: number[][]
}

export interface RoadNetwork {
  centerLng: number
  centerLat: number
  intersections: Intersection[]
  roads: RoadSegment[]
  summary: Record<string, number>
}

// ==========================================================================
// 高德地图
// ==========================================================================

export interface AmapRouteStep {
  instruction: string
  road: string
  distance: number
  duration: number
  polyline: number[][]
}

export interface AmapRoute {
  distance: number
  duration: number
  steps: AmapRouteStep[]
}

// ==========================================================================
// 系统
// ==========================================================================

export interface HealthStatus {
  status: string
  version: string
  mongo: boolean
  redis: boolean
  yolo: boolean
  videoSource: string
  videoSourceAvailable: boolean
  detail: Record<string, unknown>
}

export interface StatsOverview {
  cameraTotal: number
  cameraOnline: number
  detectionTotal: number
  eventPending: number
  eventTotal: number
  vehicleTotal: number
  avgConfidence: number
}

export interface ClientConfig {
  success: boolean
  appTitle: string
  apiPrefix: string
  amapJsKey: string
  /** JS API 2.0 安全密钥，缺失时地图会报 INVALID_USER_SCODE */
  amapJsSecurityCode: string
  shanghaiCenter: [number, number]
  thresholds: { light: number; moderate: number; heavy: number }
  videoSource: string
}

/** 可在网页上修改的一项配置 */
export interface RuntimeConfigItem {
  key: string
  /** 已打码的当前值 —— 服务端只回显头尾几位，不返回完整密钥 */
  value: string
  configured: boolean
}

/**
 * 运行时配置的当前状态。
 *
 * 功能默认关闭（写入服务器配置属于敏感操作）；
 * ``enabled`` 为 false 时，``reason`` 会说明原因与开启方式。
 */
export interface RuntimeConfigState {
  success: boolean
  enabled: boolean
  reason: string
  envPath: string
  envExists: boolean
  items: RuntimeConfigItem[]
}

export interface RuntimeConfigSaveResult {
  success: boolean
  changed: string[]
  /** 被拒绝的键及原因（不在白名单内、值含换行等） */
  rejected: string[]
  message: string
  config: RuntimeConfigState
}

// ==========================================================================
// 认证
// ==========================================================================

/** 当前登录身份。`role` 用于控制界面元素的可见性 */
export interface AuthUser {
  officerId: string
  name: string
  unit: string
  role: string
  roleLabel: string
  roleHint: string
  /** 会话到期时间（ISO）。不返回会话值本身 —— 它只走 HttpOnly Cookie */
  expiresAt: string | null
}

export interface AuthUserResponse {
  success: boolean
  user: AuthUser
  message?: string
}

export interface RoleOption {
  value: string
  label: string
  hint: string
}

export interface ModelStatus {
  available: boolean
  reason: string
  model: string
  resolved_path: string
  confidence_threshold: number
}

// ==========================================================================
// 事故识别算法
// ==========================================================================

/** 单条判据的证据（原始值 / 阈值 / 归一化得分 / 权重贡献） */
export interface AccidentEvidence {
  name: string
  label: string
  rawValue: number
  threshold: number
  /** 归一化到 [0, 1] 的判据得分 */
  score: number
  weight: number
  /** score × weight，判据对总分的贡献 */
  contribution: number
  triggered: boolean
}

/** 一次碰撞候选（L2 输出）；经 L3 确认后即为事件 */
export interface AccidentCandidate {
  frameIndex: number
  score: number
  trackIds: number[]
  classNames: string[]
  timestamp: number
  bbox: BBox
  eventType: EventType
  /** L2 规则层标记的误报抑制 */
  suppressed: boolean
  suppressReason: string
  /** 证据链，后端已按贡献降序排列 */
  evidences: AccidentEvidence[]
  /** 仅实时分析返回：事件截图 */
  snapshotUrl?: string
}

/** 轨迹摘要 */
export interface TrackSummary {
  trackId: number
  className: string
  age: number
  duration: number
  /** 末段速度（碰撞后停驶时为 0） */
  speed: number
  /** 历史最大速度，用于体现「由快变慢」的过程 */
  peakSpeed: number
  /** 末尾窗口的速度下降比例（检测逻辑使用） */
  speedDropRatio: number
  /** 历史最大速度下降比例（展示用，停驶后仍可反映骤降特征） */
  peakSpeedDropRatio: number
  headingChangeRate: number
  stationary: boolean
  lastBbox: BBox | null
}

/** 逐帧统计 */
export interface AccidentFrameStat {
  frameIndex: number
  tracks: number
  detections?: number
  candidates?: number
  suppressed?: number
  confirmed: number
}

/** 仿真场景元信息 */
export interface ScenarioInfo {
  key: string
  label: string
  expected: 'detected' | 'not_detected'
}

/** 算法阈值与权重 */
export interface AlgorithmConfig {
  iouThreshold: number
  centerDistanceThreshold: number
  speedDropThreshold: number
  ttcThreshold: number
  headingChangeThreshold: number
  weights: { contact: number; speedDrop: number; ttc: number; heading: number }
  scoreThreshold: number
  minTrackAge: number
  analysisWindow: number
}

/** 算法的运行时自描述（阈值 / 权重 / 抑制规则） */
export type AlgorithmDescription = Record<string, unknown>

/** ``GET /accident/algorithm`` 响应 */
export interface AlgorithmInfo {
  success: boolean
  framework: {
    layer: string
    name: string
    method: string
    output: string
    purpose: string
  }[]
  criterions: { name: string; label: string; formula: string; rationale: string }[]
  suppressions: { name: string; label: string; factor?: number; threshold?: number }[]
  algorithm: AlgorithmDescription
}

/** 合成轨迹仿真结果 */
export interface SimulationResult {
  success: boolean
  scenario: string
  label: string
  frames: number
  fps: number
  durationSeconds: number
  verdict: 'detected' | 'not_detected'
  eventCount: number
  rawCandidateCount: number
  tracks: TrackSummary[]
  events: AccidentCandidate[]
  rawCandidates: AccidentCandidate[]
  suppressionSamples: AccidentCandidate[]
  frameLog: AccidentFrameStat[]
  algorithm: AlgorithmDescription
}

/** 实时片段分析结果 */
export interface AnalyzeResult {
  success: boolean
  cameraNum: string
  source: string
  roadId: string
  frameCount: number
  fps: number
  durationSeconds: number
  trackCount: number
  events: AccidentCandidate[]
  tracks: TrackSummary[]
  frameLog: AccidentFrameStat[]
  algorithm: AlgorithmDescription
}

// ==========================================================================
// 时序聚合（多帧流量分析）
// ==========================================================================

/** 稳定性指标：反映「只看一帧有多不可靠」 */
export interface FlowStability {
  min: number
  median: number
  max: number
  /** 单帧计数的极差；越大说明单帧判定越不稳定 */
  range: number
  /** 聚合结果 − 单帧最大值。正 = 补回了漏检，负 = 滤除了单帧噪声 */
  recallGain: number
}

/** 一条跨帧轨迹 */
export interface FlowTrack {
  trackId: number
  className: string
  observations: number
  /** 该类别的得票明细，如 ``{car: 6, truck: 3}`` */
  voteDetail: Record<string, number>
  /** 全帧类别一致时为 true */
  classStable: boolean
}

/** 多帧流量分析结果 */
export interface FlowResult {
  method: 'temporal'
  cameraNum?: string
  source?: string
  roadId?: string
  frameCount: number
  fps: number
  durationSeconds: number
  /** 聚合后的车辆数（按轨迹去重） */
  totalVehicles: number
  vehicleCounts: Record<string, number>
  congestionLevel: CongestionLevel
  tracks: FlowTrack[]
  /** 逐帧车辆数，用于观察抖动 */
  perFrameCounts: number[]
  stability: FlowStability
  snapshotUrl: string
  config: {
    minHits: number
    maxAge: number
    iouThreshold: number
    classPenalty: number
  }
}

// ==========================================================================
// 警员
// ==========================================================================

export type OfficerStatus = 'on_duty' | 'busy' | 'off_duty' | 'leave'

export type OfficerSkill =
  | 'accident'
  | 'traffic'
  | 'first_aid'
  | 'hazmat'
  | 'investigation'

export interface Officer {
  id: string
  /** 警号（业务主键） */
  officerId: string
  name: string
  rank: string
  unit: string
  phone: string
  status: OfficerStatus
  skills: OfficerSkill[]
  latitude: number
  longitude: number
  region: string
  activeCases: number
  totalHandled: number
  avgResponseMinutes: number
  note: string
  createdAt?: string | null
  updatedAt?: string | null
}

export interface OfficerListResponse {
  success: boolean
  total: number
  items: Officer[]
}

export interface OfficerStats {
  total: number
  onDuty: number
  busy: number
  offDuty: number
  leave: number
  byUnit: Record<string, number>
}

// ==========================================================================
// 警情与派警
// ==========================================================================

export type DispatchStrategy = 'nearest' | 'balanced' | 'skill'
export type ReportStatus = 'pending' | 'sent' | 'acked' | 'failed'
export type IncidentStatus =
  | 'reported'
  | 'dispatched'
  | 'accepted'
  | 'arrived'
  | 'closed'
  | 'failed'

/** 派警评分中的单条判据（与事故识别同样的"证据链"设计） */
export interface DispatchEvidence {
  name: string
  label: string
  rawValue: number
  score: number
  weight: number
  contribution: number
  detail: string
}

/** 一名候选警员的评分明细 */
export interface DispatchCandidate {
  officerId: string
  name: string
  unit: string
  status: OfficerStatus
  distanceKm: number
  etaMinutes: number
  activeCases: number
  matchedSkills: OfficerSkill[]
  score: number
  selected: boolean
  excluded: boolean
  excludeReason: string
  evidences: DispatchEvidence[]
}

export interface TimelineEntry {
  at: string
  action: string
  note: string
}

export interface Incident {
  id: string
  incidentNo: string
  eventId: string
  type: EventType
  level: EventLevel
  roadId: string
  cameraId: string
  cameraName: string
  latitude: number
  longitude: number
  address: string
  description: string
  snapshotUrl: string
  score: number
  reporter: string
  reportStatus: ReportStatus
  externalId: string
  reportMessage: string
  retries: number
  reportedAt?: string | null
  status: IncidentStatus
  assignedOfficerId: string
  assignedOfficerName: string
  assignedAt?: string | null
  acceptedAt?: string | null
  arrivedAt?: string | null
  closedAt?: string | null
  distanceKm: number
  etaMinutes: number
  strategy: string
  candidates: DispatchCandidate[]
  timeline: TimelineEntry[]
  createdAt?: string | null
  updatedAt?: string | null
}

export interface IncidentListResponse {
  success: boolean
  total: number
  items: Incident[]
}

export interface IncidentStats {
  total: number
  byStatus: Record<string, number>
  byType: Record<string, number>
  reportFailed: number
  unassigned: number
  avgDispatchMinutes: number
}

export interface ReporterInfo {
  key: string
  displayName: string
  mode: string
  available: boolean
  reason: string
  endpoint: string
  supportsAck: boolean
}

/** ``GET /incidents/algorithm`` 响应 */
export interface DispatchAlgorithmInfo {
  success: boolean
  dispatch: {
    criterions: { name: string; label: string; formula: string; rationale: string }[]
    strategies: { key: string; weights: Record<string, number> }[]
    config: { maxRadiusKm: number; avgSpeedKmh: number }
  }
  reporters: ReporterInfo[]
  transitions: Record<string, Record<string, string>>
}

// ==========================================================================
// 持续检测（违停监控）
// ==========================================================================

/** 巡检到的单个目标的判定结果 */
export interface WatchVerdict {
  trackId: number
  className: string
  stationarySeconds: number
  score: number
  alert: boolean
  suppressed: boolean
  suppressReason: string
}

/** 单路摄像头的巡检状态 */
export interface CameraWatchState {
  cameraId: string
  cameraName: string
  hintText: string
  roadId: string
  enabled: boolean
  rounds: number
  framesAnalyzed: number
  alertsRaised: number
  lastRoundAt?: string | null
  lastRoundMs: number
  lastError: string
  lastSnapshot: string
  zoneType: string
  zoneLabel: string
  zoneMessage: string
  parkingAllowed: boolean
  trackCount: number
  stationaryCount: number
  crowded: boolean
  crowdRatio: number
  activeTracks: number
  verdicts: WatchVerdict[]
}

export interface SurveillanceStatus {
  success: boolean
  running: boolean
  startedAt?: string | null
  uptimeSeconds: number
  interval: number
  frameCount: number
  lastError: string
  cameras: CameraWatchState[]
  totals: {
    cameras: number
    rounds: number
    framesAnalyzed: number
    alertsRaised: number
  }
}

/** 违停告警中的单条判据 */
export interface AlertEvidence {
  name: string
  label: string
  rawValue: number
  score: number
  weight: number
  contribution: number
  detail: string
}

export type AlertStatus = 'pending' | 'verified' | 'dismissed' | 'handled'

export interface StallAlert {
  id: string
  cameraId: string
  cameraName: string
  roadId: string
  trackId: number
  className: string
  stationarySeconds: number
  score: number
  displacement: number
  zoneType: string
  zoneLabel: string
  snapshotUrl: string
  evidences: AlertEvidence[]
  plate: string
  plateSimulated: boolean
  plateMessage: string
  plateCharHeight: number
  plateReadability: string
  status: AlertStatus
  note: string
  createdAt?: string | null
}

export interface ChannelInfo {
  key: string
  displayName: string
  available: boolean
  reason: string
}

export interface SurveillanceAlgorithm {
  success: boolean
  detection: {
    name: string
    scenario: string
    criterions: { name: string; label: string; formula: string; rationale: string }[]
    suppressions: { name: string; label: string; detail: string }[]
    weights: Record<string, number>
    config: Record<string, number>
  }
  scheduler: {
    intervalSeconds: number
    frameCount: number
    alertCooldownSeconds: number
    note: string
  }
  sceneText: { current: string; channels: ChannelInfo[] }
  plate: {
    current: string
    channels: ChannelInfo[]
    readableCharHeight: number
    note: string
  }
  ownerLookup: { current: string; channels: ChannelInfo[]; compliance: string }
}

/** 车主信息（除 plate 外均为**脱敏后**的值） */
export interface OwnerRecord {
  found: boolean
  plate: string
  ownerName: string
  ownerIdMasked: string
  ownerPhoneMasked: string
  vehicleBrand: string
  vehicleColor: string
  registerDate: string
  vehicleType: string
  source: string
  simulated: boolean
  message: string
}

export interface OwnerLookupResult {
  success: boolean
  owner: OwnerRecord
  audit: {
    at: string
    operator: string
    reason: string
    outcome: string
    persisted: boolean
  }
}

export interface LookupAuditEntry {
  at: string
  plate: string
  operator: string
  reason: string
  purpose: string
  source: string
  outcome: string
  message: string
  incidentId: string
  cameraId: string
  persisted: boolean
}

// ==========================================================================
// WebSocket
// ==========================================================================

export type WsChannel = 'event:new' | 'event:update' | 'snapshot' | 'health' | 'pong' | 'echo'

export interface WsMessage<T = unknown> {
  channel: WsChannel | string
  ts: string
  payload: T
}

/** ``snapshot`` 频道负载 */
export interface SnapshotPayload {
  cameraId: string
  cameraName: string
  roadId: string
  totalVehicles: number
  vehicleCounts: Record<string, number>
  congestionLevel: CongestionLevel
  snapshotUrl: string
}

// ==========================================================================
// 展示辅助
// ==========================================================================

/** 拥堵等级 → 中文标签 */
export const CONGESTION_LABEL: Record<CongestionLevel, string> = {
  normal: '畅通',
  light: '轻度拥堵',
  moderate: '中度拥堵',
  heavy: '严重拥堵',
}

/** 拥堵等级 → 主题色 */
export const CONGESTION_COLOR: Record<CongestionLevel, string> = {
  normal: '#10b981',
  light: '#facc15',
  moderate: '#f97316',
  heavy: '#ef4444',
}

export const EVENT_TYPE_LABEL: Record<EventType, string> = {
  collision: '车辆碰撞',
  abnormal_stop: '异常停车',
  congestion: '交通拥堵',
  wrong_way: '车辆逆行',
  debris: '路面抛洒物',
  pedestrian: '行人闯入',
}

export const EVENT_STATUS_LABEL: Record<EventStatus, string> = {
  pending: '待复核',
  confirmed: '已确认',
  rejected: '误报',
  archived: '已归档',
}

export const EVENT_LEVEL_LABEL: Record<EventLevel, string> = {
  info: '提示',
  warning: '警告',
  critical: '严重',
}

export const EVENT_LEVEL_COLOR: Record<EventLevel, string> = {
  info: '#38bdf8',
  warning: '#facc15',
  critical: '#ef4444',
}

// ---------------- 警员与警情展示辅助 ----------------

export const OFFICER_STATUS_LABEL: Record<OfficerStatus, string> = {
  on_duty: '在岗',
  busy: '出警中',
  off_duty: '下班',
  leave: '请假',
}

export const OFFICER_STATUS_COLOR: Record<OfficerStatus, string> = {
  on_duty: '#10b981',
  busy: '#facc15',
  off_duty: '#64748b',
  leave: '#f97316',
}

export const OFFICER_SKILL_LABEL: Record<OfficerSkill, string> = {
  accident: '事故处理',
  traffic: '交通疏导',
  first_aid: '急救',
  hazmat: '危化品',
  investigation: '事故勘察',
}

export const INCIDENT_STATUS_LABEL: Record<IncidentStatus, string> = {
  reported: '已上报',
  dispatched: '已派警',
  accepted: '已接警',
  arrived: '已到场',
  closed: '已办结',
  failed: '上报失败',
}

export const INCIDENT_STATUS_COLOR: Record<IncidentStatus, string> = {
  reported: '#22d3ee',
  dispatched: '#facc15',
  accepted: '#f97316',
  arrived: '#10b981',
  closed: '#64748b',
  failed: '#ef4444',
}

export const REPORT_STATUS_LABEL: Record<ReportStatus, string> = {
  pending: '待上报',
  sent: '已发送',
  acked: '已受理',
  failed: '上报失败',
}

export const DISPATCH_STRATEGY_LABEL: Record<DispatchStrategy, string> = {
  nearest: '就近优先',
  balanced: '负载均衡',
  skill: '技能优先',
}

// ---------------- 违停监控展示辅助 ----------------

export const ALERT_STATUS_LABEL: Record<AlertStatus, string> = {
  pending: '待核查',
  verified: '已确认违停',
  dismissed: '误报',
  handled: '已处置',
}

export const ALERT_STATUS_COLOR: Record<AlertStatus, string> = {
  pending: '#facc15',
  verified: '#ef4444',
  dismissed: '#64748b',
  handled: '#10b981',
}

/** 车牌可读性等级 → 展示文案 */
export const READABILITY_LABEL: Record<string, string> = {
  readable: '可读',
  marginal: '临界',
  unreadable: '不可读',
}

export const READABILITY_COLOR: Record<string, string> = {
  readable: '#10b981',
  marginal: '#facc15',
  unreadable: '#64748b',
}
