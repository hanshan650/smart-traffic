"""
上海路网服务
============
维护上海市主要道路拓扑（路口 + 路段），为地图展示、拥堵着色与
绕行推荐提供基础数据。

数据来源
--------
基于上海真实主要道路抽象：

  · 延安高架（东西向大动脉）
  · 南北高架（南北向大动脉）
  · 内环高架 / 中环
  · 沪闵高架

设计说明
--------
本服务**与视频源完全解耦**：实时视频来自河南高速云平台，而路网、
坐标、拥堵着色均基于上海，两者在地图上分区域展示，互不影响。

当前实现使用进程内静态拓扑 + Redis 实时拥堵叠加。
若后续需要图数据库能力（多跳路径、加权绕行推荐），可将
``_INTERSECTIONS`` / ``_ROADS`` 灌入 Neo4j 并替换查询实现，
**对外函数签名保持不变**，API 层与前端无需改动。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.models.schemas import CongestionLevel, Intersection, RoadNetwork, RoadSegment

# ==========================================================================
# 静态拓扑数据
# ==========================================================================

# (id, 名称, 纬度, 经度)
_INTERSECTIONS: List[tuple] = [
    ('I001', '人民广场', 31.2304, 121.4737),
    ('I002', '外滩', 31.2400, 121.4900),
    ('I003', '陆家嘴', 31.2400, 121.5000),
    ('I004', '静安寺', 31.2250, 121.4480),
    ('I005', '徐家汇', 31.1950, 121.4370),
    ('I006', '虹桥枢纽', 31.1970, 121.3200),
    ('I007', '上海南站', 31.1550, 121.4300),
    ('I008', '五角场', 31.3000, 121.5150),
    ('I009', '世纪大道', 31.2300, 121.5300),
    ('I010', '内环沪闵立交', 31.1700, 121.4200),
    ('I011', '中环北翟路', 31.2200, 121.3600),
    ('I012', '南北高架天目路', 31.2500, 121.4650),
]

# (id, 名称, 起点, 终点, 长度 km, 限速 km/h, 车道数)
_ROADS: List[tuple] = [
    # 延安高架（东西大动脉）
    ('R001', '延安高架西段', 'I006', 'I004', 8.5, 80, 6),
    ('R002', '延安高架东段', 'I004', 'I001', 2.8, 80, 6),
    ('R003', '延安东路隧道', 'I001', 'I003', 2.2, 60, 4),
    # 南北高架（南北大动脉）
    ('R004', '南北高架北段', 'I008', 'I012', 5.5, 80, 6),
    ('R005', '南北高架中段', 'I012', 'I001', 2.0, 70, 4),
    ('R006', '南北高架南段', 'I001', 'I010', 3.5, 70, 4),
    # 内环高架
    ('R007', '内环西段', 'I010', 'I004', 6.0, 80, 6),
    ('R008', '内环北段', 'I004', 'I008', 7.5, 80, 6),
    ('R009', '内环东段', 'I008', 'I009', 5.0, 70, 4),
    ('R010', '内环南段', 'I009', 'I010', 8.0, 80, 6),
    # 中环
    ('R011', '中环西段', 'I007', 'I011', 9.5, 100, 8),
    ('R012', '中环北段', 'I011', 'I008', 8.0, 100, 8),
    # 沪闵高架
    ('R013', '沪闵高架', 'I010', 'I007', 4.5, 80, 6),
]


def _build_network() -> RoadNetwork:
    """构造路网对象（模块导入时执行一次）。"""
    index: Dict[str, Intersection] = {
        iid: Intersection(id=iid, name=name, lng=lng, lat=lat)
        for iid, name, lat, lng in _INTERSECTIONS
    }

    segments: List[RoadSegment] = []
    for rid, name, from_id, to_id, length_km, speed_limit, lanes in _ROADS:
        start, end = index.get(from_id), index.get(to_id)
        polyline: List[List[float]] = []
        if start and end:
            polyline = [[start.lng, start.lat], [end.lng, end.lat]]

        segments.append(RoadSegment(
            id=rid,
            name=name,
            from_id=from_id,
            to_id=to_id,
            length_m=round(length_km * 1000, 1),
            speed_limit=speed_limit,
            lanes=lanes,
            congestion_level=CongestionLevel.NORMAL,
            polyline=polyline,
        ))

    total_length = sum(seg.length_m for seg in segments)

    return RoadNetwork(
        center_lng=settings.shanghai_center_lng,
        center_lat=settings.shanghai_center_lat,
        intersections=list(index.values()),
        roads=segments,
        summary={
            'intersectionCount': len(index),
            'roadCount': len(segments),
            'totalLengthKm': round(total_length / 1000, 2),
            'avgLengthKm': round(total_length / 1000 / len(segments), 2) if segments else 0.0,
        },
    )


_NETWORK: RoadNetwork = _build_network()
_ROAD_INDEX: Dict[str, RoadSegment] = {seg.id: seg for seg in _NETWORK.roads}


# ==========================================================================
# 对外接口
# ==========================================================================

def get_network() -> RoadNetwork:
    """返回完整路网（含实时拥堵状态）。"""
    return _NETWORK


def get_summary() -> Dict[str, Any]:
    """路网摘要统计。"""
    return dict(_NETWORK.summary)


def get_segment(road_id: str) -> Optional[RoadSegment]:
    return _ROAD_INDEX.get(road_id)


def list_segments() -> List[RoadSegment]:
    return list(_NETWORK.roads)


def apply_congestion(levels: Dict[str, CongestionLevel]) -> int:
    """把实时拥堵等级叠加到路网上。

    :param levels: ``{road_id: CongestionLevel}``
    :return: 实际更新的路段数

    就地修改 ``_NETWORK`` 中的路段对象，因此后续 ``get_network()``
    会自动携带最新状态，无需重新构造路网。
    """
    updated = 0
    for road_id, level in levels.items():
        segment = _ROAD_INDEX.get(road_id)
        if segment is not None:
            segment.congestion_level = level
            updated += 1
    return updated


def reset_congestion() -> None:
    """把所有路段恢复为畅通（用于演示数据重置）。"""
    for segment in _NETWORK.roads:
        segment.congestion_level = CongestionLevel.NORMAL


def nearest_road(lng: float, lat: float) -> Optional[RoadSegment]:
    """按端点距离找出最近路段，用于把摄像头归属到上海路网。

    注意：河南摄像头的坐标（110~116°E）与上海（121°E）相距甚远，
    因此该函数对河南摄像头会返回距离最小的路段，但**语义上不应使用**。
    真实归属判断应由调用方按坐标范围先行筛选。
    """
    if not _NETWORK.roads:
        return None

    def squared_distance(segment: RoadSegment) -> float:
        if not segment.polyline:
            return float('inf')
        return min(
            (point[0] - lng) ** 2 + (point[1] - lat) ** 2
            for point in segment.polyline
        )

    return min(_NETWORK.roads, key=squared_distance)
