/**
 * Axios 实例与统一错误处理
 * ========================
 * 后端异常有三种形态，这里统一归一化为 :class:`ApiError`：
 *
 *  1. ``HTTPException(status_code=503, detail={success, error, hint})``
 *     —— 业务层可控错误（视频源未接入、模型未就绪等），带 ``hint``
 *  2. ``HTTPException(status_code=404, detail={success, error})``
 *  3. 未捕获异常 —— 由全局处理器转成 ``{success, error, hint}``
 */
import axios, { AxiosError } from 'axios'

const BASE_URL = import.meta.env.VITE_API_BASE || '/api/v1'

/**
 * 登录接口自身的 401 不能触发“跳登录页”：
 * 用户输错口令时表单会直接刷新掉，看起来像页面失控。
 */
const AUTH_ENDPOINTS = ['/auth/login', '/auth/me']

function isAuthEndpoint(url?: string): boolean {
  if (!url) return false
  return AUTH_ENDPOINTS.some((item) => url.includes(item))
}

export const http = axios.create({
  baseURL: BASE_URL,
  timeout: 20000,
  headers: { 'Content-Type': 'application/json' },
  // 会话走 HttpOnly Cookie。经 Vite 代理时属同源，浏览器本就会带上；
  // 显式声明是为了将来直连后端（跨域）时不用再回来改
  withCredentials: true,
})

export interface ApiErrorBody {
  success?: boolean
  error?: string
  hint?: string
}

export class ApiError extends Error {
  readonly status: number
  readonly hint: string

  constructor(message: string, status = 0, hint = '') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.hint = hint
  }
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiErrorBody>) => {
    const status = error.response?.status ?? 0
    const body = error.response?.data

    // 会话失效：统一跳登录页，并把当前路径带过去，登录后能回到原地。
    // 用动态 import 引 router —— 静态引会形成 client → router → views → client 的环
    if (status === 401 && !isAuthEndpoint(error.config?.url)) {
      try {
        const { default: router } = await import('@/router')
        const current = router.currentRoute.value
        if (current.path !== '/login') {
          void router.replace({ path: '/login', query: { redirect: current.fullPath } })
        }
      } catch {
        // 路由还没就绪（极早期请求）。不处理，交给页面自己的空状态
      }
    }

    // FastAPI 的 detail 可能是结构化对象（本项目多数接口），也可能是字符串
    // （如运行时配置保存的 403）。两种都要提取，否则页面只剩通用 Axios 文案。
    const detail = (body as unknown as { detail?: ApiErrorBody | string } | undefined)?.detail
    const structured = typeof detail === 'object' && detail !== null ? detail : undefined

    const message =
      (typeof detail === 'string' ? detail : '') ||
      structured?.error ||
      body?.error ||
      error.message ||
      '请求失败，请稍后重试'
    const hint = structured?.hint || body?.hint || ''

    return Promise.reject(new ApiError(message, status, hint))
  },
)

/** 从任意异常中提取可展示的错误文案 */
export function describeError(error: unknown): { message: string; hint: string } {
  if (error instanceof ApiError) {
    return { message: error.message, hint: error.hint }
  }
  if (error instanceof Error) {
    return { message: error.message, hint: '' }
  }
  return { message: String(error), hint: '' }
}
