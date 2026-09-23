"""
演示数据接口
============
+----------------------+-----------------------------------------------+
| ``GET    /status``   | 演示数据状态（是否生效、为什么失效）            |
| ``POST   /seed``     | 注入一批演示事件（上海，幂等）                  |
| ``DELETE /clear``    | 清除演示数据                                   |
| ``GET    /preview``  | 预览将被注入的内容（不写库）                    |
+----------------------+-----------------------------------------------+

定位说明
--------

这些是**测试接口，不是业务接口**。

它们存在的唯一理由是：**真实数据源尚未接入时，也能看到"行进视角"的
完整效果**（当前路段判定、编号标记、前方多少公里）。

一旦 `/public/traffic` 能取到真实事件，演示数据就**自动不再对外提供**
（判据见 `services/demo_data_service.should_serve`），
本组接口届时只用于查看状态，不再影响任何用户可见的内容。

三条设计约束
------------

1. **不写 `events` 集合。** 演示数据放在独立的 `demo_citizen_events`，
   这样它在复核队列、统计、播报等所有真实流程里都不存在 ——
   只经过 `/public/*` 这一条被明确标注的路径对外。

2. **不随启动自动注入。** 部署到新环境不会凭空出现一批上海的数据；
   必须显式调用 `/seed`。演示数据出现在生产环境而无人察觉，
   是这类"便利功能"最常见的翻车方式。

3. **响应必须标明是演示数据。** `/public/*` 的 `demo` 字段会传到前端，
   界面据此给出提示。编的坐标不能和真实路况长得一模一样。
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app.services import demo_data_service

router = APIRouter(prefix='/demo', tags=['演示数据（测试接口）'])


@router.get('/status', summary='演示数据状态')
def demo_status() -> Dict[str, Any]:
    """当前是否正在提供演示数据，以及原因。

    `reason` 会直接说明为什么生效或为什么失效 ——
    排查"怎么看不到演示数据"时不用翻代码。
    """
    return {'success': True, **demo_data_service.status()}


@router.get('/preview', summary='预览演示内容（不写库）')
def demo_preview() -> Dict[str, Any]:
    """看一眼将要注入哪些事件，避免先写库再发现内容不合适。"""
    events = demo_data_service.build_events()
    return {
        'success': True,
        'total': len(events),
        'items': [
            {
                'eventType': event.event_type.value,
                'level': event.level.value,
                'roadName': event.road_name,
                'title': event.title,
                'description': event.description,
                'latitude': event.latitude,
                'longitude': event.longitude,
            }
            for event in events
        ],
    }


@router.post('/seed', summary='注入演示数据')
def demo_seed() -> Dict[str, Any]:
    """注入一批**上海长宁区**的演示事件（延安西路 / 虹桥路一带）。

    幂等：重复调用只会保留一份，不会越积越多。

    注意注入≠生效。只有当 `events` 集合里没有任何真实事件时，
    这批数据才会被 `/public/*` 提供给前端；否则它只是躺在库里。
    """
    result = demo_data_service.seed()
    if not result.get('success'):
        return result
    return {**result, 'status': demo_data_service.status()}


@router.delete('/clear', summary='清除演示数据')
def demo_clear() -> Dict[str, Any]:
    """清除演示数据。只影响 `demo_citizen_events`，不碰真实事件。"""
    result = demo_data_service.clear()
    if not result.get('success'):
        return result
    return {**result, 'status': demo_data_service.status()}
