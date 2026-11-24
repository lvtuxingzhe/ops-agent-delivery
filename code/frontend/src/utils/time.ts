/**
 * 统一显示本地时间。
 *
 * 后端 API 存的是带时区的 UTC ISO 字符串（例如 `2026-11-16T04:27:58+00:00`），
 * 直接截字符串会把 UTC 当成本地时间，用户在北京看到的就是 04:27 而不是 12:27。
 * 这里用 `Date` + `Intl` 转成浏览器本地时区，所有页面共用同一规则。
 */
export function formatLocalTime(
  value: string | null | undefined,
  withSeconds = false,
): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    // 极少数非 ISO 的历史值：保留原样，避免整页显示成 Invalid Date。
    return value.replace('T', ' ')
  }
  const parts = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(date)
  const pick = (type: string) => parts.find((part) => part.type === type)?.value ?? '00'
  const day = `${pick('year')}-${pick('month')}-${pick('day')}`
  const clock = `${pick('hour')}:${pick('minute')}${withSeconds ? `:${pick('second')}` : ''}`
  return `${day} ${clock}`
}
