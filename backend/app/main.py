"""
FastAPI 应用入口
================
启动（在 ``backend/`` 目录下）::

    .venv\\Scripts\\activate
    uvicorn app.main:app --reload --port 8000

接口文档：http://127.0.0.1:8000/docs

启动时会自动完成：
  · 创建上传目录
  · 绑定事件循环（供线程池中的检测任务向 WebSocket 广播）
  · 探测 MongoDB / Redis / YOLO / 视频源，并打印降级提示
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.core.config import settings
from app.db import mongo, redis_client
from app.realtime.ws_manager import bind_loop
from app.services import detector, video_source

# 静态目录需在挂载前存在，故在模块导入阶段创建
_STATIC_DIR = settings.upload_path.parent
_STATIC_DIR.mkdir(parents=True, exist_ok=True)
settings.upload_path.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动探测 + 关闭清理。"""
    bind_loop(asyncio.get_running_loop())

    line = '=' * 64
    print(line)
    print(f'  {settings.app_title}')
    print(line)

    # ---------------- 数据库 ----------------
    if mongo.ping():
        try:
            mongo.ensure_indexes()
            print('[OK]    MongoDB 已连接，索引就绪')
        except Exception as exc:
            print(f'[WARN]  MongoDB 索引创建失败：{exc}')
    else:
        print('[WARN]  MongoDB 未连接 —— 检测记录与事件无法持久化')

    if redis_client.ping():
        print('[OK]    Redis 已连接')
    else:
        print('[WARN]  Redis 未连接 —— 实时缓存与告警限流降级运行')

    # ---------------- 推理模型 ----------------
    yolo_ok, yolo_reason = detector.is_available()
    if yolo_ok:
        print(f'[OK]    YOLO 模型就绪：{settings.yolo_model_path}')
    else:
        print(f'[WARN]  检测功能不可用：{yolo_reason}')

    # ---------------- 视频源 ----------------
    source = video_source.current_source()
    flag = 'OK  ' if source.available else 'WARN'
    print(f'[{flag}]  视频源：{source.display_name}（{source.key}）')
    if not source.available:
        print(f'        原因：{source.reason}')
    for other in video_source.list_sources():
        if other.key != source.key:
            mark = '可用' if other.available else '仅接入位'
            print(f'        备选源 {other.key}（{other.display_name}）：{mark}')

    print(f'[INFO]  上传目录：{settings.upload_path}')
    print(f'[INFO]  上海路网接口已就绪（与视频源解耦）')
    print(line + '\n')

    yield

    # ---------------- 关闭 ----------------
    mongo.close()
    redis_client.close()
    print('[INFO]  已释放数据库连接')


app = FastAPI(
    title=settings.app_title,
    version='1.0.0',
    description=(
        '基于 YOLOv8 的城市道路监控与交通事件预警平台。\n\n'
        '· 实时视频源：河南高速云视频（上海暂无公开视频接口，接口已保留）\n'
        '· 路网与地图：上海市（内环 / 中环 / 延安高架 / 南北高架 / 沪闵高架）'
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(api_router, prefix=settings.api_prefix)

# 静态文件：检测快照等
app.mount('/static', StaticFiles(directory=str(_STATIC_DIR)), name='static')


# ==========================================================================
# 根路径与错误处理
# ==========================================================================

@app.get('/', include_in_schema=False)
def root() -> Dict[str, Any]:
    return {
        'success': True,
        'app': settings.app_name,
        'title': settings.app_title,
        'version': '1.0.0',
        'docs': '/docs',
        'apiPrefix': settings.api_prefix,
        'videoSource': settings.video_source,
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """兜底异常处理，保证前端始终收到结构化 JSON。"""
    return JSONResponse(
        status_code=500,
        content={
            'success': False,
            'error': str(exc) or exc.__class__.__name__,
            'hint': '服务内部错误，请查看后端控制台日志',
        },
    )
