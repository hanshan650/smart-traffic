"""
持续检测接口（违停监控）
========================
+---------------------------------+------------------------------------------+
| ``GET  /status``                | 巡检运行状态与各路统计                    |
| ``POST /start``                 | 开始巡检                                  |
| ``POST /stop``                  | 停止巡检                                  |
| ``POST /cameras/{id}/toggle``   | 暂停 / 恢复某一路                         |
| ``GET  /alerts``                | 违停告警列表                              |
| ``GET  /alerts/stats``          | 告警概览                                  |
| ``GET  /alerts/{id}``           | 告警详情（含判据证据链）                  |
| ``POST /alerts/{id}/status``    | 告警处置（核验 / 误报 / 已处置）          |
| ``GET  /algorithm``             | 算法说明与各适配器通道状态                |
| ``POST /owner-lookup``          | 车主信息查询（脱敏 + 强制审计）           |
| ``GET  /audits``                | 查询审计记录                              |
| ``POST /plate/estimate``        | 车牌可读性估算（诊断）                    |
+---------------------------------+------------------------------------------+

路由顺序提醒
------------
``/alerts/stats`` 必须注册在 ``/alerts/{alert_id}`` **之前**。FastAPI 按声明
顺序匹配，否则 ``stats`` 会被当成 ``alert_id`` 吞掉 —— 这个坑在 officers
与 incidents 路由上已经踩过一次，此处沿用同样的顺序约定。
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.models.schemas import (
    AlertStatusRequest,
    OwnerLookupRequest,
    PlateEstimateRequest,
    SurveillanceStartRequest,
)
from app.services import (
    owner_lookup,
    plate_recognizer,
    scene_text,
    stall_detector,
    surveillance_service,
)
from app.services.owner_lookup import OwnerLookupError
from app.services.surveillance_service import surveillance

router = APIRouter(prefix='/surveillance', tags=['持续检测'])

#: 告警允许的处置状态
ALERT_STATUSES = frozenset({'pending', 'verified', 'dismissed', 'handled'})


# ==========================================================================
# 巡检控制
# ==========================================================================


@router.get('/status', summary='巡检状态')
def status() -> Dict[str, Any]:
    """返回巡检运行状态与每一路的统计。"""
    return {'success': True, **surveillance.status()}


@router.post('/start', summary='开始巡检')
def start(payload: SurveillanceStartRequest) -> Dict[str, Any]:
    """开始持续巡检。

    巡检会持续占用 CPU（单帧推理约 200ms），因此**不会**随服务自动启动，
    必须显式调用本接口。

    ``hints`` 用于声明点位名称（如"京港澳高速许昌服务区入口"），
    名称含"服务区""收费站"等关键词时该点位按可停车场所处理，不报违停。
    """
    result = surveillance.start(payload.camera_ids or None, payload.hints or None)
    if not result.get('started'):
        raise HTTPException(status_code=409, detail={'success': False, **result})
    return {'success': True, **result}


@router.post('/stop', summary='停止巡检')
def stop() -> Dict[str, Any]:
    result = surveillance.stop()
    if not result.get('stopped'):
        raise HTTPException(status_code=409, detail={'success': False, **result})
    return {'success': True, **result}


@router.post('/cameras/{camera_id}/toggle', summary='暂停/恢复单路巡检')
def toggle_camera(camera_id: str, enabled: bool = Query(True)) -> Dict[str, Any]:
    """暂停或恢复某一路巡检，不影响其他路。

    :param enabled: ``true`` 恢复，``false`` 暂停
    """
    try:
        watch = surveillance.set_enabled(camera_id, enabled)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': f'该摄像头不在巡检列表中：{camera_id}'},
        )
    return {'success': True, 'camera': watch}


# ==========================================================================
# 告警
# ==========================================================================


@router.get('/alerts/stats', summary='告警概览')
def alert_overview() -> Dict[str, Any]:
    """违停告警统计。注意本路由必须早于 ``/alerts/{alert_id}`` 注册。"""
    return {'success': True, **surveillance_service.alert_stats()}


@router.get('/alerts', summary='告警列表')
def list_alerts(
    limit: int = Query(50, ge=1, le=500),
    camera_id: str = Query('', description='按摄像头过滤'),
    status: str = Query('', description='pending / verified / dismissed / handled'),
) -> Dict[str, Any]:
    items = surveillance_service.list_alerts(
        limit=limit, camera_id=camera_id, status=status
    )
    return {'success': True, 'total': len(items), 'items': items}


@router.get('/alerts/{alert_id}', summary='告警详情')
def alert_detail(alert_id: str) -> Dict[str, Any]:
    record = surveillance_service.get_alert(alert_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail={'success': False, 'error': '告警不存在'}
        )
    return {'success': True, **record}


@router.post('/alerts/{alert_id}/status', summary='告警处置')
def update_alert(alert_id: str, payload: AlertStatusRequest) -> Dict[str, Any]:
    """标记告警的处置结果。"""
    if payload.status not in ALERT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={
                'success': False,
                'error': f'不支持的处置状态：{payload.status}',
                'allowed': sorted(ALERT_STATUSES),
            },
        )
    record = surveillance_service.set_alert_status(alert_id, payload.status, payload.note)
    if record is None:
        raise HTTPException(
            status_code=404, detail={'success': False, 'error': '告警不存在'}
        )
    return {'success': True, **record}


# ==========================================================================
# 算法与通道说明
# ==========================================================================


@router.get('/algorithm', summary='算法说明与通道状态')
def algorithm() -> Dict[str, Any]:
    """一次性返回静止检测的判据、参数，以及三个适配器通道的可用状态。

    这里刻意把**不可用的通道及其原因**一并返回，而不是只列可用的 ——
    使用者需要知道车牌识别为什么没结果，否则会误以为系统故障。
    """
    return {
        'success': True,
        'detection': stall_detector.describe(surveillance.stall_config()),
        'scheduler': {
            'intervalSeconds': settings.surveillance_interval,
            'frameCount': settings.surveillance_frame_count,
            'alertCooldownSeconds': settings.stall_alert_cooldown,
            'note': (
                '判定使用真实墙钟时间而非帧号推算 —— 巡检分批抓帧，'
                '轮次之间的空档若被忽略，30 秒阈值永远达不到'
            ),
        },
        'sceneText': {
            'current': settings.surveillance_text,
            'channels': scene_text.list_recognizers(),
        },
        'plate': {
            'current': settings.surveillance_plate_engine,
            'channels': plate_recognizer.list_recognizers(),
            'readableCharHeight': plate_recognizer.MIN_READABLE_CHAR_HEIGHT,
            'note': (
                '当前视频源 352x288，车牌字符高度实测不足 1 px，'
                '任何识别引擎都无法读取；需接入高分辨率卡口相机'
            ),
        },
        'ownerLookup': {
            'current': settings.surveillance_owner_provider,
            'channels': owner_lookup.list_providers(),
            'compliance': (
                '机动车所有人信息属受保护的个人信息：查询强制要求查询人与事由，'
                '全量写入审计，输出一律脱敏'
            ),
        },
        'plateEstimate': {
            'plateToVehicleWidth': plate_recognizer.PLATE_TO_VEHICLE_WIDTH,
            'plateAspect': round(plate_recognizer.PLATE_ASPECT, 3),
            'charHeightRatio': plate_recognizer.PLATE_CHAR_HEIGHT_RATIO,
        },
    }


@router.post('/plate/estimate', summary='车牌可读性估算')
def estimate_plate(payload: PlateEstimateRequest) -> Dict[str, Any]:
    """由车辆框尺寸估算车牌字符高度，用于判断某点位是否具备识别条件。

    这个接口的价值在于**在部署前就能回答**"这台相机够不够用"，
    而不必等接上识别引擎后才发现读不出来。
    """
    from app.models.schemas import BBox

    bbox = BBox(
        x=0.0,
        y=0.0,
        w=payload.bbox_width,
        h=payload.bbox_height,
    )
    char_height = plate_recognizer.estimate_plate_char_height(
        bbox, payload.frame_width, payload.frame_height
    )
    level, note = plate_recognizer.readability(char_height)
    return {
        'success': True,
        'estimatedCharHeight': round(char_height, 2),
        'readability': level,
        'readable': level == 'readable',
        'requiredCharHeight': plate_recognizer.MIN_READABLE_CHAR_HEIGHT,
        'message': note,
    }


# ==========================================================================
# 车主信息查询
# ==========================================================================


@router.post('/owner-lookup', summary='车主信息查询')
async def lookup_owner(payload: OwnerLookupRequest) -> Dict[str, Any]:
    """按车牌查询车主信息。

    **合规约束在服务层强制执行**，不依赖调用方自觉：

      · 缺查询人 / 事由 → 直接拒绝
      · 返回前统一脱敏，系统内没有输出明文证件号的代码路径
      · 每次查询（含失败）都写审计记录

    通道不可用时返回 ``available=false`` 与原因，而不是编造数据。
    """
    try:
        record, audit = await run_in_threadpool(
            owner_lookup.query_owner,
            payload.plate,
            operator=payload.operator,
            reason=payload.reason,
            purpose=payload.purpose,
            provider_key=payload.provider or settings.surveillance_owner_provider,
            incident_id=payload.alert_id,
            camera_id=payload.camera_id,
        )
    except OwnerLookupError as exc:
        raise HTTPException(
            status_code=400, detail={'success': False, 'error': str(exc)}
        )

    return {
        'success': True,
        'owner': record.to_dict(),
        'audit': {
            'at': audit.at.isoformat(),
            'operator': audit.operator,
            'reason': audit.reason,
            'outcome': audit.outcome,
            'persisted': audit.persisted,
        },
    }


@router.get('/audits', summary='查询审计记录')
def list_audits(
    limit: int = Query(50, ge=1, le=500),
    plate: str = Query('', description='按车牌过滤'),
) -> Dict[str, Any]:
    """查询车主信息查询的审计留痕。

    审计本身不脱敏车牌 —— 它的用途正是追溯"谁查了哪辆车"。
    但查询结果里的个人信息字段仍是脱敏后写入的。
    """
    items: List[Dict[str, Any]] = owner_lookup.list_audits(limit=limit, plate=plate)
    for item in items:
        at = item.get('at')
        if hasattr(at, 'isoformat'):
            item['at'] = at.isoformat()
    return {
        'success': True,
        'total': len(items),
        'items': items,
        'stats': owner_lookup.audit_stats(),
    }
