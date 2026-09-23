"""
系统接口
========
+-----------------+-------------------------------------------------+
| ``GET  /health``| 健康检查（MongoDB / Redis / YOLO / 视频源 / WS）  |
| ``GET  /stats`` | 大屏统计概览                                     |
| ``GET  /config``| 前端运行时配置（高德 Key、阈值、地图中心）        |
| ``GET  /runtime-config`` | 可在线修改的配置项（默认关闭）            |
| ``POST /runtime-config`` | 保存配置到 .env（需口令）                 |
| ``WS   /ws``    | 实时推送                                         |
+-----------------+-------------------------------------------------+

WebSocket 协议
--------------
客户端发送文本 ``ping``，服务端回 ``pong``；其余消息原样 ``echo``。
业务推送由服务端主动下发，频道见 ``app/realtime/ws_manager.py``。
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.models.schemas import CamelModel, HealthStatus, StatsOverview
from app.realtime import ws_manager
from app.realtime.ws_manager import manager
from app.services import detector, event_service, runtime_config, video_source

router = APIRouter(tags=['系统'])


# ==========================================================================
# 健康与配置
# ==========================================================================

@router.get('/health', response_model=HealthStatus, summary='健康检查')
def health() -> HealthStatus:
    """综合健康状态。任一依赖不可用只降级、不报错，便于前端做提示。"""
    from app.db import mongo, redis_client

    mongo_ok = mongo.ping()
    # 用带缓存的探测：Redis 不可用时避免健康检查被反复阻塞
    redis_ok = redis_client.ping_cached()
    yolo_ok, _ = detector.is_available()
    source = video_source.current_source()

    return HealthStatus(
        status='ok' if (mongo_ok and redis_ok) else 'degraded',
        mongo=mongo_ok,
        redis=redis_ok,
        yolo=yolo_ok,
        video_source=source.key,
        video_source_available=source.available,
        detail={
            'database': settings.mongo_db_name,
            # 仅在连通时才取 info，避免第二次慢探测
            'redis': redis_client.info() if redis_ok else {},
            'yolo': detector.model_info(),
            'videoSource': source.model_dump(by_alias=True),
            'websocket': ws_manager.stats(),
        },
    )


@router.get('/stats', response_model=StatsOverview, summary='统计概览')
def stats() -> StatsOverview:
    """大屏顶部统计。摄像头数量取自当前视频源，失败时按 0 处理。"""
    camera_total = 0
    camera_online = 0

    try:
        provider = video_source.get_provider()
        result = provider.list_cameras(count=1)
        camera_total = result.total_all
        camera_online = sum(1 for cam in result.cameras if cam.online)
    except Exception:
        pass

    return event_service.build_stats(camera_total, camera_online)


@router.get('/config', summary='前端运行时配置')
def client_config() -> Dict[str, Any]:
    """返回前端需要的运行时配置。

    ``amapJsKey`` 与 ``amapJsSecurityCode`` 均为浏览器端参数，
    本身即暴露在前端页面中，不属敏感信息；高德 Web 服务 Key
    （``AMAP_WEB_KEY``）**不下发**，仅在后端代取接口中使用。
    """
    return {
        'success': True,
        'appTitle': settings.app_title,
        'apiPrefix': settings.api_prefix,
        'amapJsKey': settings.amap_js_key,
        'amapJsSecurityCode': settings.amap_js_security_code,
        'shanghaiCenter': [settings.shanghai_center_lng, settings.shanghai_center_lat],
        'thresholds': {
            'light': settings.traffic_light_threshold,
            'moderate': settings.traffic_moderate_threshold,
            'heavy': settings.traffic_heavy_threshold,
        },
        'videoSource': settings.video_source,
    }


class RuntimeConfigUpdate(CamelModel):
    """网页提交的配置改动。

    ``token`` 与 ``values`` 分开传，而不是把口令塞进 values：
    口令不是配置项，不该进入白名单校验与 .env 写入的流程。
    """

    token: str = ''
    values: Dict[str, str] = {}


@router.get('/runtime-config', summary='可在线修改的配置项')
def get_runtime_config() -> Dict[str, Any]:
    """列出允许在网页上修改的配置项与当前值（已打码）。

    功能关闭时同样返回 200，但 ``enabled`` 为 false 并附 ``reason`` ——
    页面需要把"为什么不能改"直接展示出来，而不是只给一个灰按钮。
    """
    return {'success': True, **runtime_config.describe()}


@router.post('/runtime-config', summary='保存配置到 .env')
def save_runtime_config(payload: RuntimeConfigUpdate) -> Dict[str, Any]:
    """把改动写回 ``backend/.env`` 并热更新内存配置（无需重启）。

    两道门：功能开关 + 口令。任一不满足都返回 403，
    且不泄露"口令错"与"功能没开"之外的任何信息。
    """
    status = runtime_config.describe()
    if not status['enabled']:
        raise HTTPException(status_code=403, detail=status['reason'])
    if not runtime_config.check_token(payload.token):
        raise HTTPException(status_code=403, detail='口令不正确')

    changed, rejected = runtime_config.apply(payload.values)

    if changed:
        message = f'已写入 {len(changed)} 项，即时生效'
        if rejected:
            message += f'；{len(rejected)} 项被拒绝'
    else:
        message = '没有需要保存的改动'

    return {
        'success': True,
        'changed': changed,
        'rejected': rejected,
        'message': message,
        'config': runtime_config.describe(),
    }


# ==========================================================================
# WebSocket
# ==========================================================================

@router.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket) -> None:
    """实时推送长连接。

    **WebSocket 不走 HTTP 中间件**，所以鉴权要在这里单独做一次。
    握手请求会带上同源 cookie，因此能复用同一套会话。
    """
    from app.core.config import settings
    from app.services import auth_service

    token = websocket.cookies.get(settings.auth_cookie_name, '')
    document = None
    if token:
        try:
            document = auth_service.get_session(token)
        except Exception:
            document = None

    if document is None:
        # 先 accept 再 close：未握手就 close，浏览器只会看到 403 与
        # close code 1006，前端无法区分"没登录"和"网络断了"。
        # 走 1008（Policy Violation）前端才能据此提示"请先登录"并停止重连。
        await websocket.accept()
        await websocket.close(code=1008, reason='未登录')
        return

    if (document.get('role') or '') == 'auditor':
        # 审计员不接实时推送。长连接会广播告警事件，而审计员的可见范围
        # 只有查询留痕（HTTP 侧也只放行了 audits）—— 让他连上等于开了
        # 一条绕过 HTTP 权限的旁路。同一用 1008，前端据此停止重连。
        await websocket.accept()
        await websocket.close(code=1008, reason='当前角色不需要实时推送')
        return

    await manager.connect(websocket)
    try:
        await manager.send(websocket, 'health', {
            'status': 'connected',
            'connections': manager.count,
        })

        while True:
            text = await websocket.receive_text()
            if text.strip().lower() == 'ping':
                await manager.send(websocket, 'pong')
            else:
                await manager.send(websocket, 'echo', text)

    except WebSocketDisconnect:
        pass
    except Exception:
        # 任何异常都应安静地断开，避免污染日志
        pass
    finally:
        await manager.disconnect(websocket)
