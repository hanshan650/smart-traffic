"""
场景文字识别（可停车场所判定）
==============================

用途
----
违停判定的前提是「该地点**不应该**停车」。高速公路上并非处处禁停：

  · 收费站广场、服务区   —— 合法停车
  · 检查站、治超站       —— 合法停车
  · 隧道紧急停车带       —— 合法临时停车

在这些点位上报「停车 30 秒」是误报。所以需要一个环节回答：
**这个摄像头所在的位置，允不允许停车？**

两条证据来源，优先级从高到低
----------------------------
1. **点位元数据关键词**（:class:`KeywordSceneTextRecognizer`，默认启用）
   摄像头的名称与位置描述本身就是文本 —— 上游河南高速的命名里常直接带
   「收费站」「服务区」。**文本匹配是确定性的**，没有识别抖动，
   所以这应当是首选证据，而不是次选。

2. **画面文字识别**（:class:`OcrSceneTextRecognizer`，需额外依赖）
   当日志元数据不足以判断时，尝试从画面里读出标牌文字。

   必须说明的现实：本项目当前视频源为 **352 x 288**（实测，见
   ``scripts/probe_plate_pixels.py``）。画面中一块百米外的指示牌只有
   几个像素高，**OCR 在此分辨率下不可能读出文字**。因此该通道默认返回
   「不可用」并给出原因，而不是假装识别成功。

设计取向
--------
判定失败时的默认行为是**不豁免**（即视为禁停区）。理由是：违停报警的
误报代价是浪费一次核查，而漏报代价是放过一次可能的险情 —— 对高速场景
而言后者更严重。豁免必须建立在**确凿证据**上，不能建立在猜测上。
"""
from __future__ import annotations

import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ==========================================================================
# 可停车场所类型
# ==========================================================================

#: 场所类型 → 中文标签
ZONE_TYPE_LABEL: Dict[str, str] = {
    'highway': '高速公路主线',
    'toll_station': '收费站',
    'service_area': '服务区',
    'checkpoint': '检查站 / 治超站',
    'tunnel': '隧道',
    'parking': '停车场',
    'unknown': '未知',
}

#: 允许停车的场所类型。其余一律视为禁停区。
PARKING_ALLOWED_ZONES = frozenset(
    {'toll_station', 'service_area', 'checkpoint', 'parking'}
)

#: 关键词 → 场所类型。用于从点位元数据或画面文字中推断场所性质。
#: 顺序有意义：先匹配到的优先，因此把更具体的词放在前面。
ZONE_KEYWORDS: Tuple[Tuple[str, str], ...] = (
    ('服务区', 'service_area'),
    ('停车区', 'service_area'),
    ('收费站', 'toll_station'),
    ('收费站广场', 'toll_station'),
    ('治超站', 'checkpoint'),
    ('检查站', 'checkpoint'),
    ('公安检查站', 'checkpoint'),
    ('收费站入口', 'toll_station'),
    ('隧道', 'tunnel'),
    ('停车场', 'parking'),
)


def infer_zone_type(*texts: str) -> Tuple[str, str]:
    """从若干段文本中推断场所类型。

    :return: ``(zone_type, matched_keyword)``；无法判断时返回
        ``('highway', '')`` —— 默认按主线（禁停）处理，理由见模块文档。
    """
    for text in texts:
        if not text:
            continue
        for keyword, zone_type in ZONE_KEYWORDS:
            if keyword in text:
                return (zone_type, keyword)
    return ('highway', '')


# ==========================================================================
# 识别结果
# ==========================================================================


@dataclass
class TextSpan:
    """一处被识别出的文字。"""

    text: str
    confidence: float = 1.0
    #: 归一化包围盒 ``(x, y, w, h)``，OCR 通道提供；关键词通道为 None
    bbox: Optional[Tuple[float, float, float, float]] = None


@dataclass
class SceneTextResult:
    """一次场景文字识别的结果。"""

    ok: bool
    texts: List[TextSpan] = field(default_factory=list)
    #: 推断出的场所类型
    zone_type: str = 'highway'
    #: 命中的关键词（用于解释判定依据）
    matched_keyword: str = ''
    #: 证据来源：``metadata`` / ``ocr`` / ``none``
    source: str = 'none'
    message: str = ''

    @property
    def parking_allowed(self) -> bool:
        return self.zone_type in PARKING_ALLOWED_ZONES

    @property
    def zone_label(self) -> str:
        return ZONE_TYPE_LABEL.get(self.zone_type, self.zone_type)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'ok': self.ok,
            'texts': [
                {'text': item.text, 'confidence': round(item.confidence, 4)}
                for item in self.texts
            ],
            'zoneType': self.zone_type,
            'zoneLabel': self.zone_label,
            'matchedKeyword': self.matched_keyword,
            'parkingAllowed': self.parking_allowed,
            'source': self.source,
            'message': self.message,
        }


