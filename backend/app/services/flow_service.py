"""
流量分析服务（时序聚合）
========================
把「单帧检测」升级为「多帧检测 + 跨帧关联计数」。

要解决的问题
------------
单帧判定有两个固有问题，均有实测数据支撑（见 ``docs/模型选型实验.md``）：

1. **单帧漏检无法补救** —— 远处小车在某一帧恰好被遮挡或模糊就丢了
2. **计数抖动剧烈** —— 同一路连续 10 帧的单帧计数在 1~3 之间跳变（极差 200%）

实测把 10 帧跨帧关联后的独立目标数为 **4**，超过单帧最大值 3 —— 说明存在
**从未在单帧中同时出现**的目标，聚合把互补的漏检补了回来（召回约 +33%）。

实现要点
--------
- 复用 ``video_source.extract_frames``（三级降级抽帧）与
  ``tracking.IoUTracker``（事故识别链路在用的同一个跟踪器），
  不为流量统计另起一套
- 计数单位从「帧内检出的框数」改为「**被多帧确认的轨迹数**」
- 类别采用**跨帧多数投票**：实测同一目标会被识别成 car / truck / train，
  单帧判定必然摇摆
- 同时返回单帧统计（min/median/max）作为**稳定性指标**暴露给前端，
  让使用者知道这个数字有多可信，而不是给一个看似精确的假象
"""
from __future__ import annotations

import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from app.services import detector as yolo_detector
from app.services.tracking import IoUTracker
from app.services.video_source import (
    VideoSourceError,
    extract_frames,
    get_provider,
    probe_frame_rate,
)

# 抽帧时基的兜底值（探测失败时使用）
FALLBACK_FPS = 10.0


@dataclass
class FlowConfig:
    """时序聚合参数。

    :param min_hits: 轨迹被确认所需的最少观测帧数。
        设为 1 会把单帧噪声也当目标，设为 2 是对"真实目标会被连续观测到"
        这一先验的合理要求。
    :param max_age: 轨迹在无匹配后仍可存活的帧数。低分辨率下检测框抖动大，
        给 2 帧容忍度可避免同一目标被拆成多条轨迹。
    :param iou_threshold: 关联所需的最小 IoU。
    :param class_penalty: 类别不一致时的关联降权值。
        必须为正 —— 低分辨率下同一辆车会被分类成 car / truck / train，
        若像事故识别那样要求类别严格一致，一条轨迹会被拆成多条，
        反而使车辆数虚高（实测同一路 10 帧中 car 与 truck 各出现 13 次）。
    """

    min_hits: int = 2
    max_age: int = 2
    iou_threshold: float = 0.3
    class_penalty: float = 0.15


