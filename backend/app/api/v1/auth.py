"""
认证接口
========

登录 / 登出 / 查看当前身份 / 修改口令。

路径前缀 ``/auth``。其中 ``/auth/login`` 与 ``/auth/roles`` 是**公开**的
（登录页要显示角色说明），其余都要求已登录 —— 公开名单见
``app/core/permissions.py``。
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response

from app.core.config import settings
from app.core.security import clear_session_cookie, get_current_user, set_session_cookie
from app.models.schemas import CamelModel
from app.services import auth_service
from app.services.auth_service import CurrentUser

router = APIRouter(prefix='/auth', tags=['认证'])


class LoginPayload(CamelModel):
    """用 camelCase 接收，与前端一致（`officerId` 而不是 `officer_id`）"""

    officer_id: str = ''
    password: str = ''


class PasswordPayload(CamelModel):
    old_password: str = ''
    new_password: str = ''


@router.get('/roles', summary='角色说明')
def list_roles() -> Dict[str, Any]:
    """公开。登录页需要在还没登录时就告诉用户"你能干什么"。"""
    return {'success': True, 'items': auth_service.describe_roles()}


@router.post('/login', summary='登录')
def login(payload: LoginPayload, response: Response) -> Dict[str, Any]:
    """用**警号 + 口令**登录，成功后在 HttpOnly Cookie 里下发会话。

    会话值不出现在响应体里 —— 它只走 cookie，JS 读不到，
    降低 XSS 直接拿走会话的风险。

    失败提示统一为"警号或口令不正确"：区分"账号不存在"与"口令错误"
    等于提供一个枚举有效警号的接口。
    """
    try:
        credential = auth_service.authenticate(payload.officer_id, payload.password)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    officer_id = credential.get('officer_id', '')
    role = credential.get('role') or auth_service.Role.VIEWER.value

    token = auth_service.create_session(
        officer_id=officer_id,
        role=role,
        name=credential.get('name', ''),
        unit=credential.get('unit', ''),
    )
    set_session_cookie(response, token)

    user = CurrentUser(
        {
            'officer_id': officer_id,
            'role': role,
            'name': credential.get('name', ''),
            'unit': credential.get('unit', ''),
        }
    )
    return {'success': True, 'message': '登录成功', 'user': user.to_dict()}


@router.post('/logout', summary='退出登录')
def logout(request: Request, response: Response) -> Dict[str, Any]:
    """删掉服务端会话，并清掉 cookie。

    两件事都要做：只清 cookie 的话，服务端会话仍然有效，
    别人拿到那个值还能用。
    """
    token = request.cookies.get(settings.auth_cookie_name, '')
    auth_service.revoke_session(token)
    clear_session_cookie(response)
    return {'success': True, 'message': '已退出登录'}


@router.get('/me', summary='当前登录身份')
def me(request: Request) -> Dict[str, Any]:
    """前端启动时用它判断"要不要跳登录页"，同时拿到角色以控制界面。"""
    user = get_current_user(request)
    return {'success': True, 'user': user.to_dict()}


@router.post('/password', summary='修改口令')
def change_password(
    payload: PasswordPayload, request: Request, response: Response
) -> Dict[str, Any]:
    """改口令，并**踢掉该账号的全部会话**（含当前这个）。

    改完当前会话也就失效了，所以这里顺手重新建一个 —— 否则用户会被
    自己的改密操作顶出去，体验上像是"改密码把账号弄坏了"。
    """
    user = get_current_user(request)

    try:
        revoked = auth_service.change_password(
            user.officer_id, payload.old_password, payload.new_password
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    token = auth_service.create_session(
        officer_id=user.officer_id, role=user.role, name=user.name, unit=user.unit
    )
    set_session_cookie(response, token)

    return {
        'success': True,
        'message': f'口令已修改，已退出其他 {max(0, revoked - 1)} 个会话',
        'revoked': revoked,
        'user': user.to_dict(),
    }
