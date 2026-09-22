"""
事故识别算法验证脚本
====================

跑通全部仿真场景，校验判定结果是否符合预期。

用法（在 backend 目录下）::

    .venv\\Scripts\\python.exe scripts\\verify_algorithm.py

设计意图
--------
算法不能只"看起来对"。本脚本用**合成轨迹**覆盖四类场景：

  +--------------------+----------+--------------------------------------+
  | 场景               | 期望     | 考察点                               |
  +====================+==========+======================================+
  | collision          | 检出     | 正样本：四条判据能否同时命中          |
  | normal_following   | 不检出   | 负样本：匀速跟车不应误报              |
  | red_light          | 不检出   | 误报抑制：多车同步减速规则是否生效    |
  | lane_change        | 不检出   | 硬性前提：仅轨迹畸变不足以判定事故    |
  +--------------------+----------+--------------------------------------+

由于真实事故样本极度稀缺（且不可控），这种"构造式验证"是算法开发
阶段的主要正确性保障手段。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接以脚本方式运行（把 backend/ 加入 sys.path）
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import accident_service  # noqa: E402


BAR = '=' * 78


def _print_header() -> None:
    print(BAR)
    print('  事故识别算法验证 —— 三级级联（L1 感知 / L2 规则 / L3 时序确认）')
    print(BAR)
    print()


def _print_event_detail(event: dict) -> None:
    """打印一条事件的证据链，展示判据贡献。"""
    print(f"    评分 {event['score']:.4f}  目标对 {event['trackIds']}  类别 {event['classNames']}")
    print(f"    {'判据':<12}{'原始值':>10}{'阈值':>10}{'归一化':>10}{'权重':>8}{'贡献':>10}")
    print(f"    {'-' * 62}")

    for evidence in event.get('evidences', []):
        raw = evidence['rawValue']
        raw_text = f'{raw:.4f}' if isinstance(raw, (int, float)) and raw >= 0 else '—'
        print(
            f"    {evidence['label']:<12}"
            f"{raw_text:>10}"
            f"{evidence['threshold']:>10.4f}"
            f"{evidence['score']:>10.4f}"
            f"{evidence['weight']:>8.2f}"
            f"{evidence['contribution']:>10.4f}"
        )
    print()


def _verify_layer3() -> tuple[bool, list[str]]:
    """L3 时序确认层的独立验证（消融实验 A2 的基础）。

    直接向确认器投喂合成候选，检验其在「单帧抖动」与「连续多帧命中」
    两种输入下的行为差异 —— 这正是 L3 存在的意义。
    """
    from app.models.schemas import BBox
    from app.services.accident_detector import (
        AccidentCandidate,
        NoConfirmer,
        VoteConfirmer,
    )

    def make_candidate(frame_index: int, score: float) -> AccidentCandidate:
        return AccidentCandidate(
            score=score,
            track_ids=(1, 2),
            class_names=('car', 'car'),
            frame_index=frame_index,
            timestamp=frame_index / 10.0,
            bbox=BBox(x=0.40, y=0.40, w=0.10, h=0.10),
        )

    notes: list[str] = []

    # --- 场景 A：单帧抖动 → 应被抑制 ---
    confirmer = VoteConfirmer(window=5, min_hits=2, score_threshold=0.55)
    single_frame_reported = len(confirmer.update([make_candidate(0, 0.80)], 0))

    # --- 场景 B：连续 3 帧命中 → 应确认，且只确认一次 ---
    confirmer = VoteConfirmer(window=5, min_hits=2, score_threshold=0.55)
    multi_frame_reported = 0
    for frame in range(3):
        multi_frame_reported += len(confirmer.update([make_candidate(frame, 0.80)], frame))

    # --- 对照：无 L3（消融） ---
    no_confirmer = NoConfirmer()
    without_l3 = len(no_confirmer.update([make_candidate(0, 0.80)], 0))

    notes.append(f'无 L3（消融对照）      : 单帧抖动上报 {without_l3} 次')
    notes.append(f'有 L3（投票确认）      : 单帧抖动上报 {single_frame_reported} 次')
    notes.append(f'有 L3（投票确认）      : 连续 3 帧命中上报 {multi_frame_reported} 次')

    ok = without_l3 == 1 and single_frame_reported == 0 and multi_frame_reported == 1
    return ok, notes


def main() -> int:
    _print_header()

    scenarios = accident_service.list_scenarios()
    failures: list[str] = []

    print(f"{'场景':<26}{'期望':<14}{'实际':<14}{'候选':>6}{'确认':>6}{'最高分':>10}")
    print('-' * 78)

    for item in scenarios:
        key = item['key']
        result = accident_service.simulate_scenario(key)

        expected = item['expected']
        actual = result['verdict']

        # 最高候选分（含被抑制项，反映 L2 的原始判断）
        scores = [c['score'] for c in result['rawCandidates']] or [0.0]
        top_score = max(scores)

        ok = expected == actual
        mark = '✓' if ok else '✗'
        if not ok:
            failures.append(key)

        print(
            f"{item['label']:<24}{expected:<14}{actual:<14}"
            f"{result['rawCandidateCount']:>6}{result['eventCount']:>6}{top_score:>10.4f}  {mark}"
        )

    print()

    # ---------------------------------------------------------------- 详情
    print(BAR)
    print('  正样本证据链（collision）')
    print(BAR)
    collision = accident_service.simulate_scenario('collision')

    if collision['events']:
        _print_event_detail(collision['events'][0])
    else:
        print('  ⚠ 未检出事件 —— 请检查判据阈值\n')

    # ---------------------------------------------------------------- 抑制
    print(BAR)
    print('  误报抑制效果（red_light：红灯同步减速）')
    print(BAR)
    red_light = accident_service.simulate_scenario('red_light')

    suppressed_frames = sum(1 for f in red_light['frameLog'] if f['suppressed'] > 0)
    print(f"  L2 原始候选总数      : {red_light['rawCandidateCount']}")
    print(f"  触发抑制的帧数        : {suppressed_frames}")
    print(f"  最终确认事件数        : {red_light['eventCount']}")

    samples = red_light.get('suppressionSamples') or []
    if samples:
        print(f"  抑制原因示例          : {samples[0].get('suppressReason')}")
    else:
        print('  抑制原因示例          : （本场景未触发抑制规则，依赖评分阈值拦截）')

    print()

    # ---------------------------------------------------------------- L3
    print(BAR)
    print('  L3 时序确认层验证（消融实验 A2 基础）')
    print(BAR)
    layer3_ok, notes = _verify_layer3()
    for note in notes:
        print(f'  {note}')
    print()
    if not layer3_ok:
        failures.append('layer3')

    # ---------------------------------------------------------------- 结论
    print(BAR)
    if failures:
        print(f'  结果：{len(failures)} 项不符合预期 -> {", ".join(failures)}')
        print(BAR)
        return 1

    print(f'  结果：全部 {len(scenarios)} 个场景判定正确，L3 行为符合预期 ✓')
    print(BAR)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
