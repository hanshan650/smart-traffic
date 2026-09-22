"""
警情调度服务
============
把「AI 事件 → 生成警情单 → 上报外部平台 → 智能派警 → 处置流转」
串成一条完整链路。

状态机
------
::

    REPORTED ──派警──> DISPATCHED ──接警──> ACCEPTED ──到场──> ARRIVED ──办结──> CLOSED
        │
        └──上报失败──> FAILED（可重试，重试成功后回到 REPORTED）

设计要点
--------
· **上报失败不阻断派警**。外部平台不可用是常态（接口未开放、网络抖动），
  若因此拦住内部派警，等于让外部依赖决定本单位能否出警。因此：
  上报失败只标记 ``report_status``，警情单照常创建并可派警，
  由值班员决定是补报还是走线下渠道。
· **派警会同步调整警员负载**（``active_cases``），办结时回退，
  使「负载」判据在下一轮派警中反映真实情况。
· 每次状态变更都写入 ``timeline``，形成可审计的处置记录。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from app.db import mongo
from app.models.schemas import (
    DispatchRequest,
    EventLevel,
    Incident,
    IncidentCreate,
    IncidentListResponse,
    IncidentStats,
    IncidentStatus,
    ReportStatus,
    TimelineEntry,
)
from app.services import dispatch_service, incident_reporter, officer_service


class IncidentError(RuntimeError):
    """警情操作的业务异常（供 API 层转成 4xx）。"""


# 允许的状态流转：当前状态 → 可执行的动作 → 目标状态
_TRANSITIONS: Dict[str, Dict[str, IncidentStatus]] = {
    IncidentStatus.REPORTED.value: {
        'accept': IncidentStatus.ACCEPTED,
        'arrive': IncidentStatus.ARRIVED,
    },
    IncidentStatus.DISPATCHED.value: {
        'accept': IncidentStatus.ACCEPTED,
        'arrive': IncidentStatus.ARRIVED,
    },
    IncidentStatus.ACCEPTED.value: {
        'arrive': IncidentStatus.ARRIVED,
    },
    IncidentStatus.ARRIVED.value: {
        'close': IncidentStatus.CLOSED,
    },
    IncidentStatus.FAILED.value: {
        'accept': IncidentStatus.ACCEPTED,
        'close': IncidentStatus.CLOSED,
    },
}


# ==========================================================================
# 内部工具
# ==========================================================================

def _collection():
    try:
        return mongo.get_db()['incidents']
    except PyMongoError as exc:
        raise IncidentError(f'数据库不可用：{exc}') from exc


def _to_incident(document: Dict[str, Any]) -> Incident:
    payload = dict(document)
    payload['id'] = str(payload.pop('_id'))
    payload.pop('_seq', None)
    return Incident(**payload)


def _next_incident_no() -> str:
    """生成面向人工的警情编号：JQ + 日期 + 4 位序号。"""
    try:
        count = _collection().count_documents({})
    except PyMongoError:
        count = 0
    return f"JQ{datetime.utcnow().strftime('%Y%m%d')}{count + 1:04d}"


def _entry(action: str, note: str = '') -> TimelineEntry:
    return TimelineEntry(at=datetime.utcnow(), action=action, note=note)


def _append_timeline(incident_id: str, entry: TimelineEntry) -> Optional[Dict[str, Any]]:
    """追加一条时间线，并返回**已包含该条目**的最新文档。

    这里的返回值很关键：调用方通常刚用 ``find_one_and_update`` 拿到文档快照，
    而 ``$push`` 发生在那之后，快照里并没有刚写入的这一条。若直接返回旧快照，
    接口响应就会永远少最新一条 —— 前端表现为「刚点了接警，时间线上却没有
    接警记录」，看起来像操作没生效。因此这里统一重读一次。

    重读失败（数据库不可用）时返回 ``None``，由调用方回退到旧快照。
    时间线属于审计信息，其写入失败不应影响主流程。
    """
    try:
        _collection().update_one(
            {'_id': ObjectId(incident_id)},
            {'$push': {'timeline': entry.model_dump(by_alias=False)},
             '$set': {'updated_at': datetime.utcnow()}},
        )
    except PyMongoError:
        return None

    try:
        return _collection().find_one({'_id': ObjectId(incident_id)})
    except PyMongoError:
        return None


# ==========================================================================
# 创建
# ==========================================================================

def create_incident(payload: IncidentCreate) -> Incident:
    """新建警情单，可选立即上报外部平台。"""
    now = datetime.utcnow()
    document: Dict[str, Any] = {
        'incident_no': _next_incident_no(),
        'event_id': payload.event_id,
        'type': payload.type.value,
        'level': payload.level.value,
        'road_id': payload.road_id,
        'camera_id': payload.camera_id,
        'camera_name': payload.camera_name,
        'latitude': payload.latitude,
        'longitude': payload.longitude,
        'address': payload.address,
        'description': payload.description,
        'snapshot_url': payload.snapshot_url,
        'score': payload.score,
        'reporter': '',
        'report_status': ReportStatus.PENDING.value,
        'external_id': '',
        'report_message': '',
        'retries': 0,
        'reported_at': None,
        'status': IncidentStatus.REPORTED.value,
        'assigned_officer_id': '',
        'assigned_officer_name': '',
        'assigned_at': None,
        'accepted_at': None,
        'arrived_at': None,
        'closed_at': None,
        'distance_km': 0.0,
        'eta_minutes': 0.0,
        'strategy': '',
        'candidates': [],
        'timeline': [_entry('create', f'生成警情单，类型 {payload.type.value}').model_dump(by_alias=False)],
        'created_at': now,
        'updated_at': now,
    }

    try:
        result = _collection().insert_one(document)
        document['_id'] = result.inserted_id
    except PyMongoError as exc:
        raise IncidentError(f'创建警情失败：{exc}') from exc

    incident = _to_incident(document)

    if payload.auto_report:
        incident = report_incident(incident.id, payload.reporter)

    return incident


# ==========================================================================
# 上报
# ==========================================================================

def report_incident(incident_id: str, reporter_key: str = '') -> Incident:
    """向外部平台上报警情（失败只标记状态，不抛异常）。"""
    incident = get_incident(incident_id)
    if incident is None:
        raise IncidentError(f'警情不存在：{incident_id}')

    reporter = incident_reporter.get_reporter(reporter_key)
    result = reporter.send(incident)

    report_status = ReportStatus.ACKED if result.ok else ReportStatus.FAILED
    changes = {
        'reporter': reporter.key,
        'report_status': report_status.value,
        'external_id': result.external_id,
        'report_message': result.message,
        'reported_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
    }
    # 上报失败时警情状态标记为 FAILED，但**仍可派警** —— 外部不可用
    # 不应阻断内部出警，值班员可自行决定补报或走线下渠道
    if not result.ok:
        changes['status'] = IncidentStatus.FAILED.value

    try:
        document = _collection().find_one_and_update(
            {'_id': ObjectId(incident_id)},
            {'$set': changes},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise IncidentError(f'更新上报状态失败：{exc}') from exc

    refreshed = _append_timeline(
        incident_id,
        _entry('report' if result.ok else 'report_failed',
               f'{reporter.display_name}：{result.message}'),
    )

    source = refreshed or document
    return _to_incident(source) if source else incident


def retry_report(incident_id: str) -> Incident:
    """重试上报，累加重试计数。"""
    try:
        _collection().update_one(
            {'_id': ObjectId(incident_id)},
            {'$inc': {'retries': 1}, '$set': {'updated_at': datetime.utcnow()}},
        )
    except PyMongoError as exc:
        raise IncidentError(f'更新重试次数失败：{exc}') from exc

    _append_timeline(incident_id, _entry('retry', '人工触发上报重试'))
    return report_incident(incident_id)


# ==========================================================================
# 查询
# ==========================================================================

def get_incident(incident_id: str) -> Optional[Incident]:
    if not ObjectId.is_valid(incident_id):
        return None
    try:
        document = _collection().find_one({'_id': ObjectId(incident_id)})
    except PyMongoError as exc:
        raise IncidentError(f'查询警情失败：{exc}') from exc
    return _to_incident(document) if document else None


def list_incidents(
    *,
    status: str = '',
    level: str = '',
    limit: int = 50,
    skip: int = 0,
) -> IncidentListResponse:
    query: Dict[str, Any] = {}
    if status:
        query['status'] = status
    if level:
        query['level'] = level

    try:
        collection = _collection()
        total = collection.count_documents(query)
        documents = list(
            collection.find(query).sort([('created_at', -1)]).skip(skip).limit(limit)
        )
    except PyMongoError as exc:
        raise IncidentError(f'查询警情列表失败：{exc}') from exc

    return IncidentListResponse(total=total, items=[_to_incident(item) for item in documents])


def stats() -> IncidentStats:
    try:
        documents = list(_collection().find({}, {'status': 1, 'type': 1, 'assigned_at': 1}))
    except PyMongoError as exc:
        raise IncidentError(f'统计警情失败：{exc}') from exc

    by_status: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    report_failed = 0
    unassigned = 0

    for item in documents:
        status = str(item.get('status') or '')
        by_status[status] = by_status.get(status, 0) + 1
        kind = str(item.get('type') or '')
        by_type[kind] = by_type.get(kind, 0) + 1
        if status == IncidentStatus.FAILED.value:
            report_failed += 1
        if not item.get('assigned_officer_id'):
            unassigned += 1

    return IncidentStats(
        total=len(documents),
        by_status=by_status,
        by_type=by_type,
        report_failed=report_failed,
        unassigned=unassigned,
    )


# ==========================================================================
# 派警
# ==========================================================================

def dispatch_incident(incident_id: str, request: DispatchRequest) -> Incident:
    """智能派警。会把候选评分明细与选中结果一并落库。"""
    incident = get_incident(incident_id)
    if incident is None:
        raise IncidentError(f'警情不存在：{incident_id}')

    if incident.status is IncidentStatus.CLOSED:
        raise IncidentError('警情已办结，不能再次派警')

    plan = dispatch_service.plan_dispatch(incident, request)
    if plan.selected is None:
        # 无可用警力是业务状态而非错误，把候选与原因回写供人工处理
        _collection().update_one(
            {'_id': ObjectId(incident_id)},
            {'$set': {
                'candidates': [item.model_dump(by_alias=False) for item in plan.candidates],
                'strategy': plan.strategy,
                'updated_at': datetime.utcnow(),
            }},
        )
        _append_timeline(incident_id, _entry('dispatch_failed', plan.reason))
        raise IncidentError(plan.reason or '没有可派警力')

    picked = plan.selected
    now = datetime.utcnow()
    previous_officer = incident.assigned_officer_id

    changes = {
        'status': IncidentStatus.DISPATCHED.value,
        'assigned_officer_id': picked.officer_id,
        'assigned_officer_name': picked.name,
        'assigned_at': now,
        'distance_km': picked.distance_km,
        'eta_minutes': picked.eta_minutes,
        'strategy': plan.strategy,
        'candidates': [item.model_dump(by_alias=False) for item in plan.candidates],
        'updated_at': now,
    }

    try:
        document = _collection().find_one_and_update(
            {'_id': ObjectId(incident_id)},
            {'$set': changes},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise IncidentError(f'派警失败：{exc}') from exc

    # 改派时回退原警员负载，避免重复计数
    if previous_officer and previous_officer != picked.officer_id:
        officer_service.adjust_active_cases(previous_officer, -1)
    if previous_officer != picked.officer_id:
        officer_service.adjust_active_cases(picked.officer_id, 1)

    refreshed = _append_timeline(incident_id, _entry('dispatch', plan.reason))
    source = refreshed or document
    return _to_incident(source) if source else incident


# ==========================================================================
# 状态流转
# ==========================================================================

def update_status(incident_id: str, action: str, note: str = '') -> Incident:
    """推进处置状态。``action`` ∈ {accept, arrive, close}。"""
    incident = get_incident(incident_id)
    if incident is None:
        raise IncidentError(f'警情不存在：{incident_id}')

    allowed = _TRANSITIONS.get(incident.status.value, {})
    if action not in allowed:
        current = incident.status.value
        raise IncidentError(
            f'当前状态「{current}」不支持动作「{action}」，'
            f'可选：{", ".join(allowed) or "无"}'
        )

    target = allowed[action]
    now = datetime.utcnow()

    changes: Dict[str, Any] = {'status': target.value, 'updated_at': now}
    if target is IncidentStatus.ACCEPTED:
        changes['accepted_at'] = now
    elif target is IncidentStatus.ARRIVED:
        changes['arrived_at'] = now
        if incident.accepted_at is None:
            changes['accepted_at'] = now
    elif target is IncidentStatus.CLOSED:
        changes['closed_at'] = now

    try:
        document = _collection().find_one_and_update(
            {'_id': ObjectId(incident_id)},
            {'$set': changes},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise IncidentError(f'更新警情状态失败：{exc}') from exc

    # 办结时释放警员负载并累加处理量
    if target is IncidentStatus.CLOSED and incident.assigned_officer_id:
        officer_service.adjust_active_cases(incident.assigned_officer_id, -1)

    refreshed = _append_timeline(
        incident_id, _entry(action, note or f'状态变更为 {target.value}')
    )
    source = refreshed or document
    return _to_incident(source) if source else incident


# ==========================================================================
# 对外说明
# ==========================================================================

def describe() -> Dict[str, Any]:
    """派警算法与上报适配器的自描述，供前端渲染说明。"""
    return {
        'dispatch': dispatch_service.describe(),
        'reporters': [item.model_dump(by_alias=True) for item in incident_reporter.list_reporters()],
        'transitions': {
            status: {action: target.value for action, target in actions.items()}
            for status, actions in _TRANSITIONS.items()
        },
    }
