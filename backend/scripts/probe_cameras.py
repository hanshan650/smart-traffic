"""
摄像头抓帧探测
==============
遍历若干摄像头，各抓一帧并跑一次 YOLO，打印检测到的目标数。

用途：在演示/调试前快速找到**画面中有车辆**的摄像头，
以及确认「取流 → 抓帧 → 推理」链路端到端可用。

用法（在 backend 目录下）::

    .venv\\Scripts\\python.exe scripts\\probe_cameras.py [数量]
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import detector  # noqa: E402
from app.services.video_source import VideoSourceError, get_provider  # noqa: E402
from app.services.video_source import find_ffmpeg  # noqa: E402


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 6

    provider = get_provider()
    print(f'视频源：{provider.key}（{provider.display_name}）')
    print(f'ffmpeg：{find_ffmpeg() or "未找到（将走 HLS 分片离线解码）"}')
    print('-' * 72)

    cameras = provider.list_cameras(count=limit).cameras
    hits = 0

    for camera in cameras:
        label = f'{camera.camera_num:<20}{camera.road[:18]:<20}'
        try:
            path, stream_url = provider.capture_frame(camera.camera_num)
            outcome = detector.detect_image(path)
            vehicles = outcome.total_vehicles
            hits += 1 if vehicles else 0
            print(f'{label}车辆 {vehicles:>3}  目标 {len(outcome.objects):>3}  {camera.camera_num}')
        except VideoSourceError as exc:
            print(f'{label}抓帧失败：{exc}')
        except Exception as exc:  # noqa: BLE001 — 探测脚本，需容错全部异常
            print(f'{label}异常：{exc}')

    print('-' * 72)
    print(f'共探测 {len(cameras)} 路，其中 {hits} 路当帧检出车辆')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
