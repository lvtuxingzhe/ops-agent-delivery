/**
 * 审计检索接口封装。
 *
 * 只读：所有筛选条件都走 query string，后端按审计只读账号查询。
 * 前端不拼接 payload 展示内容，只负责把后端白名单字段渲染出来。
 */

import { get, getBlobWithHeaders } from './http'

export interface AuditEventItem {
  id: number
  trace_id: string
  event_type: string
  actor: string
  client_source: string
  task_id: string
  payload: Record<string, unknown>
  created_at: string | null
}

export interface AuditEventPage {
  items: AuditEventItem[]
  total: number
  limit: number
  offset: number
  has_more: boolean
}

export interface AuditActorOption {
  actor: string
  count: number
}

export interface AuditActorPage {
  items: AuditActorOption[]
}

export interface AuditFilters {
  task_id?: string
  trace_id?: string
  event_type?: string
  actor?: string
  q?: string
  since?: string
  until?: string
  limit?: number
  offset?: number
}

/** 只把有值的条件放进 URL，避免空参数把筛选条件“写死”成空字符串。 */
function toQuery(filters: AuditFilters): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === '') continue
    params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

/** 查询审计流水。 */
export function fetchAuditEvents(filters: AuditFilters): Promise<AuditEventPage> {
  return get<AuditEventPage>(`/audit/events${toQuery(filters)}`)
}

/** 查询审计里出现过的操作人，供多选筛选。 */
export function fetchAuditActors(): Promise<AuditActorOption[]> {
  return get<AuditActorPage>('/audit/actors').then((data) => data.items)
}

/** 导出当前筛选条件命中的审计流水；返回 CSV 与后端行数/截断标记。 */
export interface AuditExportResult {
  blob: Blob
  rows: number
  truncated: boolean
}

export async function exportAuditEvents(
  filters: Omit<AuditFilters, 'limit' | 'offset'>,
): Promise<AuditExportResult> {
  const { blob, headers } = await getBlobWithHeaders(
    `/audit/events/export${toQuery(filters)}`,
  )
  return {
    blob,
    rows: Number(headers.get('X-Export-Rows') || 0),
    truncated: headers.get('X-Export-Truncated') === '1',
  }
}
