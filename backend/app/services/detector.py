"""
目标检测服务
============
封装 YOLOv8 推理，向业务层输出统一的检测结果。

设计要点
--------
1. **模型懒加载** —— 首次调用才加载权重，避免拖慢应用启动速度
2. **优雅降级** —— 未安装 ultralytics 或找不到权重时抛出
   :class:`DetectorUnavailable`，由 API 层转为 503 + 明确提示，
   视频源、路网、告警等其余功能完全不受影响
3. **归一化输出** —— 边界框统一转为 [0, 1] 相对坐标，与视频实际分辨率解耦，
   前端可直接按容器尺寸还原，不需知道原始分辨率
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.models.schemas import BBox, CongestionLevel, DetectedObject


class DetectorUnavailable(Exception):
    """模型不可用：依赖缺失或权重文件找不到。"""


# COCO 数据集中与本业务相关的类别
#
# 注意：碰撞事故判定必须能区分「机动车」与「弱势交通参与者」，
# 因此除车辆外还需保留行人/自行车类别。
#
# ``train`` 本不属于道路目标，但**必须保留**：实测发现高速公路监控
# 视角下的货车/客车经常被误分类为 train（长条形轮廓相似）。
# 直接丢弃会让车辆数系统性偏低 —— 修正方式见 :data:`CLASS_ALIAS`。
CLASS_NAME_MAP: Dict[int, str] = {
    1: 'bicycle',
    2: 'car',
    3: 'motorcycle',
    5: 'bus',
    6: 'train',
    7: 'truck',
    0: 'pedestrian',
}

# 道路监控场景下的类别重映射（场景先验，非通用规则）
#
# ``train -> truck``：高速公路监控点拍到铁路的概率极低，而实测表明
# 该类别绝大多数是货车/客车的误判。不修正的话这些车辆会被丢弃，
# 导致车流量与拥堵判定系统性偏低。
#
# 若某监控点确实会拍到铁路，删掉本条即可。
CLASS_ALIAS: Dict[str, str] = {
    'train': 'truck',
}

# 仅车辆类别（用于车流量统计，行人/自行车不计入拥堵判定）
VEHICLE_CLASS_MAP: Dict[int, str] = {
    1: 'bicycle',
    2: 'car',
    3: 'motorcycle',
    5: 'bus',
    7: 'truck',
}

# 车辆类别的名称集合，供事故判定与流量统计共同使用
VEHICLE_CLASS_NAMES = frozenset(VEHICLE_CLASS_MAP.values())


@dataclass
class DetectOutcome:
    """一次检测的结构化结果。"""

    objects: List[DetectedObject] = field(default_factory=list)
    vehicle_counts: Dict[str, int] = field(default_factory=dict)
    total_vehicles: int = 0
    congestion_level: CongestionLevel = CongestionLevel.NORMAL
    duration_ms: int = 0


# 进程内单例
_model: Any = None
_model_path: Optional[Path] = None
_load_error: str = ''


# ==========================================================================
# 模型加载
# ==========================================================================

def _resolve_model_path() -> Optional[Path]:
    """在配置的搜索目录中定位权重文件。"""
    name = settings.yolo_model_path

    direct = Path(name)
    if direct.is_absolute() and direct.is_file():
        return direct

    for directory in settings.yolo_search_dirs:
        candidate = directory / name
        if candidate.is_file():
            return candidate

    return None


def load_model() -> Any:
    """懒加载并缓存 YOLO 模型；失败时抛出 :class:`DetectorUnavailable`。"""
    global _model, _model_path, _load_error

    if _model is not None:
        return _model
    if _load_error:
        raise DetectorUnavailable(_load_error)

    try:
        from ultralytics import YOLO  # 延迟导入：体积大且耗时
    except ImportError as exc:
        _load_error = (
            '未安装 ultralytics，检测功能不可用。'
            '请在虚拟环境中执行 pip install ultralytics 后重启服务。'
        )
        raise DetectorUnavailable(_load_error) from exc

    path = _resolve_model_path()
    if path is None:
        searched = ', '.join(str(d) for d in settings.yolo_search_dirs)
        _load_error = (
            f'未找到模型权重 {settings.yolo_model_path}。'
            f'已搜索目录：{searched}。'
            f'可从 https://github.com/ultralytics/assets/releases 下载后放入其中之一。'
        )
        raise DetectorUnavailable(_load_error)

    try:
        _model = YOLO(str(path))
        _model_path = path
    except Exception as exc:  # 权重损坏等
        _load_error = f'模型加载失败：{exc}'
        raise DetectorUnavailable(_load_error) from exc

    return _model


def is_available() -> Tuple[bool, str]:
    """检测能力探测，供健康检查与前端降级提示使用。"""
    try:
        load_model()
        return True, ''
    except DetectorUnavailable as exc:
        return False, str(exc)


def model_info() -> Dict[str, Any]:
    available, reason = is_available()
    return {
        'available': available,
        'reason': reason,
        'model': settings.yolo_model_path,
        'resolved_path': str(_model_path) if _model_path else '',
        'confidence_threshold': settings.yolo_confidence,
    }


# ==========================================================================
# 推理
# ==========================================================================

def classify_congestion(total_vehicles: int) -> CongestionLevel:
    """按单帧车辆数判定拥堵等级（阈值来自配置）。"""
    if total_vehicles >= settings.traffic_heavy_threshold:
        return CongestionLevel.HEAVY
    if total_vehicles >= settings.traffic_moderate_threshold:
        return CongestionLevel.MODERATE
    if total_vehicles >= settings.traffic_light_threshold:
        return CongestionLevel.LIGHT
    return CongestionLevel.NORMAL


def detect_image(image_path: str) -> DetectOutcome:
    """对单张图片执行检测。

    :raises DetectorUnavailable: 模型不可用
    :raises FileNotFoundError: 图片不存在
    """
    if not Path(image_path).is_file():
        raise FileNotFoundError(f'图片不存在：{image_path}')
    return _predict(image_path)


def detect_frame(frame: Any) -> DetectOutcome:
    """对单帧图像（``numpy.ndarray``，BGR 顺序）执行检测。

    事故识别需要连续多帧，本函数是逐帧流水线的入口，
    与 :func:`detect_image` 共用同一套解析逻辑。
    """
    return _predict(frame)


def _predict(source: Any) -> DetectOutcome:
    """执行推理并把 ultralytics 结果转为统一的 :class:`DetectOutcome`。

    :param source: 图片路径、``numpy.ndarray`` 或路径列表
    """
    model = load_model()
    started = time.perf_counter()

    results = model.predict(
        source=source,
        conf=settings.yolo_confidence,
        imgsz=settings.yolo_image_size,
        verbose=False,
    )

    objects: List[DetectedObject] = []
    counts: Dict[str, int] = {}

    if results:
        result = results[0]
        height, width = result.orig_shape[:2]
        boxes = getattr(result, 'boxes', None)

        if boxes is not None and width and height:
            for box in boxes:
                class_id = int(box.cls[0])
                raw_name = CLASS_NAME_MAP.get(class_id)
                if raw_name is None:
                    continue  # 非关注目标不计入

                # 场景先验修正（如 train -> truck）
                class_name = CLASS_ALIAS.get(raw_name, raw_name)

                x1, y1, x2, y2 = (float(value) for value in box.xyxy[0])
                objects.append(DetectedObject(
                    class_id=class_id,
                    class_name=class_name,
                    confidence=round(float(box.conf[0]), 4),
                    bbox=BBox(
                        x=round(x1 / width, 5),
                        y=round(y1 / height, 5),
                        w=round((x2 - x1) / width, 5),
                        h=round((y2 - y1) / height, 5),
                    ),
                ))

                # 拥堵判定只依据车辆；行人/自行车虽保留在 objects 中
                # （事故算法需要），但不计入车流量
                if class_name in VEHICLE_CLASS_NAMES:
                    counts[class_name] = counts.get(class_name, 0) + 1

    total = sum(counts.values())

    return DetectOutcome(
        objects=objects,
        vehicle_counts=counts,
        total_vehicles=total,
        congestion_level=classify_congestion(total),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
