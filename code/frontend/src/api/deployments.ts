/**
 * 部署任务接口。
 *
 * 方案指纹 `plan_hash` 是确认/审批的绑定值：页面只转发后台给的 hash，
 * 不自己重算；后台会在确认、审批、执行三处复验。
 */

import { del, get, getBlob, getEventStream, post, put } from './http'

export interface DeployOperation {
  argv: string[]
  command?: string
  scope: 'read' | 'dangerous'
  role?: 'precheck' | 'apply' | 'verify' | 'rollback' | 'post_rollback'
  expect?: Record<string, unknown>
  title?: string
  description?: string
  risk?: 'low' | 'medium' | 'high'
  rollback_note?: string
}

export interface DeployRisk {
  label: string
  level: 'low' | 'medium' | 'high'
}

export type TemplateStatus = 'draft' | 'published' | 'disabled'

/** 部署类型固定 8 项，没有“其他”；历史任务推不出类型时显示“未分类”，只在“全部”出现。 */
export const DEPLOY_TYPES: Array<{ value: string; label: string }> = [
  { value: 'middleware', label: '中间件' },
  { value: 'database', label: '数据库' },
  { value: 'kubernetes', label: 'K8S' },
  { value: 'docker', label: 'Docker' },
  { value: 'dependency', label: '依赖组件' },
  { value: 'network', label: '网络' },
  { value: 'security', label: '安全' },
  { value: 'monitor', label: '监控' },
]

/** 部署结果 4+1 项：全部由“不传 result”表示。 */
export const DEPLOY_RESULTS: Array<{ value: string; label: string }> = [
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
  { value: 'awaiting', label: '待确认' },
  { value: 'executing', label: '执行中' },
]

export const DEPLOY_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  DEPLOY_TYPES.map((item) => [item.value, item.label]),
)

export interface DeployTemplate {
  id: string
  name: string
  description: string
  platform: string
  os_family?: string
  package_manager?: string
  /** 部署类型；页面没选时后端按名称/命令推断。 */
  deploy_type?: string
  default_host: string
  version: number
  ttl_minutes: number
  enabled: boolean
  precheck?: DeployOperation[]
  operations: DeployOperation[]
  verify: DeployOperation[]
  rollback: DeployOperation[]
  post_rollback?: DeployOperation[]
  status?: TemplateStatus
  risk?: 'low' | 'medium' | 'high'
  visibility?: 'private' | 'team'
  source?: 'manual' | 'ai' | 'clone' | 'from_task' | 'seed'
  owner_id?: number
  created_by_name?: string
  updated_by_name?: string
  published_at?: string | null
  updated_at?: string | null
  can_edit?: boolean
  can_publish?: boolean
  /** 删除权限：管理员可删任意模板；排查员仅限自己的草稿。 */
  can_delete?: boolean
  usage_count?: number
  success_count?: number
  last_used_at?: string | null
  bad?: boolean
  ai_trace?: AiTraceItem[]
}

/** 编辑器提交的一行命令；后端会拆成 argv 并拒绝 shell 元字符。 */
export interface TemplateCommandInput {
  command: string
  scope: 'read' | 'dangerous'
  expect?: { exit_code?: number; stdout_contains?: string; stderr_contains?: string }
}

export interface TemplatePayload {
  name: string
  description: string
  platform: 'linux' | 'windows'
  os_family?: string
  package_manager?: string
  deploy_type?: string
  default_host: string
  ttl_minutes: number
  risk?: string
  visibility?: string
  operations: TemplateCommandInput[]
  verify: TemplateCommandInput[]
  rollback: TemplateCommandInput[]
  precheck?: TemplateCommandInput[]
  post_rollback?: TemplateCommandInput[]
}

export interface TemplateValidation {
  template: DeployTemplate
  command_count: number
  risk: 'low' | 'medium' | 'high'
  warnings: string[]
}

export interface TemplateHistoryItem {
  version: number
  name: string
  status: string
  operations: DeployOperation[]
  verify: DeployOperation[]
  rollback: DeployOperation[]
  precheck?: DeployOperation[]
  post_rollback?: DeployOperation[]
  published_at: string | null
  archived_at: string | null
}

export interface AiTraceItem {
  index: number
  kind: 'step' | 'thinking'
  title?: string
  status?: string
  detail?: string
}

export interface AiTemplateResponse {
  item: DeployTemplate
  ai: { model: string; model_name: string; prompt_sha256: string; generated_at: string }
  warnings: string[]
  trace?: AiTraceItem[]
}

export interface DeployPlan {
  plan_kind?: 'template' | 'ai_freeform'
  template_id: string
  template_version: number
  platform: string
  os_family?: string
  package_manager?: string
  deploy_type?: string
  host: string
  host_os_family?: string
  target: Record<string, unknown>
  title?: string
  summary?: string
  risks?: DeployRisk[]
  operations: DeployOperation[]
  verify: DeployOperation[]
  rollback: DeployOperation[]
  precheck?: DeployOperation[]
  post_rollback?: DeployOperation[]
  created_at: string
  expires_at: string
  ttl_minutes: number
  plan_hash: string
  ai_goal?: string
  ai_mode?: 'template' | 'freeform'
  ai_status?: 'queued' | 'ready' | 'failed' | string
  ai_model?: string
  ai_reason?: string
  ai_confidence?: number | null
  ai_planned_at?: string
  ai_error?: string
}

