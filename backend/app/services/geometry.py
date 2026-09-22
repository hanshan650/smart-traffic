"""
几何计算工具
============
所有边界框均为**归一化坐标**（``x, y, w, h ∈ [0, 1]``），与检测层输出一致。
因此本模块的计算与视频分辨率无关，同一套阈值可直接跨点位复用。

坐标约定
--------
``x, y`` 为左上角，``w, h`` 为宽高（不是右下角坐标）。
归一化基准为图像宽高的最大值，保证长宽比不失真。
"""
from __future__ import annotations

import math
from typing import Tuple

from app.models.schemas import BBox


def bbox_area(bbox: BBox) -> float:
    return max(0.0, bbox.w) * max(0.0, bbox.h)


def bbox_center(bbox: BBox) -> Tuple[float, float]:
    """返回归一化中心点 ``(cx, cy)``。"""
    return (bbox.x + bbox.w / 2.0, bbox.y + bbox.h / 2.0)


def bbox_iou(a: BBox, b: BBox) -> float:
    """交并比 IoU ∈ [0, 1]。"""
    ax1, ay1, ax2, ay2 = a.x, a.y, a.x + a.w, a.y + a.h
    bx1, by1, bx2, by2 = b.x, b.y, b.x + b.w, b.y + b.h

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)

    inter_w = max(0.0, ix2 - ix1)
    inter_h = max(0.0, iy2 - iy1)
    intersection = inter_w * inter_h

    union = bbox_area(a) + bbox_area(b) - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def bbox_union(a: BBox, b: BBox) -> BBox:
    """外接矩形，用于标注事件区域。"""
    x1 = min(a.x, b.x)
    y1 = min(a.y, b.y)
    x2 = max(a.x + a.w, b.x + b.w)
    y2 = max(a.y + a.h, b.y + b.h)
    return BBox(x=max(0.0, x1), y=max(0.0, y1), w=max(0.0, x2 - x1), h=max(0.0, y2 - y1))


def center_distance(a: BBox, b: BBox) -> float:
    """中心点欧氏距离（归一化）。

    在路侧俯视视角下，两个目标的 bbox 可能因遮挡而不重叠，
    但中心距急剧缩小 —— 因此 IoU 之外还需辅以本判据。
    """
    ax, ay = bbox_center(a)
    bx, by = bbox_center(b)
    return math.hypot(ax - bx, ay - by)


def normalized_distance(base: BBox, target: BBox) -> float:
    """以目标尺寸为基准归一化的中心距。

    用于消除"远近尺度差异"：远距离目标即使实际相距很近，
    其归一化坐标差也可能很大，反之亦然。
    """
    scale = max(base.w, base.h, 1e-6)
    return center_distance(base, target) / scale


def heading_angle(dx: float, dy: float) -> float:
    """由位移分量求航向角（弧度，``[-π, π]``）。"""
    return math.atan2(dy, dx)


def angle_difference(a: float, b: float) -> float:
    """两个角度的最小差值（弧度，恒为正、``[0, π]``）。

    直接相减会在 ``±π`` 处产生跳变（如从 179° 到 -179° 实为转 2°），
    必须做环绕处理。
    """
    diff = (a - b + math.pi) % (2 * math.pi) - math.pi
    return abs(diff)


def sigmoid(x: float) -> float:
    """数值稳定的 Sigmoid。"""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)
