"""
民众上报复核（警务端）
======================
+------------------------------+-------------------------------------------+
| ``GET  /reports``            | 复核队列（含未脱敏联系方式）              |
| ``GET  /reports/stats``      | 上报概览                                  |
| ``GET  /reports/{report_no}``| 上报详情                                  |
| ``POST /reports/{no}/review``| 复核：通过 / 驳回 / 判为重复              |
+------------------------------+-------------------------------------------+

为什么民众上报需要人工复核
--------------------------
AI 识别的误报有明确统计特性，可以通过调参收敛；人的上报没有 ——
可能是真实险情，也可能是看错了、重复了、甚至恶意的。两者混在同一套
告警体系里，会同时损害两件事：值班员被无效信息淹没，
而真实线索被稀释。

所以这里是一条**独立的人工闸门**：民众上报进队列 → 值班员核实 →
``approve`` 才转为正式事件（走 ``event_service.create_event``）。

联系方式在这一层**不脱敏** —— 值班员需要打电话核实。这也是为什么
该接口属于警务端而非 ``/public``。
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.models.schemas import CitizenReportReviewRequest
from app.services import citizen_report_service
from app.services.citizen_report_service import CitizenReportError

router = APIRouter(prefix='/reports', tags=['民众上报复核'])

#: 复核队列的状态过滤取值
ALLOWED_STATUSES = frozenset({'pending', 'approved', 'rejected', 'duplicate'})


@router.get('/stats', summary='上报概览')
def report_stats() -> Dict[str, Any]:
    """按状态与类型分组的统计。

    本路由必须早于 ``/reports/{report_no}`` 注册，否则 ``stats``
    会被当作上报编号匹配掉。
    """
    return {'success': True, **citizen_report_service.report_stats()}


@router.get('', summary='复核队列')
def list_reports(
    status: str = Query('pending', description='pending / approved / rejected / duplicate'),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """列出民众上报。

    默认只列待复核的 —— 值班员打开这个页面的目的就是处理积压，
    把已处理的混进来只会稀释注意力。
    """
    if status and status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={
                'success': False,
                'error': f'不支持的状态：{status}',
                'allowed': sorted(ALLOWED_STATUSES),
            },
        )

    reports = citizen_report_service.list_reports(status=status, limit=limit, skip=skip)
    return {
        'success': True,
        'total': citizen_report_service.count_reports(status),
        'items': [item.internal_dict() for item in reports],
    }


@router.get('/{report_no}', summary='上报详情')
def report_detail(report_no: str) -> Dict[str, Any]:
    report = citizen_report_service.get_report(report_no)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': f'未找到编号为 {report_no} 的上报'},
        )
    return {'success': True, **report.internal_dict()}


@router.post('/{report_no}/review', summary='复核上报')
async def review_report(
    report_no: str,
    payload: CitizenReportReviewRequest,
) -> Dict[str, Any]:
    """复核一条上报。

    ``action=approve`` 会把它转为正式事件，事件 id 写回上报记录。
    转事件失败不会让整个复核失败 —— 上报已核实这一事实仍需落库，
    事件可以后续补转。
    """
    try:
        report = await run_in_threadpool(
            citizen_report_service.review_report,
            report_no,
            action=payload.action,
            reviewer=payload.reviewer,
            note=payload.note,
            level=payload.level,
        )
    except CitizenReportError as exc:
        raise HTTPException(status_code=400, detail={'success': False, 'error': str(exc)})

    if report is None:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': f'未找到编号为 {report_no} 的上报'},
        )
    return {'success': True, **report.internal_dict()}
