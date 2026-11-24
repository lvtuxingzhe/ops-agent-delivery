/**
 * 审批页（第一层补齐）：把「只能 curl 批」变成人能点的界面。
 *
 * 三件事：
 * 1. 列出 `awaiting_approval` 的任务，选一条看报告摘要；
 * 2. 批准：逐条录入 argv（JSON 数组）+ scope，调 exec-permits 签发并派发；
 * 3. 驳回 / 撤回：任务结束为 rejected / cancelled。
 *
 * 批准后每 2 秒查一次执行结果，直到 `finished_at` 出现或超时。
 * 结果只显示元数据；令牌永远不在前端出现（后台接口已剔除）。
 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchMe, type MeInfo } from '../../api/auth'
import { getOrLogin, ApiError } from '../../api/http'
import { fetchDeployment, type DeployTask } from '../../api/deployments'
import {
  cancelTask,
  fetchDisposition,
  fetchExecution,
  fetchTaskReport,
  issueExecPermits,
  listTasks,
  rejectTask,
  reviewTaskWithAi,
  takeoverApproval,
  type AiReview,
  type ExecutionView,
  type ExecOperation,
  type TaskInfo,
  type TaskReport,
} from '../../api/tasks'
import { commandFromArgv, splitCommand } from '../../utils/command'
import { downloadCsv, exportStamp, MAX_EXPORT_ROWS, type CsvColumn } from '../../utils/csv'
import { formatLocalTime } from '../../utils/time'

export function useApprovals() {
  const POLL_INTERVAL_MS = 2000
  const POLL_TIMEOUT_MS = 2 * 60 * 1000

  interface OpRow {
    commandText: string
    scope: 'read' | 'dangerous'
  }

  const me = ref<MeInfo | null>(null)
  const route = useRoute()
  const tasks = ref<TaskInfo[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref('')
  const notice = ref('')
  const router = useRouter()
  const queueQuery = ref('')
  const typeFilters = ref<string[]>([])
  const submitterFilter = ref('')
  const queueRange = ref<'all' | 'today' | '7d' | '30d'>('all')
  const approvalState = ref<'pending' | 'approved' | 'rejected' | 'all'>('pending')
  const reviewedTasks = ref<TaskInfo[]>([])
  const reviewedTotal = ref(0)
  const reviewedLoaded = ref(false)
  const reviewedLoading = ref(false)
  const exporting = ref(false)
  const typeOptions = [
    { value: 'investigation', label: '排查处置' },
    { value: 'deployment', label: '部署审批' },
  ]
  const approvalStateOptions = [
    { value: 'pending', label: '待审批' },
    { value: 'approved', label: '已通过' },
    { value: 'rejected', label: '已驳回' },
    { value: 'all', label: '全部状态' },
  ]

  const baseTasks = computed(() => {
    if (approvalState.value === 'pending') return tasks.value
    if (approvalState.value === 'approved') {
      return reviewedTasks.value.filter((task) => task.outcome !== 'rejected' && task.outcome !== 'cancelled')
    }
    if (approvalState.value === 'rejected') {
      return reviewedTasks.value.filter((task) => task.outcome === 'rejected')
    }
    return [...tasks.value, ...reviewedTasks.value]
  })
  /** 页面数字用服务端 total，避免只展示前 20/50 条时把总数说小。 */
  const baseTotal = computed(() => {
    if (approvalState.value === 'pending') return total.value
    if (approvalState.value === 'approved' || approvalState.value === 'rejected') {
      return reviewedTotal.value
    }
    return total.value + reviewedTotal.value
  })
  const submitterOptions = computed(() => {
    const labels = new Map<string, string>()
    for (const task of baseTasks.value) {
      const id = String(task.submitted_by || '').trim()
      if (!id) continue
      const name = (task.submitted_by_name || '').trim()
      if (!labels.has(id)) labels.set(id, name || `用户 #${id}`)
    }
    return Array.from(labels, ([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
  })
  const queueFiltersActive = computed(
    () => Boolean(
      queueQuery.value.trim()
      || typeFilters.value.length
      || submitterFilter.value
      || queueRange.value !== 'all'
      || approvalState.value !== 'pending',
    ),
  )
  function matchesQueueFilters(task: TaskInfo): boolean {
    const query = queueQuery.value.trim().toLowerCase()
    if (query) {
      const haystack = `${task.title || ''} ${task.id} ${task.submitted_by || ''} ${task.submitted_by_name || ''}`.toLowerCase()
      if (!haystack.includes(query)) return false
    }
    if (typeFilters.value.length && !typeFilters.value.includes(task.task_type)) return false
    if (submitterFilter.value && String(task.submitted_by) !== submitterFilter.value) return false
    if (queueRange.value !== 'all') {
      const created = task.created_at ? new Date(task.created_at) : null
      if (!created || Number.isNaN(created.getTime())) return false
      const now = new Date()
      if (queueRange.value === 'today') {
        const start = new Date(now.getFullYear(), now.getMonth(), now.getDate())
        if (created < start) return false
      } else {
        const days = queueRange.value === '7d' ? 7 : 30
        const start = new Date(now.getTime() - days * 24 * 60 * 60 * 1000)
        if (created < start) return false
      }
    }
    return true
  }

  const filteredTasks = computed(() => baseTasks.value.filter(matchesQueueFilters))

  async function loadReviewed(): Promise<void> {
    if (reviewedLoaded.value || reviewedLoading.value) return
    reviewedLoading.value = true
    try {
      const data = await fetchAllByStatus('closed')
      reviewedTasks.value = data.items
      reviewedTotal.value = data.total
      reviewedLoaded.value = true
    } catch (err) {
      error.value = err instanceof ApiError ? err.message : '审批历史加载失败'
    } finally {
      reviewedLoading.value = false
    }
  }

  function onApprovalStateChange(): void {
    if (approvalState.value !== 'pending') void loadReviewed()
  }

  function resetQueueFilters() {
    queueQuery.value = ''
    typeFilters.value = []
    submitterFilter.value = ''
    queueRange.value = 'all'
    approvalState.value = 'pending'
  }

  async function fetchAllByStatus(
    status: string,
  ): Promise<{ items: TaskInfo[]; total: number; truncated: boolean }> {
    const all: TaskInfo[] = []
    let page = 1
    let serverTotal = 0
    // 按服务端 total 拉全；50,000 条只是前端安全上限，触发时明确提示。
    while (all.length < MAX_EXPORT_ROWS) {
      const result = await listTasks(status, page, 50)
      serverTotal = result.total
      all.push(...result.items)
      if (all.length >= serverTotal || result.items.length === 0) break
      page += 1
    }
    return { items: all, total: serverTotal, truncated: all.length < serverTotal }
  }

  const exportColumns: CsvColumn<TaskInfo>[] = [
    { label: '任务 ID', value: (task) => task.id },
    { label: '标题', value: (task) => task.title },
    { label: '类型', value: (task) => task.task_type === 'deployment' ? '部署审批' : '排查处置' },
    { label: '审批状态', value: (task) => task.status === 'awaiting_approval' ? '待审批' : (task.outcome || task.status_text || '') },
    { label: '提交人', value: (task) => task.submitted_by_name || task.submitted_by },
    { label: '创建时间', value: (task) => task.created_at ? formatLocalTime(task.created_at) : '' },
    { label: '关闭时间', value: (task) => task.closed_at ? formatLocalTime(task.closed_at) : '' },
    { label: '结果', value: (task) => task.outcome || '' },
  ]

  /** 按当前筛选导出审批队列；待审批和审批历史都逐页拉全量。 */
  async function exportApprovals() {
    if (exporting.value) return
    exporting.value = true
    error.value = ''
    notice.value = ''
    try {
      let source: TaskInfo[] = []
      let sourceTotal = 0
      let truncated = false
      if (approvalState.value === 'pending') {
        const pending = await fetchAllByStatus('awaiting_approval')
        source = pending.items
        sourceTotal = pending.total
        truncated = pending.truncated
      } else {
        const closed = await fetchAllByStatus('closed')
        sourceTotal = closed.total
        truncated = closed.truncated
        if (approvalState.value === 'approved') {
          source = closed.items.filter((task) => task.outcome !== 'rejected' && task.outcome !== 'cancelled')
        } else if (approvalState.value === 'rejected') {
          source = closed.items.filter((task) => task.outcome === 'rejected')
        } else {
          const pending = await fetchAllByStatus('awaiting_approval')
          source = [...pending.items, ...closed.items]
          sourceTotal += pending.total
          truncated = truncated || pending.truncated
        }
      }
      const rows = source.filter(matchesQueueFilters)
      if (!rows.length) {
        error.value = '当前筛选没有可导出的审批记录'
        return
      }
      downloadCsv(`审批记录-${exportStamp()}.csv`, exportColumns, rows)
      notice.value = truncated
        ? `已导出 ${rows.length} 条，服务端共 ${sourceTotal} 条；达到 ${MAX_EXPORT_ROWS} 条安全上限`
        : `已导出 ${rows.length} 条审批记录`
    } catch (err) {
      error.value = err instanceof Error ? err.message : '导出失败'
    } finally {
      exporting.value = false
    }
  }

  const selected = ref<TaskInfo | null>(null)
  const report = ref<TaskReport | null>(null)
  const reportLoading = ref(false)
  const deploymentPlan = ref<DeployTask | null>(null)
  const deploymentPlanLoading = ref(false)
  const isDeployment = computed(() => selected.value?.task_type === 'deployment')
  const screenshotEvidence = computed(() =>
    (report.value?.evidences || []).filter((item) => item.source === 'manual_screenshot'),
  )
  const hasReviewContent = computed(() => {
    const current = report.value
    if (!current) return false
    return Boolean(
      current.headline?.trim()
        || current.summary?.trim()
        || current.conclusions_count
        || current.evidences?.length,
    )
  })
  const platform = ref<'linux' | 'windows'>('linux')
  const hostId = ref('local')
  const rows = ref<OpRow[]>([{ commandText: '', scope: 'read' }])
  const jsonMode = ref(false)
  const ttlS = ref(600)
  const reason = ref('')
  const stepUpCode = ref('')
  const draftReason = ref('')
  const busy = ref(false)

  const commandTemplates = [
    { label: '看负载', command: 'uptime' },
    { label: '看磁盘', command: 'df -h' },
    { label: '看内存', command: 'free -m' },
    { label: '看端口', command: 'ss -lntp' },
    { label: '看服务', command: 'systemctl status nginx' },
    { label: '看容器', command: 'docker ps' },
    { label: '看 Pod', command: 'kubectl get pods -A' },
  ]

  const execution = ref<ExecutionView | null>(null)
  const aiReview = ref<AiReview | null>(null)
  const aiBusy = ref(false)
  const activeTab = ref<'review' | 'command' | 'execution'>('review')
  const submitted = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null
  let deadline = 0

  const isAdmin = computed(() => (me.value?.roles ?? []).includes('admin'))
  const isSelfSubmitted = computed(
    () => Boolean(selected.value && me.value && String(selected.value.submitted_by) === String(me.value.id)),
  )
  /** 自审批开关开着、且当前账号有角色豁免时，才允许同人闭环。 */
  const canSelfApproveNow = computed(
    () => Boolean(me.value?.self_approval_switch && me.value?.can_self_approve),
  )
  /** 默认禁止同人自批；只有开关+角色豁免时放行。 */
  const separationBlocked = computed(
    () => Boolean(selected.value
      && selected.value.status === 'awaiting_approval'
      && isSelfSubmitted.value
      && !canSelfApproveNow.value),
  )
  const approvalTimeline = computed(() => {
    const task = selected.value
    if (!task) return []
    return [
      {
        key: 'submit',
        label: '提交',
        time: task.created_at ? formatTime(task.created_at) : '—',
        actor: task.submitted_by_name || (task.submitted_by ? `用户 #${task.submitted_by}` : '—'),
      },
      {
        key: 'approve',
        label: task.outcome === 'rejected' ? '驳回' : '审批',
        time: task.approved_at ? formatTime(task.approved_at) : (task.status === 'awaiting_approval' ? '待审批' : '—'),
        actor: task.confirmed_by_name || (task.confirmed_by ? `用户 #${task.confirmed_by}` : '—'),
      },
      {
        key: 'close',
        label: '关闭',
        time: task.closed_at ? formatTime(task.closed_at) : (task.status === 'closed' ? '—' : '未关闭'),
        actor: task.outcome || task.status_text || '',
      },
    ]
  })
  const inputPlaceholder = computed(() =>
    jsonMode.value ? '["systemctl","status","nginx"]' : '例如 systemctl status nginx',
  )

  onMounted(async () => {
    me.value = await getOrLogin<MeInfo>('/auth/me')
    await loadList()
    // 通知铃铛深链：`/#/approvals?task=<id>` 直接打开对应待审批任务。
    const deepTask = route.query.task
    if (typeof deepTask === 'string' && deepTask) {
      const hit = tasks.value.find((item) => item.id === deepTask)
      if (hit) await selectTask(hit)
    }
  })

  onUnmounted(stopPolling)

  async function loadList() {
    loading.value = true
    error.value = ''
    try {
      const data = await fetchAllByStatus('awaiting_approval')
      tasks.value = data.items
      total.value = data.total
    } catch (err) {
      error.value = err instanceof Error ? err.message : '排查列表加载失败'
    } finally {
      loading.value = false
    }
    // 审批动作会改变 pending/closed 的分布；下次切到历史时重新拉全量，
    // 不用旧的 50 条快照冒充当前总数。
    reviewedLoaded.value = false
  }

  async function selectTask(task: TaskInfo) {
    selected.value = task
    report.value = null
    execution.value = null
    aiReview.value = null
    deploymentPlan.value = null
    deploymentPlanLoading.value = false
    notice.value = ''
    error.value = ''
    activeTab.value = 'review'
    submitted.value = false
    rows.value = [{ commandText: '', scope: 'read' }]
    platform.value = 'linux'
    hostId.value = 'local'
    reason.value = ''
    stepUpCode.value = ''
    draftReason.value = ''

    if (task.task_type === 'deployment') {
      // 部署任务没有排查报告；审批对象是模板生成的部署方案。
      reportLoading.value = false
      deploymentPlanLoading.value = true
      try {
        const loadedPlan = await fetchDeployment(task.id)
        if (selected.value?.id !== task.id) return
        deploymentPlan.value = loadedPlan
        const plan = loadedPlan.plan
        if (plan?.platform === 'linux' || plan?.platform === 'windows') {
          platform.value = plan.platform
        }
        hostId.value = plan?.host || 'local'
      } catch (err) {
        if (selected.value?.id === task.id) {
          error.value = err instanceof Error ? err.message : '部署方案加载失败'
        }
      } finally {
        if (selected.value?.id === task.id) deploymentPlanLoading.value = false
      }
      return
    }

    reportLoading.value = true
    try {
      const loadedReport = await fetchTaskReport(task.id)
      if (selected.value?.id !== task.id) return
      report.value = loadedReport
    } catch {
      // 报告可能还没写入数据库，不阻断审批
    } finally {
      if (selected.value?.id === task.id) reportLoading.value = false
    }
    try {
      const draft = await fetchDisposition(task.id)
      if (selected.value?.id !== task.id) return
      if (draft.operations?.length) {
        rows.value = draft.operations.map((operation) => ({
          commandText: commandFromArgv(operation.argv),
          scope: operation.scope,
        }))
        if (draft.platform === 'linux' || draft.platform === 'windows') {
          platform.value = draft.platform
        }
        hostId.value = draft.host || 'local'
        draftReason.value = draft.reason || ''
      }
    } catch {
      // 旧任务可能没有处置方案；保留空行，管理员可手工补。
    }
  }

  function goDeployments(): void {
    void router.push({ name: 'deployments', query: { tab: 'records' } })
  }

  function closeDetail() {
    selected.value = null
    report.value = null
    aiReview.value = null
    deploymentPlan.value = null
    deploymentPlanLoading.value = false
    activeTab.value = 'review'
    submitted.value = false
    reportLoading.value = false
  }

  function addRow(command = 'hostname') {
    rows.value.push({ commandText: command, scope: 'read' })
  }

  function removeRow(index: number) {
    rows.value.splice(index, 1)
  }

  function parseJsonRow(line: string, index: number): string[] {
    let parsed: unknown
    try {
      parsed = JSON.parse(line)
    } catch {
      throw new Error(`第 ${index + 1} 行不是合法 JSON 数组`)
    }
    if (!Array.isArray(parsed) || parsed.length === 0) {
      throw new Error(`第 ${index + 1} 行必须是数组，例如 ["systemctl","status","nginx"]`)
    }
    if (parsed.some((item) => typeof item !== 'string' || !item.trim())) {
      throw new Error(`第 ${index + 1} 行的每个参数都必须是非空字符串`)
    }
    return parsed as string[]
  }

  function parseOperations(): ExecOperation[] {
    if (!rows.value.length) throw new Error('至少录入一条命令')
    return rows.value.map((row, index) => {
      const argv = jsonMode.value
        ? parseJsonRow(row.commandText, index)
        : splitCommand(row.commandText)
      return { argv, scope: row.scope }
    })
  }

  function toggleJsonMode() {
    error.value = ''
    try {
      rows.value = rows.value.map((row, index) => ({
        ...row,
        commandText: jsonMode.value
          ? JSON.stringify(splitCommand(row.commandText))
          : commandFromArgv(parseJsonRow(row.commandText, index)),
      }))
    } catch (err) {
      jsonMode.value = !jsonMode.value
      error.value = err instanceof Error ? err.message : '切换高级模式失败'
    }
  }

  async function handleAiReview() {
    if (!selected.value) return
    error.value = ''
    notice.value = ''
    let operations: ExecOperation[]
    try {
      operations = parseOperations()
    } catch (err) {
      error.value = err instanceof Error ? err.message : '命令格式不对'
      return
    }
    aiBusy.value = true
    aiReview.value = null
    try {
      aiReview.value = await reviewTaskWithAi(
        selected.value.id, platform.value, operations, hostId.value.trim() || 'local',
      )
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'AI 预审失败'
    } finally {
      aiBusy.value = false
    }
  }

  function approvalErrorText(err: unknown, fallback: string): string {
    const raw = err instanceof Error ? err.message : fallback
    if (raw.includes('self_approval_not_allowed')) {
      return '职责分离：不能审批自己提交的任务。请让其他管理员审批；确需自批，请管理员在配置页开启“自审批豁免”。'
    }
    return raw
  }

  async function handleApprove() {
    if (!selected.value) return
    if (separationBlocked.value) {
      error.value = '职责分离：这是你提交的任务，默认不能自批；请让其他管理员审批。'
      return
    }
    error.value = ''
    notice.value = ''
    let operations: ExecOperation[]
    try {
      operations = parseOperations()
    } catch (err) {
      error.value = err instanceof Error ? err.message : '命令格式不对'
      return
    }
    const needsStepUp = operations.some((op) => op.scope === 'dangerous')
    if (needsStepUp && !stepUpCode.value.trim()) {
      error.value = '含高危操作，请输入当前账号的 6 位动态口令'
      return
    }
    busy.value = true
    try {
      const ack = await issueExecPermits(
        selected.value.id, platform.value, operations, ttlS.value, hostId.value.trim() || 'local',
        reason.value.trim(),
        stepUpCode.value.trim(),
      )
      stepUpCode.value = ''
      notice.value = `已签发 ${ack.items.length} 条许可，排查进入执行；正在等待结果…`
      submitted.value = true
      activeTab.value = 'execution'
      await loadList()
      startPolling(ack.task_id)
    } catch (err) {
      error.value = approvalErrorText(err, '审批失败')
    } finally {
      busy.value = false
    }
  }

  async function handleTakeover() {
    if (!selected.value) return
    error.value = ''
    busy.value = true
    try {
      await takeoverApproval(
        selected.value.id,
        reason.value.trim() || '管理员接管超时审批',
      )
      notice.value = '已接管。接管记录已写入审计；批准仍按原审批入口执行。'
      await loadList()
    } catch (err) {
      error.value = approvalErrorText(err, '接管失败')
    } finally {
      busy.value = false
    }
  }

  async function handleReject() {
    if (!selected.value) return
    error.value = ''
    busy.value = true
    try {
      const rejectReason = reason.value.trim()
        || (hasReviewContent.value ? '' : '无结论与证据，未批准执行')
      await rejectTask(selected.value.id, rejectReason)
      notice.value = '已驳回'
      selected.value = null
      report.value = null
      await loadList()
    } catch (err) {
      error.value = approvalErrorText(err, '驳回失败')
    } finally {
      busy.value = false
    }
  }

  async function handleCancel() {
    if (!selected.value) return
    error.value = ''
    busy.value = true
    try {
      await cancelTask(selected.value.id, reason.value)
      notice.value = '已撤回'
      selected.value = null
      report.value = null
      await loadList()
    } catch (err) {
      error.value = err instanceof Error ? err.message : '撤回失败'
    } finally {
      busy.value = false
    }
  }

  function startPolling(taskId: string) {
    stopPolling()
    deadline = Date.now() + POLL_TIMEOUT_MS
    const once = async () => {
      try {
        execution.value = await fetchExecution(taskId)
      } catch (err) {
        stopPolling()
        error.value = err instanceof Error ? err.message : '执行结果查询失败'
        return
      }
      if (execution.value?.finished_at) {
        stopPolling()
        notice.value = `执行完成：${execution.value.outcome ?? ''}`
        return
      }
      if (Date.now() > deadline) {
        stopPolling()
        notice.value = '2 分钟内没等到结果：排查可能还在队列，请确认队列 worker 在跑'
      }
    }
    void once()
    timer = setInterval(() => void once(), POLL_INTERVAL_MS)
  }

  function stopPolling() {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  function taskKind(task: TaskInfo): string {
    return task.task_type === 'deployment' ? '部署方案' : '排查处置'
  }

  function formatTime(iso: string | null | undefined): string {
    return formatLocalTime(iso)
  }

  function elapsedText(start: string | null | undefined, end?: string | null): string {
    if (!start) return ''
    const from = Date.parse(start)
    if (!Number.isFinite(from)) return ''
    const to = end ? Date.parse(end) : Date.now()
    if (!Number.isFinite(to) || to <= from) return ''
    const minutes = Math.floor((to - from) / 60_000)
    if (minutes < 1) return '不到 1 分钟'
    if (minutes < 60) return `${minutes} 分钟`
    const hours = Math.floor(minutes / 60)
    if (hours < 24) return `${hours} 小时`
    return `${Math.floor(hours / 24)} 天`
  }

  function waitingText(task: TaskInfo): string {
    if (task.status !== 'awaiting_approval') return ''
    const elapsed = elapsedText(task.created_at)
    return elapsed ? `已等待 ${elapsed}` : ''
  }

  function handledText(task: TaskInfo): string {
    if (task.status === 'awaiting_approval' || !task.closed_at) return ''
    const elapsed = elapsedText(task.created_at, task.closed_at)
    return elapsed ? `提交到关闭 ${elapsed}` : ''
  }

  function ownerText(task: TaskInfo): string {
    if (task.status !== 'awaiting_approval') return ''
    const name = task.assignee?.display_name || task.assignee?.username
    return name ? `待 ${name} 审批` : '待审批（未指派）'
  }

  function overdueText(task: TaskInfo): string {
    if (task.status !== 'awaiting_approval' || !task.approval_expires_at) return ''
    const expires = Date.parse(task.approval_expires_at)
    if (!Number.isFinite(expires)) return ''
    const remaining = expires - Date.now()
    if (remaining < 0) return '已超时未处理'
    if (remaining <= 5 * 60_000) return '即将超时'
    return ''
  }

  const EXEC_REASON_TEXT: Record<string, string> = {
    not_allowlisted: '命令不在允许清单内，执行器拒绝执行',
    dangerous_requires_confirmation: '危险命令缺少人工确认，执行器拒绝执行',
    permit_not_issued: '没有签发对应的执行许可',
    permit_scope_too_low: '执行许可权限不足',
    exit_code_nonzero: '命令退出码非 0',
    timeout: '命令执行超时',
    rollback_failed: '回滚命令失败',
  }

  function resultReason(item: Record<string, unknown>): string {
    const raw = String(item.reason || '')
    const code = raw === 'not_allowedlist' ? 'not_allowlisted' : raw
    if (!code) return ''
    const text = EXEC_REASON_TEXT[code]
    return text ? `${code} · ${text}` : code
  }

  return {
    activeTab,
    addRow,
    aiBusy,
    aiReview,
    approvalState,
    approvalStateOptions,
    approvalTimeline,
    baseTasks,
    baseTotal,
    busy,
    closeDetail,
    commandTemplates,
    deploymentPlan,
    deploymentPlanLoading,
    draftReason,
    error,
    execution,
    exportApprovals,
    exporting,
    filteredTasks,
    formatTime,
    goDeployments,
    handleAiReview,
    handleApprove,
    handleCancel,
    handleReject,
    handleTakeover,
    handledText,
    hasReviewContent,
    hostId,
    inputPlaceholder,
    isAdmin,
    isDeployment,
    isSelfSubmitted,
    jsonMode,
    loading,
    me,
    notice,
    onApprovalStateChange,
    overdueText,
    ownerText,
    platform,
    queueFiltersActive,
    queueQuery,
    queueRange,
    reason,
    removeRow,
    report,
    reportLoading,
    resetQueueFilters,
    resultReason,
    reviewedLoading,
    rows,
    screenshotEvidence,
    selectTask,
    selected,
    stepUpCode,
    separationBlocked,
    submitted,
    submitterFilter,
    submitterOptions,
    taskKind,
    toggleJsonMode,
    total,
    ttlS,
    typeFilters,
    typeOptions,
    waitingText,
  }
}
