/**
 * CSV 导出：统一 UTF-8 BOM、字段转义与下载入口。
 *
 * 六个业务页共用这一个出口，避免每页各写一套转义规则。Excel 对无 BOM 的
 * UTF-8 CSV 会按系统代码页解码，中文直接乱码，所以 BOM 是必须的。
 */

/** 前端逐页拉取的安全上限；超过时提示总数与已导出数，不静默截断。 */
export const MAX_EXPORT_ROWS = 50_000

export interface CsvColumn<T> {
  label: string
  value: (row: T) => string | number | boolean | null | undefined
}

/** CSV 单元格：双引号转义；以公式字符开头的值加前缀，防止 Excel 公式注入。 */
function cell(value: unknown): string {
  if (value === null || value === undefined) return ''
  const text = String(value)
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text
  return `"${safe.replace(/"/g, '""')}"`
}

/** 生成带 BOM 的 CSV 文本。 */
export function buildCsv<T>(columns: CsvColumn<T>[], rows: T[]): string {
  const header = columns.map((column) => cell(column.label)).join(',')
  const body = rows.map((row) => columns.map((column) => cell(column.value(row))).join(','))
  return `\ufeff${[header, ...body].join('\r\n')}`
}

/** 触发浏览器下载；文件名由调用方给出，不接收任意路径。 */
export function downloadCsv<T>(filename: string, columns: CsvColumn<T>[], rows: T[]): void {
  const blob = new Blob([buildCsv(columns, rows)], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** 统一日期后缀，避免同名文件互相覆盖。 */
export function exportStamp(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`
}
