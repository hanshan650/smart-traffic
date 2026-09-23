"""车牌像素量级探测
==================

目的
----
在动手做车牌识别之前，先回答一个绕不开的问题：
**当前视频源的分辨率，够不够读出车牌？**

方法
----
抓一帧真实画面跑 YOLO，取最大的几个车辆框，按车牌几何占比反推车牌像素尺寸：

  中国民用号牌 440 x 140 mm（宽高比 3.14:1）
  车牌宽度约占车宽的 24%（以 1.8 m 车宽、0.44 m 牌宽估算）
  →  车牌高 ≈ 车牌宽 / 3.14

然后与 OCR 的可读下限比较。经验阈值：
  · 车牌字符高度 >= 16 px  才具备稳定识别条件
  · 10 ~ 16 px             勉强，需超分或专用卡口相机
  · < 10 px                不可读，任何 OCR 引擎都救不回来

结论会直接写进 ``docs/车牌识别可行性.md``，用来决定该功能
是「本机实现」还是「预留接口 + 说明接入条件」。

用法
----
    python scripts/probe_plate_pixels.py [摄像头编号]
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import detector, video_source  # noqa: E402

VEHICLE_CLASSES = {'car', 'truck', 'bus', 'motorcycle'}

# 车牌宽 / 车宽 的经验比例
PLATE_TO_VEHICLE_WIDTH = 0.24
# 中国号牌宽高比
PLATE_ASPECT = 440 / 140


def main() -> int:
    camera = sys.argv[1] if len(sys.argv) > 1 else '2033453710067216385'
    print(f'摄像头：{camera}\n')

    provider = video_source.get_provider()
    stream_url = provider.get_stream_url(camera)
    frame_paths = video_source.extract_frames(
        stream_url, headers=provider.request_headers, count=3
    )
    if not frame_paths:
        print('抽帧失败：上游不可用或编号有误')
        return 1

    image = cv2.imread(frame_paths[0])
    height, width = image.shape[:2]
    print(f'画面尺寸：{width} x {height}\n')

    outcome = detector.detect_image(frame_paths[0])
    vehicles = [o for o in outcome.objects if o.class_name in VEHICLE_CLASSES]
    vehicles.sort(key=lambda o: -(o.bbox.w * o.bbox.h))

    print(f'检出车辆：{outcome.total_vehicles} 个，其中车辆类目标 {len(vehicles)} 个')
    print(f'{"类别":<10}{"框尺寸(px)":<18}{"估算车牌(px)":<18}{"字符高(px)":<12}判定')
    print('-' * 72)

    best = 0.0
    for obj in vehicles[:8]:
        box_w = obj.bbox.w * width
        box_h = obj.bbox.h * height
        plate_w = box_w * PLATE_TO_VEHICLE_WIDTH
        plate_h = plate_w / PLATE_ASPECT
        # 号牌共 7 个字符，可用高度约 70% 分摊到字高
        char_h = plate_h * 0.7
        best = max(best, char_h)
        verdict = '可读' if char_h >= 16 else ('勉强' if char_h >= 10 else '不可读')
        print(f'{obj.class_name:<10}{box_w:5.1f} x {box_h:5.1f}     '
              f'{plate_w:5.1f} x {plate_h:4.1f}      {char_h:5.1f}       {verdict}')

    print('-' * 72)
    print(f'本帧最佳字符高度：{best:.1f} px')
    if best >= 16:
        print('结论：当前分辨率**可以**支撑车牌识别。')
    elif best >= 10:
        print('结论：处于临界区，需要超分辨率或专用车牌检测模型才可能稳定识别。')
    else:
        print('结论：当前分辨率**不足以**识别车牌。')
        print('      车牌识别应作为「预留接口」实现，实际接入需高分辨率卡口相机。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
