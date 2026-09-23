"""
事件与检测记录服务
==================
职责：

1. 持久化检测记录（MongoDB ``detections``）
2. 依据检测结果生成交通事件（含**去重**与**限流**）
3. 事件复核（一键确认 / 误报回写）
4. 实时缓存与拥堵排行（Redis）

事件生成策略
------------
当前为**规则驱动**（阈值 + 60 秒限流），后续接入三级级联事故识别算法时，
算法模块可直接调用 :func:`create_event` 上报，本文件无需改动。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from pymongo.errors import PyMongoError

from app.core.config import settings
from app.db import mongo, redis_client
from app.models.schemas import (
    CongestionLevel,
    EventLevel,
    EventStatus,
    EventType,
    StatsOverview,
    TrafficEvent,
)

# 拥堵等级 → 事件等级
_CONGESTION_EVENT_LEVEL = {
    CongestionLevel.HEAVY: EventLevel.CRITICAL,
    CongestionLevel.MODERATE: EventLevel.WARNING,
}

# 拥堵等级 → 排序权重（越大越严重）
_SEVERITY_WEIGHT = {
    CongestionLevel.NORMAL: 1.0,
    CongestionLevel.LIGHT: 2.0,
    CongestionLevel.MODERATE: 4.0,
    CongestionLevel.HEAVY: 8.0,
}


# ==========================================================================
# 内部工具
# ==========================================================================

def _to_event(document: Dict[str, Any]) -> TrafficEvent:
    """把 MongoDB 文档转换为 API 模型。"""
    payload = dict(document)
    payload['id'] = str(payload.pop('_id'))
    return TrafficEvent(**payload)


def _serialize(event: TrafficEvent) -> Dict[str, Any]:
    """把 API 模型转换为可写入 MongoDB 的字典。"""
    data = event.model_dump(by_alias=False)
    data.pop('id', None)
    return data


# ==========================================================================
# 检测记录
# ==========================================================================

def save_detection(
    *,
    camera_id: str,
    road_id: str,
    total_vehicles: int,
    vehicle_counts: Dict[str, int],
    congestion_level: CongestionLevel,
    objects: List[Any],
    snapshot_url: str = '',
    source_url: str = '',
    duration_ms: int = 0,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> str:
    """写入一条检测记录，返回其 id。"""
    document = {
        'camera_id': camera_id,
        'road_id': road_id,
        'total_vehicles': total_vehicles,
        'vehicle_counts': vehicle_counts,
        'congestion_level': congestion_level.value,
        'objects': [obj.model_dump(by_alias=False) for obj in objects],
        'snapshot_url': snapshot_url,
        'source_url': source_url,
        'duration_ms': duration_ms,
        'latitude': latitude,
        'longitude': longitude,
        'timestamp': datetime.utcnow(),
    }

    try:
        result = mongo.get_db()['detections'].insert_one(document)
    except PyMongoError as exc:
        raise RuntimeError(f'写入检测记录失败：{exc}') from exc

    return str(result.inserted_id)


def count_detections(since_hours: Optional[int] = None) -> int:
    query: Dict[str, Any] = {}
    if since_hours:
        query['timestamp'] = {'$gte': datetime.utcnow() - timedelta(hours=since_hours)}

    try:
        return int(mongo.get_db()['detections'].count_documents(query))
    except PyMongoError:
        return 0


def sum_vehicles(since_hours: Optional[int] = None) -> int:
    match: Dict[str, Any] = {}
    if since_hours:
        match['timestamp'] = {'$gte': datetime.utcnow() - timedelta(hours=since_hours)}

    pipeline = [
        {'$match': match},
        {'$group': {'_id': None, 'total': {'$sum': '$total_vehicles'}}},
    ]
    try:
        rows = list(mongo.get_db()['detections'].aggregate(pipeline))
    except PyMongoError:
        return 0

    return int(rows[0]['total']) if rows else 0


# ==========================================================================
# 实时缓存
# ==========================================================================

def cache_snapshot(
    *,
    camera_id: str,
    camera_name: str,
    road_id: str,
    road_name: str,
    total_vehicles: int,
    vehicle_counts: Dict[str, int],
    congestion_level: CongestionLevel,
    snapshot_url: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> None:
    """写入 Redis 快照并更新拥堵排行。"""
    snapshot = {
        'cameraId': camera_id,
        'cameraName': camera_name,
        'roadId': road_id,
        'roadName': road_name,
        'totalVehicles': total_vehicles,
        'vehicleCounts': vehicle_counts,
        'congestionLevel': congestion_level.value,
        'snapshotUrl': snapshot_url,
        # 坐标存进快照，供地图展示使用
        'latitude': latitude,
        'longitude': longitude,
        'updatedAt': datetime.utcnow().isoformat(),
    }

    redis_client.set_json(
        redis_client.make_key('snapshot', camera_id),
        snapshot,
        ttl=settings.cache_ttl_snapshot * 12,   # 60 秒，供大屏轮询
    )

    if road_id:
        redis_client.zadd_rank(
            'congestion_rank',
            road_id,
            total_vehicles * _SEVERITY_WEIGHT.get(congestion_level, 1.0),
        )

    # 维护快照名单。读取方需要"列出所有当前路况"时，读这个索引是一次
    # 往返；若改为扫描 ``snapshot:*`` 前缀，SCAN 必须遍历完整个 key 空间
    # 才能确认结束，key 多时会把请求拖到超时。
    redis_client.sadd_members('snapshot_index', [camera_id])


def get_snapshot(camera_id: str) -> Optional[Dict[str, Any]]:
    return redis_client.get_json(redis_client.make_key('snapshot', camera_id))


def congestion_rank(limit: int = 10) -> List[Dict[str, Any]]:
    return redis_client.ztop('congestion_rank', limit)


# ==========================================================================
# 事件
# ==========================================================================

def create_event(
    *,
    event_type: EventType,
    level: EventLevel,
    confidence: float,
    title: str,
    description: str = '',
    camera_id: str = '',
    camera_name: str = '',
    road_id: str = '',
    road_name: str = '',
    source_key: str = '',
    snapshot_url: str = '',
    vehicle_count: int = 0,
    congestion_level: CongestionLevel = CongestionLevel.NORMAL,
    throttle_seconds: Optional[int] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> Optional[TrafficEvent]:
    """创建事件。

    对同一 ``(event_type, camera_id)`` 组合应用滑动窗口限流，
    窗口内的重复事件会被抑制，返回 ``None``。

    :return: 新建的事件；被限流抑制时返回 ``None``
    """
    window = throttle_seconds if throttle_seconds is not None else settings.alert_throttle_seconds

    if window > 0:
        throttle_key = redis_client.make_key(
            'throttle', event_type.value, camera_id or 'unknown'
        )
        if not redis_client.acquire_once(throttle_key, window):
            return None   # 窗口内已告警，抑制

    event = TrafficEvent(
        id='',   # 由 MongoDB 生成，写入前不参与
        event_type=event_type,
        level=level,
        status=EventStatus.PENDING,
        confidence=round(confidence, 4),
        title=title,
        description=description,
        camera_id=camera_id,
        camera_name=camera_name,
        road_id=road_id,
        road_name=road_name,
        source_key=source_key,
        snapshot_url=snapshot_url,
        vehicle_count=vehicle_count,
        congestion_level=congestion_level,
        latitude=latitude,
        longitude=longitude,
        created_at=datetime.utcnow(),
    )

    document = _serialize(event)

    try:
        result = mongo.get_db()['events'].insert_one(document)
    except PyMongoError as exc:
        raise RuntimeError(f'写入事件失败：{exc}') from exc

    event.id = str(result.inserted_id)

    # 同步进消息队列，供后端消费者或审计使用
    redis_client.push_queue(
        'mq:event',
        event.model_dump(mode='json', by_alias=True),
    )

    return event


def event_from_congestion(
    *,
    congestion_level: CongestionLevel,
    camera_id: str,
    camera_name: str,
    road_id: str,
    road_name: str,
    source_key: str,
    vehicle_count: int,
    snapshot_url: str = '',
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> Optional[TrafficEvent]:
    """按拥堵等级自动生成事件（未达阈值则返回 None）。"""
    level = _CONGESTION_EVENT_LEVEL.get(congestion_level)
    if level is None:
        return None

    label = '严重拥堵' if congestion_level is CongestionLevel.HEAVY else '中度拥堵'

    return create_event(
        event_type=EventType.CONGESTION,
        level=level,
        # 车辆数越多置信度越高，封顶 0.95
        confidence=min(0.55 + vehicle_count / 200.0, 0.95),
        title=f'{label}：{road_name or camera_name or road_id}',
        description=f'检测到 {vehicle_count} 辆车，判定为{label}',
        camera_id=camera_id,
        camera_name=camera_name,
        road_id=road_id,
        road_name=road_name,
        source_key=source_key,
        snapshot_url=snapshot_url,
        vehicle_count=vehicle_count,
        congestion_level=congestion_level,
    )


def list_events(
    *,
    status: Optional[EventStatus] = None,
    level: Optional[EventLevel] = None,
    limit: int = 50,
    skip: int = 0,
) -> Tuple[int, List[TrafficEvent]]:
    """按条件分页查询事件，返回 ``(总数, 事件列表)``。"""
    query: Dict[str, Any] = {}
    if status is not None:
        query['status'] = status.value
    if level is not None:
        query['level'] = level.value

    collection = mongo.get_db()['events']
    try:
        total = int(collection.count_documents(query))
        cursor = collection.find(query).sort('created_at', -1).skip(skip).limit(limit)
        items = [_to_event(doc) for doc in cursor]
    except PyMongoError as exc:
        raise RuntimeError(f'查询事件失败：{exc}') from exc

    return total, items


def get_event(event_id: str) -> Optional[TrafficEvent]:
    try:
        oid = ObjectId(event_id)
    except Exception:
        return None

    try:
        document = mongo.get_db()['events'].find_one({'_id': oid})
    except PyMongoError:
        return None

    return _to_event(document) if document else None


def review_event(
    event_id: str,
    action: str,
    reviewer: str = '值班员',
    remark: str = '',
) -> Optional[TrafficEvent]:
    """人工复核事件。

    :param action: ``confirm``（确认并派警）或 ``reject``（误报）
    :return: 更新后的事件；事件不存在或 action 非法时返回 ``None``
    """
    status_map = {
        'confirm': EventStatus.CONFIRMED,
        'reject': EventStatus.REJECTED,
    }
    new_status = status_map.get(action)
    if new_status is None:
        return None

    try:
        oid = ObjectId(event_id)
    except Exception:
        return None

    updates: Dict[str, Any] = {
        'status': new_status.value,
        'reviewed_at': datetime.utcnow(),
        'reviewed_by': reviewer,
    }
    if remark:
        updates['review_remark'] = remark

    # 误报回写：记入负样本池，供后续主动学习使用
    if new_status is EventStatus.REJECTED:
        updates['hard_negative'] = True

    try:
        collection = mongo.get_db()['events']
        document = collection.find_one_and_update(
            {'_id': oid},
            {'$set': updates},
            return_document=True,
        )
    except PyMongoError as exc:
        raise RuntimeError(f'更新事件失败：{exc}') from exc

    return _to_event(document) if document else None


def count_events(status: Optional[EventStatus] = None) -> int:
    query: Dict[str, Any] = {}
    if status is not None:
        query['status'] = status.value

    try:
        return int(mongo.get_db()['events'].count_documents(query))
    except PyMongoError:
        return 0


# ==========================================================================
# 统计概览
# ==========================================================================

def build_stats(camera_total: int = 0, camera_online: int = 0) -> StatsOverview:
    """大屏顶部统计。数据库不可用时返回零值而非抛错。"""
    return StatsOverview(
        camera_total=camera_total,
        camera_online=camera_online,
        detection_total=count_detections(),
        event_pending=count_events(EventStatus.PENDING),
        event_total=count_events(),
        vehicle_total=sum_vehicles(since_hours=24),
        avg_confidence=0.0,
    )
