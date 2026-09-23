"""
车主信息查询（适配器 + 合规约束）
==================================

这个模块的性质和项目里其他模块不同
----------------------------------
其他模块处理的是视频、轨迹、评分 —— 算错了只是误报。本模块处理的是
**公民个人信息**。在中国法律框架下，机动车所有人信息属于受保护的
个人信息，查询行为受《个人信息保护法》与公安信息查询管理规定的约束。

因此这里把合规要求**写进代码结构**，而不是写在注释里：

1. **强制查询事由与查询人**。:func:`query_owner` 要求 ``operator`` 与
   ``reason`` 非空，缺失直接拒绝 —— 不允许"顺手查一下"。
2. **全量审计留痕**。每一次查询（含失败与不可用）都写入审计记录：
   谁、何时、查了哪个车牌、什么理由、结果如何、数据来自哪个通道。
   审计写入失败**不阻断**查询返回，但会在响应中标记 —— 留痕问题要暴露出来。
3. **输出脱敏**。返回的姓名、证件号、手机号一律脱敏。系统**没有**返回
   完整证件号的代码路径，即使上游返回了完整值，也会在 :func:`mask_*`
   中被打码。这是刻意设计：减少一条泄露路径，比多一条便利更重要。
4. **不伪造数据**。正式通道未接入时明确返回"不可用"，不返回编造的车主信息。

适配器通道
----------
  · ``none``（默认）    —— 未接入，返回不可用 + 接入条件说明
  · ``mock``            —— 生成确定性的模拟数据，**必须显式启用**，
                            输出带 ``simulated`` 标记，界面须显示
  · ``official``        —— 正式通道占位。实际接入通常是通过政务专网
                            调用公安交管部门的信息查询接口，需单位资质、
                            接口授权与数据使用协议

为什么默认不是 mock
-------------------
与车牌识别同样的理由：模拟数据格式正确，若被误当成真实信息用于出警，
后果比"查不到"严重得多。所以默认通道是"诚实地不可用"。
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.db.mongo import get_db

#: 审计集合名
AUDIT_COLLECTION = 'owner_query_audits'

#: 车牌脱敏后仍保留的字符数（前段与后段）
PLATE_KEEP_HEAD = 2
PLATE_KEEP_TAIL = 5

# ==========================================================================
# 脱敏
# ==========================================================================


def mask_name(name: str) -> str:
    """姓名脱敏：``张三`` → ``张*``，``欧阳修远`` → ``欧***``。"""
    if not name:
        return ''
    if len(name) == 1:
        return name
    return name[0] + '*' * (len(name) - 1)


def mask_id_number(id_number: str) -> str:
    """证件号脱敏：保留前 4 与后 4 位，中间一律打码。"""
    value = (id_number or '').strip()
    if len(value) <= 8:
        return '*' * len(value)
    return f'{value[:4]}{"*" * (len(value) - 8)}{value[-4:]}'


def mask_phone(phone: str) -> str:
    """手机号脱敏：保留前 3 与后 4 位。"""
    value = (phone or '').strip()
    if len(value) < 7:
        return '*' * len(value)
    return f'{value[:3]}{"*" * (len(value) - 7)}{value[-4:]}'


def mask_plate(plate: str) -> str:
    """车牌脱敏：仅保留前 2 与后 5 位。

    车牌本身是否算个人信息存在争议，但在公开页面上一律脱敏是更稳的选择。
    完整车牌只出现在需要作业的接口响应中（该接口本身有审计）。
    """
    value = (plate or '').strip()
    if len(value) <= PLATE_KEEP_HEAD + PLATE_KEEP_TAIL:
        return value
    middle = '*' * (len(value) - PLATE_KEEP_HEAD - PLATE_KEEP_TAIL)
    return f'{value[:PLATE_KEEP_HEAD]}{middle}{value[-PLATE_KEEP_TAIL:]}'


# ==========================================================================
# 数据结构
# ==========================================================================


@dataclass
class OwnerRecord:
    """车主信息查询结果。

    除 ``plate`` 外，所有个人字段**都是脱敏后的值**。
    """

    found: bool
    plate: str = ''
    owner_name: str = ''
    owner_id_masked: str = ''
    owner_phone_masked: str = ''
    vehicle_brand: str = ''
    vehicle_color: str = ''
    register_date: str = ''
    vehicle_type: str = ''
    #: 数据来源通道 key
    source: str = 'none'
    #: 是否为模拟数据
    simulated: bool = False
    message: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'found': self.found,
            'plate': self.plate,
            'ownerName': self.owner_name,
            'ownerIdMasked': self.owner_id_masked,
            'ownerPhoneMasked': self.owner_phone_masked,
            'vehicleBrand': self.vehicle_brand,
            'vehicleColor': self.vehicle_color,
            'registerDate': self.register_date,
            'vehicleType': self.vehicle_type,
            'source': self.source,
            'simulated': self.simulated,
            'message': self.message,
        }


@dataclass
class LookupAudit:
    """查询审计记录。字段设计围绕"事后可追溯"。"""

    at: datetime
    plate: str
    operator: str
    reason: str
    purpose: str
    source: str
    outcome: str            # found / not_found / unavailable / rejected
    message: str = ''
    incident_id: str = ''
    camera_id: str = ''
    #: 审计本身是否成功落库
    persisted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            'at': self.at,
            'plate': self.plate,
            'operator': self.operator,
            'reason': self.reason,
            'purpose': self.purpose,
            'source': self.source,
            'outcome': self.outcome,
            'message': self.message,
            'incidentId': self.incident_id,
            'cameraId': self.camera_id,
            'persisted': self.persisted,
        }


# ==========================================================================
# 审计写入
# ==========================================================================


def _persist_audit(audit: LookupAudit) -> bool:
    """写入审计记录。失败返回 False，由调用方在响应中标记。"""
    try:
        get_db()[AUDIT_COLLECTION].insert_one(audit.to_dict())
        return True
    except Exception:  # noqa: BLE001 - 审计失败不应中断查询
        return False


def list_audits(limit: int = 50, plate: str = '') -> List[Dict[str, Any]]:
    """查询审计记录（用于合规检查）。"""
    try:
        query: Dict[str, Any] = {}
        if plate:
            query['plate'] = plate
        cursor = get_db()[AUDIT_COLLECTION].find(query).sort('at', -1).limit(limit)
        records = []
        for document in cursor:
            document['_id'] = str(document.get('_id', ''))
            records.append(document)
        return records
    except Exception:  # noqa: BLE001
        return []


def audit_stats() -> Dict[str, Any]:
    """审计概览。"""
    try:
        collection = get_db()[AUDIT_COLLECTION]
        total = collection.count_documents({})

        by_outcome: Dict[str, int] = {}
        for item in collection.aggregate([{'$group': {'_id': '$outcome', 'count': {'$sum': 1}}}]):
            by_outcome[str(item['_id'])] = item['count']

        by_source: Dict[str, int] = {}
        for item in collection.aggregate([{'$group': {'_id': '$source', 'count': {'$sum': 1}}}]):
            by_source[str(item['_id'])] = item['count']

        return {'total': total, 'byOutcome': by_outcome, 'bySource': by_source}
    except Exception:  # noqa: BLE001
        return {'total': 0, 'byOutcome': {}, 'bySource': {}}


# ==========================================================================
# 适配器基类
# ==========================================================================


class OwnerLookupProvider(ABC):
    """车主信息查询适配器。

    实现方**只需负责取回原始数据**；脱敏与审计由 :func:`query_owner`
    统一处理，避免每个通道各写一套而漏掉某一处。
    """

    key: str = ''
    display_name: str = ''

    @abstractmethod
    def is_available(self) -> Tuple[bool, str]:
        """返回 ``(是否可用, 原因)``。"""

    @abstractmethod
    def _fetch(self, plate: str, hint: str = '') -> OwnerRecord:
        """取回原始车主记录（**此处的字段应为未脱敏原始值**）。"""

    def describe(self) -> Dict[str, Any]:
        available, reason = self.is_available()
        return {
            'key': self.key,
            'displayName': self.display_name,
            'available': available,
            'reason': reason,
        }


# ==========================================================================
# 通道 1：未接入（默认）
# ==========================================================================


class UnavailableOwnerLookup(OwnerLookupProvider):
    """未接入正式查询通道。"""

    key = 'none'
    display_name = '未接入（车主查询不可用）'

    def is_available(self) -> Tuple[bool, str]:
        return (
            False,
            '未接入车主信息查询通道。机动车所有人信息属于受保护的个人信息，'
            '需通过政务专网对接公安交管部门的信息查询服务，并具备单位资质、'
            '接口授权与数据使用协议',
        )

    def _fetch(self, plate: str, hint: str = '') -> OwnerRecord:
        return OwnerRecord(
            found=False,
            plate=plate,
            source=self.key,
            message='未接入车主信息查询通道',
        )


# ==========================================================================
# 通道 2：模拟（需显式启用）
# ==========================================================================


class MockOwnerLookup(OwnerLookupProvider):
    """生成确定性的模拟车主信息。

    同一车牌总是得到同一结果（基于哈希），便于演示"查到 → 联系车主 →
    到场处置"的完整链条。所有输出带 ``simulated=True``。
    """

    key = 'mock'
    display_name = '模拟车主信息（仅用于流程演示）'

    _SURNAMES = ('张', '王', '李', '赵', '刘', '陈', '杨', '黄', '周', '吴')
    _GIVEN = ('伟', '芳', '娜', '磊', '静', '强', '敏', '杰', '涛', '明')
    _BRANDS = ('大众', '丰田', '本田', '比亚迪', '吉利', '长安', '哈弗', '五菱')
    _COLORS = ('白', '黑', '银', '灰', '蓝', '红')
    _TYPES = ('小型轿车', '小型普通客车', '轻型货车', '重型货车')

    def is_available(self) -> Tuple[bool, str]:
        return (True, '模拟通道：生成确定性演示数据，输出带 simulated 标记')

    def _fetch(self, plate: str, hint: str = '') -> OwnerRecord:
        digest = hashlib.sha1(plate.encode('utf-8'))
        raw = digest.digest()

        name = self._SURNAMES[raw[0] % len(self._SURNAMES)] + self._GIVEN[raw[1] % len(self._GIVEN)]
        # 用哈希拼出格式正确的证件号与手机号，再交给统一的脱敏函数处理，
        # 这样脱敏逻辑走的是与正式通道完全相同的代码路径
        id_number = '4101' + ''.join(str(b % 10) for b in raw[2:10]) + '00' + f'{raw[10] % 100:02d}X'
        phone = f'1{raw[11] % 9 + 1}{(raw[12] * 256 + raw[13]) % 100000000:08d}'
        year = 2015 + raw[14] % 10

        return OwnerRecord(
            found=True,
            plate=plate,
            owner_name=name,
            owner_id_masked=mask_id_number(id_number),
            owner_phone_masked=mask_phone(phone),
            vehicle_brand=self._BRANDS[raw[15] % len(self._BRANDS)],
            vehicle_color=self._COLORS[raw[16] % len(self._COLORS)],
            vehicle_type=self._TYPES[raw[17] % len(self._TYPES)],
            register_date=f'{year}-{raw[18] % 12 + 1:02d}-{raw[19] % 28 + 1:02d}',
            source=self.key,
            simulated=True,
            message='【模拟数据】非真实车主信息，仅用于演示查询流程',
        )


# ==========================================================================
# 通道 3：正式通道（占位）
# ==========================================================================


class OfficialOwnerLookup(OwnerLookupProvider):
    """正式车主信息查询通道（占位）。

    接入要点（供实际部署时参考）：

    1. 网络：通常需经**政务专网 / 公安网**，互联网侧不可达；
    2. 资质：查询单位须具备相应权限，接口按单位与查询人授权；
    3. 协议：需签订数据使用协议，明确用途限制与留存期限；
    4. 留痕：上游通常要求上报查询事由与查询人，与本模块的审计设计一致。

    在完成上述接入前，本通道返回不可用 —— 不返回任何编造或推测的信息。
    """

    key = 'official'
    display_name = '正式车主查询通道（未接入）'

    def is_available(self) -> Tuple[bool, str]:
        return (
            False,
            '正式通道尚未接入。需通过政务专网对接公安交管部门信息查询服务，'
            '并完成单位资质、接口授权与数据使用协议',
        )

    def _fetch(self, plate: str, hint: str = '') -> OwnerRecord:
        return OwnerRecord(
            found=False,
            plate=plate,
            source=self.key,
            message='正式车主查询通道尚未接入',
        )


# ==========================================================================
# 对外主入口
# ==========================================================================

_PROVIDERS: Dict[str, OwnerLookupProvider] = {
    'none': UnavailableOwnerLookup(),
    'mock': MockOwnerLookup(),
    'official': OfficialOwnerLookup(),
}


def get_provider(key: str = '') -> OwnerLookupProvider:
    if key and key in _PROVIDERS:
        return _PROVIDERS[key]
    return _PROVIDERS['none']


def list_providers() -> List[Dict[str, Any]]:
    return [item.describe() for item in _PROVIDERS.values()]


class OwnerLookupError(ValueError):
    """查询被拒绝（参数不合规）。"""


def query_owner(
    plate: str,
    *,
    operator: str,
    reason: str,
    purpose: str = '违停告警核查',
    provider_key: str = '',
    incident_id: str = '',
    camera_id: str = '',
) -> Tuple[OwnerRecord, LookupAudit]:
    """查询车主信息。

    **这是唯一允许被外部调用的入口** —— 脱敏与审计都在这里统一执行。

    :param operator: 查询人（警号或工号）。必填，用于留痕。
    :param reason: 查询事由。必填 —— 无正当理由不得查询个人信息。
    :raises OwnerLookupError: ``plate`` / ``operator`` / ``reason`` 缺失
    """
    normalized = (plate or '').strip().upper()
    if not normalized:
        raise OwnerLookupError('车牌号不能为空')
    if not (operator or '').strip():
        raise OwnerLookupError('查询人不能为空：个人信息查询必须留痕')
    if not (reason or '').strip():
        raise OwnerLookupError('查询事由不能为空：无正当理由不得查询个人信息')

    provider = get_provider(provider_key)
    available, unavailable_reason = provider.is_available()

    if not available:
        record = OwnerRecord(
            found=False,
            plate=normalized,
            source=provider.key,
            message=unavailable_reason,
        )
        audit = LookupAudit(
            at=datetime.utcnow(),
            plate=normalized,
            operator=operator.strip(),
            reason=reason.strip(),
            purpose=purpose,
            source=provider.key,
            outcome='unavailable',
            message=unavailable_reason,
            incident_id=incident_id,
            camera_id=camera_id,
        )
        audit.persisted = _persist_audit(audit)
        return (record, audit)

    try:
        record = provider._fetch(normalized)
    except Exception as exc:  # noqa: BLE001 - 上游异常也要留痕
        audit = LookupAudit(
            at=datetime.utcnow(),
            plate=normalized,
            operator=operator.strip(),
            reason=reason.strip(),
            purpose=purpose,
            source=provider.key,
            outcome='error',
            message=f'查询失败：{exc}',
            incident_id=incident_id,
            camera_id=camera_id,
        )
        audit.persisted = _persist_audit(audit)
        return (OwnerRecord(found=False, plate=normalized, source=provider.key,
                            message=f'查询失败：{exc}'), audit)

    # ---- 统一的脱敏兜底：无论通道返回什么都再过一次 ----
    # 即使某个通道实现忘记脱敏、或上游返回了完整证件号，
    # 出口处的这道处理也能保证不会把明文交给前端。
    record.plate = normalized
    record.owner_name = mask_name(record.owner_name) if record.owner_name else ''
    record.owner_id_masked = (
        mask_id_number(record.owner_id_masked) if record.owner_id_masked else ''
    )
    record.owner_phone_masked = (
        mask_phone(record.owner_phone_masked) if record.owner_phone_masked else ''
    )

    audit = LookupAudit(
        at=datetime.utcnow(),
        plate=normalized,
        operator=operator.strip(),
        reason=reason.strip(),
        purpose=purpose,
        source=provider.key,
        outcome='found' if record.found else 'not_found',
        message=record.message,
        incident_id=incident_id,
        camera_id=camera_id,
    )
    audit.persisted = _persist_audit(audit)
    return (record, audit)
