"""
民众上报服务（众包信息）
========================

这个模块处理的是**来自普通民众的信息**。它的性质与系统内其他数据源不同：

  · **来源不可控**。AI 识别的误报有明确的统计特性，可以通过调参收敛；
    而人的上报可能是真实的、看错的、重复的、甚至恶意的。
  · **必须有人复核**。民众上报**不直接生成告警**，而是进入待审队列，
    由警务端确认后才转为正式事件。这是唯一能同时兼顾"鼓励参与"与
    "不污染告警体系"的做法。
  · **要有反馈**。上报后如果石沉大海，用户就不会再报第二次。因此每个
    上报都会生成可查询的编号，状态变更对上报人可见。

状态机
------
::

    pending ──approve──> approved ──> (已转为正式事件，含事件 id)
       │
       ├────reject────> rejected   (误报 / 无效，附驳回理由)
       │
       └────duplicate─> duplicate  (与既有上报重复)

防滥用
------
开放的上报入口必然会被滥用。这里做两层限制，且都**不引入新依赖**：

1. **频率限制**：同一 ``client_key``（客户端标识，默认取 IP）在滑动窗口内
   最多上报 ``citizen_report_rate_limit`` 条。窗口内超出的直接拒绝。
2. **内容长度限制**：描述与联系方式都有长度上限，避免超大文本灌库。

频率限制用 MongoDB 计数实现而非 Redis —— 上报是低频操作，
用不上 Redis 的性能，而少一个依赖就少一处会挂的地方。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pymongo.errors import PyMongoError

from app.core.config import settings
from app.db import mongo
from app.models.schemas import EventLevel, EventType
from app.services import event_service

#: 上报集合
REPORT_COLLECTION = 'citizen_reports'

#: 上报编号前缀（民众上报）
REPORT_PREFIX = 'MZ'

#: 面向民众的上报类型。
#:
#: 刻意**不直接复用内部的 ``EventType``** —— 内部枚举是按算法可检测性划分的
#: （collision / abnormal_stop / wrong_way ...），而民众看到的是生活语言。
#: 两者混用会导致要么民众看不懂选项，要么内部数据里混入无法参与算法的事件。
CITIZEN_REPORT_TYPES: Dict[str, str] = {
    'accident': '交通事故',
    'congestion': '交通拥堵',
    'debris': '路面障碍物',
    'road_damage': '路面损坏',
    'construction': '施工占道',
    'weather': '恶劣天气 / 积水',
    'illegal_parking': '违法停车',
    'other': '其他情况',
}

#: 民众上报类型 → 内部事件类型。用于复核通过后转为正式事件。
#: 未列出的类型统一落到 ``debris``（最泛化的"路面异常"）——
#: 宁可归类粗一点，也不要为了分类好看而编造不存在的语义。
_TYPE_MAPPING: Dict[str, EventType] = {
    'accident': EventType.COLLISION,
    'congestion': EventType.CONGESTION,
    'debris': EventType.DEBRIS,
    'road_damage': EventType.DEBRIS,
    'construction': EventType.DEBRIS,
    'weather': EventType.DEBRIS,
    'illegal_parking': EventType.ABNORMAL_STOP,
    'other': EventType.DEBRIS,
}

#: 上报类型 → 默认紧急级别（复核人可覆盖）
_LEVEL_MAPPING: Dict[str, EventLevel] = {
    'accident': EventLevel.CRITICAL,
    'congestion': EventLevel.WARNING,
    'debris': EventLevel.CRITICAL,
    'road_damage': EventLevel.WARNING,
    'construction': EventLevel.INFO,
    'weather': EventLevel.WARNING,
    'illegal_parking': EventLevel.INFO,
    'other': EventLevel.INFO,
}

#: 状态 → 面向民众的说明文案
STATUS_LABEL: Dict[str, str] = {
    'pending': '已收到，等待核实',
    'approved': '已核实，已转入正式处置流程',
    'rejected': '经核实未采纳',
    'duplicate': '与已有上报重复',
}


class CitizenReportError(ValueError):
    """上报被拒绝（参数或频率不合规）。"""


# ==========================================================================
# 限流
# ==========================================================================


def _window_start() -> datetime:
    return datetime.utcnow() - timedelta(hours=settings.citizen_report_window_hours)


def check_rate_limit(client_key: str) -> tuple[bool, str]:
    """检查是否超出上报频率上限。

    :return: ``(是否允许, 拒绝原因)``
    """
    limit = settings.citizen_report_rate_limit
    if limit <= 0:
        return (True, '')

    try:
        recent = mongo.get_db()[REPORT_COLLECTION].count_documents({
            'client_key': client_key,
            'created_at': {'$gte': _window_start()},
        })
    except PyMongoError:
        # 计数失败时放行：宁可放过一次上报，也不要因为数据库抖动
        # 让真实的事故线索报不上来
        return (True, '')

    if recent >= limit:
        return (
            False,
            f'{settings.citizen_report_window_hours} 小时内上报已达上限（{limit} 条），请稍后再试',
        )
    return (True, '')


def client_key_from(ip: str, user_agent: str = '') -> str:
    """由 IP 与 UA 生成客户端标识。

    用哈希而非明文保存：频率限制只需要"区分不同客户端"，
    不需要知道具体是谁 —— 少存一点个人信息总是好的。
    """
    digest = hashlib.sha256(f'{ip}|{user_agent}'.encode('utf-8')).hexdigest()
    return digest[:32]


# ==========================================================================
# 编号
# ==========================================================================


def _next_report_no() -> str:
    """生成 ``MZ{日期}{序号}`` 形式的上报编号。"""
    today = datetime.utcnow().strftime('%Y%m%d')
    prefix = f'{REPORT_PREFIX}{today}'
    try:
        count = mongo.get_db()[REPORT_COLLECTION].count_documents({
            'report_no': {'$regex': f'^{prefix}'},
        })
    except PyMongoError:
        count = 0
    return f'{prefix}{count + 1:04d}'


# ==========================================================================
# 脱敏
# ==========================================================================


def _round_coordinate(value: Optional[float]) -> Optional[float]:
    """坐标降精度到约 100 米。

    民众端展示路况需要位置，但没有理由给出米级精度 —— 精确坐标配合
    上报时间可以推断出具体点位，属于不必要的信息暴露。3 位小数
    （约 111 m）对"附近哪儿堵了"完全够用。
    """
    if value is None:
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _contact_masked(contact: str) -> str:
    """联系方式脱敏。对外一律只显示打码后的形式。"""
    value = (contact or '').strip()
    if not value:
        return ''
    if '@' in value:
        name, _, domain = value.partition('@')
        head = name[:2] if len(name) > 2 else name[:1]
        return f'{head}***@{domain}'
    if len(value) >= 7:
        return f'{value[:3]}****{value[-4:]}'
    return '*' * len(value)


# ==========================================================================
# 数据结构
# ==========================================================================


@dataclass
class CitizenReport:
    """一条民众上报。

    ``public_dict`` 与 ``internal_dict`` 的区分是刻意的：前者给民众端和
    任何公开渠道，后者给警务端。联系方式**只出现在内部视图**里。
    """

    report_no: str
    report_type: str
    description: str
    location_text: str
    latitude: Optional[float]
    longitude: Optional[float]
    road_name: str
    contact: str
    image_url: str
    status: str = 'pending'
    level: str = ''
    #: 复核意见（对民众可见 —— 让他们知道为什么没采纳）
    review_note: str = ''
    reviewed_by: str = ''
    reviewed_at: Optional[datetime] = None
    #: 复核通过后生成的正式事件 id
    event_id: str = ''
    created_at: datetime = field(default_factory=datetime.utcnow)
    id: str = ''

    def public_dict(self) -> Dict[str, Any]:
        """面向民众的视图：脱敏、降精度、不含联系方式。"""
        return {
            'reportNo': self.report_no,
            'reportType': self.report_type,
            'reportTypeLabel': CITIZEN_REPORT_TYPES.get(self.report_type, self.report_type),
            'description': self.description,
            'locationText': self.location_text,
            'roadName': self.road_name,
            'latitude': _round_coordinate(self.latitude),
            'longitude': _round_coordinate(self.longitude),
            'imageUrl': self.image_url,
            'status': self.status,
            'statusLabel': STATUS_LABEL.get(self.status, self.status),
            'reviewNote': self.review_note,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'reviewedAt': self.reviewed_at.isoformat() if self.reviewed_at else None,
        }

    def internal_dict(self) -> Dict[str, Any]:
        """面向警务的视图：完整信息，含未脱敏联系方式。"""
        data = self.public_dict()
        data.update({
            'id': self.id,
            'contact': self.contact,
            'contactMasked': _contact_masked(self.contact),
            'level': self.level,
            'reviewedBy': self.reviewed_by,
            'eventId': self.event_id,
        })
        return data


def _to_report(document: Dict[str, Any]) -> CitizenReport:
    return CitizenReport(
        id=str(document.get('_id', '')),
        report_no=document.get('report_no', ''),
        report_type=document.get('report_type', 'other'),
        description=document.get('description', ''),
        location_text=document.get('location_text', ''),
        latitude=document.get('latitude'),
        longitude=document.get('longitude'),
        road_name=document.get('road_name', ''),
        contact=document.get('contact', ''),
        image_url=document.get('image_url', ''),
        status=document.get('status', 'pending'),
        level=document.get('level', ''),
        review_note=document.get('review_note', ''),
        reviewed_by=document.get('reviewed_by', ''),
        reviewed_at=document.get('reviewed_at'),
        event_id=document.get('event_id', ''),
        created_at=document.get('created_at') or datetime.utcnow(),
    )


# ==========================================================================
# 创建
# ==========================================================================


def create_report(
    *,
    report_type: str,
    description: str = '',
    location_text: str = '',
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    road_name: str = '',
    contact: str = '',
    image_url: str = '',
    client_key: str = '',
) -> CitizenReport:
    """创建一条民众上报。

    :raises CitizenReportError: 类型非法 / 描述或位置为空 / 超出长度限制 /
        触发频率限制
    """
    if report_type not in CITIZEN_REPORT_TYPES:
        raise CitizenReportError(
            f'不支持的上报类型：{report_type}；'
            f'可选：{"、".join(CITIZEN_REPORT_TYPES)}'
        )

    description = (description or '').strip()
    location_text = (location_text or '').strip()

    # 要求"描述与位置至少有其一"：两者全空的报单无法核实，
    # 只会给复核人员制造无效工作量
    if not description and not location_text:
        raise CitizenReportError('请至少填写情况描述或具体位置，否则无法核实')

    if len(description) > settings.citizen_report_max_description:
        raise CitizenReportError(
            f'描述过长（上限 {settings.citizen_report_max_description} 字）'
        )
    if len(contact) > settings.citizen_report_max_contact:
        raise CitizenReportError(
            f'联系方式过长（上限 {settings.citizen_report_max_contact} 字）'
        )

    allowed, reason = check_rate_limit(client_key)
    if not allowed:
        raise CitizenReportError(reason)

    document: Dict[str, Any] = {
        'report_no': _next_report_no(),
        'report_type': report_type,
        'description': description,
        'location_text': location_text,
        'latitude': latitude,
        'longitude': longitude,
        'road_name': road_name.strip(),
        'contact': contact.strip(),
        'image_url': image_url,
        'status': 'pending',
        'level': _LEVEL_MAPPING.get(report_type, EventLevel.INFO).value,
        'review_note': '',
        'reviewed_by': '',
        'reviewed_at': None,
        'event_id': '',
        'client_key': client_key,
        'created_at': datetime.utcnow(),
    }

    try:
        result = mongo.get_db()[REPORT_COLLECTION].insert_one(document)
    except PyMongoError as exc:
        raise CitizenReportError(f'上报写入失败：{exc}') from exc

    document['_id'] = result.inserted_id
    return _to_report(document)


# ==========================================================================
# 查询
# ==========================================================================


def get_report(report_no: str) -> Optional[CitizenReport]:
    """按上报编号查询（民众端用）。

    **不允许按数据库 id 查询** —— 民众只应通过自己拿到的编号访问，
    这样接口不会变成一个可以遍历的列表。
    """
    try:
        document = mongo.get_db()[REPORT_COLLECTION].find_one({'report_no': report_no.strip()})
    except PyMongoError:
        return None
    return _to_report(document) if document else None


def list_reports(
    *,
    status: str = '',
    limit: int = 50,
    skip: int = 0,
) -> List[CitizenReport]:
    """列出上报（警务端复核队列用）。"""
    query: Dict[str, Any] = {}
    if status:
        query['status'] = status
    try:
        cursor = (
            mongo.get_db()[REPORT_COLLECTION]
            .find(query)
            .sort('created_at', -1)
            .skip(skip)
            .limit(limit)
        )
        return [_to_report(item) for item in cursor]
    except PyMongoError:
        return []


def count_reports(status: str = '') -> int:
    query: Dict[str, Any] = {}
    if status:
        query['status'] = status
    try:
        return mongo.get_db()[REPORT_COLLECTION].count_documents(query)
    except PyMongoError:
        return 0


def report_stats() -> Dict[str, Any]:
    """上报概览。按类型与状态分组，便于看出哪类问题报得最多。"""
    try:
        collection = mongo.get_db()[REPORT_COLLECTION]
        by_status: Dict[str, int] = {}
        for item in collection.aggregate([{'$group': {'_id': '$status', 'count': {'$sum': 1}}}]):
            by_status[str(item['_id'])] = item['count']

        by_type: Dict[str, int] = {}
        for item in collection.aggregate([{'$group': {'_id': '$report_type', 'count': {'$sum': 1}}}]):
            by_type[str(item['_id'])] = item['count']

        return {
            'total': collection.count_documents({}),
            'byStatus': by_status,
            'byType': by_type,
            'pending': by_status.get('pending', 0),
        }
    except PyMongoError:
        return {'total': 0, 'byStatus': {}, 'byType': {}, 'pending': 0}


# ==========================================================================
# 复核
# ==========================================================================

#: 复核动作 → 目标状态
_REVIEW_ACTIONS = {
    'approve': 'approved',
    'reject': 'rejected',
    'duplicate': 'duplicate',
}


def review_report(
    report_no: str,
    *,
    action: str,
    reviewer: str,
    note: str = '',
    level: str = '',
) -> Optional[CitizenReport]:
    """复核一条上报。

    ``approve`` 会把上报**转为正式事件** —— 这是民众参与真正产生作用的
    那一步。转事件时不沿用自动限流（``throttle_seconds=0``）：该上报已经
    过人工确认，不应被面向自动告警的限流窗口吞掉。

    :raises CitizenReportError: 动作非法 / 未填复核人 / 状态不允许复核
    """
    if action not in _REVIEW_ACTIONS:
        raise CitizenReportError(
            f'不支持的复核动作：{action}；可选：{"、".join(_REVIEW_ACTIONS)}'
        )
    if not (reviewer or '').strip():
        raise CitizenReportError('复核人不能为空：上报复核必须留痕')

    report = get_report(report_no)
    if report is None:
        return None

    if report.status != 'pending':
        raise CitizenReportError(
            f'该上报已处理（当前状态：{STATUS_LABEL.get(report.status, report.status)}），'
            '不可重复复核'
        )

    target_status = _REVIEW_ACTIONS[action]
    event_id = ''

    if action == 'approve':
        resolved_level = level or report.level or EventLevel.WARNING.value
        try:
            level_enum = EventLevel(resolved_level)
        except ValueError:
            level_enum = EventLevel.WARNING

        title = f'民众上报：{CITIZEN_REPORT_TYPES.get(report.report_type, report.report_type)}'
        # 描述直接使用上报内容，**不附加任何内部提示**。
        #
        # 曾经这里拼过一句「（上报人联系方式见上报记录）」给值班员看 ——
        # 但事件描述会进入公开路况接口，那句话就跟着暴露给了所有民众：
        # 既让人困惑，也泄露了内部处理流程。联系方式本来就在
        # citizen_reports 里，值班员打开上报详情即可看到，不需要在
        # 事件描述里提示。
        #
        # 教训：给"内部读者"写的提示不能混进会被复用的字段里 ——
        # 同一个字段往往同时用于内部和对外展示。
        description = report.description or report.location_text

        try:
            event = event_service.create_event(
                event_type=_TYPE_MAPPING.get(report.report_type, EventType.DEBRIS),
                level=level_enum,
                confidence=settings.citizen_report_confidence,
                title=title,
                description=description,
                camera_id='',            # 民众上报没有摄像头归属
                camera_name=report.location_text,
                road_name=report.road_name,
                source_key='citizen',
                snapshot_url=report.image_url,
                throttle_seconds=0,      # 人工已确认，不套用自动限流
                # 带上上报时的坐标，让事件能在地图上定位。
                # 上报人未提供定位时为 None，地图会跳过该点。
                latitude=report.latitude,
                longitude=report.longitude,
            )
            event_id = event.id if event else ''
        except RuntimeError:
            # 转事件失败不应让复核动作整体失败：上报已核实这一事实
            # 本身就要记录，事件可以后续补转
            event_id = ''

    changes: Dict[str, Any] = {
        'status': target_status,
        'review_note': note.strip(),
        'reviewed_by': reviewer.strip(),
        'reviewed_at': datetime.utcnow(),
        'event_id': event_id,
    }
    if level:
        changes['level'] = level

    try:
        mongo.get_db()[REPORT_COLLECTION].update_one(
            {'report_no': report_no.strip()}, {'$set': changes}
        )
    except PyMongoError as exc:
        raise CitizenReportError(f'复核写入失败：{exc}') from exc

    return get_report(report_no)


def describe() -> Dict[str, Any]:
    """自描述：类型选项、状态流转与限制，供前端与说明页渲染。"""
    return {
        'reportTypes': [
            {'key': key, 'label': label, 'defaultLevel': _LEVEL_MAPPING[key].value}
            for key, label in CITIZEN_REPORT_TYPES.items()
        ],
        'statuses': STATUS_LABEL,
        'transitions': {
            'pending': list(_REVIEW_ACTIONS),
        },
        'limits': {
            'maxDescription': settings.citizen_report_max_description,
            'maxContact': settings.citizen_report_max_contact,
            'rateLimit': settings.citizen_report_rate_limit,
            'windowHours': settings.citizen_report_window_hours,
        },
    }
