"""
WebSocket 连接管理
==================
维护客户端连接集合并向全体广播消息。

频道约定
--------
+----------------+------------------------------------------+
| ``event:new``  | 新事件产生                               |
| ``event:update``| 事件状态变更（确认 / 误报）              |
| ``snapshot``   | 检测快照更新                             |
| ``health``     | 系统健康状态                             |
| ``pong``       | 心跳应答                                 |
+----------------+------------------------------------------+

线程安全说明
------------
检测任务运行在 FastAPI 的**线程池**中（同步端点），而 WebSocket 属于
asyncio 世界。因此提供 :func:`broadcast_threadsafe`，通过
``run_coroutine_threadsafe`` 把广播投递回主事件循环。
主循环引用在应用启动时由 :func:`bind_loop` 注入。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket

from app.models.schemas import WsMessage


class ConnectionManager:
    """管理活跃的 WebSocket 连接并支持广播。"""

    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def send(self, websocket: WebSocket, channel: str, payload: Any = None) -> None:
        """向单个连接发送消息，失败时静默移除该连接。"""
        message = WsMessage(channel=channel, payload=payload)
        try:
            await websocket.send_json(message.model_dump(mode='json', by_alias=True))
        except Exception:
            await self.disconnect(websocket)

    async def broadcast(self, channel: str, payload: Any = None) -> int:
        """向所有连接广播，返回成功投递数。

        采用「快照遍历 + 失败即移除」策略，避免在迭代中修改集合。
        """
        message = WsMessage(channel=channel, payload=payload)
        data = message.model_dump(mode='json', by_alias=True)

        async with self._lock:
            targets = list(self._connections)

        dead: list = []
        delivered = 0
        for websocket in targets:
            try:
                await websocket.send_json(data)
                delivered += 1
            except Exception:
                dead.append(websocket)

        if dead:
            async with self._lock:
                for websocket in dead:
                    self._connections.discard(websocket)

        return delivered

    @property
    def count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()

# 主事件循环引用，供跨线程广播使用
_loop: Optional[asyncio.AbstractEventLoop] = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """在应用启动时注入主事件循环。"""
    global _loop
    _loop = loop


def broadcast_threadsafe(channel: str, payload: Any = None) -> None:
    """从**非 async 上下文**（线程池、后台任务）安全广播。

    主循环未绑定或已关闭时静默返回 —— 推送失败不应影响业务主流程。
    """
    if _loop is None or _loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(manager.broadcast(channel, payload), _loop)
    except RuntimeError:
        pass


def stats() -> Dict[str, Any]:
    return {
        'connections': manager.count,
        'loop_bound': _loop is not None and not _loop.is_closed(),
    }
