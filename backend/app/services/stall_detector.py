"""
静止（违停 / 抛锚）检测
=======================

判定目标
--------
**在禁止停车的路段上，有车辆持续静止超过阈值时长。**

这是高速公路场景下最有价值的异常之一 —— 它同时覆盖三类情况：

  · 违法停车（应急车道停车、行车道停车）
  · 车辆故障抛锚
  · 事故后车辆停在原地（与碰撞检测互为补充：碰撞检测要求「速度骤降」，
    而有些事故只有静止、没有可观测的骤降过程）

为什么不直接用「速度 == 0」
---------------------------
三个原因，缺一不可：

1. **检测框抖动**。低分辨率下同一个静止目标的 bbox 中心会在 1~3 像素内
   随机跳，逐帧差分会算出"速度"，永远不等于 0。因此判据不是瞬时速度，
   而是「**尾部窗口内相对锚点的最大偏离**」—— 抖动是零均值的，锚点法能
   把它自然抹掉。
2. **缓慢漂移**。摄像头云台微动、车辆被推挤等会让目标以极低速度持续移动。
   锚点法（与固定锚点比较）会累积这类漂移，逐帧差分则不会。这正是我们要的：
   缓慢漂移**应当**被判为移动。
3. **时间基准**。判定的是「静止了多久」，必须用**真实墙钟时间**。
   抽帧是分批进行的（一轮若干帧，轮次之间有间隔），若用「帧号 / 帧率」
   推算时间，轮次间的空隙会被完全忽略，30 秒的判定会严重失真。

判据与抑制
----------
评分（加权求和，满分 1.0）：

  ======================  ======  ============================================
  判据                     权重    含义
  ======================  ======  ============================================
  ``stationary``           0.50   静止持续时长相对阈值的富余度（核心判据）
  ``stillness``            0.25   静止程度：偏离越小得分越高
  ``isolation``            0.15   周边无同步静止车辆 —— 越"孤立"越像单车违停
  ``clear_of_edge``        0.10   不贴近画面边界 —— 贴边可能是目标正在驶出画面
  ======================  ======  ============================================

**硬前提**：静止时长必须达到阈值，否则直接不报警，不进入评分。
这与事故识别「接触 ∨ 速度骤降 必须命中其一」的处理方式一致 ——
核心判据不满足时，其余判据再高也没有意义。

抑制（直接排除，不参与评分）：

  · **场所豁免** —— 收费站、服务区、检查站、停车场内停车是合法的。
    这是唯一允许"完全放行"的抑制条件，因为它基于确定性的地点属性。
  · **同步静止（拥堵排队）** —— 画面中大部分车辆同时静止，说明是排队，
    不是违停。高速公路上堵车时若不做此过滤，会在几分钟内刷出上百条告警。

**不做的抑制（重要）**：本算法**不使用信号灯等时相信息**。目标场景是
高速公路主线，不存在信号灯；引入一个用不上的判据只会增加解释成本。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.models.schemas import BBox
from app.services.geometry import bbox_center
from app.services.scene_text import ZONE_TYPE_LABEL
from app.services.tracking import Track

# ==========================================================================
# 配置
# ==========================================================================


@dataclass
class StallConfig:
    """静止检测参数。

    :param stall_seconds: **核心阈值** —— 静止多久算异常。需求给定 30 秒。
        这是可解释性最强的一个参数，前端会直接展示它。
    :param displacement_threshold: 判定"没动"的归一化位移阈值。
        归一化坐标下车宽约 0.04（352 px 画面里约 15 px），
        本阈值 0.01 相当于约 3.5 像素 —— 略高于检测框抖动幅度，
        又远小于一次真实移动，是"像素级静止"与"行驶"之间的分界。
    :param max_edge_touch: 贴边判定的容差。bbox 中心距画面边界小于该值
        视为贴近边缘。
    :param crowd_ratio: 同步静止占比达到该值即判为拥堵排队并抑制。
        0.6 意味着画面里六成以上车辆都不动 —— 单车违停不可能造成这种局面。
    :param crowd_min_vehicles: 触发同步静止抑制所需的最少车辆数。
        画面里只有 1~2 辆车时谈不上"排队"，不该套用该抑制。
    :param count_min_stationary: 统计
        "同步静止车辆数"时的最短静止时长（秒）。

        **这个参数不能省。** ``stationary_span`` 只要两帧之间位移小于阈值就会
        返回一个大于 0 的时长（约两帧间隔，几十毫秒），若拿
        ``seconds > 0`` 去统计静止车辆数，会把**所有行驶中的慢车**都算进来，
        于是 ``crowd_ratio`` 永远接近 100%，拥堵抑制被误触发，真违停反而被压掉。

        取 2 秒是一个经验的"确实停住了"下界：正常行驶的车不会连续 2 秒
        位移小于 3.5 像素。
    :param min_track_duration: 轨迹最短时长。低于该值的轨迹不足以支撑
        「静止 30 秒」的判断（轨迹本身都还没活够 30 秒）。
    :param max_observation_gap: **观测新鲜度上限（秒）**。轨迹的最后一次
        观测距当前超过该值就不再参与判定。

        这个参数是为持续检测专门加的，理由是：巡检是分批抓帧的，
        跟踪器为了跨轮次保持 ID 会把 ``max_age`` 设得较大。于是车辆驶出
        画面后，轨迹并不会立即消失，而是带着**旧的末尾观测**继续存在。
        此时 ``stationary_span`` 会拿一个很旧的时间戳去算静止时长，
        结果就是「一辆早已离开的车静止了 5 分钟」这种荒谬告警。

        用观测新鲜度一刀切开：真静止的车每轮都有新观测，通过的；
        已驶离的车没有新观测，排除。
    :param score_threshold: 判定为异常所需的最低综合得分。
    :param vulnerable_penalty: 行人 / 非机动车折减系数。行人静止在路侧
        属正常现象，不应触发违停告警。
    """

    stall_seconds: float = 30.0
    displacement_threshold: float = 0.01
    max_edge_touch: float = 0.05
    crowd_ratio: float = 0.6
    crowd_min_vehicles: int = 3
    count_min_stationary: float = 2.0
    min_track_duration: float = 30.0
    max_observation_gap: float = 20.0
    score_threshold: float = 0.60
    vulnerable_penalty: float = 0.5

    # 判据权重
    weight_stationary: float = 0.50
    weight_stillness: float = 0.25
    weight_isolation: float = 0.15
    weight_edge: float = 0.10

    def weights(self) -> Dict[str, float]:
        return {
            'stationary': self.weight_stationary,
            'stillness': self.weight_stillness,
            'isolation': self.weight_isolation,
            'clear_of_edge': self.weight_edge,
        }


#: 参与静止判定的目标类别。行人、非机动车不适用违停语义。
STALL_CLASSES = frozenset({'car', 'truck', 'bus', 'motorcycle', 'train'})


# ==========================================================================
# 证据链
# ==========================================================================


@dataclass
class StallEvidence:
    """一条判据的完整计算过程。

    与事故识别保持同一结构（原始值 / 归一化得分 / 权重 / 贡献），
    理由也一样：**结论必须能追溯到每一分的来源**，否则值班员无法采信。
    """

    name: str
    label: str
    raw_value: float
    score: float
    weight: float
    contribution: float
    detail: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'label': self.label,
            'rawValue': round(self.raw_value, 4),
            'score': round(self.score, 4),
            'weight': round(self.weight, 4),
            'contribution': round(self.contribution, 4),
            'detail': self.detail,
        }


@dataclass
class StallVerdict:
    """单个目标的静止判定结果。"""

    track_id: int
    class_name: str
    bbox: Optional[BBox]
    #: 尾部连续静止时长（秒）—— 界面上的"已静止 XX 秒"
    stationary_seconds: float
    #: 静止窗口内相对锚点的最大偏离（归一化）
    displacement: float
    #: 轨迹总时长（秒）
    track_duration: float
    #: 综合得分
    score: float
    #: 是否报警
    alert: bool
    #: 是否被抑制
    suppressed: bool
    suppress_reason: str = ''
    evidences: List[StallEvidence] = field(default_factory=list)
    #: 所在场所
    zone_type: str = 'highway'
    zone_label: str = ''

    @property
    def contribution_sum(self) -> float:
        """证据链自洽性检查：各判据贡献之和。"""
        return sum(item.contribution for item in self.evidences)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'trackId': self.track_id,
            'className': self.class_name,
            'bbox': self.bbox.model_dump() if self.bbox else None,
            'stationarySeconds': round(self.stationary_seconds, 2),
            'displacement': round(self.displacement, 5),
            'trackDuration': round(self.track_duration, 2),
            'score': round(self.score, 4),
            'alert': self.alert,
            'suppressed': self.suppressed,
            'suppressReason': self.suppress_reason,
            'zoneType': self.zone_type,
            'zoneLabel': self.zone_label,
            'evidences': [item.to_dict() for item in self.evidences],
        }


@dataclass
class StallOutcome:
    """一次画面级静止检测的总结果。"""

    verdicts: List[StallVerdict] = field(default_factory=list)
    #: 画面中确认轨迹数（用于计算同步静止占比）
    track_count: int = 0
    #: 静止目标数（含被抑制的）
    stationary_count: int = 0
    #: 同步静止占比
    crowd_ratio: float = 0.0
    #: 是否判定为拥堵排队
    crowded: bool = False
    zone_type: str = 'highway'
    zone_label: str = ''
    zone_exempt: bool = False
    zone_message: str = ''

    @property
    def alerts(self) -> List[StallVerdict]:
        return [item for item in self.verdicts if item.alert]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'trackCount': self.track_count,
            'stationaryCount': self.stationary_count,
            'crowdRatio': round(self.crowd_ratio, 4),
            'crowded': self.crowded,
            'zoneType': self.zone_type,
            'zoneLabel': self.zone_label,
            'zoneExempt': self.zone_exempt,
            'zoneMessage': self.zone_message,
            'verdicts': [item.to_dict() for item in self.verdicts],
        }


# ==========================================================================
# 静止窗口
# ==========================================================================


def stationary_span(
    track: Track,
    displacement_threshold: float,
) -> tuple[int, float, float]:
    """求轨迹尾部的连续静止段。

    以**最后一个观测点作为锚点**，从后向前扫描，直到某个点偏离锚点超过
    阈值为止。返回该段的起点索引、持续时长（秒）与最大偏离。

    用锚点而非逐帧差分的原因见模块文档：抖动是零均值的（锚点法能消除），
    漂移是累积的（锚点法能保留，正是我们想要的语义）。

    :return: ``(起点索引, 持续时长秒, 最大偏离)``
    """
    points = track.points
    if len(points) < 2:
        return (0, 0.0, 0.0)

    anchor_x, anchor_y = points[-1].center
    start = len(points) - 1
    max_offset = 0.0

    for index in range(len(points) - 2, -1, -1):
        x, y = points[index].center
        offset = math.hypot(x - anchor_x, y - anchor_y)
        if offset > displacement_threshold:
            break
        max_offset = max(max_offset, offset)
        start = index

    duration = points[-1].timestamp - points[start].timestamp
    return (start, max(duration, 0.0), max_offset)


def _edge_touch(bbox: Optional[BBox]) -> float:
    """bbox 距画面边界的最近距离（归一化）。越小越贴边。"""
    if bbox is None:
        return 1.0
    return min(bbox.x, bbox.y, 1.0 - (bbox.x + bbox.w), 1.0 - (bbox.y + bbox.h))


# ==========================================================================
# 判据
# ==========================================================================


def _stationary_evidence(seconds: float, config: StallConfig) -> StallEvidence:
    """静止时长判据（核心）。达到阈值即满分，超出部分不再加分。"""
    ratio = seconds / config.stall_seconds if config.stall_seconds > 0 else 0.0
    score = min(max(ratio, 0.0), 1.0)
    weight = config.weight_stationary
    return StallEvidence(
        name='stationary',
        label='静止时长',
        raw_value=seconds,
        score=score,
        weight=weight,
        contribution=score * weight,
        detail=f'已静止 {seconds:.1f} 秒（阈值 {config.stall_seconds:.0f} 秒）',
    )


def _stillness_evidence(displacement: float, config: StallConfig) -> StallEvidence:
    """静止程度判据。偏离越小得分越高。"""
    threshold = config.displacement_threshold
    score = 1.0 - min(displacement / threshold, 1.0) if threshold > 0 else 0.0
    weight = config.weight_stillness
    return StallEvidence(
        name='stillness',
        label='静止程度',
        raw_value=displacement,
        score=score,
        weight=weight,
        contribution=score * weight,
        detail=f'锚点最大偏离 {displacement:.4f}（判定阈值 {threshold}）',
    )


def _isolation_evidence(crowd_ratio: float, config: StallConfig) -> StallEvidence:
    """孤立性判据。周边同步静止车辆越少，越像单车违停。"""
    score = 1.0 - min(crowd_ratio / config.crowd_ratio, 1.0) if config.crowd_ratio > 0 else 1.0
    weight = config.weight_isolation
    return StallEvidence(
        name='isolation',
        label='孤立性',
        raw_value=crowd_ratio,
        score=score,
        weight=weight,
        contribution=score * weight,
        detail=f'画面同步静止占比 {crowd_ratio:.0%}（拥堵判定线 {config.crowd_ratio:.0%}）',
    )


def _edge_evidence(bbox: Optional[BBox], config: StallConfig) -> StallEvidence:
    """边界判据。贴边可能是目标正在驶出画面，检测框被截断后看着像静止。"""
    touch = _edge_touch(bbox)
    score = min(touch / config.max_edge_touch, 1.0) if config.max_edge_touch > 0 else 1.0
    weight = config.weight_edge
    return StallEvidence(
        name='clear_of_edge',
        label='远离边界',
        raw_value=touch,
        score=score,
        weight=weight,
        contribution=score * weight,
        detail=(
            f'bbox 距画面边界 {touch:.3f}'
            + ('（贴边，可能正在驶出画面）' if touch < config.max_edge_touch else '')
        ),
    )


# ==========================================================================
# 主流程
# ==========================================================================


def detect_stalls(
    tracks: Sequence[Track],
    *,
    config: Optional[StallConfig] = None,
    zone_type: str = 'highway',
    zone_message: str = '',
    parking_allowed: bool = False,
    now: Optional[float] = None,
) -> StallOutcome:
    """在一帧序列的轨迹集合上检测静止车辆。

    :param tracks: 当前画面中的**已确认轨迹**（通常来自 ``IoUTracker.update``）
    :param zone_type: 点位场所类型，来自 :mod:`app.services.scene_text`
    :param parking_allowed: 该场所是否允许停车（收费站 / 服务区等）
    :param now: 当前时刻（与轨迹时间戳同基准的秒数）。传入后会启用
        **观测新鲜度检查**，剔除那些已经没有新观测、却因跟踪器
        ``max_age`` 而残留的轨迹。持续巡检场景**必须传该参数**。
    """
    config = config or StallConfig()
    outcome = StallOutcome(
        track_count=len(tracks),
        zone_type=zone_type,
        zone_label=ZONE_TYPE_LABEL.get(zone_type, zone_type),
        zone_exempt=parking_allowed,
        zone_message=zone_message,
    )

    # ---- 第一遍：找出所有静止目标，用于计算同步静止占比 ----
    # 占比必须在评分前算出来，因为"孤立性"判据依赖它 —— 这是判据之间的
    # 唯一依赖关系，刻意保持单向，避免出现循环引用难以解释。
    raw: List[tuple[Track, int, float, float]] = []
    for track in tracks:
        if track.class_name not in STALL_CLASSES:
            continue
        # 观测新鲜度：最后一帧观测太旧说明目标已离开画面，
        # 而不是停在原地（详见 StallConfig.max_observation_gap）
        if now is not None and (now - track.last.timestamp) > config.max_observation_gap:
            continue
        start, seconds, offset = stationary_span(track, config.displacement_threshold)
        if seconds > 0:
            raw.append((track, start, seconds, offset))

    # 同步静止占比只用"确实停住了"的目标来算。
    # 拿 seconds > 0 去统计会把所有慢速行驶的车都算成静止（见
    # StallConfig.count_min_stationary），使占比虚高、拥堵抑制被误触发。
    genuinely_stationary = sum(
        1 for _t, _s, seconds, _o in raw if seconds >= config.count_min_stationary
    )
    outcome.stationary_count = genuinely_stationary
    if outcome.track_count > 0:
        outcome.crowd_ratio = genuinely_stationary / outcome.track_count
    outcome.crowded = (
        outcome.track_count >= config.crowd_min_vehicles
        and outcome.crowd_ratio >= config.crowd_ratio
    )

    # ---- 第二遍：逐目标评分 ----
    for track, _start, seconds, offset in raw:
        bbox = track.last.bbox
        evidences = [
            _stationary_evidence(seconds, config),
            _stillness_evidence(offset, config),
            _isolation_evidence(outcome.crowd_ratio, config),
            _edge_evidence(bbox, config),
        ]

        # 硬前提：静止时长不达标直接出局，不进入评分。
        # 核心判据不满足时其余判据没有意义 —— 与事故识别同一处理方式。
        if seconds < config.stall_seconds:
            outcome.verdicts.append(
                StallVerdict(
                    track_id=track.track_id,
                    class_name=track.class_name,
                    bbox=bbox,
                    stationary_seconds=seconds,
                    displacement=offset,
                    track_duration=track.duration,
                    score=0.0,
                    alert=False,
                    suppressed=True,
                    suppress_reason=(
                        f'静止 {seconds:.1f} 秒未达阈值 {config.stall_seconds:.0f} 秒'
                    ),
                    evidences=evidences,
                    zone_type=zone_type,
                    zone_label=outcome.zone_label,
                )
            )
            continue

        score = sum(item.contribution for item in evidences)

        if track.class_name in ('pedestrian',):
            score *= config.vulnerable_penalty

        suppressed = False
        reason = ''
        if parking_allowed:
            suppressed = True
            reason = (
                f'{outcome.zone_label}内停车属正常行为'
                + (f'（{zone_message}）' if zone_message else '')
            )
        elif outcome.crowded:
            suppressed = True
            reason = (
                f'画面中 {outcome.stationary_count}/{outcome.track_count} 个目标'
                f'同步静止（{outcome.crowd_ratio:.0%}），判定为拥堵排队而非违停'
            )
        elif score < config.score_threshold:
            suppressed = True
            reason = f'综合得分 {score:.3f} 低于阈值 {config.score_threshold}'

        outcome.verdicts.append(
            StallVerdict(
                track_id=track.track_id,
                class_name=track.class_name,
                bbox=bbox,
                stationary_seconds=seconds,
                displacement=offset,
                track_duration=track.duration,
                score=score,
                alert=not suppressed,
                suppressed=suppressed,
                suppress_reason=reason,
                evidences=evidences,
                zone_type=zone_type,
                zone_label=outcome.zone_label,
            )
        )

    # 报警的排在前面，其次按静止时长降序 —— 值班员最关心"停了最久的"
    outcome.verdicts.sort(key=lambda item: (not item.alert, -item.stationary_seconds))
    return outcome


# ==========================================================================
# 自描述
# ==========================================================================


def describe(config: Optional[StallConfig] = None) -> Dict[str, Any]:
    """返回判据、权重与参数说明，供前端渲染算法卡片。"""
    config = config or StallConfig()
    return {
        'name': '静止（违停/抛锚）检测',
        'scenario': '高速公路主线',
        'criterions': [
            {
                'name': 'stationary',
                'label': '静止时长',
                'formula': f't_still >= {config.stall_seconds:.0f}s（硬前提）',
                'rationale': (
                    '核心判据。用真实墙钟时间而非帧号推算 —— 抽帧分批进行，'
                    '轮次之间的空隙若被忽略，30 秒判定会失真'
                ),
            },
            {
                'name': 'stillness',
                'label': '静止程度',
                'formula': f'max|p_i − p_anchor| < {config.displacement_threshold}',
                'rationale': (
                    '与固定锚点比较而非逐帧差分：低分辨率下 bbox 抖动会让'
                    '逐帧差分算出虚假速度，而抖动是零均值的，锚点法可自然消除'
                ),
            },
            {
                'name': 'isolation',
                'label': '孤立性',
                'formula': '1 − crowd_ratio / 抑制线',
                'rationale': '单车静止才是违停；整片同步静止是排队',
            },
            {
                'name': 'clear_of_edge',
                'label': '远离边界',
                'formula': 'min(边距) / 容差',
                'rationale': '贴边目标的检测框被截断，可能只是正在驶出画面',
            },
        ],
        'suppressions': [
            {
                'name': 'zone_exempt',
                'label': '可停车场所豁免',
                'detail': '收费站、服务区、检查站、停车场内停车合法',
            },
            {
                'name': 'crowd',
                'label': '同步静止（拥堵排队）',
                'detail': (
                    f'同步静止占比 >= {config.crowd_ratio:.0%} 且车辆数 >= '
                    f'{config.crowd_min_vehicles} 时抑制'
                ),
            },
        ],
        'weights': config.weights(),
        'config': {
            'stallSeconds': config.stall_seconds,
            'displacementThreshold': config.displacement_threshold,
            'maxObservationGap': config.max_observation_gap,
            'countMinStationary': config.count_min_stationary,
            'scoreThreshold': config.score_threshold,
            'minTrackDuration': config.min_track_duration,
        },
    }