def analyze_frames(
    frame_paths: List[str],
    fps: float,
    config: Optional[FlowConfig] = None,
    detect: Optional[Callable[[str], yolo_detector.DetectOutcome]] = None,
) -> Dict[str, Any]:
    """对**已抽好的帧序列**做跨帧关联计数（不涉及网络）。

    与 :func:`analyze_flow` 的分工：本函数只做分析与统计，取流与抽帧
    由调用方负责。这样拆开有两个好处：

    1. **可测试** —— 可以直接喂本地图片序列（或注入 mock 检测函数）
       验证关联与投票逻辑，不必依赖上游网络；
    2. **职责清晰** —— 「怎么拿到帧」与「怎么算」是两个独立的关注点。

    :param detect: 检测函数，默认用 YOLO；测试时可注入替身。
    """
    config = config or FlowConfig()
    detect_fn = detect or yolo_detector.detect_image

    tracker = IoUTracker(
        iou_threshold=config.iou_threshold,
        max_age=config.max_age,
        min_hits=config.min_hits,
        class_penalty=config.class_penalty,
    )

    per_frame_counts: List[int] = []
    # track_id -> 该轨迹在各帧被判定的类别计数（**累积**，不含当前帧状态）
    votes: Dict[int, Counter] = defaultdict(Counter)
    # track_id -> 最后一次观测时的累计帧数
    last_age: Dict[int, int] = {}
    # 保持出现顺序，便于输出稳定
    order: List[int] = []

    best_frame_path = frame_paths[0]
    best_frame_count = -1

    for index, path in enumerate(frame_paths):
        outcome = detect_fn(path)
        per_frame_counts.append(outcome.total_vehicles)

        # 代表帧：取目标最多的那一帧作为快照
        if outcome.total_vehicles > best_frame_count:
            best_frame_count = outcome.total_vehicles
            best_frame_path = path

        # update() 已按 min_hits 过滤，此处出现的即为"已确认轨迹"。
        # 必须逐帧累积而不是最后读 active_tracks()：轨迹在连续 max_age 帧
        # 未匹配后会被跟踪器删除，若最后才取，则"已驶离画面的车"会丢失 ——
        # 这对事故识别无妨（只关心当前险情），但流量计数必须统计全部目标。
        for track in tracker.update(outcome.objects, index, index / fps):
            if track.track_id not in votes:
                order.append(track.track_id)
            votes[track.track_id][track.class_name] += 1
            last_age[track.track_id] = track.age

    tracks: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for track_id in order:
        counter = votes[track_id]
        if not counter:
            continue
        # 跨帧多数投票决定最终类别
        class_name = counter.most_common(1)[0][0]
        counts[class_name] = counts.get(class_name, 0) + 1

        tracks.append({
            'trackId': track_id,
            'className': class_name,
            'observations': last_age.get(track_id, 0),
            'voteDetail': dict(counter),
            'classStable': len(counter) == 1,
        })

    track_total = len(tracks)

    return {
        'method': 'temporal',
        'frameCount': len(frame_paths),
        'fps': round(fps, 3),
        'durationSeconds': round(len(frame_paths) / fps, 2) if fps else 0.0,
        # ---- 聚合结果（推荐使用）----
        'totalVehicles': track_total,
        'vehicleCounts': counts,
        'congestionLevel': yolo_detector.classify_congestion(track_total).value,
        'tracks': tracks,
        # ---- 单帧结果（用于对比与评估聚合到底带来了什么）----
        'perFrameCounts': per_frame_counts,
        'stability': _summarize(per_frame_counts, track_total),
        'snapshotUrl': f'/static/uploads/{os.path.basename(best_frame_path)}',
        'config': {
            'minHits': config.min_hits,
            'maxAge': config.max_age,
            'iouThreshold': config.iou_threshold,
            'classPenalty': config.class_penalty,
        },
    }


def analyze_flow(
    camera_num: str,
    source_key: Optional[str] = None,
    frame_count: int = 10,
    road_id: str = 'R001',
    config: Optional[FlowConfig] = None,
) -> Dict[str, Any]:
    """对一路摄像头做多帧流量分析（取流 → 抽帧 → 关联计数）。

    :raises VideoSourceError: 取流或抽帧失败
    :raises yolo_detector.DetectorUnavailable: 模型不可用
    """
    provider = get_provider(source_key)
    stream_url = provider.get_stream_url(camera_num)

    # 时基决定速度量纲，先探测真实帧率（探测失败退回兜底值）
    fps = probe_frame_rate(stream_url, provider.request_headers) or FALLBACK_FPS

    frame_paths = extract_frames(
        stream_url,
        headers=provider.request_headers,
        count=frame_count,
    )
    if not frame_paths:
        raise VideoSourceError(
            '直播流抽帧失败',
            hint='直播流地址可能已过期，或该摄像头当前离线',
            payload={'cameraNum': camera_num},
        )

    result = analyze_frames(frame_paths, fps, config)
    result.update({
        'cameraNum': camera_num,
        'source': provider.key,
        'roadId': road_id,
    })
    return result


def _summarize(per_frame_counts: List[int], aggregated: int) -> Dict[str, Any]:
    """汇总稳定性指标。

    单帧计数的极差直接反映"只看一帧有多不可靠"；
    ``recallGain`` 则量化聚合相对最好单帧的额外召回
    （负值表示聚合反而更少，即剔除了单帧噪声）。
    """
    if not per_frame_counts:
        return {
            'min': 0, 'median': 0.0, 'max': 0, 'range': 0, 'recallGain': 0,
        }

    lowest = min(per_frame_counts)
    highest = max(per_frame_counts)

    return {
        'min': lowest,
        'median': round(statistics.median(per_frame_counts), 1),
        'max': highest,
        'range': highest - lowest,
        'recallGain': aggregated - highest,
    }
