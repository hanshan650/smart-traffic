"""
上游视频源连通性诊断
====================
分层定位 ``/camera/search`` 与 ``/camera/playUrl`` 失败的真实原因。

为什么要分层
------------
``video_source._request`` 把 ``requests.RequestException`` 统一转成
"河南高速云平台请求失败"，而该异常类同时覆盖 **HTTP 4xx/5xx** 与
**网络层异常**（DNS / TCP / TLS / 超时）。从这条错误信息无法判断
究竟卡在哪一层，因此逐层探测：

  1. DNS 解析
  2. TCP 连通性
  3. TLS 握手与证书有效期
  4. HTTP 状态码与响应体（并对比「带 Referer」与「不带」的差异）

第 4 步的头部对比很关键：上游若启用了风控，通常会针对缺少
Referer/UA 的请求返回 4xx（如 418），而有头请求正常。

用法（在 backend 目录下）::

    .venv\\Scripts\\python.exe scripts\\diagnose_upstream.py
"""
from __future__ import annotations

import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import requests  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.video_source import get_provider  # noqa: E402

BASE = settings.hngs_api_base
PARSED = urlparse(BASE)
HOST = PARSED.hostname or ''
PORT = PARSED.port or (443 if PARSED.scheme == 'https' else 80)

SEARCH_PARAMS = {
    'zoomLevel': settings.hngs_viewport_zoom,
    'sbMapLevel': settings.hngs_viewport_zoom,
    'northEast': settings.hngs_viewport_ne,
    'southWest': settings.hngs_viewport_sw,
}

BAR = '=' * 76


def step_dns() -> None:
    print('[1] DNS 解析')
    try:
        infos = socket.getaddrinfo(HOST, PORT, proto=socket.IPPROTO_TCP)
        for addr in sorted({item[4][0] for item in infos}):
            print(f'    → {addr}')
        names = socket.gethostbyname_ex(HOST)[0]
        print(f'    规范名: {names}')
    except Exception as exc:  # noqa: BLE001 — 诊断脚本需展示全部失败原因
        print(f'    ✗ 失败: {type(exc).__name__}: {exc}')


def step_tcp() -> None:
    print('[2] TCP 连通性')
    try:
        started = time.perf_counter()
        with socket.create_connection((HOST, PORT), timeout=8) as sock:
            elapsed = (time.perf_counter() - started) * 1000
            print(f'    ✓ 连接成功 {elapsed:.0f}ms  对端={sock.getpeername()}')
    except Exception as exc:  # noqa: BLE001
        print(f'    ✗ 失败: {type(exc).__name__}: {exc}')


def step_tls() -> None:
    print('[3] TLS 握手')
    try:
        context = ssl.create_default_context()
        with socket.create_connection((HOST, PORT), timeout=8) as sock:
            with context.wrap_socket(sock, server_hostname=HOST) as tls:
                cert = tls.getpeercert() or {}
                subject = dict(item[0] for item in cert.get('subject', []))
                print(f'    ✓ 握手成功 协议={tls.version()}')
                print(f'    证书 CN={subject.get("commonName", "?")}')
                print(f'    有效期至 {cert.get("notAfter", "?")}')
    except Exception as exc:  # noqa: BLE001
        print(f'    ✗ 失败: {type(exc).__name__}: {exc}')


def probe(path: str, label: str, headers: dict, params: dict | None) -> None:
    url = BASE + path
    try:
        started = time.perf_counter()
        response = requests.get(url, headers=headers, params=params, timeout=12)
        elapsed = (time.perf_counter() - started) * 1000
        print(
            f'    {path:<16}{label:<18}HTTP {response.status_code}  '
            f'{elapsed:.0f}ms  {len(response.content)}B'
        )
        body = response.text[:220].replace('\n', ' ')
        print(f'        响应体: {body}')
    except Exception as exc:  # noqa: BLE001
        print(f'    {path:<16}{label:<18}✗ {type(exc).__name__}: {str(exc)[:110]}')


def step_http() -> None:
    print('[4] HTTP 请求（对比请求头的影响）')
    provider = get_provider()

    for path, params in (('/', None), ('/camera/search', SEARCH_PARAMS)):
        probe(path, '带 Referer+UA', provider.request_headers, params)
        probe(path, '无自定义头', {}, params)
        print()


def main() -> int:
    print(BAR)
    print(f'  上游诊断  目标 = {BASE}')
    print(f'  主机={HOST}  端口={PORT}')
    print(BAR)
    print()

    step_dns()
    print()
    step_tcp()
    print()
    step_tls()
    print()
    step_http()

    print(BAR)
    print('  判读指引')
    print('    · 仅「无自定义头」失败 → 风控拦截，需保留 Referer/UA')
    print('    · 两种请求头都失败且为 4xx → 接口变更或 IP 被限流')
    print('    · 两种情况均超时/连接失败 → 网络层问题')
    print('    · 返回非 JSON（如 HTML 登录页）→ 接口需鉴权或已改版')
    print(BAR)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
