"""
警务端认证与授权
================

Cookie 会话认证。选它而不是 JWT 的原因很具体：**监控画面保护不了**。
HLS 的 m3u8 与分片是 ``<video>`` 直接拉的、快照是 ``<img>`` 拉的 ——
这两类请求带不了 ``Authorization`` 头。JWT 方案下它们只能不保护，
或把 token 塞进 query（那会泄进访问日志与浏览器历史）。
Cookie 由浏览器自动携带，这些接口才能真正关上。

--------------------------------------------------------------------------
四个实现取舍
--------------------------------------------------------------------------
1. **会话存 MongoDB，不存内存也不存 Redis**。项目里 Redis 是可选的
   （未启动时降级运行），把登录态挂在"可能不在"的依赖上，会导致
   服务重启或 Redis 掉线时所有人被登出。内存方案则连重启都扛不住。
2. **库里存 token 的 SHA-256，不存明文**。这样即使数据库被读走，
   也无法直接拿去冒用。查询时对 cookie 值做同样的哈希再比对。
3. **密码用标准库 `pbkdf2_hmac`**，不引第三方密码库。迭代次数写进
   哈希串本身（``pbkdf2_sha256$迭代数$盐$哈希``），以后调高迭代数时
   旧密码仍可校验 —— 否则只能让所有人改密码。
4. **权限判定用"默认拒绝"**：只有显式列进白名单的路径才放行，
   其余一律要求登录。这样新加接口时不会因为忘记挂依赖而裸奔。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.db import mongo

SESSION_COLLECTION = 'auth_sessions'

# pbkdf2 迭代次数。写进哈希串，将来调高不影响旧密码校验
_PBKDF2_ROUNDS = 260_000
_SALT_BYTES = 16
_TOKEN_BYTES = 32

# 口令最短长度。太短的直接拒绝，避免 seed 出一个 "123" 这种东西
MIN_PASSWORD_LENGTH = 8


class Role(str, Enum):
    """四档角色。与 `officers.rank`（警衔）是两回事：

    警衔是职务等级，不代表系统权限 —— 一级警司未必能改系统配置。
    """

    ADMIN = 'admin'
    DISPATCHER = 'dispatcher'
    VIEWER = 'viewer'
    AUDITOR = 'auditor'


ROLE_LABELS: Dict[str, str] = {
    Role.ADMIN.value: '管理员',
    Role.DISPATCHER.value: '值班员',
    Role.VIEWER.value: '只读',
    Role.AUDITOR.value: '审计',
}

ROLE_HINTS: Dict[str, str] = {
    Role.ADMIN.value: '全部权限，含警员管理与系统配置',
    Role.DISPATCHER.value: '日常业务：派警、事件复核、违停处置、上报复核',
    Role.VIEWER.value: '只读：大屏、地图、监控、统计',
    Role.AUDITOR.value: '只读，且只能查阅审计记录',
}


class AuthError(RuntimeError):
    """认证/授权失败。调用方据 message 决定给 401 还是 403"""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


# ==========================================================================
# 密码
# ==========================================================================

def hash_password(password: str) -> str:
    """``pbkdf2_sha256$迭代数$盐$哈希``（盐与哈希均为 hex）"""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f'口令至少 {MIN_PASSWORD_LENGTH} 位', status_code=400)
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, _PBKDF2_ROUNDS)
    return f'pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比对。任何格式异常都当作校验失败，不抛异常 ——
    否则不同错误对应不同响应，等于给出"这个账号存在且格式对"的旁路信息。"""
    try:
        algorithm, rounds, salt_hex, digest_hex = stored.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        digest = hashlib.pbkdf2_hmac(
            'sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError, TypeError):
        return False


# ==========================================================================
# 会话
# ==========================================================================

def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _collection():
    db = mongo.get_db()
    if db is None:
        raise AuthError('数据库不可用，无法完成登录', status_code=503)
    return db[SESSION_COLLECTION]


def ensure_indexes() -> None:
    """TTL 索引让过期会话自动清理，不必写定时任务。

    `expireAfterSeconds=0` 表示"到达 `expires_at` 即删除"。
    """
    db = mongo.get_db()
    if db is None:
        return
    try:
        db[SESSION_COLLECTION].create_index('expires_at', expireAfterSeconds=0)
        db[SESSION_COLLECTION].create_index('officer_id')
    except Exception:
        # 建索引失败不该阻断启动 —— 会话过期由查询时的时间校验兜底
        pass


def create_session(officer_id: str, role: str, name: str = '', unit: str = '') -> str:
    """新建会话，返回**明文 token**（只在这一刻存在，库中存的是它的哈希）"""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    ttl_hours = max(1, int(settings.auth_session_hours))
    now = datetime.utcnow()
    _collection().insert_one(
        {
            'token_hash': _token_digest(token),
            'officer_id': officer_id,
            'role': role,
            'name': name,
            'unit': unit,
            'created_at': now,
            'expires_at': now + timedelta(hours=ttl_hours),
            'last_seen_at': now,
        }
    )
    return token


def get_session(token: str) -> Optional[Dict[str, Any]]:
    """按 cookie 值取会话。过期的一并清掉 —— TTL 索引是分钟级延迟的，
    不能只靠它。"""
    if not token:
        return None
    document = _collection().find_one({'token_hash': _token_digest(token)})
    if not document:
        return None
    if document.get('expires_at') and document['expires_at'] < datetime.utcnow():
        _collection().delete_one({'_id': document['_id']})
        return None
    return document


def touch_session(token: str) -> None:
    """记录最近活跃时间。单独写、不阻塞请求 —— 失败也不影响本次响应"""
    try:
        _collection().update_one(
            {'token_hash': _token_digest(token)},
            {'$set': {'last_seen_at': datetime.utcnow()}},
        )
    except Exception:
        pass


def revoke_session(token: str) -> None:
    if not token:
        return
    try:
        _collection().delete_one({'token_hash': _token_digest(token)})
    except Exception:
        pass


def revoke_all(officer_id: str) -> int:
    """踢掉某人的全部会话。改密码后必须调用 —— 否则旧会话仍然有效，
    而"改了密码却没把别人踢下去"是最容易被忽略的漏洞。"""
    result = _collection().delete_many({'officer_id': officer_id})
    return int(result.deleted_count)


def active_sessions(officer_id: str) -> int:
    return int(_collection().count_documents({'officer_id': officer_id}))


# ==========================================================================
# 当前用户
# ==========================================================================

class CurrentUser:
    """已登录用户。`role` 用于权限判定，其余字段仅供界面显示。"""

    def __init__(self, document: Dict[str, Any]) -> None:
        self.officer_id: str = document.get('officer_id', '')
        self.name: str = document.get('name', '')
        self.unit: str = document.get('unit', '')
        self.role: str = document.get('role') or Role.VIEWER.value
        self.expires_at = document.get('expires_at')

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            'officerId': self.officer_id,
            'name': self.name,
            'unit': self.unit,
            'role': self.role,
            'roleLabel': self.role_label,
            'roleHint': ROLE_HINTS.get(self.role, ''),
            'expiresAt': self.expires_at.isoformat() if self.expires_at else None,
        }


