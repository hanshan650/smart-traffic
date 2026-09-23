"""
数据契约（Pydantic 模型）
========================
前后端共用的请求/响应结构。前端 ``frontend/src/types/api.ts`` 与本文件
**一一对应**，修改时请同步更新两侧。

约定
----
· 字段在 Python 侧使用 ``snake_case`` 定义；
· 通过 :class:`CamelModel` 的 ``alias_generator`` 自动序列化为 ``camelCase``；
· FastAPI 默认按 alias 输出，因此前端拿到的是 camelCase。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """统一基类：自动 camelCase 别名 + 允许按字段名构造。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# ==========================================================================
# 枚举
# ==========================================================================

class CongestionLevel(str, Enum):
    """拥堵等级（与前端颜色映射一致）。"""

    NORMAL = 'normal'      # 畅通
    LIGHT = 'light'        # 轻度
    MODERATE = 'moderate'  # 中度
    HEAVY = 'heavy'        # 严重


class EventType(str, Enum):
    """交通事件类型。"""

    COLLISION = 'collision'            # 车辆碰撞
    ABNORMAL_STOP = 'abnormal_stop'    # 异常停车
    CONGESTION = 'congestion'          # 拥堵
    WRONG_WAY = 'wrong_way'            # 逆行
    DEBRIS = 'debris'                  # 抛洒物
    PEDESTRIAN = 'pedestrian'          # 行人闯入


class EventLevel(str, Enum):
    INFO = 'info'
    WARNING = 'warning'
    CRITICAL = 'critical'


class EventStatus(str, Enum):
    """事件处置状态机。

    PENDING → CONFIRMED → ARCHIVED
           ↘ REJECTED
    """

    PENDING = 'pending'      # 待复核（AI 已检出）
    CONFIRMED = 'confirmed'  # 已确认（人工一键确认）
    REJECTED = 'rejected'    # 误报（回写负样本池）
    ARCHIVED = 'archived'    # 已归档


# ==========================================================================
# 视频源
# ==========================================================================

class VideoSourceInfo(CamelModel):
    key: str
    display_name: str
    region: str
    available: bool
    reason: str = ''
    default_camera: str = ''


class CameraInfo(CamelModel):
    camera_num: str
    camera_name: str = ''
    road: str = ''
    pile_num: str = ''
    region_name: str = ''
    latitude: float = 0.0
    longitude: float = 0.0
    online: int = 1


class SourceListResponse(CamelModel):
    success: bool = True
    current: str
    sources: List[VideoSourceInfo] = Field(default_factory=list)


class CameraListResponse(CamelModel):
    success: bool = True
    source: str
    source_name: str
    cameras: List[CameraInfo] = Field(default_factory=list)
    total_available: int = 0
    total_all: int = 0


class StreamUrlResponse(CamelModel):
    success: bool = True
    source: str
    source_name: str
    camera_num: str
    # 上游原始 m3u8 地址（带时效 token，仅供后端抓帧使用）
    #
    # 注意：字段名刻意使用 ``stream_url`` 而非 ``m3u8_url``。
    # Pydantic 的 ``to_camel`` 会把数字后的字母一并大写，
    # ``m3u8_url`` 会变成 ``m3U8Url``（大写 U），与前端惯用的
    # ``m3u8Url`` 不一致，极易踩坑，故避开此类命名。
    stream_url: str
    # 同源代理地址，前端应使用此地址播放（绕过上游 Referer 与跨域限制）
    hls_proxy_url: str = ''


# ==========================================================================
# 检测
# ==========================================================================

class BBox(CamelModel):
    """归一化边界框，取值 [0, 1]，便于前端按容器尺寸还原。"""

    x: float
    y: float
    w: float
    h: float


class DetectedObject(CamelModel):
    class_id: int
    class_name: str
    confidence: float
    bbox: BBox


