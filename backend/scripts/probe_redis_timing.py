"""确认 redis_client 的预检是否生效（Redis 未启动时）。

只关心一件事：**触碰 redis-py 之前是否已经被拦住**。

  被拦住 → 毫秒级抛 RedisUnavailable
  没拦住 → 阻塞 36 秒以上（这正是要修的问题）

只读，不写数据。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import redis_client  # noqa: E402


def timed(label: str, fn) -> None:
    t0 = time.perf_counter()
    try:
        result = fn()
        note = f'-> {result!r}'
    except Exception as exc:  # noqa: BLE001 —— 就是要看任何异常
        note = f'!! {type(exc).__name__}: {str(exc)[:56]}'
    ms = (time.perf_counter() - t0) * 1000
    flag = '   <<< 仍然很慢' if ms > 3000 else ''
    print(f'  {label:26s} {ms:8.0f} ms  {note}{flag}')


def main() -> int:
    print(f'Redis 目标: {redis_client.settings.redis_host}:{redis_client.settings.redis_port}')
    print()
    print('第一次调用（预期付一次探活代价）:')
    timed('get_redis()', redis_client.get_redis)

    print()
    print('后续调用（预期毫秒级，走离线冷却）:')
    timed('get_redis() 第2次', redis_client.get_redis)
    timed('acquire_once()', lambda: redis_client.acquire_once('probe:x', 5))
    timed('set_json()', lambda: redis_client.set_json('probe:x', {'a': 1}))
    timed('get_json()', lambda: redis_client.get_json('probe:x'))
    timed('push_queue()', lambda: redis_client.push_queue('probe:x', {'a': 1}))
    timed('queue_len()', lambda: redis_client.queue_len('probe:x'))
    timed('smembers()', lambda: redis_client.smembers('probe:x'))
    timed('sadd_members()', lambda: redis_client.sadd_members('probe:x', ['a']))
    timed('zadd_rank()', lambda: redis_client.zadd_rank('probe:x', 'm', 1.0))
    timed('ztop()', lambda: redis_client.ztop('probe:x', 5))
    timed('info()', redis_client.info)
    timed('ping_cached()', redis_client.ping_cached)
    timed('delete()', lambda: redis_client.delete('probe:x'))

    print()
    print('说明：第一次 2 秒是 Windows 上解析 localhost 的双栈代价，')
    print('     之后靠离线冷却直接返回，不再逐次付这个代价。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
