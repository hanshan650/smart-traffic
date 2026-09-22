"""
HLS 分片分辨率诊断
==================
对比「浏览器播放的分辨率」与「我们下载分片解码得到的分辨率」。

背景：放大查看时画面角落显示 ``1920*1080``，说明上游原始流是全高清，
但抓帧路径解出的帧只有 352x288 —— 若能修正，小目标检测率会大幅提升
（352x288 下滑处车辆仅几十像素，是漏检的主因）。

用法（在 backend 目录下）::

    .venv\\Scripts\\python.exe scripts\\probe_resolution.py [摄像头数量]
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.video_source import (  # noqa: E402
    _download,
    _fetch_text,
    _playlist_uris,
    _resolve_media_playlist,
    get_provider,
)


def inspect(camera_num: str, headers: dict) -> None:
    provider = get_provider()
    master_url = provider.get_stream_url(camera_num)

    print('=' * 78)
    print(f'摄像头 {camera_num}')

    master = _fetch_text(master_url, headers)
    if not master:
        print('  主播放列表拉取失败')
        return

    print(f'  主播放列表变体数：{master.count("#EXT-X-STREAM-INF")}')
    for line in master.splitlines():
        if line.startswith('#EXT-X-STREAM-INF'):
            print(f'    {line}')

    resolved = _resolve_media_playlist(master_url, headers)
    if resolved is None:
        print('  未能解析到媒体播放列表')
        return

    media_url, segments = resolved
    print(f'  媒体播放列表：{media_url[:100]}…')
    print(f'  分片数：{len(segments)}')

    media_text = _fetch_text(media_url, headers) or ''
    for line in media_text.splitlines()[:8]:
        print(f'    {line[:100]}')

    # 逐个分片试解码，找出真实分辨率
    try:
        import cv2
    except ImportError:
        print('  cv2 不可用')
        return

    for index, segment in enumerate(reversed(segments[:3])):
        fd, tmp = tempfile.mkstemp(suffix='.ts')
        os.close(fd)
        try:
            if not _download(segment, headers, tmp):
                print(f'  分片[-{index + 1}] 下载失败')
                continue

            size_kb = os.path.getsize(tmp) // 1024
            capture = cv2.VideoCapture(tmp)
            try:
                width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = capture.get(cv2.CAP_PROP_FPS)
                frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
                fourcc = int(capture.get(cv2.CAP_PROP_FOURCC))
                codec = ''.join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4))
                print(
                    f'  分片[-{index + 1}] {size_kb:>5} KB  '
                    f'{width}x{height}  fps={fps:.1f}  frames={frames}  codec={codec!r}'
                )
            finally:
                capture.release()
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    provider = get_provider()
    headers = provider.request_headers

    for camera in provider.list_cameras(count=count).cameras:
        try:
            inspect(camera.camera_num, headers)
        except Exception as exc:  # noqa: BLE001 — 诊断脚本需容错
            print(f'  异常：{exc}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
