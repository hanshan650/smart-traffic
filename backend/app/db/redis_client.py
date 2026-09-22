"""
Redis 访问层
============
承载三类用途：

1. **实时缓存** —— 交通快照、路段热力、排行
2. **消息队列** —— 告警推送（List，LPUSH）
3. **限流**     —— 同一路段 60 秒内不重复告警（SET NX EX）

所有 key 统一加 ``settings.redis_key_prefix`` 前缀，便于隔离与批量清理。
本层只提供**通用原语**，业务语义放在 service 层。
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import redis
from redis.exceptions import RedisError

from app.core.config import settings

_client: Optional[redis.Redis] = None

# 连接与读写超时（秒）。
# 取较小值：Redis 未启动时 Windows 上 TCP 连接会等到超时而非立即拒绝，
# 过大的值会把健康检查拖到数十秒。
_CONNECT_TIMEOUT = 1.5

# 裸 socket 预检超时（秒）
_PROBE_TIMEOUT = 1.0

# 探测结果的缓存时长（秒）
_PROBE_TTL = 10.0
_probe_cache: Dict[str, Tuple[float, bool]] = {}
_probe_lock = threading.Lock()


# ==========================================================================
# 连接
# ==========================================================================

def get_redis() -> redis.Redis:
    """返回进程内共享的 Redis 客户端（decode_responses=True）。"""
    global _client
    if _client is None:
        _client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=_CONNECT_TIMEOUT,
            socket_timeout=_CONNECT_TIMEOUT,
            # redis-py 默认会对连接类异常做指数退避重试（3 次），
            # 在 Redis 未启动时会把一次健康检查拖到 50 秒以上，故显式关闭。
            retry_on_error=[],
            retry_on_timeout=False,
        )
    return _client


def _tcp_reachable() -> bool:
    """裸 socket 快速探活。

    redis-py 在连接失败时会走内部重试与退避逻辑，实测在 Redis 未启动的
    Windows 环境下，单次 ``ping()`` 可阻塞 40 秒以上。因此先用标准库
    socket 做一次短超时探活，不通即刻返回，彻底绕开该问题。
    """
    try:
        with socket.create_connection(
            (settings.redis_host, settings.redis_port),
            timeout=_PROBE_TIMEOUT,
        ):
            return True
    except OSError:
        return False


def ping() -> bool:
    """真实连通性探测（不缓存）。"""
    if not _tcp_reachable():
        return False
    try:
        return bool(get_redis().ping())
    except (RedisError, OSError):
        return False


def ping_cached(ttl: float = _PROBE_TTL) -> bool:
    """带缓存的连通性探测，供健康检查使用。

    健康检查会被前端周期性调用（大屏每 15 秒、后台每 15 秒）；
    若每次都真实探测，Redis 不可用时会反复阻塞。缓存后，
    同一 TTL 窗口内只实际探测一次。
    """
    now = time.monotonic()

    with _probe_lock:
        cached = _probe_cache.get('ping')
        if cached is not None and now - cached[0] < ttl:
            return cached[1]

    # 网络 IO 放在锁外，避免阻塞其他线程
    result = ping()

    with _probe_lock:
        _probe_cache['ping'] = (now, result)

    return result


def close() -> None:
    global _client
    if _client is not None:
        try:
            _client.close()
        except RedisError:
            pass
        _client = None


# ==========================================================================
# Key 与 JSON 读写
# ==========================================================================

def make_key(*parts: Any) -> str:
    """拼装带统一前缀的 key，例如 ``traffic:snapshot:CAM_001``。"""
    return settings.redis_key_prefix + ':'.join(str(part) for part in parts)


def set_json(key: str, value: Any, ttl: Optional[int] = None) -> None:
    """写入 JSON，可选 TTL。Redis 不可用时静默失败（缓存非关键路径）。"""
    payload = json.dumps(value, ensure_ascii=False, default=str)
    try:
        client = get_redis()
        if ttl:
            client.setex(key, ttl, payload)
        else:
            client.set(key, payload)
    except RedisError:
        pass


def get_json(key: str) -> Optional[Any]:
    """读取 JSON，任何异常均返回 None。"""
    try:
        raw = get_redis().get(key)
    except RedisError:
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def delete(*keys: str) -> None:
    try:
        if keys:
            get_redis().delete(*keys)
    except RedisError:
        pass


# ==========================================================================
# 限流
# ==========================================================================

def acquire_once(key: str, ttl: int) -> bool:
    """``SET NX EX`` 语义的限流原语。

    :returns: True 表示窗口内首次获取（应当发出告警）；
              False 表示窗口内已存在（应当抑制）。

    Redis 不可用时**返回 True**（放行），避免因为缓存故障吞掉真实告警。
    """
    try:
        return bool(get_redis().set(key, '1', nx=True, ex=ttl))
    except RedisError:
        return True


# ==========================================================================
# 消息队列 / 排行
# ==========================================================================

def push_queue(queue: str, payload: Dict[str, Any]) -> None:
    """生产者：LPUSH 一条消息到队列。"""
    try:
        get_redis().lpush(
            make_key(queue),
            json.dumps(payload, ensure_ascii=False, default=str),
        )
    except RedisError:
        pass


def queue_len(queue: str) -> int:
    try:
        return int(get_redis().llen(make_key(queue)))
    except RedisError:
        return 0


def zadd_rank(key: str, member: str, score: float) -> None:
    """Sorted Set 写入，用于拥堵排行。"""
    try:
        get_redis().zadd(make_key(key), {member: float(score)})
    except RedisError:
        pass


def ztop(key: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Sorted Set 降序取前 N 名。"""
    try:
        rows = get_redis().zrevrange(make_key(key), 0, max(limit - 1, 0), withscores=True)
    except RedisError:
        return []
    return [{'member': member, 'score': score} for member, score in rows]


def info() -> Dict[str, Any]:
    """Redis 运行信息摘要，供健康检查展示。"""
    try:
        raw = get_redis().info()
    except RedisError:
        return {}
    return {
        'version': raw.get('redis_version'),
        'uptime_seconds': raw.get('uptime_in_seconds'),
        'used_memory_human': raw.get('used_memory_human'),
        'connected_clients': raw.get('connected_clients'),
        'db_keys': raw.get('db0', ''),
    }
