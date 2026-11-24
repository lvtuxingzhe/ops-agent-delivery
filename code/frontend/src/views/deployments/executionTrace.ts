/** 部署执行日志的命令级分组，页面只做展示。 */

import type { DeploymentTraceEvent } from '../../api/deployments'

export type ExecutionGroupStatus = 'running' | 'ok' | 'failed' | 'unknown' | 'stage'
export type ExecutionGroupKind = 'phase' | 'command' | 'result'

export interface ExecutionTraceGroup {
  key: string
  kind: ExecutionGroupKind
  phase: string
  role: string
  index: number | null
  command: string
  title: string
  status: ExecutionGroupStatus
  exitCode: number | null
  durationMs: number | null
  startedAt: string | null
  endedAt: string | null
  lines: DeploymentTraceEvent[]
}

function payloadOf(event: DeploymentTraceEvent): Record<string, unknown> {
  return event.payload ?? {}
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * 按阶段 + 命令序号分组。
 *
 * - 命令事件：`payload.index` 有值，一条命令一张卡；
 * - 阶段事件：没有序号、也不是最终 result，归到该阶段；
 * - 执行结果：phase=result 的终态汇总，单独一张卡，不能混进阶段。
 */
export function groupExecutionTrace(events: DeploymentTraceEvent[]): ExecutionTraceGroup[] {
  const groups: ExecutionTraceGroup[] = []
  const byKey = new Map<string, ExecutionTraceGroup>()

  for (const event of events) {
    const payload = payloadOf(event)
    const phase = event.phase || '未分阶段'
    const index = numberValue(payload.index)
    const kind: ExecutionGroupKind = index !== null
      ? 'command'
      : phase === 'result'
        ? 'result'
        : 'phase'
    const key = kind === 'command'
      ? `cmd:${phase}:${index}`
      : kind === 'result'
        ? 'result'
        : `stage:${phase}`
    let group = byKey.get(key)
    if (!group) {
      group = {
        key,
        kind,
        phase,
        role: stringValue(payload.role),
        index,
        command: '',
        title: event.message || event.kind,
        status: kind === 'command' ? 'running' : kind === 'result' ? 'unknown' : 'stage',
        exitCode: null,
        durationMs: null,
        startedAt: event.created_at,
        endedAt: null,
        lines: [],
      }
      byKey.set(key, group)
      groups.push(group)
    }

    if (kind === 'command' && !group.command && payload.command) {
      group.command = stringValue(payload.command)
    }
    if (!group.title && event.message) group.title = event.message
    group.endedAt = event.created_at

    if (event.kind === 'result') {
      if (kind === 'command') {
        const ok = payload.ok
        group.status = typeof ok === 'boolean' ? (ok ? 'ok' : 'failed') : 'unknown'
        group.exitCode = numberValue(payload.exit_code)
        group.durationMs = numberValue(payload.duration_ms)
      } else if (kind === 'result') {
        if (event.level === 'error') group.status = 'failed'
        else if (group.status !== 'failed') {
          group.status = event.level === 'warn' ? 'unknown' : 'ok'
        }
      } else if (typeof payload.ok === 'boolean') {
        group.status = payload.ok ? 'ok' : 'failed'
      } else if (event.level === 'error') {
        group.status = 'failed'
      }
    }

    group.lines.push(event)
  }

  return groups
}

export function formatExecutionDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} 毫秒`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} 秒`
  const total = Math.floor(seconds)
  return `${Math.floor(total / 60)} 分 ${total % 60} 秒`
}

/**
 * 阶段用首尾事件算真实阶段耗时；命令优先用执行器上报的 duration_ms，
 * 执行中的命令才用本机时钟继续增长。阶段结束后不再跟着当前时间涨。
 */
export function executionGroupDuration(group: ExecutionTraceGroup, nowMs: number): string {
  if (group.durationMs !== null) return formatExecutionDuration(group.durationMs)
  const start = Date.parse(group.startedAt || '')
  if (!Number.isFinite(start)) return ''
  const end = group.status === 'running'
    ? nowMs
    : group.endedAt
      ? Date.parse(group.endedAt)
      : NaN
  if (!Number.isFinite(end) || end <= start) return ''
  return formatExecutionDuration(end - start)
}