def describe_roles() -> List[Dict[str, str]]:
    return [
        {'value': role.value, 'label': ROLE_LABELS[role.value], 'hint': ROLE_HINTS[role.value]}
        for role in Role
    ]


# ==========================================================================
# 登录
# ==========================================================================

# 连续失败多少次后锁定。5 次是个常用值：正常人打错不会到 5 次，
# 而暴力破解会被拖得很慢
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15

# 登录失败的**统一**提示。不能区分"警号不存在"与"口令错误" ——
# 区分开就等于提供了一个枚举有效警号的接口
_LOGIN_FAILED = '警号或口令不正确'


def authenticate(officer_id: str, password: str) -> Dict[str, Any]:
    """校验凭据。成功返回账号信息，失败抛 `AuthError`。

    失败一律只给同一句话，不泄露"这个警号存不存在"。
    """
    from app.services import officer_service

    officer_id = (officer_id or '').strip()
    if not officer_id or not password:
        raise AuthError(_LOGIN_FAILED)

    try:
        credential = officer_service.get_credential(officer_id)
    except Exception as exc:  # 数据库不可用等
        raise AuthError(f'登录失败：{exc}', status_code=503) from exc

    if not credential or not credential.get('password_hash'):
        # 账号不存在，或不带密码。两种情况对调用方是同一件事
        raise AuthError(_LOGIN_FAILED)

    locked_until = credential.get('locked_until')
    if locked_until and isinstance(locked_until, datetime) and locked_until > datetime.utcnow():
        minutes = int((locked_until - datetime.utcnow()).total_seconds() // 60) + 1
        raise AuthError(f'失败次数过多，请 {minutes} 分钟后再试', status_code=429)

    if not verify_password(password, credential['password_hash']):
        _record_failure(officer_service, officer_id, credential)
        raise AuthError(_LOGIN_FAILED)

    officer_service.update_auth_state(
        officer_id,
        {'failed_attempts': 0, 'locked_until': None, 'last_login_at': datetime.utcnow()},
    )
    return credential


def _record_failure(officer_service, officer_id: str, credential: Dict[str, Any]) -> None:
    """累计失败次数，达到阈值就锁定一段时间。"""
    failed = int(credential.get('failed_attempts') or 0) + 1
    fields: Dict[str, Any] = {'failed_attempts': failed}
    if failed >= MAX_FAILED_ATTEMPTS:
        fields['locked_until'] = datetime.utcnow() + timedelta(minutes=LOCK_MINUTES)
        # 锁定时把计数归零，解锁后重新计数。
        # 否则一次锁定后只要再错一次就永久锁死
        fields['failed_attempts'] = 0
    try:
        officer_service.update_auth_state(officer_id, fields)
    except Exception:
        # 计数写不进去也不该把登录响应变成 500
        pass


def change_password(officer_id: str, old_password: str, new_password: str) -> int:
    """修改口令，返回被踢掉的会话数。

    **改完必须清掉该账号的全部会话** —— 包括当前这个。
    否则"改了密码但旧会话仍然有效"，等于没改。
    调用方需要重新登录，接口会同时下发新的 cookie。
    """
    from app.services import officer_service

    try:
        credential = officer_service.get_credential(officer_id)
    except Exception as exc:
        raise AuthError(f'修改口令失败：{exc}', status_code=503) from exc

    if not credential or not credential.get('password_hash'):
        raise AuthError('账号不存在', status_code=404)

    if not verify_password(old_password, credential['password_hash']):
        raise AuthError('原口令不正确', status_code=400)

    if verify_password(new_password, credential['password_hash']):
        raise AuthError('新口令不能与原口令相同', status_code=400)

    officer_service.set_password_hash(officer_id, hash_password(new_password))
    # 顺手解锁：改完口令还被上一次的锁定挡在外面，是最容易忽略的一种"改完没用"
    officer_service.update_auth_state(
        officer_id, {'failed_attempts': 0, 'locked_until': None}
    )
    return revoke_all(officer_id)
