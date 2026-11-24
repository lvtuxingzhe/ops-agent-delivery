/** 运行概览：首页只读汇总，数字随当前登录人的可见范围变化。 */

import { get } from './http'

export interface TaskTypeCounts {
  total: number
  by_status: Record<string, number>
  awaiting_approval: number
  running: number
  report_ready: number
}

export interface Overview {
  generated_at: string
  tasks: {
    total: number
    by_status: Record<string, number>
    awaiting_approval: number
    running: number
    report_ready: number
    /** 按任务类型拆分；排查和部署各自计数，避免工作台重复计数。 */
    by_type: {
      investigation: TaskTypeCounts
      deployment: TaskTypeCounts
    }
  }
  datasources: {
    total: number
    enabled: number
    health: Record<string, number>
  }
  notifications: { unread: number }
}

export interface TokenBucket {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  tasks: number
  priced_tasks: number
  unpriced_tasks: number
  /** 仅管理员/审计员返回；没有配置单价时为 null。 */
  cost?: number | null
}

export interface TokenTask extends TokenBucket {
  id: string
  title: string
  updated_at: string | null
}

export interface TokenSummary {
  available: boolean
  amount_visible: boolean
  today: TokenBucket
  month: TokenBucket
  last_7_days: TokenBucket
  days: Array<{ date: string; total_tokens: number; tasks: number }>
  budget: {
    per_task_tokens: number
    per_day_tokens: number
    per_task_exceeded: number
    daily_exceeded: boolean
  }
  recent: TokenTask[]
  generated_at: string
  pricing_source?: {
    model_key: string
    model_name: string
    configured: boolean
    /** 元 / 千 token；未配置时为 null。 */
    input_price_per_1k?: number | null
    output_price_per_1k?: number | null
  }
}

export function fetchOverview(): Promise<Overview> {
  return get<Overview>('/overview')
}

export function fetchTokenSummary(days = 30): Promise<TokenSummary> {
  return get<TokenSummary>(`/overview/tokens?days=${days}`)
}