export interface DeployFailure {
  stage?: string
  code?: string
  reason?: string
  message?: string
  command_index?: number
  command?: string
  exit_code?: number | null
  duration_ms?: number | null
  stderr_tail?: string
  expected?: unknown
  actual?: unknown
  rollback_ok?: boolean
  rollback_failed_commands?: Array<Record<string, unknown>>
}

export interface DeployKnowledgeItem {
  task_id: string
  title: string
  updated_at: string
  template_id: string
  template_version: number
  os_family: string
  host: string
  outcome: string
  stage: string
  reason_code: string
  failure_summary: string
  plan_summary: string
  score: number
}

export interface DeployTask {
  id: string
  trace_id: string
  task_type: string
  status: string
  outcome: string | null
  reason_code: string
  stage: string
  title: string
  description: string
  target_scope: Record<string, unknown>
  submitted_by: number | null
  submitted_by_name?: string
  confirmed_by: number | null
  confirmed_by_name?: string
  approved_at?: string | null
  approval_reason?: string | null
  plan: DeployPlan
  plan_hash: string
  approval: Record<string, unknown>
  failure?: DeployFailure | null
  deployment_knowledge?: DeployKnowledgeItem[]
  deploy_type?: string
  deploy_type_label?: string
  error?: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
  closed_at: string | null
}

export interface DeployListItem {
  id: string
  trace_id: string
  status: string
  outcome: string | null
  reason_code: string
  title: string
  target_scope: Record<string, unknown>
  submitted_by: number | null
  plan_hash: string
  template_id: string
  /** 从任务标题/模板/命令推断，历史任务可能是空串（未分类）。 */
  deploy_type: string
  deploy_type_label: string
  created_at: string | null
  closed_at: string | null
}

export function listDeployTemplates(): Promise<{ items: DeployTemplate[] }> {
  return get('/deployments/templates')
}

export interface DeployListQuery {
  limit?: number
  offset?: number
  created_from?: string
  created_to?: string
  deploy_type?: string
  result?: string
}

export function listDeployments(
  query: DeployListQuery = {},
): Promise<{ items: DeployListItem[]; total: number }> {
  const params = new URLSearchParams()
  Object.entries({ limit: 50, ...query }).forEach(([key, value]) => {
    if (value === undefined || value === '' || value === null) return
    params.set(key, String(value))
  })
  return get(`/deployments?${params.toString()}`)
}

export function createDeployment(payload: {
  template_id: string
  host?: string
  platform?: string
  title?: string
  description?: string
  deploy_type: string
}): Promise<DeployTask> {
  return post('/deployments', payload)
}

export function fetchDeployment(taskId: string): Promise<DeployTask> {
  return get(`/deployments/${taskId}`)
}

export function confirmDeploymentPlan(
  taskId: string,
  planHash: string,
): Promise<{ task_id: string; plan_hash: string; token: string; expires_in_s: number }> {
  return post(`/deployments/${taskId}/confirm`, { plan_hash: planHash })
}

export interface DeploymentApprover {
  id: number
  username: string
  display_name: string
}

export function listDeploymentApprovers(): Promise<{ items: DeploymentApprover[] }> {
  return get('/deployments/approvers')
}

export function assignDeploymentApprover(
  taskId: string,
  approverId: number,
  reason = '',
): Promise<{ task_id: string; approver_id: number; approver_name: string; status: string }> {
  return post(`/deployments/${taskId}/assign-approver`, {
    approver_id: approverId,
    reason,
  })
}

export function approveDeploymentPlan(
  taskId: string,
  planHash: string,
  confirmToken: string,
  decision: 'APPROVE' | 'REJECT' = 'APPROVE',
  reason = '',
  stepUpCode = '',
): Promise<Record<string, unknown>> {
  return post(`/deployments/${taskId}/approve`, {
    plan_hash: planHash,
    confirm_token: confirmToken,
    decision,
    reason,
    step_up_code: stepUpCode,
  })
}

export function fetchDeploymentExecution(
  taskId: string,
): Promise<{ task_id: string; status: string; outcome: string | null; execution: Record<string, unknown> }> {
  return get(`/deployments/${taskId}/execution`)
}

export type DeploymentReportFormat = 'md' | 'pdf' | 'docx'

/** 导出单条部署任务报告（方案、审批链、执行/验证/回滚结果）。 */
export function exportDeploymentReport(
  taskId: string,
  format: DeploymentReportFormat = 'md',
): Promise<Blob> {
  return getBlob(`/deployments/${taskId}/report/export?format=${format}`)
}

export interface DeploymentTraceEvent {
  event_id: number
  attempt_no: number
  host_id: string
  local_seq: number
  phase: string
  kind: string
  level: string
  message: string
  payload: Record<string, unknown>
  created_at: string | null
  parent_event_id?: number | null
}

