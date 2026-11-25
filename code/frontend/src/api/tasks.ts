/**
 * 排查任务接口封装（提交是异步的）。
 *
 * 只有三个动作：提交、查状态、查报告。**提交返回 202 只表示已入队**，
 * 真正跑完要看 `status` 到 `report_ready`（报告完成）或 `closed`（已结束）。
 * 调用方通过 `fetchTask` 轮询结果，不把 202 当成功页。
 */

import { del, get, getBlob, post, postForm, put } from './http'

/** 提交时的三个字段，和后端 SubmitBody 一一对应。 */
export interface SubmitPayload {
  title: string
  description?: string
  target_scope?: Record<string, unknown>
}

/** 提交回执：202 的响应体。 */
export interface SubmitAck {
  id: string
  trace_id: string
  status: string
  status_text: string
  queue: string
  screenshot_count?: number
}

/** 任务状态（后端白名单字段）。 */
export interface TaskAssignee {
  id: number
  username: string
  display_name: string
  claimed_at: string | null
}

export interface TaskInfo {
  id: string
  trace_id: string
  task_type: string
  status: string
  status_text: string
  outcome: string | null
  stage: string
  title: string
  submitted_by: string
  /** 提交人展示名；旧数据没有关联到用户时为空。 */
  submitted_by_name?: string
  created_at: string | null
  closed_at: string | null
  /** 审批人；待审批时为空。 */
  confirmed_by?: number | null
  confirmed_by_name?: string | null
  /** 审批通过时间；待审批时为空。 */
  approved_at?: string | null
  /** 审批理由（代批/接管时填写），来自 approval.exec。 */
  approval_reason?: string | null
  /** 待审批方案的截止时间；只用于展示，不包含令牌。 */
  approval_expires_at?: string | null
  /** 列表展示规则：主机 / 中间件 / 数据库 / K8S / DOCKER / 其他。 */
  category?: string
  category_text?: string
  assignee?: TaskAssignee | null
}

/** Agent 过程轨迹：思考、工具调用、工具回包，按发生顺序排列。 */
export interface TaskTraceItem {
  index: number
  kind: 'thinking' | 'tool_call' | 'tool_result' | 'text' | 'step' | 'error'
  created_at?: string
  name?: string
  call_id?: string
  state?: string
  text?: string
  input?: string
  output?: string
}

/** 证据快照：worker 已脱敏并截断，前端只做文本展示。 */
export interface TaskEvidence {
  id: string
  source: string
  observed_at: string
  target: string
  data_status: string
  fake: boolean
  excerpt: string
  /** 人工截图的缩略图 data URI；普通证据没有这个字段。 */
  thumbnail?: string
  ocr_confidence?: number
}

/** 历史排查报告：只读参考，不是本次证据。 */
export interface TaskKnowledgeItem {
  task_id: string
  title: string
  verdict: string
  headline: string
  summary: string
  conclusions: Array<{ text?: string }>
  score: number
  updated_at: string
}

export interface TaskHypothesis {
  statement: string
  status: 'supported' | 'rejected' | 'inconclusive' | string
  evidence_ids: string[]
  note?: string
}

export interface TaskInventoryItem {
  host: string
  service: string
  kind: 'systemd' | 'process' | 'container' | 'port' | string
  ports: string[]
  status: 'running' | 'failed' | 'unknown' | 'no_data' | string
  evidence_ids: string[]
}

export interface TaskIncidentalFinding {
  object: string
  finding: string
  severity: 'info' | 'warning' | 'critical' | string
  evidence_ids: string[]
  impact?: string
}

export interface TaskHostCoverage {
  host: string
  status: 'ok' | 'no_data' | 'unreachable' | string
  reason?: string
  evidence_ids?: string[]
}

export interface TaskAnswerQuality {
  status: 'ok' | 'degraded' | 'unanswered' | string
  reasons: string[]
  answer_mode?: string
  primary_answer_present?: boolean
  inventory_count?: number
  inventory_dropped?: number
  incidental_count?: number
  host_gap_count?: number
  report_complete?: boolean
}

/** 报告草稿（查状态为 awaiting_approval / closed 之后再看）。 */
export interface TaskReport {
  task_id: string
  status: string
  data_quality: string
  answer_mode?: 'inventory' | 'diagnosis' | 'general' | string
  answer_quality?: TaskAnswerQuality
  primary_answer?: string
  inventory?: TaskInventoryItem[]
  incidental_findings?: TaskIncidentalFinding[]
  host_coverage?: TaskHostCoverage[]
  headline?: string
  verdict?: 'no_anomaly' | 'anomaly' | 'insufficient' | string
  scope?: string
  summary: string
  hypotheses?: TaskHypothesis[]
  root_cause?: string
  root_cause_verified?: boolean
  root_cause_evidence_ids?: string[]
  root_cause_source?: 'ai' | 'human' | string
  root_cause_note?: string
  solution?: string
  report_complete?: boolean
  report_gaps?: string[]
  next_step?: string
  conclusions: Array<Record<string, unknown>>
  unknowns: string[]
  trace?: TaskTraceItem[]
  knowledge?: TaskKnowledgeItem[]
  generated_by: string
  fake: boolean
  conclusions_count: number
  evidences: TaskEvidence[]
  evidence_count: number
}