# ==========================================================================
# 适配器基类
# ==========================================================================


class SceneTextRecognizer(ABC):
    """场景文字识别适配器。"""

    key: str = ''
    display_name: str = ''

    @abstractmethod
    def is_available(self) -> Tuple[bool, str]:
        """返回 ``(是否可用, 原因)``。"""

    @abstractmethod
    def recognize(
        self,
        *,
        hint_text: str = '',
        image_path: Optional[str] = None,
    ) -> SceneTextResult:
        """识别场景文字。

        :param hint_text: 点位元数据（摄像头名称、位置描述等）。这是
            **最可靠的证据来源**，因为它本身已经是文本。
        :param image_path: 画面路径，供 OCR 通道使用。
        """

    def describe(self) -> Dict[str, Any]:
        available, reason = self.is_available()
        return {
            'key': self.key,
            'displayName': self.display_name,
            'available': available,
            'reason': reason,
        }


# ==========================================================================
# 通道 1：元数据关键词（默认，零依赖）
# ==========================================================================


class KeywordSceneTextRecognizer(SceneTextRecognizer):
    """从点位元数据里匹配设施关键词。

    这是本项目**实际生效**的通道。它不需要 OCR —— 因为摄像头的名称与
    位置描述本来就是字符串，直接匹配即可，且结果完全确定、可复现。
    """

    key = 'keyword'
    display_name = '点位名称关键词匹配'

    def is_available(self) -> Tuple[bool, str]:
        return (True, '基于点位元数据的确定性匹配，无额外依赖')

    def recognize(
        self,
        *,
        hint_text: str = '',
        image_path: Optional[str] = None,
    ) -> SceneTextResult:
        zone_type, keyword = infer_zone_type(hint_text)

        if keyword:
            return SceneTextResult(
                ok=True,
                texts=[TextSpan(text=hint_text, confidence=1.0)],
                zone_type=zone_type,
                matched_keyword=keyword,
                source='metadata',
                message=f'点位名称含「{keyword}」，判定为{ZONE_TYPE_LABEL[zone_type]}',
            )

        return SceneTextResult(
            ok=True,
            texts=[TextSpan(text=hint_text, confidence=1.0)] if hint_text else [],
            zone_type='highway',
            source='metadata',
            message=(
                f'点位名称未包含可停车设施关键词，按{ZONE_TYPE_LABEL["highway"]}'
                '（禁停）处理'
            ),
        )


# ==========================================================================
# 通道 2：画面 OCR（需额外依赖，当前分辨率下不可用）
# ==========================================================================

#: 依次尝试的 OCR 引擎及其导入名
_OCR_BACKENDS: Tuple[Tuple[str, str, str], ...] = (
    ('rapidocr_onnxruntime', 'RapidOCR', '轻量 ONNX 方案，约 15 MB'),
    ('paddleocr', 'PaddleOCR', '中文识别精度高，依赖较重（约 500 MB）'),
    ('easyocr', 'EasyOCR', '多语种，依赖 torch（已具备）'),
)


def detect_ocr_backend() -> Optional[Tuple[str, str, str]]:
    """探测本机可用的 OCR 引擎，返回 ``(模块名, 名称, 说明)``。"""
    for module, name, note in _OCR_BACKENDS:
        if importlib.util.find_spec(module) is not None:
            return (module, name, note)
    return None


