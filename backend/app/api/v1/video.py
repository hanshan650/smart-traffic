"""
视频源接口
==========
统一走 :mod:`app.services.video_source` 适配层，前端不感知具体供应商。

当前视频源为**河南高速云视频**；上海路网与地图接口见 ``roads.py``，
两者完全解耦。

注意：``playUrl`` 返回的 m3u8 地址**带时效 token（约数小时）**，
请勿缓存复用，每次播放或抓帧都应重新调用 ``/stream-url``。
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import quote, urljoin

import requests
from fastapi import APIRouter, HTTPException, Query, Response

from app.core.config import settings
from app.models.schemas import CameraListResponse, SourceListResponse, StreamUrlResponse
from app.services import video_source
from app.services.video_source import VideoSourceError

router = APIRouter(prefix='/video', tags=['视频源'])


def _service_unavailable(exc: VideoSourceError) -> HTTPException:
    """服务层异常 → 503 + 结构化 detail（前端可直接展示 hint）。"""
    return HTTPException(status_code=503, detail=exc.to_dict())


@router.get('/sources', response_model=SourceListResponse, summary='列出视频源')
def list_sources() -> SourceListResponse:
    """返回全部已注册视频源及其可用状态，供前端做源切换器。"""
    return SourceListResponse(
        current=video_source.get_provider().key,
        sources=video_source.list_sources(),
    )


@router.get('/cameras', response_model=CameraListResponse, summary='检索摄像头')
def list_cameras(
    source: Optional[str] = Query(None, description='视频源 key，缺省取配置值'),
    count: int = Query(8, ge=1, le=100, description='期望返回数量'),
    exclude: str = Query('', description='逗号分隔的已排除摄像头编号'),
) -> CameraListResponse:
    """按视野范围检索摄像头，支持排除已添加项以支持"加载更多"。"""
    excluded = [item for item in exclude.split(',') if item]

    try:
        provider = video_source.get_provider(source)
        result = provider.list_cameras(count=count, exclude=excluded)
    except VideoSourceError as exc:
        raise _service_unavailable(exc) from exc

    return CameraListResponse(
        source=provider.key,
        source_name=provider.display_name,
        cameras=result.cameras,
        total_available=result.total_available,
        total_all=result.total_all,
    )


@router.get('/stream-url', response_model=StreamUrlResponse, summary='获取直播流地址')
def get_stream_url(
    source: Optional[str] = Query(None, description='视频源 key'),
    camera_num: str = Query('', alias='cameraNum', description='摄像头编号'),
) -> StreamUrlResponse:
    """获取 m3u8 直播地址。地址带时效 token，**不可缓存**。"""
    try:
        provider = video_source.get_provider(source)
        resolved = camera_num or provider.default_camera
        stream_url = provider.get_stream_url(resolved)
    except VideoSourceError as exc:
        raise _service_unavailable(exc) from exc

    return StreamUrlResponse(
        source=provider.key,
        source_name=provider.display_name,
        camera_num=resolved,
        stream_url=stream_url,
        hls_proxy_url=(
            f'{settings.api_prefix}/video/hls/{quote(resolved, safe="")}/playlist.m3u8'
            f'?source={quote(provider.key, safe="")}'
        ),
    )


# ==========================================================================
# HLS 代理
# --------------------------------------------------------------------------
# 上游 m3u8 与 ts 分片均校验 Referer，浏览器直连会被拒（且存在跨域限制）。
# 因此由后端带正确的请求头代取，并把播放列表中的 URI 改写为本地代理地址，
# 使前端只需访问同源的 /api/v1/video/hls/... 即可播放。
# ==========================================================================

_HLS_PLAYLIST_TYPE = 'application/vnd.apple.mpegurl'
_URI_ATTR = re.compile(r'URI="([^"]+)"')


def _proxy_url(absolute: str, camera_num: str, source_key: str) -> str:
    """构造指向本代理的分片地址。"""
    return (
        f'{settings.api_prefix}/video/hls/{quote(camera_num, safe="")}/proxy'
        f'?url={quote(absolute, safe="")}&source={quote(source_key, safe="")}'
    )


def _rewrite_playlist(text: str, base_url: str, camera_num: str, source_key: str) -> str:
    """把播放列表中的全部 URI 改写为本地代理地址。

    需处理两类内容：
      · 分片/子播放列表行（普通 URI）
      · 带 URI 属性的标签，如 ``#EXT-X-KEY:METHOD=AES-128,URI="..."``
    """
    rewritten: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            rewritten.append(raw_line)
            continue

        if line.startswith('#'):
            if 'URI="' in line:
                line = _URI_ATTR.sub(
                    lambda match: (
                        f'URI="{_proxy_url(urljoin(base_url, match.group(1)), camera_num, source_key)}"'
                    ),
                    line,
                )
            rewritten.append(line)
            continue

        rewritten.append(_proxy_url(urljoin(base_url, line), camera_num, source_key))

    return '\n'.join(rewritten)


def _fetch_upstream(url: str, provider) -> requests.Response:
    """带 Provider 请求头拉取上游资源。"""
    try:
        response = requests.get(url, headers=provider.request_headers, timeout=15)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail={
                'success': False,
                'error': f'上游流请求失败：{exc}',
                'hint': '直播流地址可能已过期，请重新获取',
            },
        ) from exc


@router.get('/hls/{camera_num}/playlist.m3u8', summary='HLS 入口播放列表')
def hls_entry(
    camera_num: str,
    source: Optional[str] = Query(None, description='视频源 key'),
) -> Response:
    """获取流地址并直接返回改写后的代理播放列表。

    前端只需把 video 标签指向本地址即可播放。
    """
    try:
        provider = video_source.get_provider(source)
        upstream_url = provider.get_stream_url(camera_num)
    except VideoSourceError as exc:
        raise _service_unavailable(exc) from exc

    upstream = _fetch_upstream(upstream_url, provider)
    body = _rewrite_playlist(upstream.text, upstream_url, camera_num, provider.key)

    return Response(
        content=body,
        media_type=_HLS_PLAYLIST_TYPE,
        headers={'Cache-Control': 'no-store'},
    )


@router.get('/hls/{camera_num}/proxy', summary='HLS 分片代理')
def hls_proxy(
    camera_num: str,
    url: str = Query(..., description='上游资源地址'),
    source: Optional[str] = Query(None),
) -> Response:
    """代理上游子播放列表与媒体分片。

    播放列表会被继续改写为走本代理，媒体分片原样透传。
    """
    try:
        provider = video_source.get_provider(source)
    except VideoSourceError as exc:
        raise _service_unavailable(exc) from exc

    upstream = _fetch_upstream(url, provider)
    content_type = upstream.headers.get('Content-Type', '')

    is_playlist = (
        'mpegurl' in content_type.lower()
        or url.split('?')[0].lower().endswith('.m3u8')
    )

    if is_playlist:
        return Response(
            content=_rewrite_playlist(upstream.text, url, camera_num, provider.key),
            media_type=_HLS_PLAYLIST_TYPE,
            headers={'Cache-Control': 'no-store'},
        )

    return Response(
        content=upstream.content,
        media_type=content_type or 'application/octet-stream',
        headers={'Cache-Control': 'no-store'},
    )
