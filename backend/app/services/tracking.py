"""
多目标跟踪与轨迹管理
====================
两条路径，输出统一的 :class:`Track` 序列，下游算法无感知：

1. **ByteTrack（优先）** —— 安装 ultralytics 时，直接使用其内置跟踪器
   ``model.track(persist=True)``。这是工业界主流方案：通过关联**低分检测框**
   显著降低遮挡导致的 ID Switch，对事故判定至关重要（ID 跳变会切断轨迹，
   使速度骤降判据失效）。

2. **IoUTracker（内置降级）** —— 不依赖 ultralytics 的极简关联器，
   用于单元测试、算法验证与无 GPU 环境。关联逻辑等价于 SORT 的匹配部分
   （IoU 代价 + 贪心分配），不含卡尔曼滤波。

轨迹（Track）
-------------
一条轨迹是同一目标在连续帧上的观测序列。事故识别所需的全部物理量
（速度、加速度、航向角、TTC）都由轨迹导出，因此**轨迹完整性直接决定
算法上限**。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.schemas import BBox, DetectedObject
from app.services.geometry import (
    angle_difference,
    bbox_center,
    bbox_iou,
    heading_angle,
)

# ==========================================================================
# 轨迹数据结构
# ==========================================================================


@dataclass
class TrackPoint:
    """一次观测。"""

    frame_index: int
    timestamp: float          # 秒
    bbox: BBox
    confidence: float
    class_name: str

    @property
    def center(self) -> Tuple[float, float]:
        return bbox_center(self.bbox)


def _segment_speed(segment: Sequence[TrackPoint]) -> float:
    """一段轨迹的首尾平均速度（归一化单位 / 秒）。

    用首尾位移而非逐点差分：后者对检测框抖动极为敏感，
    会把单帧噪声放大成"速度尖峰"，干扰骤降判据。
    """
    if len(segment) < 2:
        return 0.0
    (x0, y0), (x1, y1) = segment[0].center, segment[-1].center
    dt = max(segment[-1].timestamp - segment[0].timestamp, 1e-6)
    return math.hypot(x1 - x0, y1 - y0) / dt


@dataclass
class Track:
    """单个目标的轨迹。

    所有时间相关量均归一化到「归一化坐标 / 秒」，
    这样阈值不受视频分辨率与帧率影响。
    """

    track_id: int
    class_name: str
    points: List[TrackPoint] = field(default_factory=list)
    # 连续未匹配帧数（IoUTracker 维护；ByteTrack 路径下恒为 0）
    missed: int = 0

    # ------------------------------------------------------------ 基础属性
    def append(self, point: TrackPoint) -> None:
        self.points.append(point)
        self.class_name = point.class_name   # 以最新观测为准（类别可能被修正）
        self.missed = 0

    @property
    def age(self) -> int:
        """观测帧数。"""
        return len(self.points)

    @property
    def last(self) -> TrackPoint:
        return self.points[-1]

    @property
    def first(self) -> TrackPoint:
        return self.points[0]

    @property
    def duration(self) -> float:
        """轨迹时间跨度（秒）。"""
        if self.age < 2:
            return 0.0
        return self.last.timestamp - self.first.timestamp

    # ------------------------------------------------------------ 运动学量
    def _window_points(self, window: int) -> List[TrackPoint]:
        """取最近 ``window + 1`` 个点（用于求一阶差分）。"""
        if self.age < 2:
            return []
        size = min(window + 1, self.age)
        return self.points[-size:]

    def displacement(self, window: int = 3) -> Tuple[float, float, float]:
        """最近窗口内的位移 ``(dx, dy, dt)``。"""
        pts = self._window_points(window)
        if len(pts) < 2:
            return (0.0, 0.0, 0.0)

        (x0, y0), (x1, y1) = pts[0].center, pts[-1].center
        dt = max(pts[-1].timestamp - pts[0].timestamp, 1e-6)
        return (x1 - x0, y1 - y0, dt)

    def speed(self, window: int = 3) -> float:
        """平均速度（归一化单位 / 秒）。

        注意：这是**图像平面速度**，非真实车速。若要换算为 km/h，
        需要相机标定（透视变换 + 已知路面尺度），属后续工作。
        """
        dx, dy, dt = self.displacement(window)
        if dt <= 0:
            return 0.0
        return math.hypot(dx, dy) / dt

    def heading(self, window: int = 3) -> Optional[float]:
        """航向角（弧度）。位移过小时返回 ``None``（航向无意义）。"""
        dx, dy, _ = self.displacement(window)
        if math.hypot(dx, dy) < 1e-4:
            return None
        return heading_angle(dx, dy)

    def speed_drop_ratio(self, window: int = 3) -> float:
        """碰撞前后的速度下降比例 ∈ [0, 1]。

        取「前段速度」与「后段速度」对比：

          ``ratio = (v_before - v_after) / v_before``

        这是**碰撞判定的核心判据** —— 真实碰撞会导致目标速度骤降，
        而正常跟车、变道则不会。
        """
        pts = self.points
        if len(pts) < window * 2:
            return 0.0

        before = pts[-2 * window : -window]
        after = pts[-window :]

        v_before = _segment_speed(before)
        v_after = _segment_speed(after)

        if v_before < 1e-4:
            return 0.0   # 原本就静止，无"骤降"可言

        drop = (v_before - v_after) / v_before
        return max(0.0, min(1.0, drop))

    def heading_change_rate(self, window: int = 3) -> float:
        """航向角变化率（弧度 / 秒）—— 轨迹畸变判据。

        碰撞会让目标产生非预期转向（或被撞偏），
        而正常变道也会产生转向，因此**该判据权重应显著低于速度骤降**。
        """
        pts = self.points
        if len(pts) < window * 2:
            return 0.0

        before = pts[-2 * window : -window + 1]
        after = pts[-window :]

        def segment_heading(seg: Sequence[TrackPoint]) -> Optional[float]:
            if len(seg) < 2:
                return None
            (x0, y0), (x1, y1) = seg[0].center, seg[-1].center
            if math.hypot(x1 - x0, y1 - y0) < 1e-4:
                return None
            return heading_angle(x1 - x0, y1 - y0)

        h_before = segment_heading(before)
        h_after = segment_heading(after)
        if h_before is None or h_after is None:
            return 0.0

        # 两段之间的时间间隔
        dt = max(after[0].timestamp - before[-1].timestamp, 1e-6)
        return angle_difference(h_after, h_before) / dt

    def is_stationary(self, threshold: float = 0.005) -> bool:
        """近静止判定（用于异常停车检测）。"""
        return self.speed(window=min(5, max(2, self.age - 1))) < threshold

    def peak_speed(self, window: int = 3) -> float:
        """历史最大滑窗速度。

        :meth:`speed` 只看末段，碰撞后双方停驶时读数恒为 0，
        单看它会丢失碰撞前的速度信息。``peak_speed`` 补足这一视角，
        使「从 v 降至 0」这一碰撞特征在展示层可见。
        """
        pts = self.points
        if len(pts) < 2:
            return 0.0

        span = max(2, min(window, len(pts)))
        best = 0.0
        for end in range(span, len(pts) + 1):
            seg = pts[end - span : end]
            (x0, y0), (x1, y1) = seg[0].center, seg[-1].center
            dt = max(seg[-1].timestamp - seg[0].timestamp, 1e-6)
            best = max(best, math.hypot(x1 - x0, y1 - y0) / dt)
        return best

    def peak_speed_drop_ratio(self, window: int = 3) -> float:
        """历史最大速度骤降比。

        :meth:`speed_drop_ratio` 只看末尾窗口：碰撞后目标停驶，
        前后段速度均为 0，读数会退化到 0，导致「曾经发生过骤降」
        这一事实在摘要中丢失。本方法扫描全部窗口取最大值，
        使摘要能稳定反映轨迹的骤降特征（检测逻辑仍用瞬时值）。
        """
        pts = self.points
        span = max(2, window)
        if len(pts) < span * 2:
            return 0.0

        best = 0.0
        for end in range(span * 2, len(pts) + 1):
            before = pts[end - span * 2 : end - span]
            after = pts[end - span : end]
            v_before = _segment_speed(before)
            if v_before < 1e-4:
                continue
            drop = (v_before - _segment_speed(after)) / v_before
            best = max(best, max(0.0, min(1.0, drop)))
        return best

    def to_summary(self) -> Dict[str, object]:
        """供前端/日志展示的轨迹摘要。"""
        return {
            'trackId': self.track_id,
            'className': self.class_name,
            'age': self.age,
            'duration': round(self.duration, 2),
            'speed': round(self.speed(), 5),
            'peakSpeed': round(self.peak_speed(), 5),
            'speedDropRatio': round(self.speed_drop_ratio(), 3),
            'peakSpeedDropRatio': round(self.peak_speed_drop_ratio(), 3),
            'headingChangeRate': round(self.heading_change_rate(), 3),
            'stationary': self.is_stationary(),
            'lastBbox': self.last.bbox.model_dump(by_alias=True) if self.age else None,
        }


# ==========================================================================
# 内置 IoU 关联跟踪器（降级 / 测试用）
# ==========================================================================


class IoUTracker:
    """极简 IoU 关联跟踪器。

    算法
    ----
    1. 预测：不做运动预测（无卡尔曼滤波），直接沿用上一帧 bbox
    2. 关联：计算 检测 × 轨迹 的 IoU 代价矩阵，按 IoU 降序**贪心**匹配
    3. 管理：未匹配检测新建轨迹；未匹配轨迹累计 ``missed``，
       超过 ``max_age`` 则删除

    :param iou_threshold: 关联所需最小 IoU
    :param max_age: 轨迹在无匹配情况下可存活的帧数
    :param min_hits: 轨迹被"确认"（对外可见）所需的最少观测帧数
    :param class_penalty: 类别不一致时的匹配降权值。
        ``0`` 表示"类别必须一致才允许关联"（事故识别的默认行为，
        避免人车 ID 串号）；设为正值则允许跨类别关联但降权，
        适用于**流量计数** —— 低分辨率下同一辆车会被分类成
        car/truck/train，硬约束会把一条轨迹拆成多条，使计数虚高。

    局限
    ----
    无运动模型，快速移动目标在低帧率下 IoU 会归零导致 ID 切换。
    生产环境应使用 ByteTrack（见模块文档）。
    """

    def __init__(
        self,
        iou_threshold: float = 0.3,
        max_age: int = 5,
        min_hits: int = 2,
        class_penalty: float = 0.0,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.min_hits = min_hits
        self.class_penalty = class_penalty

        self._tracks: Dict[int, Track] = {}
        self._next_id = 1

    # ------------------------------------------------------------------ 内部
    def _match(
        self,
        detections: List[DetectedObject],
        track_ids: List[int],
    ) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
        """贪心匹配，返回 ``(匹配对, 未匹配检测索引, 未匹配轨迹 id)``。"""
        if not detections or not track_ids:
            return ([], list(range(len(detections))), list(track_ids))

        pairs: List[Tuple[float, int, int]] = []
        for det_idx, det in enumerate(detections):
            for track_id in track_ids:
                track = self._tracks[track_id]
                if not track.age:
                    continue

                iou = bbox_iou(det.bbox, track.last.bbox)
                if iou < self.iou_threshold:
                    continue

                if track.class_name == det.class_name:
                    score = iou
                elif self.class_penalty > 0:
                    # 允许跨类别关联但降权：保证同类优先，
                    # 同时避免因单帧分类摇摆而拆散同一目标的轨迹
                    score = iou - self.class_penalty
                    if score <= 0:
                        continue
                else:
                    # 默认硬约束：类别不一致不给匹配机会（防人车串号）
                    continue

                pairs.append((score, det_idx, track_id))

        # IoU 降序贪心
        pairs.sort(key=lambda item: item[0], reverse=True)

        matched_dets: set = set()
        matched_tracks: set = set()
        matches: List[Tuple[int, int]] = []

        for _, det_idx, track_id in pairs:
            if det_idx in matched_dets or track_id in matched_tracks:
                continue
            matched_dets.add(det_idx)
            matched_tracks.add(track_id)
            matches.append((det_idx, track_id))

        unmatched_dets = [i for i in range(len(detections)) if i not in matched_dets]
        unmatched_tracks = [t for t in track_ids if t not in matched_tracks]

        return (matches, unmatched_dets, unmatched_tracks)

    # ------------------------------------------------------------------ 对外
    def update(
        self,
        detections: Sequence[DetectedObject],
        frame_index: int,
        timestamp: float,
    ) -> List[Track]:
        """用当前帧检测更新轨迹，返回**已确认**的轨迹列表。"""
        det_list = list(detections)
        track_ids = list(self._tracks.keys())

        matches, unmatched_dets, unmatched_tracks = self._match(det_list, track_ids)

        # 1. 已匹配 → 追加观测
        for det_idx, track_id in matches:
            det = det_list[det_idx]
            self._tracks[track_id].append(
                TrackPoint(
                    frame_index=frame_index,
                    timestamp=timestamp,
                    bbox=det.bbox,
                    confidence=det.confidence,
                    class_name=det.class_name,
                )
            )

        # 2. 未匹配检测 → 新建轨迹
        for det_idx in unmatched_dets:
            det = det_list[det_idx]
            track = Track(track_id=self._next_id, class_name=det.class_name)
            track.append(
                TrackPoint(
                    frame_index=frame_index,
                    timestamp=timestamp,
                    bbox=det.bbox,
                    confidence=det.confidence,
                    class_name=det.class_name,
                )
            )
            self._tracks[self._next_id] = track
            self._next_id += 1

        # 3. 未匹配轨迹 → 计missed，超龄删除
        for track_id in unmatched_tracks:
            track = self._tracks[track_id]
            track.missed += 1
            if track.missed > self.max_age:
                del self._tracks[track_id]

        # 4. 只有达到 min_hits 的轨迹才对外可见（抑制闪烁的假阳性）
        return [t for t in self._tracks.values() if t.age >= self.min_hits]

    def active_tracks(self) -> List[Track]:
        """当前全部轨迹（含未确认）。"""
        return list(self._tracks.values())

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1
