"""
检测参数对比实验
================
在**同一帧真实监控画面**上对比不同模型 / 推理尺寸 / 置信度阈值的检出效果，
用于回答"检测效果不理想"到底是哪个环节的问题。

用法（在 backend 目录下）::

    .venv\\Scripts\\python.exe scripts\\compare_detectors.py
    .venv\\Scripts\\python.exe scripts\\compare_detectors.py <图片路径>

变量隔离
--------
逐项只改一个变量，其余固定，避免"多因素同时变"导致的归因错误：

  1. 模型：yolov8n（当前）vs yolov8s
  2. 推理尺寸：640（默认）vs 960
  3. 置信度：0.30（当前）vs 0.15 vs 0.08

报告每组的检出数量与耗时，并给出车辆类别明细。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.detector import VEHICLE_CLASS_NAMES  # noqa: E402
from app.services.video_source import get_provider  # noqa: E402

# 逐项对比的候选值
MODELS = ('yolov8n.pt', 'yolov8s.pt')
IMAGE_SIZES = (640, 960, 1280)
CONFIDENCES = (0.30, 0.25, 0.15, 0.08)

BAR = '=' * 84


def resolve_weights(name: str) -> Path | None:
    """定位权重文件（backend/ 与 ../traffic_monitor/ 都找一遍）。"""
    for directory in (BACKEND_DIR, BACKEND_DIR.parent.parent / 'traffic_monitor'):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def grab_sample() -> Path:
    """抓一帧真实画面作为对比样本。"""
    provider = get_provider()
    cameras = provider.list_cameras(count=8).cameras

    for camera in cameras:
        try:
            path, _ = provider.capture_frame(camera.camera_num)
        except Exception:  # noqa: BLE001 — 抓帧失败就换下一路
            continue
        print(f'样本来源：{camera.road}  {camera.camera_num}')
        return Path(path)

    raise SystemExit('未能抓取到任何画面')


def evaluate(model, image, imgsz: int, conf: float) -> tuple[int, int, list[str], float]:
    """跑一次推理，返回 ``(目标总数, 车辆数, 类别列表, 耗时ms)``。"""
    started = time.perf_counter()
    result = model(image, conf=conf, imgsz=imgsz, verbose=False)[0]
    elapsed = (time.perf_counter() - started) * 1000

    names = [model.names[int(index)] for index in result.boxes.cls.tolist()]
    vehicles = sum(1 for name in names if name in VEHICLE_CLASS_NAMES)
    return len(names), vehicles, names, elapsed


def main() -> int:
    from ultralytics import YOLO
    import cv2

    sample = Path(sys.argv[1]) if len(sys.argv) > 1 else grab_sample()
    image = cv2.imread(str(sample))
    if image is None:
        raise SystemExit(f'无法读取图片：{sample}')

    height, width = image.shape[:2]
    print(f'样本：{sample.name}   分辨率 {width}x{height}')
    print(BAR)

    # 当前线上配置作为基线
    print(f'{"模型":<12}{"尺寸":>6}{"置信度":>8}{"目标":>6}{"车辆":>6}{"耗时":>9}   类别明细')
    print('-' * 84)

    for model_name in MODELS:
        weights = resolve_weights(model_name)
        if weights is None:
            print(f'{model_name:<12} 未找到权重，跳过')
            continue

        model = YOLO(str(weights))
        for imgsz in IMAGE_SIZES:
            for conf in CONFIDENCES:
                total, vehicles, names, elapsed = evaluate(model, image, imgsz, conf)
                detail = ', '.join(names) if names else '—'
                if len(detail) > 30:
                    detail = detail[:30] + '…'
                mark = ' ←当前' if (model_name == 'yolov8s.pt' and imgsz == 960 and conf == 0.25) else ''
                print(
                    f'{model_name:<12}{imgsz:>6}{conf:>8.2f}{total:>6}{vehicles:>6}'
                    f'{elapsed:>8.0f}ms   {detail}{mark}'
                )
        print('-' * 84)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
