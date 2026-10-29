"""HLS 代理安全与响应边界离线验证。"""
from __future__ import annotations

import socket
import sys
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import requests  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.api.v1 import video  # noqa: E402


class Provider:
    options = {
        'api_base': 'https://weixin.hngscloud.com',
        'referer': 'https://weixin.hngscloud.com/',
    }
    request_headers = {'Referer': 'https://weixin.hngscloud.com/'}


class FakeResponse:
    def __init__(
        self,
        url: str = 'https://media.hngscloud.com/live.m3u8',
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.url = url
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks or []
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f'HTTP {self.status_code}')

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield from self.chunks

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]


def expect_http_error(name: str, callback, status: int) -> None:
    try:
        callback()
    except HTTPException as exc:
        assert exc.status_code == status, (name, exc.status_code, exc.detail)
        print(f'PASS  {name}')
        return
    raise AssertionError(f'{name}: 未拒绝危险输入')


def main() -> int:
    provider = Provider()
    with (
        patch.object(
            video.settings, 'hls_allowed_hosts', 'hngscloud.com,livehwc4.com'
        ),
        patch.object(video.socket, 'getaddrinfo', side_effect=public_dns),
    ):
        video._validate_upstream_url('https://media.hngscloud.com/live.m3u8', provider)
        print('PASS  白名单子域名可访问')
        video._validate_upstream_url(
            'https://8cfc2af6a1d27ed5c668d9651c63e8c7.livehwc4.com/playlist.m3u8',
            provider,
        )
        print('PASS  HLS CDN 子播放列表可访问')
        expect_http_error(
            '非 HTTP 协议被拒绝',
            lambda: video._validate_upstream_url('file:///etc/passwd', provider),
            400,
        )
        expect_http_error(
            '白名单外主机被拒绝',
            lambda: video._validate_upstream_url('https://example.com/live.m3u8', provider),
            403,
        )
        expect_http_error(
            'URL 凭据被拒绝',
            lambda: video._validate_upstream_url(
                'https://user:secret@media.hngscloud.com/live.m3u8', provider
            ),
            403,
        )
        with patch.object(video.socket, 'getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))
        ]):
            expect_http_error(
                '白名单域名解析到内网时被拒绝',
                lambda: video._validate_upstream_url(
                    'https://media.hngscloud.com/live.m3u8', provider
                ),
                403,
            )

        response = FakeResponse(chunks=[b'ab', b'cd'])
        assert video._read_upstream(response, 4) == b'abcd'
        print('PASS  响应边界内正常读取')
        expect_http_error(
            'Content-Length 超限被拒绝',
            lambda: video._read_upstream(
                FakeResponse(headers={'Content-Length': '5'}, chunks=[b'abcde']), 4
            ),
            502,
        )
        expect_http_error(
            '流式读取累计超限被拒绝',
            lambda: video._read_upstream(FakeResponse(chunks=[b'abc', b'de']), 4),
            502,
        )
        assert video._decode_playlist(b'\xef\xbb\xbf#EXTM3U\n').startswith('#EXTM3U')
        expect_http_error(
            '伪播放列表被拒绝', lambda: video._decode_playlist(b'<html></html>'), 502
        )

        redirect = FakeResponse(status=302, headers={'Location': '/index.m3u8'})
        final = FakeResponse('https://media.hngscloud.com/index.m3u8')
        with patch.object(video.requests, 'get', side_effect=[redirect, final]) as mocked_get:
            assert video._fetch_upstream('https://media.hngscloud.com/live.m3u8', provider) is final
            assert redirect.closed
            assert mocked_get.call_count == 2
            assert mocked_get.call_args.kwargs['allow_redirects'] is False
            assert mocked_get.call_args.kwargs['stream'] is True
        print('PASS  同源重定向逐跳校验且关闭自动跳转')

        private_redirect = FakeResponse(
            status=302, headers={'Location': 'http://127.0.0.1/internal'}
        )
        with patch.object(video.requests, 'get', return_value=private_redirect) as mocked_get:
            expect_http_error(
                '重定向到内网被拒绝',
                lambda: video._fetch_upstream('https://media.hngscloud.com/live.m3u8', provider),
                403,
            )
            assert mocked_get.call_count == 1
            assert private_redirect.closed

    print('\nHLS 代理离线验证全部通过')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'FAIL  {type(exc).__name__}: {exc}')
        raise SystemExit(1) from exc
