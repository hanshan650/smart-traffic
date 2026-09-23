"""
请求级认证
==========

两件事：

1. ``get_current_user`` —— 供接口声明的依赖，拿到当前登录用户
2. ``AuthMiddleware`` —— 全局拦截，**默认拒绝**未登录请求

中间件放在这里而不是 `main.py`：它需要同时用 `permissions` 与 `auth_service`，
写在 main 里会让 main 牵扯进权限细节。

--------------------------------------------------------------------------
两个容易写错的地方
--------------------------------------------------------------------------
* **OPTIONS 必须放行**。跨域预检本身不带 cookie，拦掉它会让所有跨域请求
  在浏览器侧直接失败，而错误信息只会说"CORS 失败"，很难定位到这里。
* **中间件执行顺序**。`add_middleware` 是压栈，**后加的在外层、先执行**。
  所以 CORS 要加在认证**之后**，让预检先被 CORS 处理掉。见 `main.py` 里的
  注释，那里顺序是刻意排的。
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core import permissions
from app.core.config import settings
from app.services import auth_service
from app.services.auth_service import CurrentUser


def get_current_user(request: Request) -> CurrentUser:
    """当前登录用户。中间件已保证到这里一定是已登录状态；
    这个依赖是给**需要知道"是谁"**的接口用的（比如审计留痕）。"""
    user = getattr(request.state, 'user', None)
    if user is None:
        # 正常不会走到：中间件会先拦掉。留个明确的错误而不是 None 泄露到下游
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail='未登录')
    return user


def optional_user(request: Request) -> Optional[CurrentUser]:
    """可能为空的用户。用于公开接口里"登录了就多给一点信息"的场景。"""
    return getattr(request.state, 'user', None)


def _unauthorized(detail: str) -> JSONResponse:
    response = JSONResponse(status_code=401, content={'success': False, 'error': detail})
    # 明确告诉客户端"这次确实没登录"，而不是"没权限" ——
    # 前端据此决定跳登录页还是只弹个提示
    response.headers['X-Auth-Status'] = 'anonymous'
    return response


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # 预检放行：它不带 cookie，拦掉等于让所有跨域请求在浏览器侧失败
        if method == 'OPTIONS':
            return await call_next(request)

        if permissions.is_public(path):
            # 公开接口也可能带 cookie（民众端与警务端同域）。带上就顺手解析，
            # 让公开接口有机会知道"是谁在访问"，但不强制
            request.state.user = _try_load(request)
            return await call_next(request)

        token = request.cookies.get(settings.auth_cookie_name, '')
        if not token:
            return _unauthorized('请先登录')

        try:
            document = auth_service.get_session(token)
        except auth_service.AuthError as exc:
            return _unauthorized(str(exc))

        if document is None:
            return _unauthorized('登录已过期，请重新登录')

        user = CurrentUser(document)
        request.state.user = user
        request.state.session_token = token

        try:
            permissions.check(user, method, path)
        except permissions.PermissionError_ as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={'success': False, 'error': str(exc)},
            )

        # 记住最近活跃时间。不阻塞主流程，失败也不影响本次请求
        auth_service.touch_session(token)

        return await call_next(request)


def _try_load(request: Request) -> Optional[CurrentUser]:
    token = request.cookies.get(settings.auth_cookie_name, '')
    if not token:
        return None
    try:
        document = auth_service.get_session(token)
    except auth_service.AuthError:
        return None
    return CurrentUser(document) if document else None


# ==========================================================================
# Cookie 读写
# ==========================================================================

def set_session_cookie(response, token: str) -> None:
    """写入会话 Cookie。

    * ``httponly`` —— JS 读不到，降低 XSS 拿到会话的风险
    * ``samesite=lax`` —— 前端经 Vite 代理访问后端，浏览器视为同源，
      lax 足够；用 none 反而必须配 secure，本地 http 下会直接失效
    * ``secure`` 由配置控制 —— 本地 http 调试必须为 false，
      否则浏览器**根本不会发送**这个 cookie，表现为"登录成功却仍是未登录"
    """
    ttl_seconds = max(1, int(settings.auth_session_hours)) * 3600
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=ttl_seconds,
        httponly=True,
        samesite='lax',
        secure=bool(settings.auth_cookie_secure),
        path='/',
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name,
        httponly=True,
        samesite='lax',
        secure=bool(settings.auth_cookie_secure),
        path='/',
    )
