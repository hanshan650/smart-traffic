/**
 * WebSocket 长连接
 * ================
 * 单例实现：整个应用共用一条连接，组件通过 ``on(channel, handler)`` 订阅。
 *
 * 特性
 * ----
 * · 自动重连，退避策略 1s → 2s → 4s → … 上限 15s
 * · 30 秒心跳（发 ``ping``，期待 ``pong``）
 * · 页面重新可见时立即尝试重连（切回标签页不再等待退避）
 * · 订阅返回取消函数，便于在组件卸载时清理
 *
 * 频道
 * ----
 * ``event:new`` / ``event:update`` / ``snapshot`` / ``health`` / ``pong``
 */
import { ref } from 'vue'

import type { WsMessage } from '@/types/api'

type Handler = (payload: unknown, message: WsMessage) => void

const connected = ref(false)
const lastMessageAt = ref('')
const reconnectAttempts = ref(0)

const handlers = new Map<string, Set<Handler>>()

let socket: WebSocket | null = null
let heartbeatTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let manualClose = false

const HEARTBEAT_INTERVAL = 30_000
const MAX_BACKOFF = 15_000

function buildUrl(): string {
  const path = import.meta.env.VITE_WS_PATH || '/api/v1/ws'
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${path}`
}

function dispatch(channel: string, payload: unknown, message: WsMessage): void {
  const subscribers = handlers.get(channel)
  if (!subscribers?.size) return
  subscribers.forEach((handler) => {
    try {
      handler(payload, message)
    } catch (err) {
      console.error(`[ws] 频道 ${channel} 的处理器抛出异常`, err)
    }
  })
}

function stopHeartbeat(): void {
  if (heartbeatTimer !== null) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

function startHeartbeat(): void {
  stopHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send('ping')
    }
  }, HEARTBEAT_INTERVAL)
}

function scheduleReconnect(): void {
  if (manualClose || reconnectTimer !== null) return

  // 指数退避：1s, 2s, 4s, 8s, 上限 15s
  const delay = Math.min(1000 * 2 ** reconnectAttempts.value, MAX_BACKOFF)
  reconnectAttempts.value += 1

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connect()
  }, delay)
}

export function connect(): void {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return
  }

  manualClose = false

  try {
    socket = new WebSocket(buildUrl())
  } catch (err) {
    console.error('[ws] 创建连接失败', err)
    scheduleReconnect()
    return
  }

  socket.onopen = () => {
    connected.value = true
    reconnectAttempts.value = 0
    startHeartbeat()
  }

  socket.onmessage = (event: MessageEvent<string>) => {
    lastMessageAt.value = new Date().toISOString()

    let message: WsMessage
    try {
      message = JSON.parse(event.data) as WsMessage
    } catch {
      return
    }

    dispatch(message.channel, message.payload, message)
  }

  socket.onclose = () => {
    connected.value = false
    stopHeartbeat()
    scheduleReconnect()
  }

  socket.onerror = () => {
    // onclose 会紧随其后触发，重连逻辑统一放在那里
    connected.value = false
  }
}

export function disconnect(): void {
  manualClose = true
  stopHeartbeat()

  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }

  socket?.close()
  socket = null
  connected.value = false
}

/** 订阅频道，返回取消订阅函数 */
export function on(channel: string, handler: Handler): () => void {
  if (!handlers.has(channel)) {
    handlers.set(channel, new Set())
  }
  handlers.get(channel)!.add(handler)

  return () => {
    handlers.get(channel)?.delete(handler)
  }
}

/** 主动发送文本（如 ``ping``） */
export function send(data: string): void {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(data)
  }
}

/** 页面重新可见时立刻重连，避免用户切回来还在等退避 */
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !connected.value) {
      reconnectAttempts.value = 0
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      connect()
    }
  })
}

export function useWebSocket() {
  return { connected, lastMessageAt, reconnectAttempts, connect, disconnect, on, send }
}