export function executionGroupStatusText(group: ExecutionTraceGroup): string {
  const labels: Record<ExecutionGroupStatus, string> = {
    running: '执行中',
    ok: '成功',
    failed: '失败',
    unknown: '结果未知',
    stage: '阶段',
  }
  return labels[group.status]
}

export function executionGroupOrder(group: ExecutionTraceGroup): string {
  if (group.kind === 'result') return '执行结果'
  if (group.kind === 'phase') return `${executionPhaseText(group)}阶段`
  return `第 ${(group.index ?? 0) + 1} 条命令`
}

const PHASE_LABELS: Record<string, string> = {
  precheck: '预检',
  execute: '执行',
  verify: '验证',
  rollback: '回滚',
  post_rollback: '回滚后验证',
  plan: '规划',
  result: '结果',
}

const ROLE_LABELS: Record<string, string> = {
  precheck: '预检',
  apply: '执行',
  verify: '验证',
  rollback: '回滚',
  post_rollback: '回滚后验证',
}

const REASON_LABELS: Record<string, string> = {
  permit_nonce_store_unavailable: '执行许可存储不可用',
  permit_nonce_store_required: '未配置执行许可存储',
  permit_replayed: '执行许可已被使用',
  permit_expired: '执行许可已过期',
  permit_signature_invalid: '执行许可签名无效',
  permit_task_mismatch: '执行许可与任务不匹配',
  ssh_connect_failed: 'SSH 连接失败',
  ssh_credentials_missing: '缺少 SSH 凭据',
  ssh_target_invalid: 'SSH 目标无效',
  verify_expect_mismatch: '验证结果不符合预期',
  timed_out: '执行超时',
}

/** 阶段名转中文；未知阶段保留原值，避免把信息藏掉。 */
export function executionPhaseText(group: ExecutionTraceGroup): string {
  return PHASE_LABELS[group.phase] || group.phase
}

/** 角色名转中文；未知角色保留原值。 */
export function executionRoleText(group: ExecutionTraceGroup): string {
  return ROLE_LABELS[group.role] || group.role
}

function reasonText(reason: string): string {
  const code = reason.split(':')[0]
  const label = REASON_LABELS[code]
  return label ? `${label}（${reason}）` : reason
}

/** 单条日志的展示文本；结果事件换成退出码、耗时和可读原因。 */
export function executionLineText(event: DeploymentTraceEvent): string {
  const payload = payloadOf(event)
  if (event.kind === 'result') {
    const message = stringValue(event.message)
    const reason = stringValue(payload.reason) || stringValue(payload.code)
    const isCommand = numberValue(payload.index) !== null

    // 阶段汇总：有中文结论就直接显示，不再伪装成“命令失败”。
    if (!isCommand && message && !reason) return message
    // 终态结果卡：标题已经是中文结论，这里只补原因码和“命令是否发出”。
    if (!isCommand && reason) {
      const parts = [`原因：${reasonText(reason)}`]
      if (numberValue(payload.exit_code) === null) parts.push('命令未发出')
      return parts.join(' · ')
    }

    const parts: string[] = []
    if (typeof payload.ok === 'boolean') parts.push(payload.ok ? '命令成功' : '命令失败')
    else parts.push('命令结果')
    const exitCode = numberValue(payload.exit_code)
    if (exitCode !== null) parts.push(`退出码 ${exitCode}`)
    else if (payload.ok === false) parts.push('未取得退出码')
    const durationMs = numberValue(payload.duration_ms)
    if (durationMs !== null) parts.push(`耗时 ${formatExecutionDuration(durationMs)}`)
    if (reason) parts.push(`原因：${reasonText(reason)}`)
    return parts.join(' · ')
  }
  const line = stringValue(payload.line)
  if (line) return line.slice(0, 500)
  const detail = stringValue(payload.detail)
  if (detail) return detail.slice(0, 500)
  return String(event.message || event.kind || '').slice(0, 500)
}
