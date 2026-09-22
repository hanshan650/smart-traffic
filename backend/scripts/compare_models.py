"""
模型规模对比
============
在**固定推理尺寸与置信度**（当前线上配置 imgsz=960 / conf=0.25）的前提下，
只改变模型规模，衡量精度与耗时的权衡。

为什么要单独做这个实验
----------------------
``compare_detectors.py`` 同时扫了模型 / 尺寸 / 阈值三个维度，结论已经确定
（尺寸是关键、模型有增益）。这里固定住已验证的参数，只看「再换更大模型
还值不值」，因为它的代价是 CPU 推理时间线性增长。

样本取多路（默认 4 路）并报告合计与逐路结果，避免单张画面的偶然性。

用法（在 backend 目录下）::

    .venv\\Scripts\\python.exe scripts\\compare_models.py [样本数]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.detector import VEHICLE_CLASS_NAMES  # noqa: E402
from app.services.video_source import get_provider, grab_frames_from_hls  # noqa: E402

IMGSZ = 960
CONF = 0.25
MEDIA_PLAYLIST_CONF = CONF

MODELS = ('yolov8n.pt', 'yolov8s.pt', 'yolov8m.pt', 'yolov8l.pt')

BAR = '=' * 82


def resolve_weights(name: str) -> Path | None:
    """定位本地已有的权重；没有则返回 None（由 ultralytics 自行下载）。"""
    for directory in (BACKEND_DIR, BACKEND_DIR.parent.parent / 'traffic_monitor'):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def collect_samples(count: int) -> list[tuple[str, Path]]:
    """从不同摄像头各抓一帧，作为对比样本集。"""
    provider = get_provider()
    samples: list[tuple[str, Path]] = []

    for camera in provider.list_cameras(count=count * 2).cameras:
        if len(samples) >= count:
            break
        try:
            # 直接走 HLS 分片路径，避免依赖 ffmpeg
            work = str(BACKEND_DIR / 'static' / 'uploads' / '_model_cmp')
            frames = grab_frames_from_hls(
                provider.get_stream_url(camera.camera_num),
                provider.request_headers,
                1,
                work,
            )
            if frames:
                samples.append((camera.road or camera.camera_num, Path(frames[0])))
        except Exception:  # noqa: BLE001 — 抓帧失败就换下一路
            continue

    return samples


def main() -> int:
    from ultralytics import YOLO
    import cv2

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    print('正在采集样本…')
    samples = collect_samples(count)
    if not samples:
        raise SystemExit('未能采集到样本')

    print(f'样本 {len(samples)} 张：')
    for name, path in samples:
        image = cv2.imread(str(path))
        shape = f'{image.shape[1]}x{image.shape[0]}' if image is not None else '?'
        print(f'  {name[:28]:<30}{path.name}  {shape}')
    print(BAR)
    print(f'固定参数：imgsz={IMGSZ}  conf={CONF}')
    print(BAR)
    print(f'{"模型":<12}{"车辆合计":>9}{"平均每张":>9}{"单张耗时":>11}   逐张车辆数')
    print('-' * 82)

    images = [cv2.imread(str(path)) for _, path in samples]

    for name in MODELS:
        weights = resolve_weights(name)
        try:
            model = YOLO(str(weights) if weights else name)
        except Exception as exc:  # noqa: BLE001 — 权重缺失/下载失败
            print(f'{name:<12}不可用：{str(exc)[:50]}')
            continue

        per_image: list[int] = []
        elapsed_total = 0.0

        for image in images:
            if image is None:
                per_image.append(0)
                continue
            started = time.perf_counter()
            result = model(image, conf=CONF, imgsz=IMGSZ, verbose=False)[0]
            elapsed_total += time.perf_counter() - started

            names = [model.names[int(index)] for index in result.boxes.cls.tolist()]
            per_image.append(sum(1 for item in names if item in VEHICLE_CLASS_NAMES))

        total = sum(per_image)
        per_frame_ms = elapsed_total / max(1, len(images)) * 1000
        detail = ', '.join(str(value) for value in per_image)
        print(
            f'{name:<12}{total:>9}{total / max(1, len(per_image)):>9.1f}'
            f'{per_frame_ms:>10.0f}ms   {detail}'
        )

    print('-' * 82)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
