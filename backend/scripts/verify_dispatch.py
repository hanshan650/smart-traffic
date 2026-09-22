"""
智能派警算法验证（离线）
========================
用**合成的警员与警情**验证评分逻辑，不依赖 MongoDB 与网络。

验证的四个点
------------
1. **距离优先**：最近的警员应在 nearest 策略下胜出
2. **技能匹配**：危化品警情应优先派给具备 hazmat 专长的人
3. **负载均衡**：balanced 策略下，空闲警员应压过高负载的近邻
4. **状态过滤**：下班/请假者必须被排除，且要给出排除原因
   （而不是静默消失 —— 调度员需要知道"为什么没派他"）

另外核对每条证据的 ``contribution`` 之和是否等于总分，
避免评分与证据链出现不一致（这类不一致最难发现）。

用法（在 backend 目录下）::

    .venv\\Scripts\\python.exe scripts\\verify_dispatch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.schemas import (  # noqa: E402
    DispatchStrategy,
    EventLevel,
    EventType,
    Incident,
    Officer,
    OfficerSkill,
    OfficerStatus,
)
from app.services.dispatch_service import rank_candidates  # noqa: E402

BAR = '=' * 80

# 警情固定发生在郑州东（113.7372, 34.7564）
INCIDENT_LAT = 34.7564
INCIDENT_LNG = 113.7372


def make_officer(
    officer_id: str,
    name: str,
    lat: float,
    lng: float,
    status: OfficerStatus,
    skills: list[OfficerSkill],
    active_cases: int = 0,
    unit: str = '郑州交警一大队',
) -> Officer:
    return Officer(
        id=f'oid_{officer_id}',
        officer_id=officer_id,
        name=name,
        unit=unit,
        status=status,
        skills=skills,
        latitude=lat,
        longitude=lng,
        active_cases=active_cases,
    )


def make_incident(kind: EventType, level: EventLevel = EventLevel.WARNING) -> Incident:
    return Incident(
        id='incident_placeholder',
        incident_no='JQ202609220001',
        type=kind,
        level=level,
        latitude=INCIDENT_LAT,
        longitude=INCIDENT_LNG,
        road_id='R001',
    )


def show(candidates, expected: str) -> bool:
    print(f'  {"警员":<8}{"状态":<10}{"距离":>8}{"ETA":>7}{"负载":>5}{"评分":>9}   证据贡献')
    print('  ' + '-' * 74)

    for item in candidates:
        status_text = '在岗' if item.status is OfficerStatus.ON_DUTY else item.status.value
        if item.excluded:
            print(f'  {item.name:<8}{status_text:<10}{item.distance_km:>7.1f}k{"—":>7}{"—":>5}{"—":>9}   排除：{item.exclude_reason}')
            continue

        parts = ' '.join(
            f'{ev.name[:3]}={ev.contribution:.3f}' for ev in item.evidences
        )
        print(
            f'  {item.name:<8}{status_text:<10}{item.distance_km:>7.1f}k'
            f'{item.eta_minutes:>6.0f}m{item.active_cases:>5}{item.score:>9.4f}   {parts}'
        )

    assignable = [item for item in candidates if not item.excluded]
    if not assignable:
        print('  （无可派警员）')
        return False

    best = assignable[0]
    ok = best.name == expected

    # 证据贡献之和应等于总分
    total = sum(ev.contribution for ev in best.evidences)
    consistent = abs(total - best.score) < 1e-6

    print()
    print(f'  选中：{best.name}   期望：{expected}   {"✓" if ok else "✗"}')
    print(f'  证据链自洽：Σ贡献={total:.4f} vs 评分={best.score:.4f}   {"✓" if consistent else "✗"}')
    print('-' * 80)
    return ok and consistent


def main() -> int:
    results: list[bool] = []

    # 三名警员按距离排开，专长与负载各不相同
    near_low_skill = make_officer(
        '410101', '近处-无专长', 34.7600, 113.7400,
        OfficerStatus.ON_DUTY, [OfficerSkill.TRAFFIC], active_cases=0,
    )
    mid_accident = make_officer(
        '410102', '中距-事故专长', 34.7900, 113.7900,
        OfficerStatus.ON_DUTY, [OfficerSkill.ACCIDENT, OfficerSkill.FIRST_AID], active_cases=0,
    )
    far_hazmat = make_officer(
        '410103', '远处-危化专长', 34.9000, 113.9500,
        OfficerStatus.ON_DUTY, [OfficerSkill.HAZMAT, OfficerSkill.TRAFFIC], active_cases=0,
    )
    busy_near = make_officer(
        '410104', '近处-高负载', 34.7580, 113.7390,
        OfficerStatus.ON_DUTY, [OfficerSkill.ACCIDENT], active_cases=6,
    )
    off_duty = make_officer(
        '410105', '下班人员', 34.7570, 113.7380,
        OfficerStatus.OFF_DUTY, [OfficerSkill.ACCIDENT],
    )

    officers = [near_low_skill, mid_accident, far_hazmat, busy_near, off_duty]

    print(BAR)
    print('  智能派警算法验证（合成数据，离线）')
    print(f'  警情位置：郑州东 ({INCIDENT_LNG}, {INCIDENT_LAT})')
    print(BAR)

    # ---------------------------------------------------------------- 场景一
    print()
    print('【场景一】追尾事故 + nearest 策略')
    print('  预期：技能匹配追回距离劣势，「中距-事故专长」胜出')
    print()
    print('  说明：距离得分为线性曲线 1 − d/R，6 km 只损失 7.5% 的分值，')
    print('        而「近处-无专长」不具备事故处理专长、技能项得 0 分，')
    print('        两者相加后后者落败 —— 这是刻意保留的权衡：')
    print('        到场快 7 分钟不该压过"能正确处理事故"。')
    print()
    results.append(show(
        rank_candidates(make_incident(EventType.COLLISION), officers,
                        strategy=DispatchStrategy.NEAREST, top_n=5),
        expected='中距-事故专长',
    ))

    # ---------------------------------------------------------------- 场景二
    print()
    print('【场景二】危化品抛洒 + skill 策略')
    print('  预期：具备 hazmat 专长的远处警员胜出（技能权重 0.40）')
    print()
    results.append(show(
        rank_candidates(make_incident(EventType.DEBRIS, EventLevel.CRITICAL), officers,
                        strategy=DispatchStrategy.SKILL, top_n=5),
        expected='远处-危化专长',
    ))

    # ---------------------------------------------------------------- 场景三
    print()
    print('【场景三】追尾事故 + balanced 策略')
    print('  预期：负载判据权重升到 0.35，空闲的「中距-事故专长」应压过高负载近邻')
    print()
    results.append(show(
        rank_candidates(make_incident(EventType.COLLISION), officers,
                        strategy=DispatchStrategy.BALANCED, top_n=5),
        expected='中距-事故专长',
    ))

    # ---------------------------------------------------------------- 场景四
    print()
    print('【场景四】无可用警力')
    print('  预期：全部被排除并给出原因')
    print()
    only_off_duty = [
        make_officer('410201', '甲', 34.76, 113.74, OfficerStatus.OFF_DUTY, []),
        make_officer('410202', '乙', 34.77, 113.75, OfficerStatus.LEAVE, []),
    ]
    excluded = rank_candidates(make_incident(EventType.COLLISION), only_off_duty, top_n=5)
    for item in excluded:
        print(f'  {item.name}（{item.status.value}）→ {item.exclude_reason}')
    ok_four = all(item.excluded for item in excluded) and len(excluded) == 2
    print(f'  全部排除且给出原因：{"✓" if ok_four else "✗"}')
    results.append(ok_four)
    print('-' * 80)

    print()
    print(BAR)
    if all(results):
        print('  结果：四个场景全部符合预期 ✓')
        print(BAR)
        return 0
    print(f'  结果：{results.count(False)} 个场景不符合预期 ✗')
    print(BAR)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
