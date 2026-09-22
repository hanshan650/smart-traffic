"""
警情调度接口
============
+-----------------------------------+---------------------------------------+
| ``GET  /incidents``               | 警情列表（状态 / 等级筛选）            |
| ``GET  /incidents/stats``         | 警情统计                              |
| ``GET  /incidents/algorithm``     | 派警算法与上报适配器说明               |
| ``POST /incidents``               | 创建警情（可自动上报外部平台）          |
| ``GET  /incidents/{id}``          | 详情                                  |
| ``POST /incidents/{id}/report``   | 上报外部平台                          |
| ``POST /incidents/{id}/retry``    | 重试上报                              |
| ``POST /incidents/{id}/dispatch`` | 智能派警                              |
| ``POST /incidents/{id}/status``   | 处置状态流转（接警 / 到场 / 办结）      |
+-----------------------------------+---------------------------------------+

注意：``/stats`` 与 ``/algorithm`` 必须注册在 ``/{incident_id}`` **之前**。

设计取舍：**上报失败不返回 5xx**。外部平台不可用是常态，
警情单已成功创建，返回值里带着 ``reportStatus=failed`` 与失败原因即可，
由前端提示"可重试"——若因外部依赖失败就整体报错，会让值班员
误以为警情没建上而重复录入。
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    DispatchRequest,
    Incident,
    IncidentCreate,
    IncidentListResponse,
    IncidentStats,
)
from app.services import incident_service
from app.services.incident_service import IncidentError

router = APIRouter(prefix='/incidents', tags=['警情调度'])


def _service_unavailable(exc: IncidentError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            'success': False,
            'error': str(exc),
            'hint': '请确认 MongoDB 已启动（警情处置需要持久化）',
        },
    )


def _not_found(incident_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={'success': False, 'error': f'警情不存在：{incident_id}'},
    )


@router.get('', response_model=IncidentListResponse, summary='警情列表')
def list_incidents(
    status: str = Query('', description='reported / dispatched / accepted / arrived / closed / failed'),
    level: str = Query('', description='info / warning / critical'),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
) -> IncidentListResponse:
    try:
        return incident_service.list_incidents(
            status=status, level=level, limit=limit, skip=skip
        )
    except IncidentError as exc:
        raise _service_unavailable(exc) from exc


@router.get('/stats', response_model=IncidentStats, summary='警情统计')
def incident_stats() -> IncidentStats:
    """按状态与类型汇总，并给出上报失败数与未派警数。"""
    try:
        return incident_service.stats()
    except IncidentError as exc:
        raise _service_unavailable(exc) from exc


@router.get('/algorithm', summary='派警算法与上报适配器说明')
def algorithm_description() -> Dict[str, Any]:
    """返回派警判据、策略权重、上报适配器清单与状态机定义。

    前端据此渲染「为什么派给他」的说明与策略选择器，
    也便于论文撰写时对照参数。
    """
    return {'success': True, **incident_service.describe()}


@router.post('', response_model=Incident, summary='创建警情', status_code=201)
def create_incident(payload: IncidentCreate) -> Incident:
    """从 AI 事件创建警情单。

    ``autoReport=true``（默认）时会立即推送外部平台；
    失败不阻断创建，只在 ``reportStatus`` 中标记。
    """
    try:
        return incident_service.create_incident(payload)
    except IncidentError as exc:
        raise _service_unavailable(exc) from exc


@router.get('/{incident_id}', response_model=Incident, summary='警情详情')
def get_incident(incident_id: str) -> Incident:
    try:
        incident = incident_service.get_incident(incident_id)
    except IncidentError as exc:
        raise _service_unavailable(exc) from exc

    if incident is None:
        raise _not_found(incident_id)
    return incident


@router.post('/{incident_id}/report', response_model=Incident, summary='上报外部平台')
def report_incident(
    incident_id: str,
    reporter: str = Query('', description='上报适配器 key，留空取配置默认值'),
) -> Incident:
    """向交警平台上报。失败时返回 200 且 ``reportStatus=failed``（见模块文档）。"""
    try:
        return incident_service.report_incident(incident_id, reporter)
    except IncidentError as exc:
        raise _not_found(incident_id) from exc


@router.post('/{incident_id}/retry', response_model=Incident, summary='重试上报')
def retry_report(incident_id: str) -> Incident:
    try:
        return incident_service.retry_report(incident_id)
    except IncidentError as exc:
        raise _not_found(incident_id) from exc


@router.post('/{incident_id}/dispatch', response_model=Incident, summary='智能派警')
def dispatch_incident(incident_id: str, request: DispatchRequest) -> Incident:
    """按策略选出最优警员并落库。

    响应中的 ``candidates`` 含每名候选的评分明细（距离 / 技能 / 负载 / 状态
    四条的原始值、归一化得分、权重与贡献），可直接渲染"为什么是他"。

    无人可派时返回 **409**，因为这是业务状态而非服务故障 —— 前端应提示
    "当前无可派警力"而不是"服务器错误"。
    """
    try:
        return incident_service.dispatch_incident(incident_id, request)
    except IncidentError as exc:
        message = str(exc)
        if '不存在' in message:
            raise _not_found(incident_id) from exc
        raise HTTPException(
            status_code=409,
            detail={'success': False, 'error': message},
        ) from exc


@router.post('/{incident_id}/status', response_model=Incident, summary='处置状态流转')
def update_status(
    incident_id: str,
    action: str = Query(..., description='accept / arrive / close'),
    note: str = Query('', description='处置备注，写入时间线'),
) -> Incident:
    """推进状态：``accept`` 接警 → ``arrive`` 到场 → ``close`` 办结。

    办结时会释放警员负载（``activeCases -1``），
    使下一轮派警的「负载」判据反映真实情况。
    """
    try:
        return incident_service.update_status(incident_id, action, note)
    except IncidentError as exc:
        message = str(exc)
        if '不存在' in message:
            raise _not_found(incident_id) from exc
        raise HTTPException(
            status_code=409,
            detail={'success': False, 'error': message},
        ) from exc
