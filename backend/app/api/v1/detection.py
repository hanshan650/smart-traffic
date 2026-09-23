"""
检测接口
========
+-------------------+------------------------------------------------+
| ``POST /image``   | 上传图片检测                                    |
| ``POST /snapshot``| 从视频源抓帧检测（走视频源适配层）               |
| ``GET  /history`` | 检测历史                                        |
| ``GET  /model``   | 模型状态与降级原因                              |
+-------------------+------------------------------------------------+

检测属于 CPU/GPU 密集操作，统一通过 ``run_in_threadpool`` 调度，
避免阻塞事件循环与 WebSocket 推送。
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.models.schemas import DetectionResult, EventLevel, EventType
from app.realtime.ws_manager import broadcast_threadsafe
from app.services import detector, event_service, flow_service, video_source
from app.services.detector import DetectorUnavailable
from app.services.video_source import VideoSourceError

router = APIRouter(prefix='/detection', tags=['检测'])

ALLOWED_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}


# ==========================================================================
# 内部：检测 → 落库 → 缓存 → 事件 → 推送
# ==========================================================================

def _snapshot_url(image_path: str) -> str:
    return '/static/uploads/' + Path(image_path).name


def _run_pipeline(
    image_path: str,
    *,
    camera_id: str,
    road_id: str,
    camera_name: str = '',
    road_name: str = '',
    source_key: str = '',
    source_url: str = '',
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> DetectionResult:
    """同步执行完整检测流水线（在线程池中调用）。

    ``latitude`` / ``longitude`` 由**调用方提供**而非在此查询：
    调用方（前端）已经从摄像头列表里拿到了坐标，而服务端并没有
    "按编号查摄像头"的能力（上游检索接口是按视野随机抽样返回的）。
    坐标会写进检测记录、Redis 快照与生成的告警事件，供地图展示使用。
    """
    outcome = detector.detect_image(image_path)
    snapshot = _snapshot_url(image_path)

    # ---- 1. 落库（失败不阻断返回，仅丢失持久化）----
    detection_id = ''
    try:
        detection_id = event_service.save_detection(
            camera_id=camera_id,
            road_id=road_id,
            total_vehicles=outcome.total_vehicles,
            vehicle_counts=outcome.vehicle_counts,
            congestion_level=outcome.congestion_level,
            objects=outcome.objects,
            snapshot_url=snapshot,
            source_url=source_url,
            duration_ms=outcome.duration_ms,
            latitude=latitude,
            longitude=longitude,
        )
    except RuntimeError:
        pass

    # ---- 2. 实时缓存 + 拥堵排行 ----
    event_service.cache_snapshot(
        camera_id=camera_id,
        camera_name=camera_name or camera_id,
        road_id=road_id,
        road_name=road_name,
        total_vehicles=outcome.total_vehicles,
        vehicle_counts=outcome.vehicle_counts,
        congestion_level=outcome.congestion_level,
        snapshot_url=snapshot,
        latitude=latitude,
        longitude=longitude,
    )

    # ---- 3. 按拥堵等级生成事件（内含 60s 限流）----
    event = None
    try:
        event = event_service.event_from_congestion(
            congestion_level=outcome.congestion_level,
            camera_id=camera_id,
            camera_name=camera_name or camera_id,
            road_id=road_id,
            road_name=road_name,
            source_key=source_key,
            vehicle_count=outcome.total_vehicles,
            snapshot_url=snapshot,
            latitude=latitude,
            longitude=longitude,
        )
    except RuntimeError:
        pass

    # ---- 4. 实时推送 ----
    if event is not None:
        broadcast_threadsafe('event:new', event.model_dump(mode='json', by_alias=True))

    broadcast_threadsafe('snapshot', {
        'cameraId': camera_id,
        'cameraName': camera_name or camera_id,
        'roadId': road_id,
        'totalVehicles': outcome.total_vehicles,
        'vehicleCounts': outcome.vehicle_counts,
        'congestionLevel': outcome.congestion_level.value,
        'snapshotUrl': snapshot,
    })

    return DetectionResult(
        camera_id=camera_id,
        road_id=road_id,
        total_vehicles=outcome.total_vehicles,
        vehicle_counts=outcome.vehicle_counts,
        congestion_level=outcome.congestion_level,
        objects=outcome.objects,
        snapshot_url=snapshot,
        source_url=source_url,
        detection_id=detection_id or None,
        timestamp=datetime.utcnow(),
        duration_ms=outcome.duration_ms,
    )


# ==========================================================================
# 端点
# ==========================================================================

@router.get('/model', summary='模型状态')
def model_status() -> Dict[str, Any]:
    """返回模型可用性与降级原因，供前端提示。"""
    return detector.model_info()


@router.post('/image', response_model=DetectionResult, summary='上传图片检测')
async def detect_uploaded_image(
    file: UploadFile = File(...),
    camera_id: str = Form('CAM_UPLOAD'),
    road_id: str = Form('R001'),
) -> DetectionResult:
    """上传单张图片并执行 YOLO 检测。"""
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail={'success': False, 'error': f'不支持的图片格式：{suffix or "未知"}'},
        )

    settings.upload_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime('%Y%m%d%H%M%S%f')
    target = settings.upload_path / f'upload_{stamp}{suffix}'
    target.write_bytes(await file.read())

    try:
        return await run_in_threadpool(
            _run_pipeline,
            str(target),
            camera_id=camera_id,
            road_id=road_id,
            camera_name=camera_id,
        )
    except DetectorUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={'success': False, 'error': str(exc), 'hint': '模型未就绪，检测功能暂不可用'},
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={'success': False, 'error': str(exc)}) from exc


@router.post('/snapshot', response_model=DetectionResult, summary='直播抓帧检测')
async def detect_live_snapshot(
    source: Optional[str] = Query(None, description='视频源 key'),
    camera_num: str = Query('', alias='cameraNum', description='摄像头编号'),
    road_id: str = Query('R001'),
    latitude: Optional[float] = Query(None, description='摄像头纬度（由前端传入）'),
    longitude: Optional[float] = Query(None, description='摄像头经度（由前端传入）'),
) -> DetectionResult:
    """从视频源抓取一帧并检测。

    这是把「视频源适配层」与「检测服务」串起来的核心接口。

    ``latitude`` / ``longitude`` 是可选的：调用方从摄像头列表里已经拿到
    坐标，顺手传过来即可让生成的告警带上位置；不传也能正常工作，
    只是地图上无法定位该事件。
    """
    try:
        provider = video_source.get_provider(source)
        resolved = camera_num or provider.default_camera
        frame_path, stream_url = await run_in_threadpool(provider.capture_frame, resolved)
    except VideoSourceError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict()) from exc

    try:
        return await run_in_threadpool(
            _run_pipeline,
            frame_path,
            camera_id=f'{provider.key.upper()}_{resolved}',
            camera_name=resolved,
            road_id=road_id,
            road_name=road_id,
            source_key=provider.key,
            source_url=stream_url,
            latitude=latitude,
            longitude=longitude,
        )
    except DetectorUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={'success': False, 'error': str(exc), 'hint': '模型未就绪，检测功能暂不可用'},
        ) from exc


@router.post('/flow', summary='多帧流量分析（时序聚合）')
async def detect_flow(
    source: Optional[str] = Query(None, description='视频源 key'),
    camera_num: str = Query('', alias='cameraNum', description='摄像头编号'),
    frame_count: int = Query(10, ge=3, le=30, alias='frameCount', description='分析帧数'),
    road_id: str = Query('R001', alias='roadId'),
) -> Dict[str, Any]:
    """对一路摄像头做多帧流量分析（时序聚合）。

    与 ``/snapshot`` 的单帧判定相比：

    · 计数单位从「帧内检出的框数」改为「**被多帧确认的轨迹数**」
    · 类别采用跨帧多数投票，消除单帧的类别摇摆
    · 互补单帧漏检（实测召回约 +33%）

    代价是需要逐帧推理，耗时约为单帧的数倍，故帧数上限设为 30。
    响应中的 ``stability`` 字段给出单帧计数的波动范围，
    供前端展示这次结果的可信度。
    """
    try:
        provider = video_source.get_provider(source)
        resolved = camera_num or provider.default_camera
    except VideoSourceError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict()) from exc

    try:
        return await run_in_threadpool(
            flow_service.analyze_flow,
            resolved,
            source,
            frame_count,
            road_id,
        )
    except VideoSourceError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict()) from exc
    except DetectorUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                'success': False,
                'error': str(exc),
                'hint': '模型未就绪，流量分析不可用',
            },
        ) from exc


@router.get('/history', summary='检测历史')
def detection_history(
    limit: int = Query(20, ge=1, le=200),
    road_id: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """读取最近的检测记录。MongoDB 不可用时返回空列表而非报错。"""
    from app.db import mongo

    query: Dict[str, Any] = {}
    if road_id:
        query['road_id'] = road_id

    try:
        cursor = (
            mongo.get_db()['detections']
            .find(query)
            .sort('timestamp', -1)
            .limit(limit)
        )
        items: List[Dict[str, Any]] = []
        for doc in cursor:
            doc['id'] = str(doc.pop('_id'))
            doc['timestamp'] = doc.get('timestamp', datetime.utcnow()).isoformat()
            items.append(doc)
    except Exception:
        items = []

    return {'success': True, 'total': len(items), 'items': items}


@router.get('/thresholds', summary='拥堵判定阈值')
def thresholds() -> Dict[str, Any]:
    """返回当前拥堵分级阈值，供前端图例展示。"""
    return {
        'light': settings.traffic_light_threshold,
        'moderate': settings.traffic_moderate_threshold,
        'heavy': settings.traffic_heavy_threshold,
        'confidence': settings.yolo_confidence,
    }