class DetectionResult(CamelModel):
    """一次检测的完整结果。"""

    camera_id: str
    road_id: str
    total_vehicles: int = 0
    vehicle_counts: Dict[str, int] = Field(default_factory=dict)
    congestion_level: CongestionLevel = CongestionLevel.NORMAL
    objects: List[DetectedObject] = Field(default_factory=list)
    snapshot_url: str = ''
    source_url: str = ''
    detection_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    duration_ms: int = 0


# ==========================================================================
# 交通事件
# ==========================================================================

class TrafficEvent(CamelModel):
    id: str
    event_type: EventType
    level: EventLevel = EventLevel.WARNING
    status: EventStatus = EventStatus.PENDING
    confidence: float = 0.0
    title: str = ''
    description: str = ''
    camera_id: str = ''
    camera_name: str = ''
    road_id: str = ''
    road_name: str = ''
    source_key: str = ''
    snapshot_url: str = ''
    vehicle_count: int = 0
    congestion_level: CongestionLevel = CongestionLevel.NORMAL
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None


class EventReviewRequest(CamelModel):
    """人工复核请求。"""

    action: str = Field(description="confirm | reject")
    reviewer: str = '值班员'
    remark: str = ''


class EventListResponse(CamelModel):
    success: bool = True
    total: int = 0
    items: List[TrafficEvent] = Field(default_factory=list)


# ==========================================================================
# 路况快照与排行
# ==========================================================================

class TrafficSnapshot(CamelModel):
    """路段 / 摄像头的实时交通快照，供大屏高频轮询或推送。"""

    camera_id: str
    camera_name: str = ''
    road_id: str = ''
    road_name: str = ''
    total_vehicles: int = 0
    vehicle_counts: Dict[str, int] = Field(default_factory=dict)
    congestion_level: CongestionLevel = CongestionLevel.NORMAL
    snapshot_url: str = ''
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RoadRankItem(CamelModel):
    road_id: str
    road_name: str = ''
    vehicle_count: int = 0
    congestion_level: CongestionLevel = CongestionLevel.NORMAL
    score: float = 0.0


# ==========================================================================
# 上海路网
# ==========================================================================

class Intersection(CamelModel):
    id: str
    name: str
    lng: float
    lat: float


class RoadSegment(CamelModel):
    id: str
    name: str
    from_id: str
    to_id: str
    length_m: float
    speed_limit: int = 60
    lanes: int = 4
    congestion_level: CongestionLevel = CongestionLevel.NORMAL
    polyline: List[List[float]] = Field(default_factory=list)


class RoadNetwork(BaseModel):
    """路网整体结构（面向前端一次性拉取）。"""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    center_lng: float = 121.4737
    center_lat: float = 31.2304
    intersections: List[Intersection] = Field(default_factory=list)
    roads: List[RoadSegment] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)


# ==========================================================================
# 高德地图
# ==========================================================================

class GeoPoint(CamelModel):
    lng: float
    lat: float


class AmapRouteStep(CamelModel):
    instruction: str = ''
    road: str = ''
    distance: int = 0
    duration: int = 0
    polyline: List[List[float]] = Field(default_factory=list)


class AmapRoute(CamelModel):
    distance: int = 0
    duration: int = 0
    steps: List[AmapRouteStep] = Field(default_factory=list)


# ==========================================================================
# 实时推送
# ==========================================================================

class WsMessage(CamelModel):
    """WebSocket 统一信封。

    频道（channel）约定：
      · ``event:new``      新事件产生
      · ``event:update``   事件状态变更
      · ``snapshot``       检测快照更新
      · ``health``         系统健康状态
      · ``pong``           心跳应答
    """

    channel: str
    ts: datetime = Field(default_factory=datetime.utcnow)
    payload: Any = None


# ==========================================================================
# 系统
# ==========================================================================

class HealthStatus(CamelModel):
    status: str = 'ok'
    version: str = '1.0.0'
    mongo: bool = False
    redis: bool = False
    yolo: bool = False
    video_source: str = ''
    video_source_available: bool = False
    detail: Dict[str, Any] = Field(default_factory=dict)


class StatsOverview(CamelModel):
    """大屏顶部统计。"""

    camera_total: int = 0
    camera_online: int = 0
    detection_total: int = 0
    event_pending: int = 0
    event_total: int = 0
    vehicle_total: int = 0
    avg_confidence: float = 0.0