/** 提交任务。返回 202 回执，任务此时只是「入队」。 */
export function submitTask(payload: SubmitPayload): Promise<SubmitAck> {
  return post<SubmitAck>('/tasks', payload)
}

/** 带报错截图提交：multipart，截图由后端 OCR 后作为证据入报告。 */
export function submitTaskWithScreenshots(
  payload: SubmitPayload,
  screenshots: File[],
): Promise<SubmitAck> {
  const form = new FormData()
  form.append('title', payload.title)
  form.append('description', payload.description || '')
  form.append('target_scope', JSON.stringify(payload.target_scope || {}))
  for (const file of screenshots) {
    form.append('screenshots', file, file.name)
  }
  return postForm<SubmitAck>('/tasks/with-screenshots', form)
}

/** 查任务状态。轮询就调它。 */
export function fetchTask(taskId: string): Promise<TaskInfo> {
  return get<TaskInfo>(`/tasks/${taskId}`)
}

/** 一条过程事件；payload.item 是可直接展示的轨迹条目。 */
export interface TaskTraceEvent {
  event_id: number
  attempt_no: number
  host_id: string
  local_seq: number
  phase: string
  kind: string
  level: string
  message: string
  payload: { item?: TaskTraceItem; [key: string]: unknown }
  created_at: string | null
  parent_event_id: number | null
}

/** 查报告草稿。 */
export function fetchTaskReport(taskId: string): Promise<TaskReport> {
  return get<TaskReport>(`/tasks/${taskId}/report`)
}

/** 增量读取排查过程；运行中每 2.5 秒拉一次。 */
export function fetchTaskTrace(
  taskId: string,
  afterEventId = 0,
  limit = 100,
): Promise<{ events: TaskTraceEvent[]; last_event_id: number }> {
  return get(`/tasks/${taskId}/trace?after_event_id=${afterEventId}&limit=${limit}`)
}

/** 导出报告：Markdown 或 PDF（与页面同源的白名单内容）。 */
export function exportTaskReport(
  taskId: string,
  format: 'md' | 'pdf' | 'docx' = 'md',
): Promise<Blob> {
  return getBlob(`/tasks/${taskId}/report/export?format=${format}`)
}

/** 一条协作讨论；正文是纯文本，前端不做 Markdown 渲染。 */
export interface TaskComment {
  id: string
  author_id: number
  author_name: string
  body: string
  created_at: string
  /** 写入时解析出的被 @ 用户 ID。 */
  mentions: number[]
  /** discussion=讨论；clue=排查线索，会进入 Agent 上下文。 */
  kind: 'discussion' | 'clue'
}

export interface TaskCommentCursor {
  created_at: string
  id: string
}

export interface TaskCommentList {
  items: TaskComment[]
  total: number
  has_more: boolean
  next_cursor: TaskCommentCursor | null
  unread_count: number
  truncated: boolean
}

/** 读任务的协作讨论；默认取最新一页，传游标取更早一页。 */
export function fetchTaskComments(
  taskId: string,
  cursor: TaskCommentCursor | null = null,
  limit = 50,
): Promise<TaskCommentList> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (cursor) {
    params.set('before_created_at', cursor.created_at)
    params.set('before_id', cursor.id)
  }
  return get<TaskCommentList>(`/tasks/${taskId}/comments?${params.toString()}`)
}

/** 追加一条讨论或排查线索；空白和超长正文由后端拒绝。 */
export function addTaskComment(
  taskId: string,
  body: string,
  kind: 'discussion' | 'clue' = 'discussion',
): Promise<TaskComment> {
  return post<TaskComment>(`/tasks/${taskId}/comments`, { body, kind })
}

/** 把当前用户在这条任务下的讨论标为已读。 */
export function markTaskCommentsRead(
  taskId: string,
): Promise<{ task_id: string; last_read_at: string }> {
  return post<{ task_id: string; last_read_at: string }>(
    `/tasks/${taskId}/comments/read`,
  )
}