class OcrSceneTextRecognizer(SceneTextRecognizer):
    """从画面中识别标牌文字。

    **当前状态：不具备使用条件。** 两个独立的原因，任一都足以否决：

    1. 本机未安装任何 OCR 引擎；
    2. 即使安装，视频源分辨率仅 352 x 288（实测），画面中的指示牌高度
       通常只有几个像素，远低于 OCR 的可读下限（字符高约 16 px）。

    因此该通道默认不可用，并明确返回原因 —— 不伪造识别结果。
    接入高分辨率卡口相机（或对画面做超分预处理）后，把 ``surveillance_text``
    配置切换为 ``ocr`` 即可启用。
    """

    key = 'ocr'
    display_name = '画面 OCR（标牌文字识别）'

    def is_available(self) -> Tuple[bool, str]:
        backend = detect_ocr_backend()
        if backend is None:
            names = '、'.join(item[1] for item in _OCR_BACKENDS)
            return (
                False,
                f'本机未安装 OCR 引擎（可安装 {names} 后启用）；'
                '另外当前视频源分辨率 352x288 低于文字可读下限，'
                '建议接入高分辨率卡口相机',
            )
        return (True, f'已检测到 {backend[1]}（{backend[2]}）')

    def recognize(
        self,
        *,
        hint_text: str = '',
        image_path: Optional[str] = None,
    ) -> SceneTextResult:
        available, reason = self.is_available()
        if not available:
            # 关键：OCR 不可用时**退回元数据通道**，而不是直接放弃判定。
            # 元数据本身就是可靠证据，没有理由因为 OCR 缺失而丢掉它。
            fallback = KeywordSceneTextRecognizer().recognize(hint_text=hint_text)
            fallback.message = (
                f'OCR 通道不可用（{reason}）；已退回点位元数据判定：'
                f'{fallback.message}'
            )
            fallback.source = 'metadata'
            return fallback

        if not image_path:
            fallback = KeywordSceneTextRecognizer().recognize(hint_text=hint_text)
            fallback.message = f'未提供画面路径，退回点位元数据判定：{fallback.message}'
            return fallback

        # 引擎存在时在此处调用；当前依赖组合下走不到这里，保留接入位。
        return self._run_ocr(image_path, hint_text)

    def _run_ocr(self, image_path: str, hint_text: str) -> SceneTextResult:
        """执行 OCR 并推断场所类型。

        接入时按所选引擎补全调用即可 —— 上层不感知具体引擎。
        """
        backend = detect_ocr_backend()
        if backend is None:  # pragma: no cover - 防御性分支
            return KeywordSceneTextRecognizer().recognize(hint_text=hint_text)

        try:
            spans = _invoke_engine(backend[0], image_path)
        except Exception as exc:  # noqa: BLE001 - 识别失败不应中断巡检
            fallback = KeywordSceneTextRecognizer().recognize(hint_text=hint_text)
            fallback.message = f'OCR 执行失败（{exc}），退回点位元数据判定：{fallback.message}'
            return fallback

        joined = ' '.join(item.text for item in spans)
        zone_type, keyword = infer_zone_type(joined, hint_text)
        return SceneTextResult(
            ok=True,
            texts=spans,
            zone_type=zone_type,
            matched_keyword=keyword,
            source='ocr',
            message=(
                f'画面中识别到「{keyword}」，判定为{ZONE_TYPE_LABEL[zone_type]}'
                if keyword
                else f'画面文字未命中设施关键词（识别到 {len(spans)} 段文字）'
            ),
        )


def _invoke_engine(module_name: str, image_path: str) -> List[TextSpan]:
    """按引擎分发调用，统一归一化为 :class:`TextSpan`。"""
    if module_name == 'rapidocr_onnxruntime':
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        engine = _rapid_cache or RapidOCR()  # type: ignore[attr-defined]
        result, _ = engine(image_path)
        return [
            TextSpan(text=str(line[1]), confidence=float(line[2]))
            for line in (result or [])
        ]

    if module_name == 'paddleocr':
        from paddleocr import PaddleOCR  # type: ignore

        engine = PaddleOCR(use_angle_cls=True, show_log=False)
        result = engine.ocr(image_path, cls=True)
        spans: List[TextSpan] = []
        for page in result or []:
            for line in page or []:
                spans.append(TextSpan(text=str(line[1][0]), confidence=float(line[1][1])))
        return spans

    if module_name == 'easyocr':
        import easyocr  # type: ignore

        reader = easyocr.Reader(['ch_sim', 'en'], gpu=False)
        return [
            TextSpan(text=str(item[1]), confidence=float(item[2]))
            for item in reader.readtext(image_path)
        ]

    return []


#: 简单的引擎单例缓存（OCR 初始化开销大，不能每次重建）
_rapid_cache: Any = None


# ==========================================================================
# 工厂
# ==========================================================================

_RECOGNIZERS: Dict[str, SceneTextRecognizer] = {
    'keyword': KeywordSceneTextRecognizer(),
    'ocr': OcrSceneTextRecognizer(),
}


def get_recognizer(key: str = '') -> SceneTextRecognizer:
    """按 key 取识别器，未知 key 回退到关键词通道。"""
    if key and key in _RECOGNIZERS:
        return _RECOGNIZERS[key]
    return _RECOGNIZERS['keyword']


def list_recognizers() -> List[Dict[str, Any]]:
    return [item.describe() for item in _RECOGNIZERS.values()]
