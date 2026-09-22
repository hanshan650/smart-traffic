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

export const http = axios.create({
  baseURL: BASE_URL,
  timeout: 20000,
  headers: { 'Content-Type': 'application/json' },
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
  (error: AxiosError<ApiErrorBody>) => {
    const status = error.response?.status ?? 0
    const body = error.response?.data

    // FastAPI 把 HTTPException 的 detail 放在 detail 字段
    const detail = (body as unknown as { detail?: ApiErrorBody } | undefined)?.detail

    const message =
      detail?.error || body?.error || error.message || '请求失败，请稍后重试'
    const hint = detail?.hint || body?.hint || ''

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
