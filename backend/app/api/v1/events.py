"""
事件与告警接口
==============
+----------------------------+--------------------------------------+
| ``GET  /``                 | 事件列表（支持状态 / 等级筛选）        |
| ``GET  /stats``            | 事件统计                              |
| ``GET  /rank``             | 拥堵排行（Redis Sorted Set）          |
| ``GET  /{event_id}``       | 事件详情                              |
| ``POST /{event_id}/review``| 人工一键确认 / 误报                    |
+----------------------------+--------------------------------------+

一键确认流程
------------
AI 检出 → 事件落库（PENDING）→ 大屏弹窗置顶 → 值班员点击确认 →
状态转 CONFIRMED 并通过 WebSocket 广播 ``event:update``。

误报则转 REJECTED，并标记 ``hard_negative`` 回写负样本池，
供后续主动学习迭代模型。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    EventLevel,
    EventListResponse,
    EventReviewRequest,
    EventStatus,
    TrafficEvent,
)
from app.realtime.ws_manager import broadcast_threadsafe
from app.services import event_service

router = APIRouter(prefix='/events', tags=['事件'])


@router.get('', response_model=EventListResponse, summary='事件列表')
def list_events(
    status: Optional[EventStatus] = Query(None, description='事件状态'),
    level: Optional[EventLevel] = Query(None, description='事件等级'),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
) -> EventListResponse:
    try:
        total, items = event_service.list_events(
            status=status, level=level, limit=limit, skip=skip
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={'success': False, 'error': str(exc), 'hint': '请确认 MongoDB 已启动'},
        ) from exc

    return EventListResponse(total=total, items=items)


@router.get('/stats', summary='事件统计')
def event_stats() -> Dict[str, Any]:
    """各状态与各等级的事件数量，供大屏与后台图表使用。"""
    from app.db import mongo

    by_status: Dict[str, int] = {}
    by_level: Dict[str, int] = {}

    try:
        collection = mongo.get_db()['events']
        for row in collection.aggregate([{'$group': {'_id': '$status', 'count': {'$sum': 1}}}]):
            by_status[str(row['_id'])] = int(row['count'])
        for row in collection.aggregate([{'$group': {'_id': '$level', 'count': {'$sum': 1}}}]):
            by_level[str(row['_id'])] = int(row['count'])
    except Exception:
        pass

    return {
        'success': True,
        'byStatus': by_status,
        'byLevel': by_level,
        'total': sum(by_status.values()),
        'pending': by_status.get(EventStatus.PENDING.value, 0),
    }


@router.get('/rank', summary='拥堵排行')
def congestion_rank(limit: int = Query(10, ge=1, le=50)) -> Dict[str, Any]:
    """基于 Redis Sorted Set 的路段拥堵排行。"""
    return {'success': True, 'items': event_service.congestion_rank(limit)}


@router.get('/{event_id}', response_model=TrafficEvent, summary='事件详情')
def get_event(event_id: str) -> TrafficEvent:
    event = event_service.get_event(event_id)
    if event is None:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': '事件不存在', 'eventId': event_id},
        )
    return event


@router.post('/{event_id}/review', response_model=TrafficEvent, summary='事件复核')
def review_event(event_id: str, payload: EventReviewRequest) -> TrafficEvent:
    """一键确认（``action=confirm``）或误报回写（``action=reject``）。"""
    if payload.action not in ('confirm', 'reject'):
        raise HTTPException(
            status_code=400,
            detail={'success': False, 'error': "action 必须是 confirm 或 reject"},
        )

    try:
        event = event_service.review_event(
            event_id,
            action=payload.action,
            reviewer=payload.reviewer,
            remark=payload.remark,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={'success': False, 'error': str(exc), 'hint': '请确认 MongoDB 已启动'},
        ) from exc

    if event is None:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': '事件不存在', 'eventId': event_id},
        )

    # 广播状态变更，所有大屏同步刷新
    broadcast_threadsafe('event:update', event.model_dump(mode='json', by_alias=True))

    return event