# ==========================================================================
# 警员
# ==========================================================================

class OfficerStatus(str, Enum):
    """警员值班状态。

    只有 ``ON_DUTY`` 与 ``BUSY`` 参与派警评分，其余在候选阶段即被过滤。
    """

    ON_DUTY = 'on_duty'      # 在岗，可派
    BUSY = 'busy'            # 出警中（仍可派，但降权）
    OFF_DUTY = 'off_duty'    # 下班
    LEAVE = 'leave'          # 请假


class OfficerSkill(str, Enum):
    """警员专长，用于与警情类型做匹配。"""

    ACCIDENT = 'accident'            # 事故处理
    TRAFFIC = 'traffic'              # 交通疏导
    FIRST_AID = 'first_aid'          # 急救
    HAZMAT = 'hazmat'                # 危险品处置
    INVESTIGATION = 'investigation'  # 事故勘察


class OfficerBase(CamelModel):
    """警员基础字段（录入与更新共用）。"""

    officer_id: str = Field(description='警号')
    name: str = ''
    rank: str = ''        # 警衔
    unit: str = ''        # 所属单位 / 中队
    phone: str = ''
    status: OfficerStatus = OfficerStatus.OFF_DUTY
    skills: List[OfficerSkill] = Field(default_factory=list)
    latitude: float = 0.0
    longitude: float = 0.0
    region: str = ''      # 辖区
    # 负载与绩效，供派警评分使用
    active_cases: int = 0
    total_handled: int = 0
    avg_response_minutes: float = 0.0
    note: str = ''


class Officer(OfficerBase):
    """警员（出参）。"""

    id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class OfficerCreate(OfficerBase):
    """新增警员入参。"""


class OfficerUpdate(CamelModel):
    """更新警员入参。全部字段可选，仅提交需要修改的项。"""

    name: Optional[str] = None
    rank: Optional[str] = None
    unit: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[OfficerStatus] = None
    skills: Optional[List[OfficerSkill]] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    region: Optional[str] = None
    active_cases: Optional[int] = None
    total_handled: Optional[int] = None
    avg_response_minutes: Optional[float] = None
    note: Optional[str] = None


class OfficerListResponse(CamelModel):
    success: bool = True
    total: int = 0
    items: List[Officer] = Field(default_factory=list)


class OfficerStats(CamelModel):
    """警员勤务概览。"""

    total: int = 0
    on_duty: int = 0
    busy: int = 0
    off_duty: int = 0
    leave: int = 0
    by_unit: Dict[str, int] = Field(default_factory=dict)


# ==========================================================================
# 警情上报适配器
# ==========================================================================

class ReportStatus(str, Enum):
    """向外部交警平台的上报状态。"""

    PENDING = 'pending'  # 待上报
    SENT = 'sent'        # 已发出
    ACKED = 'acked'      # 对方已回执
    FAILED = 'failed'    # 上报失败（可重试）


class ReporterInfo(CamelModel):
    """上报适配器自描述。"""

    key: str
    display_name: str
    mode: str = 'mock'                 # mock / real
    available: bool = True
    reason: str = ''
    endpoint: str = ''
    supports_ack: bool = False         # 是否提供回执查询


class ReporterListResponse(CamelModel):
    success: bool = True
    current: str
    reporters: List[ReporterInfo] = Field(default_factory=list)


# ==========================================================================
# 智能派警
# ==========================================================================

class DispatchEvidence(CamelModel):
    """派警评分中的单条判据。

    与事故识别的证据链同样保留「原始值 / 归一化得分 / 权重 / 贡献」，
    使"为什么派给这个人"完全可追溯 —— 调度决策需要能向当事人解释。
    """

    name: str
    label: str
    raw_value: float
    score: float
    weight: float
    contribution: float
    detail: str = ''