/** 撤回/清理一条讨论；作者本人或管理员可用。 */
export function deleteTaskComment(
  taskId: string,
  commentId: string,
): Promise<{ id: string; deleted: boolean; already: boolean }> {
  return del<{ id: string; deleted: boolean; already: boolean }>(
    `/tasks/${taskId}/comments/${commentId}`,
  )
}

export type ReportFeedbackTarget = 'root_cause' | 'conclusion'
export type ReportFeedbackVerdict = 'confirm' | 'reject' | 'correct'

/** 一条逐条反馈：同一个人对同一目标位置只保留最新一条。 */
export interface ReportFeedback {
  id: string
  task_id: string
  target_type: ReportFeedbackTarget
  target_index: number
  target_text: string
  report_hash: string
  verdict: ReportFeedbackVerdict
  corrected_text: string
  note: string
  created_by: number
  created_by_name: string
  created_at: string
  updated_at: string
}

/** 读这份报告已有的逐条反馈。 */
export async function fetchReportFeedback(
  taskId: string,
): Promise<{ items: ReportFeedback[] }> {
  return get<{ items: ReportFeedback[] }>(`/tasks/${taskId}/report/feedback`)
}

/** 对根因或某条结论写反馈；同一人同一位置覆盖旧判断。 */
export function submitReportFeedback(
  taskId: string,
  targetType: ReportFeedbackTarget,
  targetIndex: number,
  verdict: ReportFeedbackVerdict,
  correctedText = '',
  note = '',
): Promise<ReportFeedback> {
  return post<ReportFeedback>(`/tasks/${taskId}/report/feedback`, {
    target_type: targetType,
    target_index: targetIndex,
    verdict,
    corrected_text: correctedText,
    note,
  })
}

export interface AssignableUser {
  id: number
  username: string
  display_name: string
  roles: string[]
}

/** 转派候选人；只有管理员或当前处理人能读。 */
export function fetchAssignableUsers(
  taskId: string,
): Promise<{ items: AssignableUser[] }> {
  return get<{ items: AssignableUser[] }>(`/tasks/${taskId}/assignable-users`)
}

/** 转派处理人。 */
export function assignTask(
  taskId: string,
  assigneeId: number,
): Promise<{ task_id: string; status: string; assignee: TaskAssignee }> {
  return post<{ task_id: string; status: string; assignee: TaskAssignee }>(
    `/tasks/${taskId}/assign`, { assignee_id: assigneeId },
  )
}

/** 任务列表（审批页用；只含白名单字段）。 */
export interface TaskList {
  items: TaskInfo[]
  total: number
  page: number
  size: number
}

/** 一条待批准命令：argv 是数组，scope 取值范围是 read / dangerous。 */
export interface ExecOperation {
  argv: string[]
  scope: 'read' | 'dangerous'
}

/** 提交人先填好的处置方案；审批页用它预填，不再让人从零录入。 */
export interface DispositionDraft {
  task_id: string
  status: string
  operations: ExecOperation[]
  platform: 'linux' | 'windows' | string
  host: string
  reason: string
  submitted_by: number | null
  submitted_at: string | null
}

/** 执行结果视图（不含许可令牌；preview 只在短期内存在）。 */
export interface ExecPreview {
  fingerprint?: string
  stdout?: string
  stderr?: string
}

export interface ExecutionView {
  task_id: string
  status: string
  outcome: string | null
  platform: string
  host: string
  issued_by: number | null
  issued_at: string | null
  submitted_by?: number | null
  is_self_approval?: boolean
  approval_reason?: string | null
  ttl_s: number | null
  ops: Array<{ argv: string[]; scope: string; fingerprint: string }>
  results: Array<Record<string, unknown> & { preview?: ExecPreview | null }>
  finished_at: string | null
  terminated_at: string | null
  terminated_by: number | null
  termination_reason: string
  lease_revoked: boolean
}

/** 按状态/处理人/任务大类/业务分类/创建时间列出任务。 */
export interface TaskListFilters {
  taskType?: 'investigation' | 'deployment'
  category?: string
  q?: string
  createdAfter?: string
  createdBefore?: string
}

export function listTasks(
  status?: string,
  page = 1,
  size = 20,
  assignee?: string,
  filters: TaskListFilters = {},
): Promise<TaskList> {
  const params = new URLSearchParams({ page: String(page), size: String(size) })
  if (status) params.set('status', status)
  if (assignee) params.set('assignee', assignee)
  if (filters.taskType) params.set('task_type', filters.taskType)
  if (filters.category) params.set('category', filters.category)
  if (filters.q) params.set('q', filters.q)
  if (filters.createdAfter) params.set('created_after', filters.createdAfter)
  if (filters.createdBefore) params.set('created_before', filters.createdBefore)
  return get<TaskList>(`/tasks?${params.toString()}`)
}

