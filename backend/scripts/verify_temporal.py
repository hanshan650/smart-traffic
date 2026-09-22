"""
时序聚合收益验证
================
现在每次检测都是**单帧**判定，问题有两个：

  1. 单帧漏检无法补救 —— 远处小车在某一帧恰好被遮挡/模糊就丢了
  2. 计数抖动 —— 同一路连续检测，结果在 2/3/4 之间跳

本脚本对同一路抓 N 帧连续画面，量化对比：

  · 单帧检出：逐帧车辆数的最小/均值/最大值（反映抖动幅度）
  · 聚合检出：跨帧关联后的独立目标数（反映真实目标数）

聚合用项目里已有的 ``IoUTracker`` —— 事故识别模块本就在用它，
这里只是把它复用到计数场景。

用法（在 backend 目录下）::

    .venv\\Scripts\\python.exe scripts\\verify_temporal.py [帧数]
"""
from __future__ import annotations

import statistics
import sys
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import detector  # noqa: E402
from app.services.tracking import IoUTracker  # noqa: E402
from app.services.video_source import get_provider, grab_frames_from_hls  # noqa: E402

BAR = '=' * 78


def analyze(camera_num: str, frame_count: int) -> dict | None:
    provider = get_provider()
    work = str(BACKEND_DIR / 'static' / 'uploads' / '_temporal')

    frames = grab_frames_from_hls(
        provider.get_stream_url(camera_num),
        provider.request_headers,
        frame_count,
        work,
    )
    if len(frames) < 2:
        return None

    tracker = IoUTracker(iou_threshold=0.3, max_age=2, min_hits=1)
    per_frame: list[int] = []
    class_votes: Counter[str] = Counter()
    weak_hits = 0   # 只在少数帧出现的目标（单帧检测必然漏掉）

    for index, path in enumerate(frames):
        outcome = detector.detect_image(path)
        per_frame.append(outcome.total_vehicles)

        # update() 返回当前帧的活跃轨迹列表
        for track in tracker.update(outcome.objects, index, index / 25.0):
            class_votes[track.class_name] += 1

    tracks = tracker.active_tracks()
    # 只被少数帧观测到的轨迹 —— 这些正是"单帧检测会漏掉"的目标
    weak_hits = sum(1 for track in tracks if track.age < max(2, len(frames) // 3))

    return {
        'frames': len(frames),
        'per_frame': per_frame,
        'aggregated': len(tracks),
        'weak': weak_hits,
        'classes': dict(class_votes),
        'tracks': [
            {'id': t.track_id, 'class': t.class_name, 'age': t.age}
            for t in sorted(tracks, key=lambda x: -x.age)
        ],
    }


def main() -> int:
    frame_count = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    provider = get_provider()

    print(f'抓取 {frame_count} 帧连续画面，逐路对比单帧检出与跨帧聚合')
    print(BAR)

    for camera in provider.list_cameras(count=4).cameras:
        try:
            result = analyze(camera.camera_num, frame_count)
        except Exception as exc:  # noqa: BLE001 — 诊断脚本
            print(f'{camera.road[:22]:<24}异常：{str(exc)[:44]}')
            continue

        if result is None:
            print(f'{camera.road[:22]:<24}抓帧失败')
            continue

        counts = result['per_frame']
        print(f"{camera.road[:22]:<24}实际抓取 {result['frames']} 帧")
        print(f"  单帧检出序列 : {counts}")
        print(
            f"  单帧统计     : min={min(counts)}  中位={statistics.median(counts)}  "
            f"max={max(counts)}  极差={max(counts) - min(counts)}"
        )
        print(f"  聚合后目标数 : {result['aggregated']}   （其中低置信轨迹 {result['weak']} 条）")
        print(f"  类别分布     : {result['classes']}")
        print('-' * 78)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
