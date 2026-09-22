"""
警情上报适配器
==============
把内部警情单推送到外部交警平台。沿用 :mod:`app.services.video_source`
的 Provider 模式：业务层只调 :func:`get_reporter`，不感知具体对接方。

为什么要有适配器层
------------------
各地交警平台的接口协议、字段命名、鉴权方式各不相同，且**大多不公开**。
如果让业务代码直接 ``requests.post``，接一个新平台就要改动派警与调度逻辑。
把差异收敛到适配器后：

· 换平台 = 新增一个适配器类 + 注册，其余代码零改动
· 没有真实接口时可用 Mock 适配器跑通全流程（演示与开发都需要）
· 接口不可用是**预期状态**而非异常：像上海源那样返回明确原因，
  让告警中心能显示"已生成警情单，但上报失败，可人工补报"

当前适配器
----------
+----------------------+--------------------------------------+------------------+
| key                  | 说明                                 | 状态             |
+======================+======================================+==================+
| ``mock``             | 模拟交警集成指挥平台（默认）          | 可用             |
| ``shanghai``         | 上海交警集成指挥平台                  | 仅保留接入位     |
+----------------------+--------------------------------------+------------------+
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.models.schemas import Incident, ReporterInfo


@dataclass
class ReportResult:
    """一次上报的结果。"""

    ok: bool
    external_id: str = ''
    message: str = ''
    raw: Dict[str, Any] = field(default_factory=dict)


# ==========================================================================
# 抽象基类
# ==========================================================================

class IncidentReporter(ABC):
    """警情上报适配器抽象基类。

    子类需声明 ``key`` / ``display_name`` 并实现 :meth:`send`。
    """

    key: str = ''
    display_name: str = ''
    mode: str = 'mock'          # mock = 模拟；real = 对接真实平台
    endpoint: str = ''
    supports_ack: bool = False

    def is_available(self) -> Tuple[bool, str]:
        """是否可上报。缺省视为可用；无真实接口的子类应覆盖此方法。"""
        return True, ''

    @abstractmethod
    def send(self, incident: Incident) -> ReportResult:
        """推送一条警情单。实现方**不应抛出异常**，失败请返回 ``ok=False``。"""

    def query_ack(self, external_id: str) -> Optional[str]:
        """查询外部系统的处置回执。不支持的适配器返回 ``None``。"""
        return None

    def describe(self) -> ReporterInfo:
        available, reason = self.is_available()
        return ReporterInfo(
            key=self.key,
            display_name=self.display_name,
            mode=self.mode,
            available=available,
            reason=reason,
            endpoint=self.endpoint,
            supports_ack=self.supports_ack,
        )


# ==========================================================================
# Mock 适配器
# ==========================================================================

class MockPoliceReporter(IncidentReporter):
    """模拟交警集成指挥平台。

    用途：在没有真实接口的情况下跑通「生成警情单 → 上报 → 回执 → 派警」
    全链路。返回的警情编号格式刻意贴近真实平台（``JQ`` + 日期 + 序号），
    便于前端与文档直接使用。

    :param failure_rate: 人为失败概率。默认 0（演示顺畅）；
        设为正值可验证重试与失败提示的展示效果。
    """

    key = 'mock'
    display_name = '模拟交警集成指挥平台'
    mode = 'mock'
    endpoint = 'mock://police/incident/report'
    supports_ack = True

    def __init__(self, failure_rate: float = 0.0, options: Optional[Dict[str, Any]] = None) -> None:
        self.failure_rate = max(0.0, min(1.0, failure_rate))
        self.options = options or {}
        self._sequence = 0

    def send(self, incident: Incident) -> ReportResult:
        if self.failure_rate and random.random() < self.failure_rate:
            return ReportResult(
                ok=False,
                message='模拟平台返回 503：指挥平台繁忙，请稍后重试',
                raw={'simulated': True},
            )

        self._sequence += 1
        stamp = datetime.utcnow().strftime('%Y%m%d')
        external_id = f'JQ{stamp}{self._sequence:04d}'

        # 按事件等级模拟受理单位的差异：严重事件由支队受理，其余由大队受理
        level = incident.level.value if hasattr(incident.level, 'value') else str(incident.level)
        handler = '交警支队指挥中心' if level == 'critical' else '辖区大队值班室'

        return ReportResult(
            ok=True,
            external_id=external_id,
            message=f'{handler}已受理（模拟），警情编号 {external_id}',
            raw={
                'simulated': True,
                'handler': handler,
                'acceptedAt': datetime.utcnow().isoformat(),
            },
        )

    def query_ack(self, external_id: str) -> Optional[str]:
        if not external_id:
            return None
        return f'{external_id} 已进入指挥平台处置队列（模拟回执）'


# ==========================================================================
# 上海（仅保留接入位）
# ==========================================================================

class ShanghaiPoliceReporter(IncidentReporter):
    """上海交警集成指挥平台。

    上海交警的警情接口**未对外开放**（需通过政务专网对接并申请权限），
    因此这里只保留调用契约：返回明确原因，供前端提示，
    而不是抛异常或伪装成功 —— 后者会让"已上报"的假象误导值班员。

    接入真实接口时，只需在 :meth:`send` 中替换为实际请求。
    """

    key = 'shanghai'
    display_name = '上海交警集成指挥平台'
    mode = 'real'
    endpoint = ''
    supports_ack = False

    def is_available(self) -> Tuple[bool, str]:
        return False, '上海交警警情接口未对外开放，需通过政务专网对接并申请权限'

    def send(self, incident: Incident) -> ReportResult:
        return ReportResult(
            ok=False,
            message='上海交警警情接口未接入：该接口未对外开放，需申请政务专网权限后对接',
            raw={'reporter': self.key, 'reason': 'not_implemented'},
        )


# ==========================================================================
# 注册与选择
# ==========================================================================

_REPORTER_CLASSES: Dict[str, type[IncidentReporter]] = {
    MockPoliceReporter.key: MockPoliceReporter,
    ShanghaiPoliceReporter.key: ShanghaiPoliceReporter,
}

_INSTANCES: Dict[str, IncidentReporter] = {}


def get_reporter(key: Optional[str] = None) -> IncidentReporter:
    """按 key 获取适配器实例（进程内缓存），缺省取配置值。"""
    resolved = (key or settings.report_reporter or 'mock').strip() or 'mock'
    if resolved not in _REPORTER_CLASSES:
        resolved = 'mock'

    if resolved not in _INSTANCES:
        cls = _REPORTER_CLASSES[resolved]
        options: Dict[str, Any] = {}
        if resolved == MockPoliceReporter.key:
            options['failure_rate'] = settings.report_mock_failure_rate
        _INSTANCES[resolved] = cls(**options)

    return _INSTANCES[resolved]


def list_reporters() -> List[ReporterInfo]:
    """列出全部适配器及其可用状态，供前端做选择器。"""
    reporters: List[ReporterInfo] = []
    for key in _REPORTER_CLASSES:
        reporters.append(get_reporter(key).describe())
    return reporters
