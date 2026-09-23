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

# 探活失败后的冷却窗口（秒）。窗口内不再重复探活 ——
# 探活本身要 2 秒，每个请求都付一次比“直接用不了 Redis”还糟。
_OFFLINE_COOLDOWN = 30.0
_offline_until = 0.0


# ==========================================================================
# 连接
# ==========================================================================

def get_redis() -> redis.Redis:
    """返回进程内共享的 Redis 客户端（decode_responses=True）。

    **Redis 不可用时抛 :class:`RedisUnavailable`，而不是返回一个
    会在使用时阻塞 36 秒的 client。**

    这一点很关键：把预检放在这里而不是每个调用点，是为了让
    “先探活再使用”成为默认行为 —— 原先只在 ``ping()`` 里做了预检，
    其他原语直接调 redis-py，结果单次调用阻塞 36～37 秒
    （详见 :func:`_available` 的说明）。

    抛出而非返回 None，是为了不改变调用方已有的 `except RedisError`
    降级写法 —— ``RedisUnavailable`` 是 ``RedisError`` 的子类。
    """
    global _client

    if not _available():
        raise RedisUnavailable(
            f'Redis 不可用（{settings.redis_host}:{settings.redis_port}），已跳过'
        )

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


class RedisUnavailable(RedisError):
    """Redis 不可用（预检未通过）。

    继承 ``RedisError`` 是刻意的：所有调用点本来就用
    ``except RedisError`` 做优雅降级，这样不必逐个改捕获列表，
    已有的降级行为原样保留。
    """


def _available() -> bool:
    """统一的可用性预检，**所有原语在触碰 redis-py 之前都必须先过这里**。

    为什么不能只靠 socket_connect_timeout
    -------------------------------------
    实测（Redis 未启动，Windows）：

      ``acquire_once`` / ``set_json`` / ``get_json``  各阻塞 **36～37 秒**

    而 client 上明明配了 ``socket_connect_timeout=1.5`` 与
    ``retry_on_error=[]``。原因是 redis-py 对 ``localhost`` 会先试
    IPv6 再回退 IPv4，加上内部的连接尝试逻辑，超时参数兜不住这个组合。

    原来的写法只在 ``ping()`` 里做预检，其余原语直接调 redis-py ——
    于是"处处优雅降级"的设计仍在 `/reports/{no}/review` 上卡了 30 秒
    （数据其实已经写进 MongoDB，客户端却等不到响应）。

    冷却机制
    --------
    探活本身也要 2 秒（同样是双栈解析的代价）。所以失败后进入冷却窗口，
    窗口内直接返回 False，不再重复探活 —— 否则每个请求都要付这 2 秒。
    冷却结束后自动重新探活，Redis 恢复后无需重启进程。
    """
    global _offline_until

    now = time.monotonic()

    # 已知离线：冷却窗口内不重复探活
    if now < _offline_until:
        return False

    ok = _tcp_reachable()
    if ok:
        _offline_until = 0.0
        return True

    _offline_until = now + _OFFLINE_COOLDOWN
    return False


def ping() -> bool:
    """真实连通性探测（不缓存）。"""
    return _available()


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


def scan_json_values(
    prefix: str,
    limit: int = 200,
    max_iterations: int = 20,
) -> List[Dict[str, Any]]:
    """扫描匹配前缀的 JSON 值（**优先用索引，不要依赖本函数**）。

    为什么默认不用它：``SCAN`` 必须**完整遍历整个 key 空间**才能确认
    "确实找完了"，返回游标 0 才代表结束。key 较多时往返次数会急剧上升，
    实测可把一次请求拖到 30 秒以上。因此这里额外加了 ``max_iterations``
    上限 —— 宁可返回不完整的结果，也不要挂住请求线程。

    需要"列出所有某某"时应当维护显式索引（见 :func:`smembers` 与
    ``snapshot_index`` 的用法），那是一次往返且结果确定。

    无 Redis 或读取失败时返回空列表，由调用方优雅降级。
    """
    # 预检交给 get_redis() 统一做（含离线冷却）。
    # 这里原先单独调 _tcp_reachable()，那会绕开冷却 ——
    # 每个请求都付一次 2 秒探活，Redis 挂着时反而比直接失败更慢。
    try:
        client = get_redis()
        pattern = make_key(prefix) + '*'
        values: List[Dict[str, Any]] = []
        cursor = 0
        for _ in range(max_iterations):
            cursor, batch = client.scan(cursor=cursor, match=pattern, count=100)
            for raw_key in batch:
                payload = get_json(raw_key)
                if isinstance(payload, dict):
                    values.append(payload)
                if len(values) >= limit:
                    return values
            if cursor == 0:
                break
        return values
    except (RedisError, OSError):
        return []


def sadd_members(key: str, members: List[str]) -> None:
    """把一个集合写入索引。用于维护"当前有哪些对象"这类名单。"""
    if not members:
        return
    try:
        get_redis().sadd(make_key(key), *[str(item) for item in members])
    except (RedisError, OSError):
        pass


def smembers(key: str) -> List[str]:
    """读取索引集合。无 Redis 时返回空列表。"""
    # 同 scan_json_values：预检统一由 get_redis() 负责，
    # 否则冷却机制会被绕过，每次调用都要重新探活
    try:
        return [str(item) for item in get_redis().smembers(make_key(key))]
    except (RedisError, OSError):
        return []


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
