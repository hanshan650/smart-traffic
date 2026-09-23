"""
民众端公开接口
==============
+-----------------------------+-----------------------------------------------+
| ``GET  /overview``          | 路况概览（首页用）                             |
| ``GET  /traffic``           | 实时路况列表                                   |
| ``GET  /traffic/rank``      | 拥堵路段排行                                   |
| ``GET  /advice``            | 出行建议（基于实时数据，**非导航**）           |
| ``POST /reports``           | 提交上报（图片 + 表单）                        |
| ``GET  /reports/{no}``      | 按上报编号查进度                               |
| ``GET  /options``           | 上报类型等选项（自描述）                       |
| ``GET  /emergency``         | 应急联系方式与安全须知                         |
+-----------------------------+-----------------------------------------------+

数据隔离原则
------------
本模块是**唯一面向非授权用户的接口**，因此隔离必须做在**路由层**，
而不是依赖前端"记得不要显示某个字段"—— 后者一旦漏改就是数据泄露。

对外**不返回**的内容及理由：

  ====================  ==================================================
  字段                  不返回的理由
  ====================  ==================================================
  ``camera_id``         可用于精确关联到具体摄像头，属基础设施信息
  ``confidence``        算法内部指标，对民众无意义，且容易被误读为"可信度"
  ``reviewed_by``       值班员姓名，属内部人员信息
  ``snapshot_url``      AI 快照含真实道路画面，可能带车牌与行人面部；
                        公开渠道发布存在个人信息风险，且民众看路况
                        并不需要看图
  ``source_key``        视频源标识，属接入细节
  数据库 ``id``         避免接口变成可遍历的入口
  ====================  ==================================================

坐标统一降精度到 3 位小数（约 100 m）—— 够表达"附近哪里堵了"，
又不足以定位到具体车道。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.db import redis_client
from app.models.schemas import EventStatus
from app.services import citizen_report_service, event_service
from app.services.citizen_report_service import CitizenReportError

router = APIRouter(prefix='/public', tags=['民众端'])

#: 允许上传的图片格式
ALLOWED_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}

#: 事件类型 → 面向民众的中文标签
EVENT_TYPE_LABEL: Dict[str, str] = {
    'collision': '交通事故',
    'abnormal_stop': '车辆异常停车',
    'congestion': '交通拥堵',
    'wrong_way': '车辆逆行',
    'debris': '路面障碍物',
    'pedestrian': '行人闯入',
}

#: 紧急级别 → 中文标签
LEVEL_LABEL: Dict[str, str] = {
    'info': '提示',
    'warning': '注意',
    'critical': '严重',
}

#: 拥堵等级 → 中文标签
CONGESTION_LABEL: Dict[str, str] = {
    'normal': '畅通',
    'light': '缓行',
    'moderate': '拥堵',
    'heavy': '严重拥堵',
}


# ==========================================================================
# 脱敏
# ==========================================================================


def _round_coordinate(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _public_event(event: Any) -> Dict[str, Any]:
    """把内部事件转为公开视图。

    白名单式构造 —— 逐个挑出可公开的字段，而不是从完整字典里删除敏感字段。
    这样将来内部事件结构新增字段时，**默认不会被带出去**，
    安全性随字段增加而不会退化。

    坐标经过降精度（见 :func:`_round_coordinate`），且仅用于在地图上
    标出大致位置。事件数据在模型层面就自带坐标，不靠反查摄像头 ——
    公开接口本来也不允许返回摄像头列表。
    """
    return {
        'eventType': event.event_type.value,
        'eventTypeLabel': EVENT_TYPE_LABEL.get(event.event_type.value, event.event_type.value),
        'level': event.level.value,
        'levelLabel': LEVEL_LABEL.get(event.level.value, event.level.value),
        'title': event.title,
        'description': event.description,
        'roadName': event.road_name,
        'congestionLevel': event.congestion_level.value,
        'congestionLabel': CONGESTION_LABEL.get(
            event.congestion_level.value, event.congestion_level.value
        ),
        'latitude': _round_coordinate(event.latitude),
        'longitude': _round_coordinate(event.longitude),
        'createdAt': event.created_at.isoformat() if event.created_at else None,
    }


def _public_rank_item(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """把 Redis 里的路况快照转为公开视图。

    数据来源是 ``snapshot:{camera_id}`` 缓存（由检测流水线写入），
    它包含路段名、车辆数与拥堵等级 —— 而拥堵排行 Sorted Set 里
    只有 ``road_id`` 和分数，拿不到能展示给民众的内容。
    """
    level = snapshot.get('congestionLevel') or 'normal'
    return {
        'roadName': snapshot.get('roadName') or snapshot.get('cameraName') or '未知路段',
        'vehicleCount': snapshot.get('totalVehicles', 0),
        'congestionLevel': level,
        'congestionLabel': CONGESTION_LABEL.get(level, level),
        'latitude': _round_coordinate(snapshot.get('latitude')),
        'longitude': _round_coordinate(snapshot.get('longitude')),
        'updatedAt': snapshot.get('updatedAt'),
    }


def _congestion_snapshots(limit: int = 50) -> List[Dict[str, Any]]:
    """取当前路况快照，按车辆数降序。无 Redis 时返回空列表。

    通过 ``snapshot_index`` 名单逐条读取，而不是扫描 key 空间 ——
    摄像头数量是有限的，即使名单里残留了已失效的条目，逐条 ``GET``
    也只是一次往返的代价，且不会随 Redis 里无关 key 的增多而变慢。
    """
    camera_ids = redis_client.smembers('snapshot_index')
    snapshots: List[Dict[str, Any]] = []
    for camera_id in camera_ids:
        payload = redis_client.get_json(redis_client.make_key('snapshot', camera_id))
        if isinstance(payload, dict):
            snapshots.append(payload)
    snapshots.sort(key=lambda item: item.get('totalVehicles', 0), reverse=True)
    return snapshots[:limit]


# ==========================================================================
# 路况
# ==========================================================================


@router.get('/traffic', summary='实时路况')
def public_traffic(
    limit: int = Query(30, ge=1, le=200),
    level: str = Query('', description='按紧急级别过滤：info / warning / critical'),
) -> Dict[str, Any]:
    """公开的实时路况事件列表。

    只返回已确认或待复核的**非误报**事件 —— 误报（``rejected``）
    与已归档的不应出现在面向公众的信息流里。
    """
    events: List[Any] = []
    for status in (EventStatus.CONFIRMED, EventStatus.PENDING):
        # list_events 返回 (总数, 列表) 元组，不是列表
        _total, items = event_service.list_events(status=status, limit=limit)
        events.extend(items)

    if level:
        events = [item for item in events if item.level.value == level]

    events.sort(key=lambda item: item.created_at, reverse=True)
    events = events[:limit]

    items = [_public_event(item) for item in events]
    return {
        'success': True,
        'total': len(items),
        'items': items,
        'generatedAt': datetime.utcnow().isoformat(),
    }


@router.get('/traffic/rank', summary='拥堵路段排行')
def public_rank(limit: int = Query(10, ge=1, le=50)) -> Dict[str, Any]:
    """当前最拥堵的路段排行，用于出行建议页。"""
    items = [_public_rank_item(item) for item in _congestion_snapshots(limit)]
    # 只保留有实际车辆的，避免把"刚重置还没数据"的点位也列进去
    items = [item for item in items if item['vehicleCount'] > 0]
    return {'success': True, 'total': len(items), 'items': items[:limit]}


@router.get('/overview', summary='路况概览')
def public_overview() -> Dict[str, Any]:
    """首页用的汇总数字。

    **刻意只给数量与等级分布，不给精确到点位的明细** —— 明细在
    ``/traffic`` 里已经有了，概览的作用是让人一眼看出"今天路况如何"。
    """
    counts = {'critical': 0, 'warning': 0, 'info': 0}
    for status in (EventStatus.CONFIRMED, EventStatus.PENDING):
        # 注意：list_events 返回 (总数, 列表) 元组 —— 不解包会直接抛错
        _total, items = event_service.list_events(status=status, limit=200)
        for item in items:
            key = item.level.value
            counts[key] = counts.get(key, 0) + 1

    congestion = {'normal': 0, 'light': 0, 'moderate': 0, 'heavy': 0}
    for item in _congestion_snapshots(100):
        key = item.get('congestionLevel') or 'normal'
        congestion[key] = congestion.get(key, 0) + 1

    total = sum(counts.values())
    # 给一个整体印象标签，避免让用户自己去解读四个数字
    if counts.get('critical', 0) > 0:
        overall = 'attention'
        overall_label = '有严重事件，请注意绕行'
    elif congestion.get('heavy', 0) + congestion.get('moderate', 0) > 0:
        overall = 'busy'
        overall_label = '部分路段拥堵'
    else:
        overall = 'smooth'
        overall_label = '整体通行顺畅'

    return {
        'success': True,
        'overall': overall,
        'overallLabel': overall_label,
        'eventCounts': counts,
        'eventTotal': total,
        'congestionCounts': congestion,
        'generatedAt': datetime.utcnow().isoformat(),
    }


@router.get('/advice', summary='出行建议')
def public_advice(limit: int = Query(5, ge=1, le=20)) -> Dict[str, Any]:
    """基于实时数据给出**提示性**建议。

    这是刻意的定位说明：本接口**不是导航**。它做的是「把当前最堵的
    几个路段列出来，建议避开」，而不做路径规划 —— 真正的路线规划
    需要路网拓扑与实时速度数据，那超出本系统的数据范围。

    与其给出一个看起来像导航、实际不靠谱的结果，不如如实说明
    它是什么。
    """
    try:
        snapshots = _congestion_snapshots(limit)
    except Exception:  # noqa: BLE001
        snapshots = []

    avoid: List[Dict[str, Any]] = []
    for item in snapshots:
        level_value = item.get('congestionLevel') or 'normal'
        if level_value in ('normal', 'light'):
            continue
        avoid.append({
            'roadName': item.get('roadName') or item.get('cameraName') or '未知路段',
            # 同时给出 key 与中文标签：前端按 key 配色（稳定），
            # 按标签做展示（可读）。只给其中一个都会让另一端去猜。
            'congestionLevel': level_value,
            'congestionLabel': CONGESTION_LABEL.get(level_value, level_value),
            'vehicleCount': item.get('totalVehicles', 0),
            'latitude': _round_coordinate(item.get('latitude')),
            'longitude': _round_coordinate(item.get('longitude')),
            'suggestion': (
                '建议提前规划替代路线'
                if level_value == 'heavy'
                else '预计通行缓慢，请预留时间'
            ),
        })

    return {
        'success': True,
        'disclaimer': (
            '本建议基于监测点的实时车流数据生成，**不是导航**，'
            '不提供路径规划。出行请以实际路况与交管部门发布为准。'
        ),
        'avoid': avoid,
        'generatedAt': datetime.utcnow().isoformat(),
    }


# ==========================================================================
# 民众上报
# ==========================================================================


@router.get('/options', summary='上报选项（自描述）')
def public_options() -> Dict[str, Any]:
    """返回上报类型、状态说明与限制，供前端渲染表单。"""
    return {'success': True, **citizen_report_service.describe()}


@router.post('/reports', summary='提交上报')
async def create_report(
    request: Request,
    report_type: str = Form(...),
    description: str = Form(''),
    location_text: str = Form(''),
    road_name: str = Form(''),
    contact: str = Form(''),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    image: Optional[UploadFile] = File(None),
) -> Dict[str, Any]:
    """提交一条民众上报。

    上报**不会直接生成告警** —— 它进入待复核队列，由警务端确认后
    才转为正式事件。这样既鼓励参与，又不会让未核实的信息污染告警体系。

    图片可选。若有图片会保存到上传目录并记录 URL；民众凭上报编号
    可以查回自己上传的图片。
    """
    image_url = ''
    if image is not None and image.filename:
        suffix = Path(image.filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail={
                    'success': False,
                    'error': f'不支持的图片格式：{suffix or "未知"}；'
                             f'支持 {", ".join(sorted(ALLOWED_SUFFIXES))}',
                },
            )
        settings.upload_path.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime('%Y%m%d%H%M%S%f')
        target = settings.upload_path / f'citizen_{stamp}{suffix}'
        target.write_bytes(await image.read())
        image_url = '/static/uploads/' + target.name

    # 频率限制的客户端标识：优先取代理头，兼容反向代理部署
    forwarded = request.headers.get('x-forwarded-for', '')
    client_ip = forwarded.split(',')[0].strip() if forwarded else (request.client.host if request.client else '')
    client_key = citizen_report_service.client_key_from(
        client_ip, request.headers.get('user-agent', '')
    )

    try:
        report = await run_in_threadpool(
            citizen_report_service.create_report,
            report_type=report_type,
            description=description,
            location_text=location_text,
            latitude=latitude,
            longitude=longitude,
            road_name=road_name,
            contact=contact,
            image_url=image_url,
            client_key=client_key,
        )
    except CitizenReportError as exc:
        raise HTTPException(status_code=400, detail={'success': False, 'error': str(exc)})

    # 提交后返回编号 —— 这是民众查询进度的唯一凭据，必须醒目标注
    return {
        'success': True,
        'reportNo': report.report_no,
        'message': (
            f'上报已收到，编号 {report.report_no}。'
            '请记录该编号，可凭它查询处理进度。'
        ),
        'report': report.public_dict(),
    }


@router.get('/reports/{report_no}', summary='查询上报进度')
def get_report(report_no: str) -> Dict[str, Any]:
    """按上报编号查询处理进度。

    必须使用编号查询，而不是数据库 id —— 这样接口不会变成
    一个可以顺序遍历、窥探他人上报的入口。
    """
    report = citizen_report_service.get_report(report_no)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': f'未找到编号为 {report_no} 的上报记录'},
        )
    return {'success': True, 'report': report.public_dict()}


# ==========================================================================
# 应急信息
# ==========================================================================

#: 应急联系方式。
#:
#: 这些是**真实的公共服务号码**，写死在代码里而不是做成可配置项 ——
#: 报错一个电话号码的代价远大于"灵活配置"带来的便利。
EMERGENCY_CONTACTS = [
    {'name': '交通事故报警', 'number': '122', 'note': '发生交通事故时优先拨打'},
    {'name': '高速公路报警救援', 'number': '12122', 'note': '高速公路上的故障与事故'},
    {'name': '医疗急救', 'number': '120', 'note': '有人员受伤时同时呼叫'},
    {'name': '火警', 'number': '119', 'note': '车辆起火、隧道火灾'},
    {'name': '公安报警', 'number': '110', 'note': '治安事件、人身安全受威胁'},
]

#: 高速行车安全须知
SAFETY_TIPS = [
    {
        'title': '车辆故障或事故后如何处置',
        'steps': [
            '立即开启危险报警闪光灯（双闪）',
            '能移动的车辆尽快移至应急车道或紧急停车带',
            '在来车方向 150 米外设置三角警示牌（夜间延长至 200 米以上）',
            '车上人员全部撤离到护栏外的安全地带，**不要留在车内或在车旁停留**',
            '拨打 12122 报警求助，说明所在高速名称、方向、桩号',
        ],
    },
    {
        'title': '遇到团雾或恶劣天气',
        'steps': [
            '开启雾灯与危险报警闪光灯，降低车速',
            '不要急刹车、不要随意变道，避免后车追尾',
            '就近驶入服务区或驶离高速，不要停在行车道',
        ],
    },
    {
        'title': '为什么不能在高速公路随意停车',
        'steps': [
            '高速公路车速快，后方车辆很难及时发现静止目标',
            '非紧急情况占用应急车道属违法行为，且会阻断救援通道',
            '确有紧急情况应停靠在紧急停车带，并设置警示标志',
        ],
    },
    {
        'title': '发现前方异常时',
        'steps': [
            '提前减速，不要猛打方向',
            '开启危险报警闪光灯提示后车',
            '可通过本页面上报，帮助后方车辆提前避让',
        ],
    },
]


@router.get('/emergency', summary='应急联系方式与安全须知')
def public_emergency() -> Dict[str, Any]:
    """应急电话与高速行车安全须知。

    内容为静态信息，不依赖数据库 —— 这类信息必须在系统其他部分
    都不可用时仍然可用。
    """
    return {
        'success': True,
        'contacts': EMERGENCY_CONTACTS,
        'tips': SAFETY_TIPS,
        'note': '遇紧急情况请先确保自身安全并拨打报警电话，本平台不替代报警服务。',
    }
