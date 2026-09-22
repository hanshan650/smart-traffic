"""
智能派警服务
============
在候选警员中按多判据融合评分选出最优派警对象。

设计哲学：可解释
----------------
派警是**直接影响真人**的决策，必须能回答"为什么派给他而不是别人"，
否则调度员无法信任、被派警员也无法申辩。因此本模块与事故识别算法
采用同一套设计：每条判据都保留「原始值 / 归一化得分 / 权重 / 贡献」，
一次派警返回完整的候选评分明细，而不是只给一个名字。

四条判据
--------
  1. **距离**   —— 逆序归一化，超出半径得 0（否则"极远但技能强"会胜出）
  2. **技能**   —— 与警情类型所需专长的匹配率
  3. **负载**   —— 在办案件数，用 ``1/(1+n)`` 衰减（避免所有人集中到一个人）
  4. **状态**   —— 在岗 1.0 / 出警中 0.5

前两项决定"能不能快速有效地处置"，后两项决定"合不合理"。
三者缺一都会出现荒谬的派警结果（如把 60 公里外的人派给 3 公里外的警情）。

三种策略
--------
针对不同指挥偏好提供预设权重，而不是让用户直接调权重 ——
权重是专业参数，暴露给操作员只会造成误配。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.models.schemas import (
    DispatchCandidate,
    DispatchEvidence,
    DispatchRequest,
    DispatchStrategy,
    EventType,
    Incident,
    Officer,
    OfficerSkill,
    OfficerStatus,
)
from app.services import officer_service
from app.services.geometry import haversine_km

# 警情类型 → 所需专长。用于「技能匹配」判据。
_REQUIRED_SKILLS: Dict[EventType, List[OfficerSkill]] = {
    EventType.COLLISION: [OfficerSkill.ACCIDENT, OfficerSkill.FIRST_AID],
    EventType.ABNORMAL_STOP: [OfficerSkill.TRAFFIC],
    EventType.WRONG_WAY: [OfficerSkill.TRAFFIC],
    EventType.PEDESTRIAN: [OfficerSkill.TRAFFIC, OfficerSkill.FIRST_AID],
    EventType.DEBRIS: [OfficerSkill.HAZMAT, OfficerSkill.TRAFFIC],
    EventType.CONGESTION: [OfficerSkill.TRAFFIC],
}

# 状态权重：出警中的警员仍可派（就近支援），但明显降权
_STATUS_SCORE: Dict[OfficerStatus, float] = {
    OfficerStatus.ON_DUTY: 1.0,
    OfficerStatus.BUSY: 0.5,
}

# 不参与派警的状态
_EXCLUDED_STATUS = {OfficerStatus.OFF_DUTY, OfficerStatus.LEAVE}

# 策略 → 各判据权重。刻意不暴露给前端调参（见模块文档）。
_STRATEGY_WEIGHTS: Dict[DispatchStrategy, Dict[str, float]] = {
    # 就近优先：默认策略，到场时间最短
    DispatchStrategy.NEAREST: {'distance': 0.60, 'skill': 0.15, 'workload': 0.15, 'status': 0.10},
    # 负载均衡：避免少数人连轴转
    DispatchStrategy.BALANCED: {'distance': 0.35, 'skill': 0.15, 'workload': 0.35, 'status': 0.15},
    # 技能优先：危化品、重伤等需要专业能力时使用
    DispatchStrategy.SKILL: {'distance': 0.30, 'skill': 0.40, 'workload': 0.15, 'status': 0.15},
}


@dataclass
class DispatchConfig:
    """派警评分的物理参数。"""

    # 超出该距离时距离得分归零。设上限是为了避免
    # "60 公里外但技能满配"的人胜出 —— 到场太晚则无意义。
    max_radius_km: float = 80.0
    # ETA 估算速度（km/h）。高速场景偏快、城市道路偏慢，取折中值。
    avg_speed_kmh: float = 55.0


@dataclass
class DispatchPlan:
    """一次派警规划的结果。"""

    strategy: str
    total_candidates: int = 0
    candidates: List[DispatchCandidate] = field(default_factory=list)
    selected: Optional[DispatchCandidate] = None
    reason: str = ''


# ==========================================================================
# 单条判据
# ==========================================================================

def _distance_evidence(distance_km: float, config: DispatchConfig, weight: float) -> DispatchEvidence:
    score = max(0.0, 1.0 - distance_km / config.max_radius_km) if config.max_radius_km else 0.0
    return DispatchEvidence(
        name='distance',
        label='距离',
        raw_value=round(distance_km, 2),
        score=round(score, 4),
        weight=weight,
        contribution=round(score * weight, 4),
        detail=f'{distance_km:.1f} km（有效半径 {config.max_radius_km:.0f} km）',
    )


def _skill_evidence(
    officer: Officer,
    required: List[OfficerSkill],
    weight: float,
) -> tuple[DispatchEvidence, List[OfficerSkill]]:
    owned = set(officer.skills)
    matched = [item for item in required if item in owned]

    if required:
        score = len(matched) / len(required)
    else:
        # 警情类型未定义所需专长时不做筛选，给满分避免误伤
        score = 1.0

    required_text = '、'.join(item.value for item in required) or '无特定要求'
    matched_text = '、'.join(item.value for item in matched) or '无'

    return (
        DispatchEvidence(
            name='skill',
            label='技能匹配',
            raw_value=float(len(matched)),
            score=round(score, 4),
            weight=weight,
            contribution=round(score * weight, 4),
            detail=f'需要 [{required_text}]，命中 [{matched_text}]',
        ),
        matched,
    )


def _workload_evidence(active_cases: int, weight: float) -> DispatchEvidence:
    # 1/(1+n)：0 案得 1.0，1 案得 0.5，2 案得 0.33 —— 前期下降快，
    # 符合"从空闲到忙碌"的实际影响远大于"从忙到更忙"的直觉。
    score = 1.0 / (1.0 + max(0, active_cases))
    return DispatchEvidence(
        name='workload',
        label='负载',
        raw_value=float(active_cases),
        score=round(score, 4),
        weight=weight,
        contribution=round(score * weight, 4),
        detail=f'在办 {active_cases} 件',
    )


def _status_evidence(status: OfficerStatus, weight: float) -> DispatchEvidence:
    score = _STATUS_SCORE.get(status, 0.0)
    label = '在岗' if status is OfficerStatus.ON_DUTY else '出警中'
    return DispatchEvidence(
        name='status',
        label='勤务状态',
        raw_value=1.0 if status is OfficerStatus.ON_DUTY else 0.0,
        score=round(score, 4),
        weight=weight,
        contribution=round(score * weight, 4),
        detail=label,
    )


# ==========================================================================
# 排序（纯函数，便于离线验证）
# ==========================================================================

def rank_candidates(
    incident: Incident,
    officers: List[Officer],
    strategy: DispatchStrategy = DispatchStrategy.NEAREST,
    top_n: int = 5,
    config: Optional[DispatchConfig] = None,
) -> List[DispatchCandidate]:
    """对候选警员打分排序。**不做数据库访问**，便于离线验证。

    返回全部候选（含被排除者，带 ``exclude_reason``），
    调用方按需截取 —— 让"为什么没派他"同样可见。
    """
    config = config or DispatchConfig()
    weights = _STRATEGY_WEIGHTS.get(strategy, _STRATEGY_WEIGHTS[DispatchStrategy.NEAREST])
    required = _REQUIRED_SKILLS.get(incident.type, [])

    candidates: List[DispatchCandidate] = []

    for officer in officers:
        distance = haversine_km(
            incident.latitude, incident.longitude, officer.latitude, officer.longitude
        )
        eta = distance / config.avg_speed_kmh * 60.0 if config.avg_speed_kmh else 0.0

        # 状态不符的直接排除，但仍出现在结果中（供人工核查）
        if officer.status in _EXCLUDED_STATUS:
            candidates.append(DispatchCandidate(
                officer_id=officer.officer_id,
                name=officer.name,
                unit=officer.unit,
                status=officer.status,
                distance_km=round(distance, 2),
                eta_minutes=round(eta, 1),
                active_cases=officer.active_cases,
                score=0.0,
                excluded=True,
                exclude_reason=f'当前状态为「{officer.status.value}」，不参与派警',
            ))
            continue

        distance_ev = _distance_evidence(distance, config, weights['distance'])
        skill_ev, matched = _skill_evidence(officer, required, weights['skill'])
        workload_ev = _workload_evidence(officer.active_cases, weights['workload'])
        status_ev = _status_evidence(officer.status, weights['status'])

        evidences = [distance_ev, skill_ev, workload_ev, status_ev]
        score = sum(item.contribution for item in evidences)

        candidates.append(DispatchCandidate(
            officer_id=officer.officer_id,
            name=officer.name,
            unit=officer.unit,
            status=officer.status,
            distance_km=round(distance, 2),
            eta_minutes=round(eta, 1),
            active_cases=officer.active_cases,
            matched_skills=matched,
            score=round(score, 4),
            evidences=evidences,
        ))

    # 可派者按分数降序，被排除者沉底（但仍可见）
    candidates.sort(key=lambda item: (item.excluded, -item.score))
    return candidates[: max(1, top_n)] + [item for item in candidates[top_n:] if item.excluded]


def plan_dispatch(
    incident: Incident,
    request: Optional[DispatchRequest] = None,
) -> DispatchPlan:
    """读取警员数据并生成派警方案（含落库前的候选明细）。"""
    request = request or DispatchRequest()
    strategy = request.strategy

    try:
        officers = officer_service.list_available()
    except officer_service.OfficerError:
        officers = []

    if not officers:
        return DispatchPlan(
            strategy=strategy.value,
            reason='当前没有可派警力（无在岗/出警中的警员，或警员库为空）',
        )

    candidates = rank_candidates(
        incident, officers, strategy=strategy, top_n=request.top_n
    )
    assignable = [item for item in candidates if not item.excluded]

    # 指定警员的场景：把该人提到首位，其余保留供对照
    if request.officer_id:
        picked = next(
            (item for item in assignable if item.officer_id == request.officer_id), None
        )
        if picked is None:
            return DispatchPlan(
                strategy=strategy.value,
                total_candidates=len(candidates),
                candidates=candidates,
                reason=f'指定的警员 {request.officer_id} 不在可派列表中（不存在或状态不符）',
            )
        for item in assignable:
            item.selected = item is picked
        return DispatchPlan(
            strategy=f'{strategy.value}/manual',
            total_candidates=len(candidates),
            candidates=candidates,
            selected=picked,
            reason='按指定警员派单',
        )

    if not assignable:
        return DispatchPlan(
            strategy=strategy.value,
            total_candidates=len(candidates),
            candidates=candidates,
            reason='候选警员均不可派（状态不符）',
        )

    assignable[0].selected = True
    best = assignable[0]
    return DispatchPlan(
        strategy=strategy.value,
        total_candidates=len(candidates),
        candidates=candidates,
        selected=best,
        reason=(
            f'策略「{strategy.value}」下评分最高：{best.name}'
            f'（{best.distance_km:.1f} km，预计 {best.eta_minutes:.0f} 分钟到场，'
            f'综合评分 {best.score:.4f}）'
        ),
    )


# ==========================================================================
# 对外说明（供 API 返回）
# ==========================================================================

def describe() -> Dict[str, Any]:
    """派警算法的自描述，供前端渲染说明卡片。"""
    return {
        'criterions': [
            {
                'name': 'distance',
                'label': '距离',
                'formula': '1 − d / R，R 为有效半径',
                'rationale': '到场时间是派警的第一要素；超出半径直接归零，'
                             '避免"极远但技能强"的人胜出',
            },
            {
                'name': 'skill',
                'label': '技能匹配',
                'formula': '命中的所需专长数 / 所需专长总数',
                'rationale': '危化品、重伤等警情需要专业处置能力',
            },
            {
                'name': 'workload',
                'label': '负载',
                'formula': '1 / (1 + 在办案件数)',
                'rationale': '前期下降快，符合"从空闲到忙碌"的影响大于'
                             '"从忙到更忙"的直觉，避免少数人连轴转',
            },
            {
                'name': 'status',
                'label': '勤务状态',
                'formula': '在岗 1.0 / 出警中 0.5',
                'rationale': '出警中的人仍可指派（就近支援），但明显降权',
            },
        ],
        'strategies': [
            {'key': key.value, 'weights': values}
            for key, values in _STRATEGY_WEIGHTS.items()
        ],
        'config': {
            'maxRadiusKm': DispatchConfig().max_radius_km,
            'avgSpeedKmh': DispatchConfig().avg_speed_kmh,
        },
    }
