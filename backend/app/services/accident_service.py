"""
事故分析编排服务
================
把「视频源 → 抽帧 → 检测 → 跟踪 → 判定 → 事件」串成一条完整流水线。

流水线
------
::

    VideoSourceProvider.get_stream_url()     取流地址（带 Referer 上下文）
              │
              ▼
    extract_frames()                          ffmpeg 抽帧（回退 OpenCV）
              │
              ▼
    yolo_detector.detect_image()              逐帧 YOLO 推理
              │
              ▼
    IoUTracker.update()                       轨迹关联
              │
              ▼
    AccidentDetector.process()                L2 规则 + L3 时序确认
              │
              ▼
    事件返回 / 落库 / WebSocket 广播

两种运行模式
------------
1. **实时片段分析** :func:`analyze_camera`
   抓取直播流的一段（默认 30 帧 ≈ 3 秒）后一次性分析。
   适合人工触发的事故复核与在线算法调参。

2. **合成轨迹仿真** :func:`simulate_scenario`
   用人工构造的轨迹验证算法正确性，**无需真实事故视频**即可演示
   「为什么判为事故 / 为什么不判」。这是答辩演示与消融实验的基础设施。

坐标系与单位
------------
全流程使用归一化坐标（``x, y, w, h ∈ [0, 1]``），速度单位为
「归一化单位 / 秒」。因此阈值不受视频分辨率影响，可跨点位复用。

时基假设
--------
抽帧后以 ``ANALYSIS_FPS`` 作为时基。真实直播流帧率通常为 25 fps，
但**抽帧间隔才是决定速度量纲的关键** —— 若上游以固定间隔出帧，
则该假设成立；若帧间隔抖动较大，需改用真实 PTS 时间戳（后续工作）。
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.models.schemas import BBox
from app.services import detector as yolo_detector
from app.services.accident_detector import (
    AccidentConfig,
    AccidentDetector,
    detect_collisions,
)
from app.services.tracking import IoUTracker, Track, TrackPoint
from app.services.video_source import (
    VideoSourceError,
    extract_frames,
    get_provider,
    probe_frame_rate,
)

# ==========================================================================
# 常量
# ==========================================================================

# 抽帧时基（帧/秒）。用于把帧序号换算为时间，进而计算速度与 TTC。
ANALYSIS_FPS = 10.0

# 默认分析帧数（30 帧 ≈ 3 秒，足以观测一次完整的碰撞过程）
DEFAULT_FRAME_COUNT = 30


# ==========================================================================
# 抽帧
# --------------------------------------------------------------------------
# 实现已移至 ``video_source.extract_frames`` —— 抓帧是视频源的职责，
# 且流量分析（flow_service）与事故分析需要共用同一套三级降级逻辑。
# ==========================================================================


# ==========================================================================
# 实时片段分析
# ==========================================================================


def analyze_camera(
    camera_num: str,
    source_key: Optional[str] = None,
    frame_count: int = DEFAULT_FRAME_COUNT,
    road_id: str = 'R001',
    include_stops: bool = True,
    config: Optional[AccidentConfig] = None,
) -> Dict[str, Any]:
    """抓取实时流的一段并执行事故分析。

    :raises VideoSourceError: 取流或抽帧失败
    :raises yolo_detector.DetectorUnavailable: 推理模型不可用
    """
    provider = get_provider(source_key)
    stream_url = provider.get_stream_url(camera_num)

    # 时基决定速度与 TTC 的量纲，必须在抽帧前先探测真实帧率。
    # 探测失败时退回 ANALYSIS_FPS（此时比值型判据仍正确，绝对阈值有偏差）。
    fps = probe_frame_rate(stream_url, provider.request_headers) or ANALYSIS_FPS

    frame_paths = extract_frames(
        stream_url,
        headers=provider.request_headers,
        count=frame_count,
    )

    if not frame_paths:
        raise VideoSourceError(
            '直播流抽帧失败',
            hint='直播流地址可能已过期，或该摄像头当前离线；建议安装 ffmpeg 以提高成功率',
            payload={'cameraNum': camera_num, 'streamUrl': stream_url},
        )

    algorithm = AccidentDetector(config)
    tracker = IoUTracker()

    events: List[Dict[str, Any]] = []
    frame_log: List[Dict[str, Any]] = []

    for index, path in enumerate(frame_paths):
        outcome = yolo_detector.detect_image(path)
        tracks = tracker.update(outcome.objects, index, index / fps)

        confirmed = algorithm.process(tracks, index)
        if include_stops:
            confirmed += algorithm.process_stops(tracks, index)

        for candidate in confirmed:
            payload = candidate.to_dict()
            payload['snapshotUrl'] = f'/static/uploads/{os.path.basename(path)}'
            events.append(payload)

        frame_log.append({
            'frameIndex': index,
            'detections': len(outcome.objects),
            'tracks': len(tracks),
            'confirmed': len(confirmed),
        })

    active_tracks = tracker.active_tracks()

    return {
        'cameraNum': camera_num,
        'source': provider.key,
        'roadId': road_id,
        'frameCount': len(frame_paths),
        'fps': round(fps, 3),
        'durationSeconds': round(len(frame_paths) / fps, 2),
        'trackCount': len(active_tracks),
        'events': events,
        'tracks': [track.to_summary() for track in active_tracks],
        'frameLog': frame_log,
        'algorithm': algorithm.describe(),
    }


# ==========================================================================
# 合成轨迹仿真（算法验证 / 答辩演示）
# ==========================================================================

SIMULATION_FPS = 10.0
SIMULATION_FRAMES = 30


def _track_from_points(
    track_id: int,
    class_name: str,
    points: List[Tuple[float, float, float, float]],
) -> Track:
    """由 ``(x, y, w, h)`` 序列构造轨迹。"""
    track = Track(track_id=track_id, class_name=class_name)
    for index, (x, y, w, h) in enumerate(points):
        track.append(
            TrackPoint(
                frame_index=index,
                timestamp=index / SIMULATION_FPS,
                bbox=BBox(x=x, y=y, w=w, h=h),
                confidence=0.92,
                class_name=class_name,
            )
        )
    return track


def _snapshot(track: Track, frame_index: int) -> Optional[Track]:
    """截取「截至某帧」的轨迹副本，用于逐帧仿真。"""
    points = [point for point in track.points if point.frame_index <= frame_index]
    if not points:
        return None
    return Track(track_id=track.track_id, class_name=track.class_name, points=list(points))


# ---------------------------------------------------------------- 场景定义


def _scenario_collision() -> List[Track]:
    """场景一：追尾碰撞（**正样本**）。

    后车高速逼近前车，第 15 帧发生接触，之后双方骤停。
    期望：L2 判据全部命中，L3 确认，最终**检出**。
    """
    front_points: List[Tuple[float, float, float, float]] = []
    rear_points: List[Tuple[float, float, float, float]] = []

    for index in range(SIMULATION_FRAMES):
        # 前车：缓慢移动，被撞后停驶
        front_x = 0.500 + min(index, 15) * 0.002
        front_points.append((front_x, 0.40, 0.10, 0.14))

        # 后车：快速接近（0.02/帧），接触后骤停
        rear_x = 0.200 + min(index, 15) * 0.020
        rear_points.append((rear_x, 0.40, 0.10, 0.14))

    return [
        _track_from_points(1, 'car', front_points),
        _track_from_points(2, 'car', rear_points),
    ]


def _scenario_normal_following() -> List[Track]:
    """场景二：正常跟车（**负样本**）。

    两车保持 0.20 的纵向间距匀速行驶，速度完全一致。
    期望：**不检出**（无接触、无速度骤降）。
    """
    lead_points: List[Tuple[float, float, float, float]] = []
    follow_points: List[Tuple[float, float, float, float]] = []

    for index in range(SIMULATION_FRAMES):
        travel = index * 0.010
        lead_points.append((0.30 + travel, 0.40, 0.09, 0.13))
        follow_points.append((0.30 + travel - 0.20, 0.40, 0.09, 0.13))

    return [
        _track_from_points(1, 'car', lead_points),
        _track_from_points(2, 'car', follow_points),
    ]


def _scenario_red_light() -> List[Track]:
    """场景三：红灯同步减速（**误报陷阱**）。

    同向 4 辆车同时降速，前后间距基本不变（0.09 < 车长 0.10，bbox 轻微重叠）。
    若无抑制规则，相邻两车会因「速度下降 + 距离接近」被判为碰撞。

    期望：命中 ``has_synchronized_slowdown`` 规则 → **不检出**。
    """
    tracks: List[Track] = []

    for vehicle_index in range(4):
        points: List[Tuple[float, float, float, float]] = []
        for index in range(SIMULATION_FRAMES):
            # 前 12 帧匀速，之后同步减速至近乎停止
            if index <= 12:
                travel = index * 0.028
            else:
                travel = 12 * 0.028 + (index - 12) * 0.003

            x = 0.55 + travel - vehicle_index * 0.09
            points.append((x, 0.40, 0.10, 0.14))

        tracks.append(_track_from_points(vehicle_index + 1, 'car', points))

    return tracks


def _scenario_lane_change() -> List[Track]:
    """场景四：车辆变道（**误报陷阱**）。

    目标横向大幅位移，航向角变化率很高，但**无接触、无速度骤降**。

    期望：硬性前提（接触 ∨ 速度骤降）不满足 → **不检出**。
    用于验证「仅凭轨迹畸变不足以判定事故」这一设计。
    """
    changing_points: List[Tuple[float, float, float, float]] = []
    cruising_points: List[Tuple[float, float, float, float]] = []

    for index in range(SIMULATION_FRAMES):
        travel = index * 0.016
        # 变道车：前 15 帧从 y=0.56 斜向移动到 y=0.30
        progress = min(index, 15) / 15
        lane_y = 0.56 - progress * 0.26
        changing_points.append((0.15 + travel, lane_y, 0.09, 0.13))

        # 相邻车道匀速行驶的车（保持距离，不参与碰撞）
        cruising_points.append((0.15 + travel, 0.80, 0.09, 0.13))

    return [
        _track_from_points(1, 'car', changing_points),
        _track_from_points(2, 'car', cruising_points),
    ]


SCENARIOS: Dict[str, Tuple[str, Callable[[], List[Track]]]] = {
    'collision': ('追尾碰撞（正样本）', _scenario_collision),
    'normal_following': ('正常跟车（负样本）', _scenario_normal_following),
    'red_light': ('红灯同步减速（误报陷阱）', _scenario_red_light),
    'lane_change': ('车辆变道（误报陷阱）', _scenario_lane_change),
}


# ---------------------------------------------------------------- 仿真入口


def simulate_scenario(
    scenario: str = 'collision',
    config: Optional[AccidentConfig] = None,
) -> Dict[str, Any]:
    """用合成轨迹逐帧仿真，返回判定结果与**完整证据链**。

    价值：在**没有真实事故视频**的情况下验证算法正确性，
    并直观展示「为什么判为事故 / 为什么不判」。

    :raises ValueError: 场景名不在 :data:`SCENARIOS` 中
    """
    if scenario not in SCENARIOS:
        raise ValueError(
            f'未知场景：{scenario}；可选：{", ".join(SCENARIOS.keys())}'
        )

    label, builder = SCENARIOS[scenario]
    full_tracks = builder()

    algorithm = AccidentDetector(config)
    events: List[Dict[str, Any]] = []
    raw_candidates: List[Dict[str, Any]] = []
    frame_log: List[Dict[str, Any]] = []

    for frame_index in range(SIMULATION_FRAMES):
        snapshot = [
            item
            for item in (_snapshot(track, frame_index) for track in full_tracks)
            if item is not None
        ]

        # L2 原始候选（含被抑制项），用于观察误报抑制规则的作用
        raw = detect_collisions(snapshot, algorithm.config, frame_index)
        for candidate in raw:
            raw_candidates.append({'frameIndex': frame_index, **candidate.to_dict()})

        confirmed = algorithm.process(snapshot, frame_index)
        for candidate in confirmed:
            events.append({'frameIndex': frame_index, **candidate.to_dict()})

        frame_log.append({
            'frameIndex': frame_index,
            'tracks': len(snapshot),
            'candidates': len(raw),
            'suppressed': sum(1 for item in raw if item.suppressed),
            'confirmed': len(confirmed),
        })

    suppressed_samples = [item for item in raw_candidates if item.get('suppressed')][:3]

    return {
        'scenario': scenario,
        'label': label,
        'frames': SIMULATION_FRAMES,
        'fps': SIMULATION_FPS,
        'durationSeconds': round(SIMULATION_FRAMES / SIMULATION_FPS, 2),
        'verdict': 'detected' if events else 'not_detected',
        'eventCount': len(events),
        'rawCandidateCount': len(raw_candidates),
        'tracks': [track.to_summary() for track in full_tracks],
        'events': events,
        'rawCandidates': raw_candidates[-10:],   # 仅保留末段，避免响应过大
        # 被抑制的候选样例（用于展示误报抑制规则的实际作用）
        'suppressionSamples': suppressed_samples,
        'frameLog': frame_log,
        'algorithm': algorithm.describe(),
    }


def list_scenarios() -> List[Dict[str, str]]:
    """列出全部仿真场景，供前端下拉选择。"""
    return [
        {'key': key, 'label': label, 'expected': _expected_of(key)}
        for key, (label, _) in SCENARIOS.items()
    ]


def _expected_of(key: str) -> str:
    return 'detected' if key == 'collision' else 'not_detected'
