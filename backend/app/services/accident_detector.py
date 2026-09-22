"""
交通事故识别算法（三级级联的 L2 + L3 层）
==========================================

整体框架
--------
::

    L1 感知层     YOLO 检测 + ByteTrack 跟踪         → Track 序列（tracking.py）
    L2 规则层     轨迹物理量多判据融合               → AccidentCandidate（本文件）
    L3 时序确认   多帧一致性投票 / 时序模型           → 最终事件（本文件）

L2 规则层：四条判据
-------------------
设目标 i、j 的边界框为 bᵢ、bⱼ，速度为 vᵢ，航向角为 θᵢ：

① **接触判据** ``I_contact``
   ``IoU(bᵢ, bⱼ) > τ_iou`` 或 归一化中心距小于阈值。
   路侧俯视视角下 bbox 常因遮挡而不重叠，故以中心距作为补充。

② **速度骤降判据** ``Δv``
   ``(v_before − v_after) / v_before > τ_v``（默认 0.6）。
   **这是最核心的判据** —— 真实碰撞必然导致速度骤降，
   而正常跟车、变道不会。

③ **碰撞时间判据** ``TTC``
   ``TTC = d / (−ḋ)``，仅在两目标相互接近时成立。
   ``TTC < τ_ttc``（默认 1.5 s）判定为高危接近。

④ **轨迹畸变判据** ``κ``
   ``|θ_after − θ_before| / Δt > τ_θ``。
   碰撞会令目标非预期转向，但正常变道同样会转向，
   因此**该判据权重最低**。

融合评分
--------
各判据先归一化到 ``[0, 1]``，再线性加权：

    S = w₁·Î + w₂·Δv̂ + w₃·(1 − TT̂C) + w₄·κ̂

并设**硬性前提**：接触与速度骤降必须至少命中其一，否则直接判定为
非碰撞。这一先验能过滤掉绝大多数"轨迹畸变但无接触"的误报
（如车辆急转弯、摄像头抖动）。

误报抑制
--------
内置针对典型误报场景的规则：

+------------------------+------------------------------------------------+
| 误报场景               | 抑制策略                                        |
+========================+================================================+
| 行人/非机动车在车流中穿插 | 降低人-车组合的评分系数                        |
| 多车同步减速（红灯）    | 若同帧另有 ≥2 辆车同样减速，判定为交通流        |
| 静止目标贴靠            | 双方均近静止时不判定为碰撞（属"违停"范畴）      |
| 轨迹过短                | 要求轨迹达到最小观测帧数                        |
+------------------------+------------------------------------------------+

L3 时序确认层
-------------
单帧评分存在抖动，L3 在时间维度做一致性确认：

· **默认实现** :class:`VoteConfirmer` —— 滑动窗口投票，窗口内命中
  ≥ ``min_votes`` 次才确认，可显著压低误报率（FAR）
· **可替换实现** —— 满足 :class:`SequentialConfirmer` 协议即可接入
  训练好的时序模型（LSTM / TSM / 3D-CNN），业务层无需改动

设计取舍
--------
未采用端到端事故分类模型作为主路径，原因有三：
  1. 事故样本极度稀缺（长尾事件），端到端模型难以收敛
  2. 黑盒模型不可解释，而交警采信的前提是"能说清依据"
  3. 级联结构天然支持**逐级消融实验**，便于量化各层贡献
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from app.models.schemas import BBox, EventType
from app.services.geometry import (
    bbox_iou,
    bbox_union,
    center_distance,
    normalized_distance,
    sigmoid,
)
from app.services.tracking import Track

# 机动车类别（只有机动车参与才可能构成碰撞事故）
VEHICLE_CLASSES = {'car', 'bus', 'truck', 'motorcycle'}
# 弱势交通参与者
VULNERABLE_CLASSES = {'pedestrian', 'bicycle'}


# ==========================================================================
# 配置
# ==========================================================================


@dataclass
class AccidentConfig:
    """算法参数。所有距离均为**归一化坐标**，速度单位为「归一化单位/秒」。"""

    # ---- 判据阈值 ----
    iou_threshold: float = 0.05
    center_distance_threshold: float = 0.10
    speed_drop_threshold: float = 0.60
    ttc_threshold: float = 1.5
    heading_change_threshold: float = 1.5   # 弧度/秒

    # ---- 融合权重（和为 1）----
    weight_contact: float = 0.35
    weight_speed_drop: float = 0.35
    weight_ttc: float = 0.20
    weight_heading: float = 0.10

    # ---- 判定 ----
    score_threshold: float = 0.55

    # ---- 轨迹质量门槛 ----
    min_track_age: int = 4        # 参与判定所需最少观测帧数
    analysis_window: int = 3      # 计算一阶差分所用的窗口

    # ---- 误报抑制 ----
    vulnerable_penalty: float = 0.6    # 人-车组合的评分折减系数
    stationary_speed: float = 0.005    # 近静止阈值
    synchronized_count: int = 2        # 同帧多少辆车同步减速即判为交通流

    # ---- 异常停车 ----
    stop_duration_frames: int = 20     # 持续静止多少帧判定为异常停车
    stop_min_age: int = 20

    # ---- L3 ----
    vote_window: int = 5
    vote_min_hits: int = 2

    def to_dict(self) -> Dict[str, float]:
        return {
            'iouThreshold': self.iou_threshold,
            'centerDistanceThreshold': self.center_distance_threshold,
            'speedDropThreshold': self.speed_drop_threshold,
            'ttcThreshold': self.ttc_threshold,
            'headingChangeThreshold': self.heading_change_threshold,
            'weights': {
                'contact': self.weight_contact,
                'speedDrop': self.weight_speed_drop,
                'ttc': self.weight_ttc,
                'heading': self.weight_heading,
            },
            'scoreThreshold': self.score_threshold,
            'minTrackAge': self.min_track_age,
            'analysisWindow': self.analysis_window,
        }


# ==========================================================================
# 证据与候选
# ==========================================================================


@dataclass
class CriterionEvidence:
    """单条判据的证据。

    显式保留「原始值 / 阈值 / 归一化得分 / 权重贡献」四项，
    使最终判定完全可追溯 —— 这是交警采信与算法调优的共同前提。
    """

    name: str
    label: str
    raw_value: float
    threshold: float
    score: float          # 归一化到 [0, 1]
    weight: float
    triggered: bool

    @property
    def contribution(self) -> float:
        return self.score * self.weight

    def to_dict(self) -> Dict[str, object]:
        return {
            'name': self.name,
            'label': self.label,
            'rawValue': round(self.raw_value, 4),
            'threshold': self.threshold,
            'score': round(self.score, 4),
            'weight': self.weight,
            'contribution': round(self.contribution, 4),
            'triggered': self.triggered,
        }


@dataclass
class AccidentCandidate:
    """一次碰撞候选（L2 输出）。"""

    score: float
    track_ids: Tuple[int, int]
    class_names: Tuple[str, str]
    frame_index: int
    timestamp: float
    bbox: BBox
    evidences: List[CriterionEvidence] = field(default_factory=list)
    suppressed: bool = False
    suppress_reason: str = ''
    event_type: EventType = EventType.COLLISION

    def to_dict(self) -> Dict[str, object]:
        return {
            'score': round(self.score, 4),
            'trackIds': list(self.track_ids),
            'classNames': list(self.class_names),
            'frameIndex': self.frame_index,
            'timestamp': self.timestamp,
            'bbox': self.bbox.model_dump(by_alias=True),
            'eventType': self.event_type.value,
            'suppressed': self.suppressed,
            'suppressReason': self.suppress_reason,
            # 证据链（按贡献降序），前端可直接渲染为"判定依据"
            'evidences': [
                e.to_dict() for e in sorted(self.evidences, key=lambda x: -x.contribution)
            ],
        }


# ==========================================================================
# L2：单条判据计算
# ==========================================================================


def contact_score(track_a: Track, track_b: Track, config: AccidentConfig) -> Tuple[float, float]:
    """① 接触判据。

    :return: ``(归一化得分, 原始 IoU)``

    以 IoU 为主、归一化中心距为辅，取二者较大值 —— 只要满足其一
    即视为"发生了接触"。归一化中心距用目标自身尺寸做基准，
    可消除远近目标的尺度差异。
    """
    iou = bbox_iou(track_a.last.bbox, track_b.last.bbox)

    iou_score = min(iou / config.iou_threshold, 1.0) if config.iou_threshold > 0 else 0.0

    distance = normalized_distance(track_a.last.bbox, track_b.last.bbox)
    distance_score = max(0.0, 1.0 - distance / config.center_distance_threshold)

    return (max(iou_score, distance_score), iou)


def speed_drop_score(track: Track, config: AccidentConfig) -> Tuple[float, float]:
    """② 速度骤降判据。

    :return: ``(归一化得分, 原始下降比例)``

    取两条轨迹中下降更多的一方 —— 碰撞中通常只有一方骤停，
    另一方可能只是被轻微减速。
    """
    ratio = track.speed_drop_ratio(config.analysis_window)
    if config.speed_drop_threshold <= 0:
        return (0.0, ratio)
    return (min(ratio / config.speed_drop_threshold, 1.0), ratio)


def time_to_collision(
    track_a: Track,
    track_b: Track,
    window: int = 3,
) -> Optional[float]:
    """计算碰撞时间 TTC（秒）。

    :return: TTC；两目标未相互接近时返回 ``None``

    以最近 ``window`` 帧的距离变化率外推：

        ``TTC = d_now / ((d_old − d_now) / Δt)``
    """
    size = min(track_a.age, track_b.age, window + 1)
    if size < 2:
        return None

    pts_a = track_a.points[-size:]
    pts_b = track_b.points[-size:]

    d_old = center_distance(pts_a[0].bbox, pts_b[0].bbox)
    d_now = center_distance(pts_a[-1].bbox, pts_b[-1].bbox)
    dt = pts_a[-1].timestamp - pts_a[0].timestamp

    if dt <= 0:
        return None

    closing_rate = (d_old - d_now) / dt
    if closing_rate <= 1e-6:
        return None        # 未接近（或正在远离）

    return d_now / closing_rate


def ttc_score(ttc: Optional[float], config: AccidentConfig) -> Tuple[float, float]:
    """③ TTC 判据。TTC 越小得分越高；未接近则得 0。"""
    if ttc is None:
        return (0.0, float('inf'))
    score = max(0.0, 1.0 - ttc / config.ttc_threshold)
    return (min(score, 1.0), ttc)


def heading_change_score(track: Track, config: AccidentConfig) -> Tuple[float, float]:
    """④ 轨迹畸变判据。"""
    rate = track.heading_change_rate(config.analysis_window)
    if config.heading_change_threshold <= 0:
        return (0.0, rate)
    return (min(rate / config.heading_change_threshold, 1.0), rate)


# ==========================================================================
# L2：误报抑制
# ==========================================================================


def has_synchronized_slowdown(
    all_tracks: Sequence[Track],
    participant_ids: Tuple[int, int],
    config: AccidentConfig,
) -> bool:
    """检测是否为「多车同步减速」场景。

    典型情形：前方红灯，车流整体减速。此时两辆相邻车都会出现
    「速度下降 + 距离接近」，极易误判为碰撞。

    判定：若**非参与者**的轨迹中也有 ≥ ``synchronized_count`` 条
    出现同等级别的减速，则认为是交通流行为而非碰撞。
    """
    slowed_others = 0

    for track in all_tracks:
        if track.track_id in participant_ids:
            continue
        if track.age < config.min_track_age * 2:
            continue
        if track.class_name not in VEHICLE_CLASSES:
            continue
        if track.speed_drop_ratio(config.analysis_window) >= config.speed_drop_threshold:
            slowed_others += 1
            if slowed_others >= config.synchronized_count:
                return True

    return False


# ==========================================================================
# L2：配对评估
# ==========================================================================


def evaluate_pair(
    track_a: Track,
    track_b: Track,
    config: AccidentConfig,
    all_tracks: Optional[Sequence[Track]] = None,
    frame_index: int = 0,
) -> Optional[AccidentCandidate]:
    """对一对轨迹做碰撞评估。

    :return: 候选对象；不符合硬性前提或轨迹质量不足时返回 ``None``
    """
    # ---- 轨迹质量门槛 ----
    if track_a.age < config.min_track_age or track_b.age < config.min_track_age:
        return None

    classes = (track_a.class_name, track_b.class_name)

    # 至少一方是机动车，否则不构成交通事故
    if not any(name in VEHICLE_CLASSES for name in classes):
        return None

    # ---- 计算四条判据 ----
    contact_val, raw_iou = contact_score(track_a, track_b, config)

    drop_a, raw_drop_a = speed_drop_score(track_a, config)
    drop_b, raw_drop_b = speed_drop_score(track_b, config)
    speed_val = max(drop_a, drop_b)
    raw_drop = max(raw_drop_a, raw_drop_b)

    ttc = time_to_collision(track_a, track_b, config.analysis_window)
    ttc_val, raw_ttc = ttc_score(ttc, config)

    heading_val = max(
        heading_change_score(track_a, config)[0],
        heading_change_score(track_b, config)[0],
    )
    raw_heading = max(
        heading_change_score(track_a, config)[1],
        heading_change_score(track_b, config)[1],
    )

    # ---- 硬性前提：速度变化为必需要件 ----
    # 物理上碰撞必然改变目标速度，因此「速度骤降」不可省略。
    # 反之，单纯的 bbox 重叠**不足以**判定碰撞：路侧视角下前后车
    # 在图像上经常重叠（透视压缩），检测框也会因遮挡而抖动，
    # 两者都会造成「接触命中但速度不变」的假阳性。
    # 实测：真实高速流上，「仅接触」组合的误报率显著高于本门槛之后。
    if speed_val <= 0.0:
        return None

    # ---- 双方均近静止 ----
    # 若两车始终缓慢（排队 / 违停），不构成碰撞；
    # 但若此前有明显运动而后骤停（raw_drop 显著），则属“碰撞后停驶”，
    # 仍需判定 —— 否则会在碰撞发生后数帧内漏检。
    both_stationary = track_a.is_stationary(
        config.stationary_speed
    ) and track_b.is_stationary(config.stationary_speed)
    if both_stationary and raw_drop < config.speed_drop_threshold:
        return None

    # ---- 融合评分 ----
    score = (
        contact_val * config.weight_contact
        + speed_val * config.weight_speed_drop
        + ttc_val * config.weight_ttc
        + heading_val * config.weight_heading
    )

    # 弱势交通参与者折减
    if any(name in VULNERABLE_CLASSES for name in classes):
        score *= config.vulnerable_penalty

    # ---- 组装证据链 ----
    evidences = [
        CriterionEvidence(
            name='contact',
            label='目标接触',
            raw_value=raw_iou,
            threshold=config.iou_threshold,
            score=contact_val,
            weight=config.weight_contact,
            triggered=contact_val > 0.0,
        ),
        CriterionEvidence(
            name='speed_drop',
            label='速度骤降',
            raw_value=raw_drop,
            threshold=config.speed_drop_threshold,
            score=speed_val,
            weight=config.weight_speed_drop,
            triggered=speed_val > 0.0,
        ),
        CriterionEvidence(
            name='ttc',
            label='碰撞时间',
            raw_value=raw_ttc if raw_ttc != float('inf') else -1.0,
            threshold=config.ttc_threshold,
            score=ttc_val,
            weight=config.weight_ttc,
            triggered=ttc_val > 0.0,
        ),
        CriterionEvidence(
            name='heading_change',
            label='轨迹畸变',
            raw_value=raw_heading,
            threshold=config.heading_change_threshold,
            score=heading_val,
            weight=config.weight_heading,
            triggered=heading_val > 0.0,
        ),
    ]

    candidate = AccidentCandidate(
        score=max(0.0, min(score, 1.0)),
        track_ids=(track_a.track_id, track_b.track_id),
        class_names=classes,
        frame_index=frame_index,
        timestamp=track_a.last.timestamp,
        bbox=bbox_union(track_a.last.bbox, track_b.last.bbox),
        evidences=evidences,
    )

    # ---- 交通流同步减速抑制 ----
    if all_tracks is not None and has_synchronized_slowdown(
        all_tracks, candidate.track_ids, config
    ):
        candidate.suppressed = True
        candidate.suppress_reason = '同帧存在多车同步减速，判定为交通流而非碰撞'

    return candidate


def detect_collisions(
    tracks: Sequence[Track],
    config: Optional[AccidentConfig] = None,
    frame_index: int = 0,
) -> List[AccidentCandidate]:
    """对当前帧的全部轨迹做两两碰撞评估。

    复杂度为 O(n²)。实际场景中单帧车辆数通常在 50 以内，
    且已通过轨迹质量门槛预筛，开销可接受。

    :return: 按评分降序排列的候选列表

    注意：**包含被抑制的候选**（``suppressed=True``），便于离线分析
    误报抑制规则的触发情况；实际判定应由 :class:`AccidentDetector`
    统一过滤后再交给 L3。
    """
    cfg = config or AccidentConfig()
    track_list = list(tracks)
    candidates: List[AccidentCandidate] = []

    for i in range(len(track_list)):
        for j in range(i + 1, len(track_list)):
            candidate = evaluate_pair(
                track_list[i],
                track_list[j],
                cfg,
                all_tracks=track_list,
                frame_index=frame_index,
            )
            if candidate is not None:
                candidates.append(candidate)

    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates


# ==========================================================================
# L2：异常停车（独立事件类型）
# ==========================================================================


def detect_abnormal_stop(
    tracks: Sequence[Track],
    config: Optional[AccidentConfig] = None,
    frame_index: int = 0,
) -> List[AccidentCandidate]:
    """检测异常停车（长时间静止）。

    与碰撞不同，异常停车是**单目标**事件，判定依据为
    「轨迹足够长」+「持续近静止」。仅适用于行车道场景，
    若点位本身包含停车区/收费站广场，会产生大量误报 ——
    这类点位应通过配置排除（后续可增加 ROI 掩膜）。
    """
    cfg = config or AccidentConfig()
    results: List[AccidentCandidate] = []

    for track in tracks:
        if track.age < cfg.stop_min_age:
            continue
        if track.class_name not in VEHICLE_CLASSES:
            continue
        if not track.is_stationary(cfg.stationary_speed):
            continue

        # 静止占比：整条轨迹中速度低于阈值的比例
        stationary_points = 0
        for index in range(1, track.age):
            prev, curr = track.points[index - 1], track.points[index]
            dt = max(curr.timestamp - prev.timestamp, 1e-6)
            (x0, y0), (x1, y1) = prev.center, curr.center
            if ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / dt < cfg.stationary_speed:
                stationary_points += 1

        ratio = stationary_points / max(track.age - 1, 1)
        if ratio < 0.8:
            continue

        evidence = CriterionEvidence(
            name='stationary_ratio',
            label='静止时长占比',
            raw_value=ratio,
            threshold=0.8,
            score=min(ratio, 1.0),
            weight=1.0,
            triggered=True,
        )

        results.append(
            AccidentCandidate(
                score=round(ratio, 4),
                track_ids=(track.track_id, track.track_id),
                class_names=(track.class_name, track.class_name),
                frame_index=frame_index,
                timestamp=track.last.timestamp,
                bbox=track.last.bbox,
                evidences=[evidence],
                event_type=EventType.ABNORMAL_STOP,
            )
        )

    results.sort(key=lambda item: item.score, reverse=True)
    return results


# ==========================================================================
# L3：时序确认
# ==========================================================================


class SequentialConfirmer(Protocol):
    """L3 时序确认层协议。

    任何满足此协议的实现都可插入 :class:`AccidentDetector`，
    用于替换默认的投票确认器（例如换成训练好的 LSTM）。
    """

    def update(
        self,
        candidates: Sequence[AccidentCandidate],
        frame_index: int,
    ) -> List[AccidentCandidate]:
        """输入本帧候选，返回**被确认**的候选。"""
        ...

    def reset(self) -> None:
        ...


class VoteConfirmer:
    """滑动窗口投票确认（L3 默认实现）。

    动机
    ----
    单帧评分存在抖动：遮挡、检测框跳动都可能让某帧评分突然越过阈值。
    真实事故则会在**连续多帧**中持续满足判据。

    算法
    ----
    对每个目标对 ``(id_a, id_b)``，在最近 ``window`` 帧内统计
    「评分 ≥ 阈值」的次数；达到 ``min_hits`` 即确认，并把该目标对
    加入冷却名单，避免重复告警。

    这是 FAR（误报率）抑制的主要手段，消融实验中应单独量化其贡献。
    """

    def __init__(
        self,
        window: int = 5,
        min_hits: int = 2,
        score_threshold: float = 0.55,
    ) -> None:
        self.window = window
        self.min_hits = min_hits
        self.score_threshold = score_threshold
        # (id_a, id_b) -> [(frame_index, score), ...]
        self._history: Dict[Tuple[int, int], List[Tuple[int, float]]] = {}
        # 已确认过的目标对，避免重复告警
        self._confirmed: set = set()

    @staticmethod
    def _key(candidate: AccidentCandidate) -> Tuple[int, int]:
        return tuple(sorted(candidate.track_ids))  # type: ignore[return-value]

    def update(
        self,
        candidates: Sequence[AccidentCandidate],
        frame_index: int,
    ) -> List[AccidentCandidate]:
        confirmed: List[AccidentCandidate] = []

        # 1. 记录本帧全部候选
        for candidate in candidates:
            key = self._key(candidate)
            self._history.setdefault(key, []).append((frame_index, candidate.score))

        # 2. 清理窗口外的历史
        lower_bound = frame_index - self.window
        for key in list(self._history.keys()):
            kept = [(f, s) for (f, s) in self._history[key] if f >= lower_bound]
            if kept:
                self._history[key] = kept
            else:
                del self._history[key]

        # 3. 投票确认
        for candidate in candidates:
            key = self._key(candidate)
            if key in self._confirmed:
                continue

            records = self._history.get(key, [])
            hits = sum(1 for _, score in records if score >= self.score_threshold)

            if hits >= self.min_hits:
                confirmed.append(candidate)
                self._confirmed.add(key)

        # 4. 附带投票信息，便于前端展示与调优
        for candidate in confirmed:
            records = self._history.get(self._key(candidate), [])
            candidate.evidences.append(
                CriterionEvidence(
                    name='temporal_vote',
                    label='时序一致性',
                    raw_value=float(len(records)),
                    threshold=float(self.min_hits),
                    score=min(len(records) / max(self.min_hits, 1), 1.0),
                    weight=0.0,       # 不参与评分，仅供展示
                    triggered=True,
                )
            )

        return confirmed

    def reset(self) -> None:
        self._history.clear()
        self._confirmed.clear()


class NoConfirmer:
    """L3 空实现 —— 不做任何时序确认，直接放行。

    用途：**消融实验**。对比 ``VoteConfirmer`` 与 ``NoConfirmer`` 在相同
    数据上的误报情况，可量化时序确认层对 FAR（误报率）的贡献（实验 A2）。

    无 L3 时，任何单帧抖动导致的超阈候选都会直接上报，
    这在真实场景中会产生大量误报。
    """

    def update(
        self,
        candidates: Sequence[AccidentCandidate],
        frame_index: int,
    ) -> List[AccidentCandidate]:
        return list(candidates)

    def reset(self) -> None:
        pass


# ==========================================================================
# 门面：整合 L2 + L3
# ==========================================================================


class AccidentDetector:
    """事故识别门面：串联 L2 规则层与 L3 时序确认层。

    典型用法（逐帧调用）::

        detector = AccidentDetector()
        for frame_index, tracks in enumerate(stream):
            confirmed = detector.process(tracks, frame_index)
            for event in confirmed:
                report(event)
    """

    def __init__(
        self,
        config: Optional[AccidentConfig] = None,
        confirmer: Optional[SequentialConfirmer] = None,
    ) -> None:
        self.config = config or AccidentConfig()
        self.confirmer: SequentialConfirmer = confirmer or VoteConfirmer(
            window=self.config.vote_window,
            min_hits=self.config.vote_min_hits,
            score_threshold=self.config.score_threshold,
        )

    def process(
        self,
        tracks: Sequence[Track],
        frame_index: int = 0,
    ) -> List[AccidentCandidate]:
        """处理一帧：L2 生成候选 → 过滤误报 → L3 时序确认。

        :return: 经确认的事故候选（已去重，同一目标对只报一次）
        """
        candidates = detect_collisions(tracks, self.config, frame_index)
        active = [item for item in candidates if not item.suppressed]
        return self.confirmer.update(active, frame_index)

    def process_stops(
        self,
        tracks: Sequence[Track],
        frame_index: int = 0,
    ) -> List[AccidentCandidate]:
        """检测异常停车（同样经 L3 确认）。"""
        candidates = detect_abnormal_stop(tracks, self.config, frame_index)
        return self.confirmer.update(candidates, frame_index)

    def reset(self) -> None:
        self.confirmer.reset()

    def describe(self) -> Dict[str, object]:
        """算法参数与当前状态，供 API 与前端展示。"""
        return {
            'config': self.config.to_dict(),
            'confirmer': {
                'type': self.confirmer.__class__.__name__,
                'window': self.config.vote_window,
                'minHits': self.config.vote_min_hits,
            },
            'criterions': [
                {'name': 'contact', 'label': '目标接触', 'weight': self.config.weight_contact},
                {'name': 'speed_drop', 'label': '速度骤降', 'weight': self.config.weight_speed_drop},
                {'name': 'ttc', 'label': '碰撞时间', 'weight': self.config.weight_ttc},
                {'name': 'heading_change', 'label': '轨迹畸变', 'weight': self.config.weight_heading},
            ],
        }
