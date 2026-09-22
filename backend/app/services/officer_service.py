"""
警员管理服务
============
警员档案与勤务状态的持久化（MongoDB ``officers``）。

设计约定
--------
· 以 :data:`OFFICER_ID` 作为业务主键（警号），``_id`` 仅作存储主键；
  对外一律使用警号（见 :func:`get_officer` 与 :func:`update_officer` 的入参）
· **坐标按 lat / lng 分离字段存储**，而不是 GeoJSON。
  派警需要按距离排序，但警员规模在百量级，应用层算直线距离
  （见 :mod:`app.services.dispatch_service`）比维护 2dsphere 索引更简单，
  也避免了与其它模块（摄像头、路网）坐标字段风格不一致
· 删除采用**物理删除**。警员是主数据，其历史处置记录保存在
  ``incidents`` 中（冗余了姓名与警号），因此删除警员不会破坏历史可读性
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.db import mongo
from app.models.schemas import (
    Officer,
    OfficerCreate,
    OfficerListResponse,
    OfficerSkill,
    OfficerStats,
    OfficerStatus,
    OfficerUpdate,
)

OFFICER_ID = 'officer_id'


class OfficerError(RuntimeError):
    """警员操作的业务异常（供 API 层转成 4xx）。"""


# ==========================================================================
# 内部工具
# ==========================================================================

def _to_officer(document: Dict[str, Any]) -> Officer:
    payload = dict(document)
    payload['id'] = str(payload.pop('_id'))
    payload['skills'] = [
        OfficerSkill(item) for item in payload.get('skills') or [] if item
    ]
    return Officer(**payload)


def _collection():
    try:
        return mongo.get_db()['officers']
    except PyMongoError as exc:
        raise OfficerError(f'数据库不可用：{exc}') from exc


# ==========================================================================
# 查询
# ==========================================================================

def list_officers(
    *,
    status: Optional[str] = None,
    unit: str = '',
    keyword: str = '',
    limit: int = 200,
    skip: int = 0,
) -> OfficerListResponse:
    """按条件检索警员。``keyword`` 匹配警号或姓名。"""
    query: Dict[str, Any] = {}
    if status:
        query['status'] = status
    if unit:
        query['unit'] = unit
    if keyword:
        escaped = keyword.replace('\\', '\\\\')
        query['$or'] = [
            {OFFICER_ID: {'$regex': escaped, '$options': 'i'}},
            {'name': {'$regex': escaped, '$options': 'i'}},
        ]

    try:
        collection = _collection()
        total = collection.count_documents(query)
        documents = list(
            collection.find(query).sort([('status', 1), (OFFICER_ID, 1)]).skip(skip).limit(limit)
        )
    except PyMongoError as exc:
        raise OfficerError(f'查询警员失败：{exc}') from exc

    return OfficerListResponse(
        total=total,
        items=[_to_officer(item) for item in documents],
    )


def get_officer(officer_id: str) -> Optional[Officer]:
    """按**警号**获取警员。"""
    try:
        document = _collection().find_one({OFFICER_ID: officer_id})
    except PyMongoError as exc:
        raise OfficerError(f'查询警员失败：{exc}') from exc
    return _to_officer(document) if document else None


def get_by_object_id(object_id: str) -> Optional[Officer]:
    """按存储主键获取警员（供内部关联使用）。"""
    if not ObjectId.is_valid(object_id):
        return None
    try:
        document = _collection().find_one({'_id': ObjectId(object_id)})
    except PyMongoError as exc:
        raise OfficerError(f'查询警员失败：{exc}') from exc
    return _to_officer(document) if document else None


def list_available() -> List[Officer]:
    """返回可参与派警的警员（在岗 / 出警中）。"""
    try:
        documents = _collection().find(
            {'status': {'$in': [OfficerStatus.ON_DUTY.value, OfficerStatus.BUSY.value]}}
        )
    except PyMongoError as exc:
        raise OfficerError(f'查询警员失败：{exc}') from exc
    return [_to_officer(item) for item in documents]


def stats() -> OfficerStats:
    """勤务概览（供首页与调度台展示）。"""
    try:
        documents = list(_collection().find({}, {'status': 1, 'unit': 1}))
    except PyMongoError as exc:
        raise OfficerError(f'统计警员失败：{exc}') from exc

    by_status: Dict[str, int] = {item.value: 0 for item in OfficerStatus}
    by_unit: Dict[str, int] = {}
    for item in documents:
        status = str(item.get('status') or OfficerStatus.OFF_DUTY.value)
        by_status[status] = by_status.get(status, 0) + 1
        unit = str(item.get('unit') or '未分配')
        by_unit[unit] = by_unit.get(unit, 0) + 1

    return OfficerStats(
        total=len(documents),
        on_duty=by_status.get(OfficerStatus.ON_DUTY.value, 0),
        busy=by_status.get(OfficerStatus.BUSY.value, 0),
        off_duty=by_status.get(OfficerStatus.OFF_DUTY.value, 0),
        leave=by_status.get(OfficerStatus.LEAVE.value, 0),
        by_unit=by_unit,
    )


# ==========================================================================
# 写入
# ==========================================================================

def create_officer(payload: OfficerCreate) -> Officer:
    """新增警员。警号重复时抛 :class:`OfficerError`。"""
    data = payload.model_dump(by_alias=False)
    data['skills'] = [item.value if isinstance(item, OfficerSkill) else item
                      for item in data.get('skills') or []]
    data['status'] = payload.status.value
    data['created_at'] = datetime.utcnow()
    data['updated_at'] = data['created_at']

    try:
        result = _collection().insert_one(data)
        data['_id'] = result.inserted_id
    except DuplicateKeyError as exc:
        raise OfficerError(f'警号已存在：{payload.officer_id}') from exc
    except PyMongoError as exc:
        raise OfficerError(f'新增警员失败：{exc}') from exc

    return _to_officer(data)


def update_officer(officer_id: str, payload: OfficerUpdate) -> Optional[Officer]:
    """部分更新警员（按警号定位）。未找到返回 ``None``。"""
    changes = payload.model_dump(by_alias=False, exclude_none=True)
    if not changes:
        return get_officer(officer_id)

    if 'skills' in changes:
        changes['skills'] = [
            item.value if isinstance(item, OfficerSkill) else item
            for item in changes['skills']
        ]
    if 'status' in changes and isinstance(changes['status'], OfficerStatus):
        changes['status'] = changes['status'].value

    changes['updated_at'] = datetime.utcnow()

    try:
        document = _collection().find_one_and_update(
            {OFFICER_ID: officer_id},
            {'$set': changes},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise OfficerError(f'更新警员失败：{exc}') from exc

    return _to_officer(document) if document else None


def delete_officer(officer_id: str) -> bool:
    """删除警员。返回是否真的删掉了一条。"""
    try:
        result = _collection().delete_one({OFFICER_ID: officer_id})
    except PyMongoError as exc:
        raise OfficerError(f'删除警员失败：{exc}') from exc
    return result.deleted_count > 0


def adjust_active_cases(officer_id: str, delta: int) -> None:
    """调整警员在办案件数（派警 +1，办结 -1）。

    刻意用 ``$inc`` 而非先读后写：并发派警时读改写会互相覆盖。
    """
    if not delta:
        return
    try:
        _collection().update_one(
            {OFFICER_ID: officer_id},
            {
                '$inc': {'active_cases': delta},
                '$set': {'updated_at': datetime.utcnow()},
            },
        )
    except PyMongoError as exc:
        raise OfficerError(f'更新在办案件数失败：{exc}') from exc


# ==========================================================================
# 示例数据
# ==========================================================================

# 河南主要城市坐标，用于生成分散的示例警员（与视频源所在区域一致）
_SEED_UNITS = [
    ('郑州交警一大队', 113.6254, 34.7466),
    ('洛阳交警二大队', 112.4540, 34.6197),
    ('开封交警三大队', 114.3074, 34.7972),
    ('南阳交警四大队', 112.5283, 32.9908),
    ('信阳交警五大队', 114.0919, 32.1470),
    ('新乡交警六大队', 113.9268, 35.3030),
]

_SEED_NAMES = [
    ('410001', '张伟', '一级警司', OfficerStatus.ON_DUTY,
     [OfficerSkill.ACCIDENT, OfficerSkill.INVESTIGATION]),
    ('410002', '李强', '二级警司', OfficerStatus.ON_DUTY,
     [OfficerSkill.TRAFFIC]),
    ('410003', '王芳', '三级警司', OfficerStatus.ON_DUTY,
     [OfficerSkill.FIRST_AID, OfficerSkill.ACCIDENT]),
    ('410004', '刘洋', '一级警员', OfficerStatus.BUSY,
     [OfficerSkill.TRAFFIC, OfficerSkill.HAZMAT]),
    ('410005', '陈静', '二级警司', OfficerStatus.ON_DUTY,
     [OfficerSkill.ACCIDENT]),
    ('410006', '赵敏', '三级警司', OfficerStatus.OFF_DUTY,
     [OfficerSkill.INVESTIGATION]),
    ('410007', '孙磊', '一级警员', OfficerStatus.ON_DUTY,
     [OfficerSkill.HAZMAT, OfficerSkill.FIRST_AID]),
    ('410008', '周涛', '二级警员', OfficerStatus.LEAVE,
     [OfficerSkill.TRAFFIC]),
    ('410009', '吴倩', '一级警司', OfficerStatus.ON_DUTY,
     [OfficerSkill.ACCIDENT, OfficerSkill.FIRST_AID]),
    ('410010', '郑凯', '三级警司', OfficerStatus.BUSY,
     [OfficerSkill.INVESTIGATION]),
    ('410011', '冯雪', '二级警员', OfficerStatus.ON_DUTY,
     [OfficerSkill.TRAFFIC, OfficerSkill.ACCIDENT]),
    ('410012', '褚鹏', '一级警员', OfficerStatus.ON_DUTY,
     [OfficerSkill.ACCIDENT]),
]


def seed_officers(reset: bool = False) -> int:
    """导入示例警员，返回新增条数。

    幂等：已存在的警号跳过。``reset=True`` 时先清空该集合，
    便于演示时恢复到干净状态。
    """
    try:
        collection = _collection()
        if reset:
            collection.delete_many({})

        existing = {item.get(OFFICER_ID) for item in collection.find({}, {OFFICER_ID: 1})}
        now = datetime.utcnow()
        documents: List[Dict[str, Any]] = []

        for index, (officer_id, name, rank, status, skills) in enumerate(_SEED_NAMES):
            if officer_id in existing:
                continue
            unit, lng, lat = _SEED_UNITS[index % len(_SEED_UNITS)]
            # 在同一城市内做小幅偏移，避免多个警员坐标完全重合
            offset = (index // len(_SEED_UNITS)) * 0.02
            documents.append({
                OFFICER_ID: officer_id,
                'name': name,
                'rank': rank,
                'unit': unit,
                'phone': f'138{index:08d}',
                'status': status.value,
                'skills': [item.value for item in skills],
                'latitude': round(lat + offset, 5),
                'longitude': round(lng + offset, 5),
                'region': unit[:2],
                'active_cases': 1 if status is OfficerStatus.BUSY else 0,
                'total_handled': 20 + index * 3,
                'avg_response_minutes': round(6 + (index % 5) * 1.5, 1),
                'note': '示例数据',
                'created_at': now,
                'updated_at': now,
            })

        if not documents:
            return 0

        collection.insert_many(documents, ordered=False)
        return len(documents)
    except PyMongoError as exc:
        raise OfficerError(f'导入示例警员失败：{exc}') from exc
