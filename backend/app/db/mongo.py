"""
MongoDB 访问层
==============
仅负责**连接管理**与**索引维护**；具体集合的读写语义由各 service 承担。

集合一览
--------
+--------------+--------------------------------------------------+
| detections   | 检测记录（图片 / 视频帧 / 直播帧的 YOLO 结果）    |
| events       | 交通事件与告警（供大屏与告警中心消费）             |
| officers     | 警员档案与勤务状态（供智能派警使用）              |
| incidents    | 警情单（外部上报 + 内部派警 + 处置时间线）        |
+--------------+--------------------------------------------------+

MongoClient 自身线程安全且自带连接池，因此进程内复用单例即可。
"""
from __future__ import annotations

from typing import List, Optional

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

from app.core.config import settings

_client: Optional[MongoClient] = None


def get_client() -> MongoClient:
    """返回进程内共享的 MongoClient。"""
    global _client
    if _client is None:
        _client = MongoClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=settings.mongo_timeout_ms,
        )
    return _client


def get_db() -> Database:
    return get_client()[settings.mongo_db_name]


def ping() -> bool:
    """连通性探测，供健康检查使用。"""
    try:
        get_client().admin.command('ping')
        return True
    except PyMongoError:
        return False


def ensure_indexes() -> List[str]:
    """创建索引（幂等），返回索引标识列表。可在启动时反复调用。"""
    db = get_db()
    created: List[str] = []

    detections = db['detections']
    detections.create_index([('timestamp', DESCENDING)], name='idx_ts')
    detections.create_index([('camera_id', ASCENDING)], name='idx_camera')
    detections.create_index([('road_id', ASCENDING)], name='idx_road')
    detections.create_index(
        [('camera_id', ASCENDING), ('timestamp', DESCENDING)], name='idx_camera_ts'
    )
    created.append('detections(ts, camera, road, camera+ts)')

    events = db['events']
    events.create_index([('created_at', DESCENDING)], name='idx_created')
    events.create_index([('status', ASCENDING)], name='idx_status')
    events.create_index([('level', ASCENDING)], name='idx_level')
    events.create_index(
        [('road_id', ASCENDING), ('created_at', DESCENDING)], name='idx_road_created'
    )
    created.append('events(created_at, status, level, road+created_at)')

    # 警员档案。**不建地理索引** —— 派警需要按距离排序，
    # 但警员规模在百量级，应用层算直线距离（见 dispatch_service）
    # 比维护 GeoJSON 字段与 2dsphere 索引更简单。
    officers = db['officers']
    officers.create_index([('officer_id', ASCENDING)], name='idx_officer_id', unique=True)
    officers.create_index([('status', ASCENDING)], name='idx_officer_status')
    officers.create_index([('unit', ASCENDING)], name='idx_officer_unit')
    created.append('officers(officer_id unique, status, unit)')

    # 警情单
    incidents = db['incidents']
    incidents.create_index([('created_at', DESCENDING)], name='idx_incident_created')
    incidents.create_index([('status', ASCENDING)], name='idx_incident_status')
    incidents.create_index([('event_id', ASCENDING)], name='idx_incident_event')
    incidents.create_index(
        [('assigned_officer_id', ASCENDING)], name='idx_incident_officer'
    )
    created.append('incidents(created_at, status, event, officer)')

    return created


def close() -> None:
    """释放连接，供应用关闭钩子调用。"""
    global _client
    if _client is not None:
        _client.close()
        _client = None
