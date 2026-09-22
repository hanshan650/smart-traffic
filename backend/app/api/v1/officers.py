"""
警员管理接口
============
+------------------------------+------------------------------------------+
| ``GET    /officers``         | 列表（支持状态 / 单位 / 关键词筛选）        |
| ``GET    /officers/stats``   | 勤务概览                                  |
| ``GET    /officers/{id}``    | 详情（``id`` 为**警号**）                  |
| ``POST   /officers``         | 新增                                      |
| ``POST   /officers/seed``    | 导入示例警员（便于演示）                    |
| ``PUT    /officers/{id}``    | 部分更新                                  |
| ``DELETE /officers/{id}``    | 删除                                      |
+------------------------------+------------------------------------------+

注意：``/stats`` 与 ``/seed`` 必须注册在 ``/{officer_id}`` **之前**，
否则会被后者当作警号匹配掉（FastAPI 按声明顺序匹配路由）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    Officer,
    OfficerCreate,
    OfficerListResponse,
    OfficerStats,
    OfficerUpdate,
)
from app.services import officer_service
from app.services.officer_service import OfficerError

router = APIRouter(prefix='/officers', tags=['警员'])


def _bad_request(exc: OfficerError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={'success': False, 'error': str(exc)},
    )


def _service_unavailable(exc: OfficerError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            'success': False,
            'error': str(exc),
            'hint': '请确认 MongoDB 已启动（警员档案需要持久化）',
        },
    )


@router.get('', response_model=OfficerListResponse, summary='警员列表')
def list_officers(
    status: Optional[str] = Query(None, description='on_duty / busy / off_duty / leave'),
    unit: str = Query('', description='按单位筛选'),
    keyword: str = Query('', description='按警号或姓名模糊匹配'),
    limit: int = Query(200, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> OfficerListResponse:
    try:
        return officer_service.list_officers(
            status=status, unit=unit, keyword=keyword, limit=limit, skip=skip
        )
    except OfficerError as exc:
        raise _service_unavailable(exc) from exc


@router.get('/stats', response_model=OfficerStats, summary='勤务概览')
def officer_stats() -> OfficerStats:
    """在岗 / 出警中 / 下班 / 请假的人数与单位分布。"""
    try:
        return officer_service.stats()
    except OfficerError as exc:
        raise _service_unavailable(exc) from exc


@router.post('/seed', summary='导入示例警员')
def seed_officers(
    reset: bool = Query(False, description='是否先清空现有警员数据'),
) -> Dict[str, Any]:
    """导入 12 名示例警员（河南各大队，状态与专长各异），便于演示派警。

    幂等：已存在的警号会跳过。``reset=true`` 时先清空。
    """
    try:
        created = officer_service.seed_officers(reset=reset)
    except OfficerError as exc:
        raise _service_unavailable(exc) from exc

    return {
        'success': True,
        'created': created,
        'message': f'已导入 {created} 名示例警员' if created else '示例警员已存在，未重复导入',
    }


@router.post('', response_model=Officer, summary='新增警员', status_code=201)
def create_officer(payload: OfficerCreate) -> Officer:
    try:
        return officer_service.create_officer(payload)
    except OfficerError as exc:
        # 警号重复属参数问题，给 400 而非 503
        raise _bad_request(exc) from exc


@router.get('/{officer_id}', response_model=Officer, summary='警员详情')
def get_officer(officer_id: str) -> Officer:
    """``officer_id`` 为**警号**（业务主键），非数据库 ``_id``。"""
    try:
        officer = officer_service.get_officer(officer_id)
    except OfficerError as exc:
        raise _service_unavailable(exc) from exc

    if officer is None:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': f'警员不存在：{officer_id}'},
        )
    return officer


@router.put('/{officer_id}', response_model=Officer, summary='更新警员')
def update_officer(officer_id: str, payload: OfficerUpdate) -> Officer:
    try:
        officer = officer_service.update_officer(officer_id, payload)
    except OfficerError as exc:
        raise _bad_request(exc) from exc

    if officer is None:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': f'警员不存在：{officer_id}'},
        )
    return officer


@router.delete('/{officer_id}', summary='删除警员')
def delete_officer(officer_id: str) -> Dict[str, Any]:
    """物理删除。历史处置记录保留在 ``incidents`` 中（已冗余姓名与警号），
    因此删除警员不会影响既往警情的可读性。"""
    try:
        deleted = officer_service.delete_officer(officer_id)
    except OfficerError as exc:
        raise _service_unavailable(exc) from exc

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={'success': False, 'error': f'警员不存在：{officer_id}'},
        )
    return {'success': True, 'message': f'已删除警员 {officer_id}'}