class DispatchCandidate(CamelModel):
    """一名候选警员的评分明细。"""

    officer_id: str
    name: str
    unit: str = ''
    status: OfficerStatus = OfficerStatus.OFF_DUTY
    distance_km: float = 0.0
    eta_minutes: float = 0.0
    active_cases: int = 0
    matched_skills: List[OfficerSkill] = Field(default_factory=list)
    score: float = 0.0
    selected: bool = False
    excluded: bool = False          # 状态不符被排除
    exclude_reason: str = ''
    evidences: List[DispatchEvidence] = Field(default_factory=list)


class DispatchStrategy(str, Enum):
    """派警策略。"""

    NEAREST = 'nearest'        # 就近优先（默认）
    BALANCED = 'balanced'      # 负载均衡（考虑在办案件数）
    SKILL = 'skill'            # 技能优先（按警情类型匹配专长）


class DispatchRequest(CamelModel):
    """派警请求。"""

    strategy: DispatchStrategy = DispatchStrategy.NEAREST
    # 可选：直接指定警员（跳过算法）
    officer_id: str = ''
    top_n: int = 5             # 返回候选数量
    note: str = ''


# ==========================================================================
# 警情（上报 + 派警单）
# ==========================================================================

class IncidentStatus(str, Enum):
    """警情处置状态机。

    REPORTED → DISPATCHED → ACCEPTED → ARRIVED → CLOSED
    """

    REPORTED = 'reported'      # 已上报，待派警
    DISPATCHED = 'dispatched'  # 已派警，待接警
    ACCEPTED = 'accepted'      # 警员已接警
    ARRIVED = 'arrived'        # 警员已到场
    CLOSED = 'closed'          # 已办结
    FAILED = 'failed'          # 上报失败


class TimelineEntry(CamelModel):
    """处置时间线的一条记录。"""

    at: datetime
    action: str
    note: str = ''


class Incident(CamelModel):
    """警情单：AI 事件 → 外部上报 → 内部派警的载体。"""

    id: str
    incident_no: str = ''            # 警情编号（面向人工的短号）
    event_id: str = ''               # 关联的 AI 识别事件
    type: EventType
    level: EventLevel = EventLevel.WARNING
    # 位置与现场
    road_id: str = ''
    camera_id: str = ''
    camera_name: str = ''
    latitude: float = 0.0
    longitude: float = 0.0
    address: str = ''
    description: str = ''
    snapshot_url: str = ''
    score: float = 0.0               # 事故识别评分（来自 L2 融合）
    # 上报
    reporter: str = ''
    report_status: ReportStatus = ReportStatus.PENDING
    external_id: str = ''
    report_message: str = ''
    retries: int = 0
    reported_at: Optional[datetime] = None
    # 派警与处置
    status: IncidentStatus = IncidentStatus.REPORTED
    assigned_officer_id: str = ''
    assigned_officer_name: str = ''
    assigned_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    arrived_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    distance_km: float = 0.0
    eta_minutes: float = 0.0
    strategy: str = ''
    candidates: List[DispatchCandidate] = Field(default_factory=list)
    timeline: List[TimelineEntry] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class IncidentCreate(CamelModel):
    """从 AI 事件创建警情的入参。"""

    event_id: str = ''
    type: EventType = EventType.COLLISION
    level: EventLevel = EventLevel.WARNING
    road_id: str = ''
    camera_id: str = ''
    camera_name: str = ''
    latitude: float = 0.0
    longitude: float = 0.0
    address: str = ''
    description: str = ''
    snapshot_url: str = ''
    score: float = 0.0
    # 创建后是否立即上报外部平台
    auto_report: bool = True
    reporter: str = ''


class IncidentListResponse(CamelModel):
    success: bool = True
    total: int = 0
    items: List[Incident] = Field(default_factory=list)


class IncidentStats(CamelModel):
    """警情统计。"""

    total: int = 0
    by_status: Dict[str, int] = Field(default_factory=dict)
    by_type: Dict[str, int] = Field(default_factory=dict)
    report_failed: int = 0
    unassigned: int = 0
    avg_dispatch_minutes: float = 0.0


# ==========================================================================
# 持续检测（违停监控）
# ==========================================================================

