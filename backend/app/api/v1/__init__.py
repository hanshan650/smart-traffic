"""API v1 路由聚合。"""
from fastapi import APIRouter

from app.api.v1 import (
    accident,
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
