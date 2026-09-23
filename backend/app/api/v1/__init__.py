"""API v1 路由聚合。"""
from fastapi import APIRouter

from app.api.v1 import (
    accident,
    auth,
    demo,
    detection,
    events,
    incidents,
    officers,
    public,
    reports,
    roads,
    surveillance,
    system,
    video,
)

api_router = APIRouter()
# 认证放最前。它的路由自身在 permissions 里是公开的，
# 顺序上放前面只是为了让 /docs 里的分组好看
api_router.include_router(auth.router)
api_router.include_router(system.router)
api_router.include_router(video.router)
api_router.include_router(detection.router)
api_router.include_router(accident.router)
api_router.include_router(events.router)
api_router.include_router(roads.router)
api_router.include_router(officers.router)
api_router.include_router(incidents.router)
api_router.include_router(surveillance.router)
api_router.include_router(reports.router)
api_router.include_router(public.router)
api_router.include_router(demo.router)