/** 驳回任务（仅待审批阶段）。 */
export function rejectTask(
  taskId: string,
  reason = '',
): Promise<{ task_id: string; status: string; outcome: string }> {
  return post<{ task_id: string; status: string; outcome: string }>(
    `/tasks/${taskId}/reject`, { reason },
  )
}

/** 管理员接管超时审批：只登记接管人，不代替批准。 */
export function takeoverApproval(
  taskId: string,
  reason = '',
): Promise<{
  task_id: string
  status: string
  takeover_by: number
  takeover_at: string
  reason: string
}> {
  return post<{
    task_id: string
    status: string
    takeover_by: number
    takeover_at: string
    reason: string
  }>(`/tasks/${taskId}/takeover`, { reason })
}

/** 撤回任务（仅待审批阶段）。 */
export function cancelTask(
  taskId: string,
  reason = '',
): Promise<{ task_id: string; status: string; outcome: string }> {
  return post<{ task_id: string; status: string; outcome: string }>(
    `/tasks/${taskId}/cancel`, { reason },
  )
}

/** 认领排查单：同一条只允许一个处理人；重复认领幂等。 */
export function claimTask(taskId: string): Promise<{
  task_id: string
  status: string
  assignee: TaskAssignee
}> {
  return post<{ task_id: string; status: string; assignee: TaskAssignee }>(
    `/tasks/${taskId}/claim`,
  )
}

/** 释放认领：认领人本人或管理员可释放。 */
export function releaseTask(taskId: string): Promise<{
  task_id: string
  status: string
  assignee: null
}> {
  return post<{ task_id: string; status: string; assignee: null }>(
    `/tasks/${taskId}/release`,
  )
}

/** 终止执行中的任务（仅 admin；终止后任务立即 closed/cancelled）。 */
export function terminateExecution(
  taskId: string,
  reason = '',
): Promise<{
  task_id: string
  status: string
  outcome: string
  terminated_at: string
  lease_revoked: boolean
}> {
  return post<{
    task_id: string
    status: string
    outcome: string
    terminated_at: string
    lease_revoked: boolean
  }>(`/tasks/${taskId}/terminate`, { reason })
}

/** 报告完成后提交处置方案：命令由人填写，审批查看方案。 */
export function requestExecution(
  taskId: string,
  reason: string,
  operations: ExecOperation[],
  platform = 'linux',
  host = 'local',
): Promise<{ task_id: string; status: string; outcome: null }> {
  return post<{ task_id: string; status: string; outcome: null }>(
    `/tasks/${taskId}/request-execution`,
    { reason, operations, platform, host },
  )
}

/** 待审批阶段补填或修改处置方案；不重复推进状态，也不会执行。 */
export function updateDisposition(
  taskId: string,
  reason: string,
  operations: ExecOperation[],
  platform = 'linux',
  host = 'local',
): Promise<{ task_id: string; status: string; outcome: null }> {
  return put<{ task_id: string; status: string; outcome: null }>(
    `/tasks/${taskId}/disposition`,
    { reason, operations, platform, host },
  )
}

/** 读取提交人填好的处置方案；审批页预填用。 */
export function fetchDisposition(taskId: string): Promise<DispositionDraft> {
  return get<DispositionDraft>(`/tasks/${taskId}/disposition`)
}

/** 查询执行结果（平台 / 操作 / 元数据）。 */
export function fetchExecution(taskId: string): Promise<ExecutionView> {
  return get<ExecutionView>(`/tasks/${taskId}/execution`)
}

export interface AiReview {
  available: boolean
  decision: 'approve' | 'reject' | 'needs_human'
  risk: 'low' | 'medium' | 'high'
  summary: string
  reasons: string[]
  red_flags: string[]
  model?: string
}

/** AI 预审：只返回建议，不签发许可、不改任务状态。 */
export function reviewTaskWithAi(
  taskId: string,
  platform: string,
  operations: ExecOperation[],
  host = 'local',
): Promise<AiReview> {
  return post<AiReview>(`/tasks/${taskId}/ai-review`, { platform, operations, host })
}

/** 审批通过：签发许可并派发执行。 */
export interface ExecPermitAck {
  task_id: string
  platform: string
  host: string
  status: string
  ttl_s: number
  items: Array<{ fingerprint: string; scope: string }>
  note: string
}

export function issueExecPermits(
  taskId: string,
  platform: string,
  operations: ExecOperation[],
  ttlS = 600,
  host = 'local',
  reason = '',
  stepUpCode = '',
): Promise<ExecPermitAck> {
  return post<ExecPermitAck>(`/tasks/${taskId}/exec-permits`, {
    platform,
    operations,
    ttl_s: ttlS,
    host,
    reason,
    step_up_code: stepUpCode,
  })
}
