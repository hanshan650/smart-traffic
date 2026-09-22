"""
时序聚合逻辑验证（离线）
========================
用**合成的检测序列**验证跨帧关联计数与类别投票，不依赖上游视频源与网络。

为什么要能做离线验证
--------------------
``flow_service.analyze_frames`` 只接收「已抽好的帧路径 + 一个检测函数」，
检测函数可注入。因此可以用构造好的目标序列替代真实的 YOLO 推理，
从而在**上游故障或离线**时仍能验证核心逻辑：

  · 互补漏检能否被补回（聚合结果 > 单帧最大值）
  · 单帧噪声能否被过滤（聚合结果 < 单帧最大值）
  · 类别投票能否稳定摇摆的判定
  · 稳定性指标与召回增益的计算

用法（在 backend 目录下）::

    .venv\\Scripts\\python.exe scripts\\verify_flow.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.schemas import BBox  # noqa: E402
from app.services.detector import DetectOutcome, DetectedObject, VEHICLE_CLASS_NAMES  # noqa: E402
from app.services.flow_service import FlowConfig, analyze_frames  # noqa: E402

BAR = '=' * 78
FRAME_COUNT = 10
FPS = 25.0


def make_object(class_id: int, class_name: str, x: float) -> DetectedObject:
    """构造一个位于 (x, 0.4) 的车辆检测框，尺寸固定便于 IoU 关联。"""
    return DetectedObject(
        class_id=class_id,
        class_name=class_name,
        confidence=0.9,
        bbox=BBox(x=round(x, 4), y=0.4, w=0.08, h=0.12),
    )


def outcome_of(objects: List[DetectedObject]) -> DetectOutcome:
    counts: Dict[str, int] = {}
    for item in objects:
        if item.class_name in VEHICLE_CLASS_NAMES:
            counts[item.class_name] = counts.get(item.class_name, 0) + 1

    total = sum(counts.values())
    return DetectOutcome(
        objects=objects,
        vehicle_counts=counts,
        total_vehicles=total,
    )


def build_scenario(visible: Dict[str, Dict[int, DetectedObject]]) -> List[DetectOutcome]:
    """把「目标 → {帧号: 检测框}」展开成逐帧的检测结果。"""
    frames: List[DetectOutcome] = []
    for index in range(FRAME_COUNT):
        objects = [table[index] for table in visible.values() if index in table]
        frames.append(outcome_of(objects))
    return frames


def run(name: str, frames: List[DetectOutcome], expect_total: int, note: str) -> bool:
    """跑一个场景并核对结果。"""
    # 检测函数按帧序号返回预设结果（路径参数只是占位）
    by_path = {f'/fake/frame_{i:03d}.jpg': outcome for i, outcome in enumerate(frames)}
    paths = list(by_path)

    result = analyze_frames(
        paths,
        FPS,
        FlowConfig(min_hits=2, max_age=2),
        detect=lambda path: by_path[path],
    )

    total = result['totalVehicles']
    stability = result['stability']
    ok = total == expect_total

    print(f'{name}')
    print(f'  设计说明    : {note}')
    print(f'  单帧计数序列 : {result["perFrameCounts"]}')
    print(
        f'  单帧统计    : min={stability["min"]}  中位={stability["median"]}  '
        f'max={stability["max"]}  极差={stability["range"]}'
    )
    print(
        f'  聚合结果    : {total} 辆  '
        f'(期望 {expect_total})  '
        f'召回增益={stability["recallGain"]:+d}  {"✓" if ok else "✗"}'
    )

    for track in result['tracks']:
        votes = ', '.join(f'{k}×{v}' for k, v in track['voteDetail'].items())
        stable = '稳定' if track['classStable'] else '摇摆'
        print(
            f'    轨迹 {track["trackId"]}: {track["className"]:<8} '
            f'观测 {track["observations"]:>2} 帧  投票 [{votes}]  {stable}'
        )
    print('-' * 78)
    return ok


def main() -> int:
    # ---------------------------------------------------------------- 场景一
    # 目标 A 全程可见；B 于第 4 帧进入；C 于第 4 帧离开。
    # 任意单帧最多只看到 2 个目标，但聚合能还原出 3 个 → 体现互补漏检。
    moving_a = {i: make_object(2, 'car', 0.10 + i * 0.02) for i in range(FRAME_COUNT)}
    moving_b = {i: make_object(2, 'car', 0.60 + i * 0.01) for i in range(4, FRAME_COUNT)}
    moving_c = {i: make_object(7, 'truck', 0.30 + i * 0.015) for i in range(0, 4)}

    # 目标 A 的类别刻意做成摇摆：前 7 帧判为 car，后 3 帧判为 truck，
    # 用于验证跨帧投票取多数（预期 car）。
    for i in range(7, FRAME_COUNT):
        moving_a[i] = make_object(7, 'truck', 0.10 + i * 0.02)

    scenario_one = build_scenario({'A': moving_a, 'B': moving_b, 'C': moving_c})

    # ---------------------------------------------------------------- 场景二
    # 目标 A 全程可见；D 仅在第 5 帧闪现一次。
    # D 属于单帧噪声，应被 min_hits=2 过滤 → 聚合结果小于单帧最大值。
    steady_a = {i: make_object(2, 'car', 0.20 + i * 0.015) for i in range(FRAME_COUNT)}
    noise_d = {5: make_object(2, 'car', 0.75)}
    scenario_two = build_scenario({'A': steady_a, 'D': noise_d})

    print(BAR)
    print('  时序聚合逻辑验证（合成检测序列，不依赖网络）')
    print(BAR)
    print()

    ok_one = run(
        '场景一：互补漏检',
        scenario_one,
        expect_total=3,
        note='A 全程可见，B 后 6 帧进入，C 前 4 帧离开；单帧最多只见 2 个',
    )
    ok_two = run(
        '场景二：单帧噪声过滤',
        scenario_two,
        expect_total=1,
        note='A 全程可见，D 仅第 5 帧闪现；D 应被 min_hits=2 剔除',
    )

    print(BAR)
    if ok_one and ok_two:
        print('  结果：两个场景均符合预期 ✓')
        print('  结论：跨帧聚合既能补回漏检，也能滤除单帧噪声')
        print(BAR)
        return 0

    print('  结果：存在不符合预期的场景 ✗')
    print(BAR)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
