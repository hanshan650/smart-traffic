"""
路网与地图接口（上海）
======================
+--------------------------+-------------------------------------------+
| ``GET  /network``        | 上海路网拓扑（路口 + 路段 + 实时拥堵）     |
| ``GET  /summary``        | 路网摘要统计                               |
| ``GET  /segments/{id}``  | 路段详情                                   |
| ``GET  /amap/geocode``   | 高德地理编码（默认 ``city=上海``）         |
| ``GET  /amap/route``     | 高德驾车路线规划                           |
+--------------------------+-------------------------------------------+

**本组接口与视频源完全解耦**：实时视频来自河南高速云平台，
而路网、坐标、拥堵着色均基于上海。切换视频源不影响任何上海路网能力。

坐标为 **GCJ-02**（高德坐标系），可直接用于高德地图 JS API。
若需与 GPS（WGS84）数据对齐，请先做坐标转换。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.models.schemas import AmapRoute, AmapRouteStep, RoadNetwork
from app.services import road_network

router = APIRouter(prefix='/roads', tags=['上海路网'])

_AMAP_TIMEOUT = 8.0


def _require_amap_key() -> str:
    if not settings.amap_web_key:
        raise HTTPException(
            status_code=503,
            detail={
                'success': False,
                'error': '未配置高德 Web 服务 Key',
                'hint': '请在 backend/.env 中设置 AMAP_WEB_KEY（在高德开放平台申请「Web服务」类型 Key）',
            },
        )
    return settings.amap_web_key


def _parse_polyline(raw: str) -> List[List[float]]:
    """解析高德 ``"lng,lat;lng,lat"`` 形式的坐标串。"""
    points: List[List[float]] = []
    for pair in (raw or '').split(';'):
        parts = pair.split(',')
        if len(parts) != 2:
            continue
        try:
            points.append([float(parts[0]), float(parts[1])])
        except ValueError:
            continue
    return points


# ==========================================================================
# 路网
# ==========================================================================

@router.get('/network', response_model=RoadNetwork, summary='上海路网拓扑')
def get_network() -> RoadNetwork:
    """返回完整路网，路段携带当前拥堵等级，前端据此着色。"""
    return road_network.get_network()


@router.get('/summary', summary='路网摘要')
def get_summary() -> Dict[str, Any]:
    return {'success': True, **road_network.get_summary()}


@router.get('/segments/{road_id}', summary='路段详情')
def get_segment(road_id: str) -> Dict[str, Any]:
    segment = road_network.get_segment(road_id)
    if segment is None:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': f'路段不存在：{road_id}'},
        )
    return {'success': True, 'segment': segment.model_dump(by_alias=True)}


# ==========================================================================
# 高德代理
# ==========================================================================

@router.get('/amap/geocode', summary='高德地理编码')
def amap_geocode(
    address: str = Query(..., description='结构化地址，如「五角场」'),
    city: str = Query('上海', description='限定城市'),
) -> Dict[str, Any]:
    """地址 → 坐标（GCJ-02）。"""
    key = _require_amap_key()

    try:
        response = httpx.get(
            'https://restapi.amap.com/v3/geocode/geo',
            params={'key': key, 'address': address, 'city': city},
            timeout=_AMAP_TIMEOUT,
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail={'success': False, 'error': f'高德接口调用失败：{exc}'},
        ) from exc

    geocodes = payload.get('geocodes') or []
    if not geocodes:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': f'未解析到地址：{address}'},
        )

    location = str(geocodes[0].get('location', '')).split(',')
    if len(location) != 2:
        raise HTTPException(
            status_code=502,
            detail={'success': False, 'error': '高德返回坐标格式异常'},
        )

    return {
        'success': True,
        'address': address,
        'formattedAddress': geocodes[0].get('formatted_address', ''),
        'lng': float(location[0]),
        'lat': float(location[1]),
    }


@router.get('/amap/route', response_model=AmapRoute, summary='高德驾车路线规划')
def amap_route(
    origin: str = Query(..., description='起点，格式 lng,lat'),
    destination: str = Query(..., description='终点，格式 lng,lat'),
) -> AmapRoute:
    """驾车路线规划，返回分段折线与文字导航。"""
    key = _require_amap_key()

    try:
        response = httpx.get(
            'https://restapi.amap.com/v3/direction/driving',
            params={
                'key': key,
                'origin': origin,
                'destination': destination,
                'extensions': 'all',
                'strategy': 10,   # 不走高速（演示用默认策略）
            },
            timeout=_AMAP_TIMEOUT,
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail={'success': False, 'error': f'高德接口调用失败：{exc}'},
        ) from exc

    paths = (payload.get('route') or {}).get('paths') or []
    if not paths:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': '未规划到可用路线'},
        )

    best = paths[0]
    steps = [
        AmapRouteStep(
            instruction=step.get('instruction', ''),
            road=step.get('road', ''),
            distance=int(float(step.get('distance') or 0)),
            duration=int(float(step.get('duration') or 0)),
            polyline=_parse_polyline(step.get('polyline', '')),
        )
        for step in (best.get('steps') or [])
    ]

    return AmapRoute(
        distance=int(float(best.get('distance') or 0)),
        duration=int(float(best.get('duration') or 0)),
        steps=steps,
    )