class SurveillanceStartRequest(CamelModel):
    """启动巡检。省略 ``camera_ids`` 时取配置中的列表。

    ``hints`` 是**点位名称映射**（摄像头编号 -> 名称），用于判定该地点
    是否允许停车。

    为什么不自动从上游取名称：上游的摄像头检索接口是按视野范围
    **随机抽样**返回的，没有"按编号查详情"的能力，无法可靠地拿到
    指定摄像头的名称。而且"这个点位是不是服务区"本来就是**部署时应当
    核实**的事实，由使用方显式声明比系统猜测更可信。

    名称里包含"服务区""收费站""检查站"等关键词即判定为可停车场所。
    """

    camera_ids: List[str] = Field(default_factory=list)
    hints: Dict[str, str] = Field(default_factory=dict)


class AlertStatusRequest(CamelModel):
    """告警处置。``status`` ∈ {verified, dismissed, handled}。"""

    status: str
    note: str = ''


class OwnerLookupRequest(CamelModel):
    """车主信息查询请求。

    ``operator`` 与 ``reason`` 都是**必填** —— 机动车所有人信息属于受保护的
    个人信息，查询行为必须可追溯。缺少任一项时服务层会直接拒绝。

    ``plate`` 应当来自车牌识别结果；若识别通道不可用，则不会有可查询的车牌，
    本接口也不会被调用 —— 这条链路是自洽的。
    """

    plate: str
    operator: str
    reason: str
    purpose: str = '违停告警核查'
    # 留空则使用配置中的默认通道
    provider: str = ''
    # 关联的告警单与摄像头，用于让审计记录能回溯到具体事件
    alert_id: str = ''
    camera_id: str = ''


class PlateEstimateRequest(CamelModel):
    """车牌可读性估算（诊断用）。"""

    bbox_width: float
    bbox_height: float
    frame_width: int = 352
    frame_height: int = 288


# ==========================================================================
# 民众上报
# ==========================================================================

class CitizenReportReviewRequest(CamelModel):
    """民众上报的复核请求。

    ``reviewer`` 必填 —— 复核意味着一次人工判断，必须能追溯到人。
    缺少时服务层会直接拒绝。
    """

    action: str = Field(description='approve | reject | duplicate')
    reviewer: str
    note: str = ''
    #: 仅 approve 时有意义，可覆盖上报的默认紧急级别
    level: str = ''


class AlertEvidence(CamelModel):
    """违停告警中的单条判据。结构与事故识别、派警的证据链一致。"""

    name: str
    label: str
    raw_value: float
    score: float
    weight: float
    contribution: float
    detail: str = ''


class StallAlert(CamelModel):
    """违停告警记录。

    这个模型存在的意义不只是"定义字段" —— 它还承担**输出格式统一**的职责。
    此前接口直接把 MongoDB 文档原样返回，于是响应里是 ``camera_id`` 这样的
    snake_case 字段，而项目其余接口一律输出 camelCase。前端按统一约定解析时
    就会拿到空值。经 Pydantic 走一遍，格式就与其它接口对齐了。
    """

    id: str = ''
    camera_id: str = ''
    camera_name: str = ''
    road_id: str = ''
    track_id: int = 0
    class_name: str = ''
    stationary_seconds: float = 0.0
    score: float = 0.0
    displacement: float = 0.0
    zone_type: str = 'highway'
    zone_label: str = ''
    snapshot_url: str = ''
    evidences: List[AlertEvidence] = Field(default_factory=list)

    # 车牌相关。识别不可用时 plate 为空，原因写在 plate_message 里 ——
    # 前端要能区分"没有车牌"和"识别失败"
    plate: str = ''
    plate_simulated: bool = False
    plate_message: str = ''
    plate_char_height: float = 0.0
    plate_readability: str = 'unreadable'

    status: str = 'pending'
    note: str = ''
    created_at: Optional[str] = None


class StallAlertListResponse(CamelModel):
    success: bool = True
    total: int = 0
    items: List[StallAlert] = Field(default_factory=list)
