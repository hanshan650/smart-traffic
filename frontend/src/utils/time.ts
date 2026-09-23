/**
 * 时间解析工具
 * ============
 *
 * 解决一个很容易被忽略、但会让所有时间显示都错的问题。
 *
 * 后端用 `datetime.utcnow().isoformat()` 生成时间戳，得到的是
 * **UTC 时间但不带时区标记**，形如：
 *
 *     2026-09-23T02:30:00.123456
 *
 * 而 ECMAScript 规范规定：不带时区标记的日期时间字符串按**本地时间**解析
 * （只有带 `T` 的日期形式例外）。于是浏览器会把这个 UTC 时刻当成本地时刻，
 * 在 UTC+8 环境下所有时间都**偏小 8 小时** —— 表现为"刚刚发生的事"
 * 显示成"8 小时前"，或者反过来。
 *
 * 这类错误的隐蔽之处在于：它不是崩溃，只是数字不对。不特意核对很难发现，
 * 而在交通场景里"这条事故是 3 分钟前还是 8 小时前"直接影响判断。
 *
 * 处理办法：解析前先补上 `Z` 标记，明确告知这是 UTC 时间。
 * 已经带有时区信息的（`Z` 或 `+08:00`）不动，避免重复处理。
 *
 * 为什么不改后端：项目里大量接口都用 `datetime.utcnow()`，逐个改造面太广；
 * 而解析入口是可控的少数几处。**在边界处一次性修正，比在源头改一堆更稳。**
 */

/** 时区标记检测：末尾是 `Z` 或 `+HH:MM` / `-HH:MM` 就认为已带时区 */
const HAS_TIMEZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i

/**
 * 解析服务端时间戳，返回本地 Date。
 *
 * :returns: 解析失败时返回 `null`（而不是 Invalid Date），
 *           让调用方显式处理"没有时间"这种情况。
 */
export function parseServerTime(value?: string | null): Date | null {
  if (!value) return null
  const text = String(value).trim()
  if (!text) return null

  // 只对"日期 + 时间"形式补时区。纯日期（2026-09-23）本身不带时刻，
  // 补了 Z 反而会把当天推到前一天（东八区的 00:00 减 8 小时）。
  const normalized =
    text.includes('T') && !HAS_TIMEZONE.test(text) ? `${text}Z` : text

  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

/** 格式化为本地日期时间串（`YYYY/MM/DD HH:mm:ss`） */
export function formatServerTime(value?: string | null): string {
  const date = parseServerTime(value)
  if (!date) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}

/**
 * 相对时间描述：`刚刚` / `N 分钟前` / `N 小时前` / `N 天前`。
 *
 * 超过 7 天直接回落到具体日期 —— "23 天前" 这种表述不如日期直观。
 */
export function relativeTime(value?: string | null): string {
  const date = parseServerTime(value)
  if (!date) return ''

  const seconds = (Date.now() - date.getTime()) / 1000
  // 时钟漂移或服务端时间略超前时，不要显示成"负 N 秒前"
  if (seconds < 0) return '刚刚'
  if (seconds < 60) return '刚刚'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)} 天前`
  return date.toLocaleDateString('zh-CN')
}
