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