export interface DeploymentTraceResponse {
  task_id: string
  status: string
  outcome: string | null
  events: DeploymentTraceEvent[]
  next_after_event_id: number
}

export function fetchDeploymentTrace(
  taskId: string,
  afterEventId = 0,
  limit = 100,
): Promise<DeploymentTraceResponse> {
  return get(`/deployments/${taskId}/trace?after_event_id=${afterEventId}&limit=${limit}`)
}

export interface DeploymentTraceState {
  task_id: string
  status: string
  outcome: string | null
  next_after_event_id: number
}

export interface DeploymentTraceStreamHandlers {
  onTrace: (event: DeploymentTraceEvent) => void
  onState: (state: DeploymentTraceState) => void
}

/** 解析一帧 SSE；事件体是本项目约定的 JSON。格式坏掉时跳过，不中断整条连接。 */
function parseTraceFrame(block: string, handlers: DeploymentTraceStreamHandlers): void {
  let eventName = 'message'
  const dataLines: string[] = []
  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon < 0 ? line : line.slice(0, colon)
    let value = colon < 0 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') eventName = value
    else if (field === 'data') dataLines.push(value)
  }
  if (!dataLines.length) return
  try {
    const payload = JSON.parse(dataLines.join('\n')) as DeploymentTraceEvent | DeploymentTraceState | { detail?: string }
    if (eventName === 'trace') handlers.onTrace(payload as DeploymentTraceEvent)
    else if (eventName === 'state') handlers.onState(payload as DeploymentTraceState)
    else if (eventName === 'error') {
      const detail = (payload as { detail?: string }).detail
      throw new Error(detail || '实时连接已断开')
    }
  } catch (error) {
    if (eventName === 'error') throw error
  }
}

/**
 * 读取部署 trace 的 SSE 长连接。
 *
 * 不使用原生 EventSource：当前令牌在 sessionStorage，请求需要带
 * Authorization 头，只有 fetch 支持。调用方负责在切换任务时 abort。
 */
export async function streamDeploymentTrace(
  taskId: string,
  afterEventId: number,
  handlers: DeploymentTraceStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await getEventStream(
    `/deployments/${taskId}/stream?after_event_id=${Math.max(0, afterEventId)}`,
    signal,
  )
  const reader = response.body?.getReader()
  if (!reader) throw new Error('当前浏览器不支持流式读取')
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        parseTraceFrame(buffer.slice(0, boundary), handlers)
        buffer = buffer.slice(boundary + 2)
        boundary = buffer.indexOf('\n\n')
      }
    }
    if (buffer.trim()) parseTraceFrame(buffer, handlers)
  } finally {
    reader.releaseLock()
  }
}

export function createAiDeployment(payload: {
  goal: string
  host?: string
  platform?: 'linux' | 'windows'
  title?: string
  description?: string
  mode?: 'template' | 'freeform'
  deploy_type: string
}): Promise<{ id: string; status: string; stage: string }> {
  return post('/deployments/ai-plan', payload)
}


// ---------------------------------------------------------------------------
// 模板库（管理页）—— 与服务端 /deployments/templates/* 对应
// ---------------------------------------------------------------------------

export function listTemplateLibrary(): Promise<{ items: DeployTemplate[] }> {
  return get('/deployments/templates/library')
}

export function fetchTemplate(templateId: string): Promise<DeployTemplate> {
  return get(`/deployments/templates/${templateId}`)
}

export function validateDeployTemplate(payload: TemplatePayload): Promise<TemplateValidation> {
  return post('/deployments/templates/validate', payload)
}

export function createDeployTemplate(payload: TemplatePayload): Promise<{ item: DeployTemplate }> {
  return post('/deployments/templates', payload)
}

export function updateDeployTemplate(
  templateId: string,
  payload: TemplatePayload,
): Promise<{ item: DeployTemplate }> {
  return put(`/deployments/templates/${templateId}`, payload)
}

export function publishDeployTemplate(templateId: string): Promise<{ item: DeployTemplate }> {
  return post(`/deployments/templates/${templateId}/publish`)
}

export function disableDeployTemplate(templateId: string): Promise<{ item: DeployTemplate }> {
  return post(`/deployments/templates/${templateId}/disable`)
}

/** 软删除：模板从库中隐藏，任务引用和版本历史保留。 */
export function deleteDeployTemplate(templateId: string): Promise<{ ok: boolean; item: { id: string; status: string } }> {
  return del(`/deployments/templates/${templateId}`)
}

export function cloneDeployTemplate(templateId: string): Promise<{ item: DeployTemplate }> {
  return post(`/deployments/templates/${templateId}/clone`)
}

export function listTemplateHistory(templateId: string): Promise<{ items: TemplateHistoryItem[] }> {
  return get(`/deployments/templates/${templateId}/history`)
}

export function createTemplateFromTask(taskId: string): Promise<{ item: DeployTemplate }> {
  return post('/deployments/templates/from-task', { task_id: taskId })
}

export function generateAiTemplate(payload: {
  goal: string
  platform: 'linux' | 'windows'
  host: string
  pasted_commands?: string
}): Promise<AiTemplateResponse> {
  return post('/deployments/templates/ai-draft', payload)
}
