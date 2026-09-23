"""
车牌识别（适配器 + 分辨率可行性预检）
======================================

诚实声明放在最前面
------------------
**本项目当前的视频源无法识别车牌，这是物理限制，不是实现缺陷。**

实测数据（``scripts/probe_plate_pixels.py``，真实上游画面）：

  ============================================  ==============
  上游分辨率                                     352 x 288
  画面中车辆检测框                               14.8 x 16.1 px
  按几何比例反推的车牌尺寸                       3.6 x 1.1 px
  车牌字符高度（7 字符、字高约占牌高 70%）        0.8 px
  OCR 可读下限（经验阈值）                       约 16 px
  ============================================  ==============

差了约 20 倍。车牌字符只有不到 1 个像素 —— 信息在成像阶段就已经丢失，
任何超分或算法都无法恢复。**要真正识别车牌，必须接入高分辨率卡口相机**
（车牌字符高度需达到 16 px 以上，对应约 720p 以上、且车辆成像足够的位）。

因此本模块的定位是：**把流程写完整、把接口留清楚、把限制说明白**，
而不是返回一个看起来能用、实际上全是噪声的"识别结果"。

分辨率预检
----------
:func:`estimate_plate_char_height` 在调用任何识别引擎**之前**先估算车牌
字符高度。若低于可读下限，直接返回「不可读」并附上估算值 —— 这样做的
价值在于**可解释**：用户看到的是"这个框只有 0.8 px/字符，读不出来"，
而不是一个莫名其妙的空结果。

设计上还有一层保险：即使将来接入高分辨率相机，预检仍会拦住那些
**远处的、本来就读不出**的车辆，避免把识别噪声当成车牌号送进车主查询。
在公安业务里，一个错误的车牌号意味着查错人 —— 这个代价必须避免。
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.models.schemas import BBox

# ==========================================================================
# 几何常量
# ==========================================================================

#: 中国民用号牌宽高比（440 x 140 mm）
PLATE_ASPECT = 440 / 140

#: 车牌宽度占车宽的比例。以 1.8 m 车宽、0.44 m 牌宽估算。
PLATE_TO_VEHICLE_WIDTH = 0.24

#: 号牌字符高度占牌高的比例（上下留白）。
PLATE_CHAR_HEIGHT_RATIO = 0.7

#: OCR 可读下限（像素）。低于此值的字符无法稳定识别。
MIN_READABLE_CHAR_HEIGHT = 16.0

#: 临界区间下限。此区间内需配合超分或专用检测模型。
MARGINAL_CHAR_HEIGHT = 10.0


def estimate_plate_char_height(
    bbox: Optional[BBox],
    frame_width: int,
    frame_height: int,
) -> float:
    """由车辆框估算车牌字符高度（像素）。

    这是纯粹的几何推算，不涉及任何模型 —— 正因如此它**完全可靠**，
    可以作为"能否识别"的守门条件。
    """
    if bbox is None or frame_width <= 0:
        return 0.0
    box_width_px = bbox.w * frame_width
    plate_width_px = box_width_px * PLATE_TO_VEHICLE_WIDTH
    plate_height_px = plate_width_px / PLATE_ASPECT
    return plate_height_px * PLATE_CHAR_HEIGHT_RATIO


def readability(estimated_char_height: float) -> Tuple[str, str]:
    """返回 ``(可读性等级, 说明)``。"""
    if estimated_char_height >= MIN_READABLE_CHAR_HEIGHT:
        return ('readable', '字符高度达到可读下限')
    if estimated_char_height >= MARGINAL_CHAR_HEIGHT:
        return ('marginal', '处于临界区，需超分辨率或专用车牌检测模型')
    return (
        'unreadable',
        f'字符仅约 {estimated_char_height:.1f} px，远低于可读下限 '
        f'{MIN_READABLE_CHAR_HEIGHT:.0f} px',
    )


# ==========================================================================
# 结果
# ==========================================================================


@dataclass
class PlateResult:
    """一次车牌识别的结果。"""

    ok: bool
    plate: str = ''
    #: 号牌种类：blue / yellow / green / white / black
    plate_color: str = ''
    confidence: float = 0.0
    engine: str = 'none'
    message: str = ''
    #: 估算的字符高度（像素）—— 解释"为什么读得出/读不出"的关键数据
    estimated_char_height: float = 0.0
    #: 可读性等级：readable / marginal / unreadable
    readability: str = 'unreadable'
    vehicle_bbox: Optional[BBox] = None
    #: 是否为模拟数据（Mock 通道置 True，避免误当作真实车牌使用）
    simulated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            'ok': self.ok,
            'plate': self.plate,
            'plateColor': self.plate_color,
            'confidence': round(self.confidence, 4),
            'engine': self.engine,
            'message': self.message,
            'estimatedCharHeight': round(self.estimated_char_height, 2),
            'readability': self.readability,
            'simulated': self.simulated,
            'vehicleBBox': self.vehicle_bbox.model_dump() if self.vehicle_bbox else None,
        }


# ==========================================================================
# 适配器基类
# ==========================================================================


class PlateRecognizer(ABC):
    """车牌识别适配器。

    :meth:`recognize` 的实现**必须先做分辨率预检**，再决定是否调用引擎。
    """

    key: str = ''
    display_name: str = ''

    @abstractmethod
    def is_available(self) -> Tuple[bool, str]:
        """返回 ``(是否可用, 原因)``。"""

    def recognize(
        self,
        *,
        image_path: str,
        vehicle_bbox: Optional[BBox],
        frame_width: int,
        frame_height: int,
        hint: str = '',
    ) -> PlateResult:
        """识别车牌。

        :param hint: 稳定标识（如 track_id），供 Mock 通道生成确定性假数据。
        """
        char_height = estimate_plate_char_height(vehicle_bbox, frame_width, frame_height)
        level, note = readability(char_height)

        # ---- 预检：读不出就不调用引擎，直接给出可解释的结论 ----
        if level == 'unreadable':
            return PlateResult(
                ok=False,
                engine=self.key,
                message=(
                    f'{note}。估算依据：车辆框 {vehicle_bbox.w * frame_width:.1f} px 宽，'
                    f'按车牌占车宽 {PLATE_TO_VEHICLE_WIDTH:.0%} 反推得车牌字符高度。'
                    '需接入高分辨率卡口相机'
                ),
                estimated_char_height=char_height,
                readability=level,
                vehicle_bbox=vehicle_bbox,
            )

        return self._do_recognize(
            image_path=image_path,
            vehicle_bbox=vehicle_bbox,
            frame_width=frame_width,
            frame_height=frame_height,
            char_height=char_height,
            level=level,
            hint=hint,
        )

    @abstractmethod
    def _do_recognize(
        self,
        *,
        image_path: str,
        vehicle_bbox: Optional[BBox],
        frame_width: int,
        frame_height: int,
        char_height: float,
        level: str,
        hint: str,
    ) -> PlateResult:
        """实际识别逻辑（已完成预检）。"""

    def describe(self) -> Dict[str, Any]:
        available, reason = self.is_available()
        return {
            'key': self.key,
            'displayName': self.display_name,
            'available': available,
            'reason': reason,
        }


# ==========================================================================
# 通道 1：不可用（默认）
# ==========================================================================


class UnavailablePlateRecognizer(PlateRecognizer):
    """占位实现：不进行任何识别，只如实报告不可用。

    这是当前的**默认通道**。之所以不默认用 Mock，是因为 Mock 会产出
    格式正确的车牌号 —— 若被误接入业务流程，可能触发对无辜车主的查询。
    模拟数据必须**显式启用**。
    """

    key = 'none'
    display_name = '未接入（车牌识别不可用）'

    def is_available(self) -> Tuple[bool, str]:
        return (
            False,
            '未接入车牌识别引擎。当前视频源 352x288，车牌字符高度实测不足 1 px，'
            '任何引擎都无法识别；接入高分辨率卡口相机后把 '
            'SURVEILLANCE_PLATE_ENGINE 设为 official 即可启用',
        )

    def _do_recognize(
        self,
        *,
        image_path: str,
        vehicle_bbox: Optional[BBox],
        frame_width: int,
        frame_height: int,
        char_height: float,
        level: str,
        hint: str,
    ) -> PlateResult:
        return PlateResult(
            ok=False,
            engine=self.key,
            message='未接入车牌识别引擎',
            estimated_char_height=char_height,
            readability=level,
            vehicle_bbox=vehicle_bbox,
        )


# ==========================================================================
# 通道 2：模拟（演示 / 联调用，需显式启用）
# ==========================================================================


class MockPlateRecognizer(PlateRecognizer):
    """生成格式正确但**明确标记为模拟**的车牌号。

    用途是让「识别 → 车主查询 → 处置」的完整链路在无卡口相机时也能演示。
    输出带 ``simulated=True`` 标记，界面必须显示该标记 —— 避免模拟数据
    被当成真实车牌用于实际业务。
    """

    key = 'mock'
    display_name = '模拟车牌（仅用于流程演示）'

    #: 模拟号牌前缀，明显区别于真实号牌
    PREFIX = '豫A·SIM'

    def is_available(self) -> Tuple[bool, str]:
        return (True, '模拟通道：生成确定性的演示号牌，输出带 simulated 标记')

    def _do_recognize(
        self,
        *,
        image_path: str,
        vehicle_bbox: Optional[BBox],
        frame_width: int,
        frame_height: int,
        char_height: float,
        level: str,
        hint: str,
    ) -> PlateResult:
        # 用 hint 的哈希保证同一目标在同一会话中号码稳定，
        # 否则每轮巡检都会"换一辆车"，无法演示轨迹与查询的关联
        digest = hashlib.sha1((hint or image_path).encode('utf-8')).hexdigest()
        serial = int(digest[:6], 16) % 10000
        return PlateResult(
            ok=True,
            plate=f'{self.PREFIX}{serial:04d}',
            plate_color='blue',
            confidence=0.99,
            engine=self.key,
            message='【模拟数据】非真实车牌，仅用于演示识别与车主查询流程',
            estimated_char_height=char_height,
            readability=level,
            vehicle_bbox=vehicle_bbox,
            simulated=True,
        )


# ==========================================================================
# 通道 3：正式接入（占位）
# ==========================================================================


class OfficialPlateRecognizer(PlateRecognizer):
    """正式的卡口 / 第三方车牌识别通道（占位）。

    接入时在此处调用具体的识别服务：可能是本地部署的专用车牌检测 +
    字符识别模型，也可能是交警部门提供的卡口图片比对接口。

    **前置条件（无此条件则不应启用）**：

    1. 图像中车牌字符高度 >= 16 px —— 需要在
       :meth:`PlateRecognizer.recognize` 的预检中实际通过；
    2. 已完成与业务方的接口授权与数据使用协议。

    在此之前调用会返回不可用，而不是给出猜测结果。
    """

    key = 'official'
    display_name = '正式车牌识别通道（未接入）'

    def is_available(self) -> Tuple[bool, str]:
        return (
            False,
            '正式通道尚未接入。需高分辨率卡口相机（车牌字符高度 >= 16 px）'
            '并完成接口授权',
        )

    def _do_recognize(
        self,
        *,
        image_path: str,
        vehicle_bbox: Optional[BBox],
        frame_width: int,
        frame_height: int,
        char_height: float,
        level: str,
        hint: str,
    ) -> PlateResult:
        return PlateResult(
            ok=False,
            engine=self.key,
            message='正式车牌识别通道尚未接入',
            estimated_char_height=char_height,
            readability=level,
            vehicle_bbox=vehicle_bbox,
        )


# ==========================================================================
# 工厂
# ==========================================================================

_RECOGNIZERS: Dict[str, PlateRecognizer] = {
    'none': UnavailablePlateRecognizer(),
    'mock': MockPlateRecognizer(),
    'official': OfficialPlateRecognizer(),
}


def get_recognizer(key: str = '') -> PlateRecognizer:
    if key and key in _RECOGNIZERS:
        return _RECOGNIZERS[key]
    return _RECOGNIZERS['none']


def list_recognizers() -> List[Dict[str, Any]]:
    return [item.describe() for item in _RECOGNIZERS.values()]
