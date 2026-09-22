"""
YOLO vs RT-DETR 场景对比实验
=============================
在同一批真实监控画面上，对比两种检测范式的表现。

为什么选 RT-DETR 而不是原版 DETR
--------------------------------
原版 DETR（2020）只用 ResNet 的 C5 特征（stride 32），没有多尺度。
而本场景的输入只有 352x288（实测上游限制），远处车辆仅 5~15 像素，
在 stride 32 的特征图上不足一个像素 —— 拿它来比是"稻草人论证"。
RT-DETR 是真正在实时检测上对标 YOLO 的现代 DETR 变体，才是公平的对手。

核心指标：按目标尺寸分桶
------------------------
本场景公认的瓶颈是**小目标**（低分辨率 + 远距离），所以不能只看总检出数，
必须把检出按 bbox 相对面积分桶，看两种范式各自擅长什么尺寸的目标：
  · 小目标：相对面积 < 0.5%   （352x288 下约 < 500 px²）
  · 中目标：0.5% ~ 4%
  · 大目标：> 4%

用法（在 backend 目录下）::

    .venv\\Scripts\\python.exe scripts\\compare_yolo_detr.py [样本数]

依赖：transformers（已装）。首次运行会从 hf-mirror 下载 RT-DETR 权重。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 走国内镜像，GitHub 直连会超时
os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')

from app.services.detector import VEHICLE_CLASS_NAMES  # noqa: E402
from app.services.video_source import get_provider, grab_frames_from_hls  # noqa: E402

# COCO 索引（YOLO 与 RT-DETR 一致）
COCO_VEHICLE_IDS = {1, 2, 3, 5, 7}

YOLO_WEIGHTS = 'yolov8s.pt'
YOLO_IMGSZ = 960
YOLO_CONF = 0.25

DETR_MODELS = (
    'PekingU/rtdetr_r18vd',
    'PekingU/rtdetr_r50vd',
)
DETR_CONF = 0.25

# 尺寸分桶阈值（相对面积）
SMALL = 0.005
LARGE = 0.04

BAR = '=' * 86


def bucket(ratio: float) -> str:
    if ratio < SMALL:
        return 'small'
    if ratio < LARGE:
        return 'medium'
    return 'large'


def collect_samples(count: int) -> list[Path]:
    provider = get_provider()
    work = str(BACKEND_DIR / 'static' / 'uploads' / '_yolo_detr')
    samples: list[Path] = []

    for camera in provider.list_cameras(count=count * 2).cameras:
        if len(samples) >= count:
            break
        try:
            frames = grab_frames_from_hls(
                provider.get_stream_url(camera.camera_num),
                provider.request_headers,
                1,
                work,
            )
            if frames:
                samples.append(Path(frames[0]))
        except Exception:  # noqa: BLE001 — 抓帧失败换下一路
            continue
    return samples


def run_yolo(images: list) -> dict:
    from ultralytics import YOLO

    weights = next(
        (p for p in (BACKEND_DIR / YOLO_WEIGHTS, Path(YOLO_WEIGHTS)) if Path(p).is_file()),
        Path(YOLO_WEIGHTS),
    )
    model = YOLO(str(weights))

    counts = {'small': 0, 'medium': 0, 'large': 0}
    elapsed = 0.0

    for image in images:
        started = time.perf_counter()
        result = model(image, conf=YOLO_CONF, imgsz=YOLO_IMGSZ, verbose=False)[0]
        elapsed += time.perf_counter() - started

        height, width = image.shape[:2]
        for box in result.boxes:
            if int(box.cls[0]) not in COCO_VEHICLE_IDS:
                continue
            x1, y1, x2, y2 = (float(value) for value in box.xyxy[0])
            counts[bucket((x2 - x1) * (y2 - y1) / (width * height))] += 1

    counts['ms'] = elapsed / max(1, len(images)) * 1000
    return counts


def run_rtdetr(model_id: str, images: list) -> dict | None:
    try:
        import torch
        from transformers import AutoImageProcessor, AutoModelForObjectDetection
    except ImportError as exc:
        print(f'  transformers 不可用：{exc}')
        return None

    try:
        processor = AutoImageProcessor.from_pretrained(model_id)
        model = AutoModelForObjectDetection.from_pretrained(model_id)
    except Exception as exc:  # noqa: BLE001 — 下载失败等
        print(f'  模型加载失败：{str(exc)[:110]}')
        return None

    model.eval()
    counts = {'small': 0, 'medium': 0, 'large': 0}
    elapsed = 0.0

    for image in images:
        height, width = image.shape[:2]
        rgb = image[:, :, ::-1].copy()   # BGR -> RGB

        inputs = processor(images=rgb, return_tensors='pt')
        started = time.perf_counter()
        with torch.no_grad():
            outputs = model(**inputs)
        elapsed += time.perf_counter() - started

        results = processor.post_process_object_detection(
            outputs,
            target_sizes=torch.tensor([(height, width)]),
            threshold=DETR_CONF,
        )[0]

        for label, box in zip(results['labels'].tolist(), results['boxes'].tolist()):
            if int(label) not in COCO_VEHICLE_IDS:
                continue
            x1, y1, x2, y2 = (float(value) for value in box)
            counts[bucket((x2 - x1) * (y2 - y1) / (width * height))] += 1

    counts['ms'] = elapsed / max(1, len(images)) * 1000
    return counts


def report(name: str, result: dict) -> None:
    total = result['small'] + result['medium'] + result['large']
    print(
        f'{name:<26}{total:>7}{result["small"]:>8}{result["medium"]:>8}'
        f'{result["large"]:>8}{result["ms"]:>10.0f}ms'
    )


def main() -> int:
    import cv2

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    print('正在采集样本…')
    samples = collect_samples(count)
    if not samples:
        raise SystemExit('未能采集到样本')

    images = [cv2.imread(str(path)) for path in samples]
    images = [image for image in images if image is not None]
    print(f'样本 {len(images)} 张（{images[0].shape[1]}x{images[0].shape[0]}）')
    print(BAR)
    print(f'{"检测器":<26}{"车辆":>7}{"小目标":>8}{"中目标":>8}{"大目标":>8}{"单张耗时":>11}')
    print('-' * 86)

    report(f'YOLOv8s (imgsz={YOLO_IMGSZ})', run_yolo(images))

    for model_id in DETR_MODELS:
        short = model_id.split('/')[-1]
        print(f'  正在加载 {short} …')
        result = run_rtdetr(model_id, images)
        if result is not None:
            report(f'{short} ({DETR_CONF})', result)

    print('-' * 86)
    print(f'说明：小目标 = 相对面积 < {SMALL:.1%}，大目标 = 相对面积 > {LARGE:.0%}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
