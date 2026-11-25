/**
 * 任务详情页：把一条任务从“提交”到“收尾”摊开。
 *
 * 这一页只读，不重复审批动作；审批仍在审批中心。
 * 关键是把报告结论和证据快照放在同一屏：
 * 结论里引用的 evidence_id 能点回下面的证据原文，避免“只有结论没有依据”。
 */

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  addTaskComment,
  assignTask,
  cancelTask,
  deleteTaskComment,
  exportTaskReport,
  fetchAssignableUsers,
  fetchExecution,
  fetchTask,
  fetchTaskComments,
  fetchTaskReport,
  fetchTaskTrace,
  fetchReportFeedback,
  markTaskCommentsRead,
  submitReportFeedback,
  terminateExecution,
  type AssignableUser,
  type ExecutionView,
  type ReportFeedback,
  type ReportFeedbackTarget,
  type ReportFeedbackVerdict,
  type TaskComment,
  type TaskCommentCursor,
  type TaskInfo,
  type TaskReport,
  type TaskTraceItem,
} from '../../api/tasks'
import { fetchMe, type MeInfo } from '../../api/auth'
import { markTaskNotificationsRead } from '../../api/notifications'
import { ApiError } from '../../api/http'
import { formatLocalTime } from '../../utils/time'
import { useTaskDetailView } from './useTaskDetailView'

export function useTaskDetail() {
  const route = useRoute()
  const router = useRouter()
  const taskId = computed(() => String(route.params.id || ''))
  type DetailTab = 'summary' | 'solution' | 'evidence' | 'process'
  const DETAIL_TABS: DetailTab[] = ['summary', 'solution', 'evidence', 'process']
  function normalizeDetailTab(value: unknown): DetailTab {
    const raw = Array.isArray(value) ? value[0] : value
    return DETAIL_TABS.includes(raw as DetailTab) ? (raw as DetailTab) : 'summary'
  }
  const activeTab = ref<DetailTab>(normalizeDetailTab(route.query.tab))
  let tabExplicit = Boolean(route.query.tab)

  function selectDetailTab(tab: DetailTab, resetScroll = true): void {
    tabExplicit = true
    activeTab.value = tab
    const query = { ...route.query }
    if (tab === 'summary') delete query.tab
    else query.tab = tab
    void router.replace({ query })
    if (resetScroll) window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  watch(
    () => route.query.tab,
    (value) => {
      if (value) tabExplicit = true
      activeTab.value = normalizeDetailTab(value)
    },
  )
  const task = ref<TaskInfo | null>(null)
  const report = ref<TaskReport | null>(null)
  const detailView = useTaskDetailView(report)
  const execution = ref<ExecutionView | null>(null)
  const me = ref<MeInfo | null>(null)
  const comments = ref<TaskComment[]>([])
  const commentsTruncated = ref(false)
  const commentsHasMore = ref(false)
  const commentsCursor = ref<TaskCommentCursor | null>(null)
  const commentUnreadNotice = ref(0)
  const commentBody = ref('')
  const commentKind = ref<'discussion' | 'clue'>('discussion')
  const commentLoading = ref(false)
  const commentSaving = ref(false)
  const commentDeletingId = ref('')
  const commentError = ref('')
  let commentSeq = 0
  const reportFeedback = ref<ReportFeedback[]>([])
  const feedbackSaving = ref('')
  const feedbackError = ref('')
  const assignableUsers = ref<AssignableUser[]>([])
  const selectedAssigneeId = ref<number | null>(null)
  const assigning = ref(false)
  const assignError = ref('')
  const loading = ref(true)
  const error = ref('')
  const exporting = ref<'' | 'md' | 'pdf' | 'docx'>('')
  const exportError = ref('')
  const canceling = ref(false)
  const terminating = ref(false)
  const confirmingWithdraw = ref(false)
  const confirmingTerminate = ref(false)
  const actionError = ref('')
  const actionNotice = ref('')
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let markedNotificationTask = ''

  const REPORT_NOTIFICATION_STATUSES = new Set([
    'report_ready',
    'awaiting_approval',
    'executing',
    'closed',
  ])

  async function markReportNotificationsRead(): Promise<void> {
    const id = taskId.value
    const current = task.value
    if (!id || !current || current.task_type === 'deployment') return
    if (!REPORT_NOTIFICATION_STATUSES.has(current.status)) return
    if (markedNotificationTask === id) return
    markedNotificationTask = id
    try {
      await markTaskNotificationsRead(id)
      window.dispatchEvent(new Event('ops:notifications-read'))
    } catch {
      // 通知状态更新失败不能影响报告阅读；铃铛下次轮询会自行对齐。
    }
  }

  const isAdmin = computed(() => (me.value?.roles ?? []).includes('admin'))
  const isDeployment = computed(() => task.value?.task_type === 'deployment')
  const liveTrace = ref<TaskTraceItem[]>([])
  let liveTraceCursor = 0
  let loadSeq = 0
  const trace = computed(() => {
    const reportTrace = report.value?.trace ?? []
    return reportTrace.length ? reportTrace : liveTrace.value
  })
  const traceIsLive = computed(() => {
    const status = task.value?.status
    return (status === 'draft' || status === 'running') && liveTrace.value.length > 0
  })
  const isInvestigating = computed(() => {
    const status = task.value?.status
    return status === 'draft' || status === 'running'
  })
  const traceLabel: Record<string, string> = {
    thinking: '思考',
    tool_call: '调用工具',
    tool_result: '工具结果',
    text: '模型说明',
    step: '步骤',
    error: '失败',
  }
  const canDispose = computed(() => (me.value?.roles ?? []).some((role) => (
    ['admin', 'investigator', 'oncall'].includes(role)
  )))
  /** 讨论只给能处理任务的角色写；审计员可参与，viewer 只能看。 */
  const canComment = computed(() => (me.value?.roles ?? []).some((role) => (
    ['admin', 'investigator', 'auditor', 'oncall'].includes(role)
  )))
  const canAssign = computed(() => {
    const current = task.value
    if (!current || current.status === 'closed') return false
    if (isAdmin.value) return true
    const uid = me.value?.id
    const assigneeId = current.assignee?.id
    return Boolean(uid && assigneeId && Number(uid) === Number(assigneeId))
  })
  const canDeleteComment = (item: TaskComment): boolean => (
    isAdmin.value || Number(me.value?.id) === Number(item.author_id)
  )
  const assigneeLabel = computed(() => {
    const assignee = task.value?.assignee
    if (!assignee) return ''
    return assignee.display_name || assignee.username || `用户 ${assignee.id}`
  })

  const statusLabels: Record<string, string> = {
    draft: '已提交',
    running: '排查中',
    report_ready: '报告已生成',
    awaiting_approval: '处置待审批',
    executing: '命令执行中',
    closed: '排查已结束',
  }

  const outcomeLabels: Record<string, string> = {
    succeeded: '执行成功',
    failed: '执行失败',
    cancelled: '已取消',
    rejected: '未通过审批',
    terminated: '执行已终止',
  }

  const outcomeLabel = computed(() => {
    const outcome = task.value?.outcome
    if (!outcome) return ''
    return outcomeLabels[outcome] ?? outcome
  })

  const statusTone = computed(() => {
    const status = task.value?.status
    if (status === 'awaiting_approval') return 'badge-warn'
    if (status === 'running' || status === 'executing') return 'badge-info'
    if (status === 'report_ready') return 'badge-success'
    if (status === 'closed') {
      if (task.value?.outcome === 'failed') return 'badge-danger'
      return task.value?.outcome === 'rejected' || task.value?.outcome === 'cancelled'
        ? 'badge-danger'
        : 'badge-success'
    }
    return 'badge-muted'
  })

  const statusBadge = computed(() => {
    const current = task.value
    if (!current) return { label: '加载中', tone: 'badge-muted' }
    if (current.status === 'closed' && current.outcome === 'failed') {
      return { label: '执行失败', tone: 'badge-danger' }
    }
    return {
      label: statusLabels[current.status] ?? current.status,
      tone: statusTone.value,
    }
  })

  const aiLabel = computed(() => {
    const model = report.value?.generated_by?.trim()
    return model ? `AI 归纳 · ${model}` : 'AI 归纳草稿'
  })

  const knowledge = computed(() => {
    const seen = new Set<string>()
    return (report.value?.knowledge ?? []).filter((item) => {
      if (seen.has(item.title)) return false
      seen.add(item.title)
      return true
    }).slice(0, 3)
  })

  function knowledgeVerdictLabel(verdict: string): string {
    if (verdict === 'no_anomaly') return '当时未发现异常'
    if (verdict === 'anomaly') return '当时发现异常'
    if (verdict === 'insufficient') return '当时证据不足'
    return '历史判断'
  }

  function knowledgeScore(score: number): string {
    if (!Number.isFinite(score)) return '相似度 —'
    return `相似度 ${Math.round(score * 100)}%`
  }

  const executionResults = computed(() => execution.value?.results ?? [])

  const processCount = computed(() => trace.value.length + knowledge.value.length)
  const showDetailTabs = computed(() =>
    Boolean(report.value || trace.value.length || executionResults.value.length),
  )

  watch(
    () => [Boolean(report.value), trace.value.length, executionResults.value.length] as const,
    () => {
      if (tabExplicit) return
      if (!report.value && (trace.value.length || executionResults.value.length)) {
        activeTab.value = 'process'
      } else if (report.value) {
        activeTab.value = 'summary'
      }
    },
    { immediate: true },
  )

  const nextAction = computed(() => {
    const status = task.value?.status ?? 'draft'
    if (status === 'draft' || status === 'running') {
      return {
        tone: 'info',
        title: 'Agent 正在排查',
        text: '页面会自动刷新。报告生成后就结束，不会自动进入审批。',
        cta: '',
        action: '',
      }
    }
    if (status === 'report_ready') {
      return {
        tone: 'success',
        title: '报告已生成',
        text: '报告和证据已保存，排查到此结束。需要改机器时，另开一份处置方案，由人填写命令后再审批。',
        cta: '',
        action: '',
      }
    }
    if (status === 'awaiting_approval') {
      return {
        tone: 'warn',
        title: '处置方案待审批',
        text: isAdmin.value
          ? '报告和命令都在审批页；核对命令后批准，批准前不会执行。'
          : '等待管理员审批处置方案；批准前不会执行。',
        cta: isAdmin.value ? '去审批处置方案' : '',
        action: 'approval',
      }
    }
    if (status === 'executing') {
      return {
        tone: 'info',
        title: '命令正在执行',
        text: '页面会自动刷新执行结果；管理员可终止执行，终止后不再等执行器返回。',
        cta: '',
        action: '',
      }
    }
    if (status === 'closed') {
      const terminated = Boolean(execution.value?.terminated_at)
      const failed = task.value?.outcome === 'failed'
      return {
        tone: task.value?.outcome === 'cancelled' || task.value?.outcome === 'rejected' || failed ? 'danger' : 'success',
        title: terminated
          ? '执行已终止'
          : failed
            ? '查看失败原因'
            : task.value?.outcome === 'cancelled'
              ? '这次排查已撤回'
              : task.value?.outcome === 'rejected'
                ? '这次排查未通过审批'
                : '这次排查已结束',
        text: terminated
          ? '命令可能已产生部分影响。展开“执行结果”查看返回码和输出；报告、证据和审计记录都已保留。'
          : failed
            ? '命令执行没有成功。展开“执行结果”查看返回码和输出；报告、证据和审计记录都已保留，任务不会自动重试。'
            : '报告、证据和执行结果都保留在本页，审计里能查到完整链路。',
        cta: terminated || failed ? '查看执行结果' : '',
        action: terminated || failed ? 'results' : '',
      }
    }
    return { tone: 'info', title: '加载中', text: '', cta: '', action: '' }
  })

  function formatTime(iso: string | null): string {
    return formatLocalTime(iso, true)
  }

  function evidenceAnchor(id: string): string {
    return `ev-${id.replace(/[^a-zA-Z0-9_-]/g, '-')}`
  }

  function scrollToEvidence(id: string): void {
    selectDetailTab('evidence', false)
    void nextTick(() => {
      const element = document.getElementById(evidenceAnchor(id))
      if (!element) return
      element.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
  }

  function formatArgv(item: Record<string, unknown>): string {
    const argv = item.argv
    return Array.isArray(argv) ? argv.join(' ') : String(argv ?? '')
  }

  function resultPreview(item: Record<string, unknown>): string {
    const preview = item.preview as { stdout?: string; stderr?: string } | null | undefined
    return preview?.stdout || preview?.stderr || '无回显'
  }

  async function downloadReport(format: 'md' | 'pdf' | 'docx'): Promise<void> {
    if (!task.value) return
    exporting.value = format
    exportError.value = ''
    try {
      const blob = await exportTaskReport(taskId.value, format)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `report-${taskId.value}.${format}`
      link.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      exportError.value = err instanceof ApiError ? err.message : '报告导出失败'
    } finally {
      exporting.value = ''
    }
  }

  function goApprovals(): void {
    void router.push('/approvals')
  }

  function scrollToExecution(): void {
    const element = document.querySelector('.execution-card')
    if (!(element instanceof HTMLDetailsElement)) return
    element.open = true
    element.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  function handleNextAction(): void {
    if (nextAction.value.action === 'approval') goApprovals()
    if (nextAction.value.action === 'results') scrollToExecution()
  }

  async function withdrawTask(): Promise<void> {
    if (!task.value || task.value.status !== 'awaiting_approval') return
    if (!confirmingWithdraw.value) {
      confirmingWithdraw.value = true
      actionError.value = ''
      actionNotice.value = ''
      return
    }
    canceling.value = true
    actionError.value = ''
    actionNotice.value = ''
    try {
      await cancelTask(taskId.value, '从任务详情页撤回')
      confirmingWithdraw.value = false
      actionNotice.value = '排查已撤回，等待刷新状态。'
      await load()
    } catch (err) {
      actionError.value = err instanceof ApiError ? err.message : '撤回失败'
    } finally {
      canceling.value = false
    }
  }

  async function terminateRun(): Promise<void> {
    if (!task.value || task.value.status !== 'executing' || !isAdmin.value) return
    if (!confirmingTerminate.value) {
      confirmingTerminate.value = true
      actionError.value = ''
      actionNotice.value = ''
      return
    }
    terminating.value = true
    actionError.value = ''
    actionNotice.value = ''
    try {
      const result = await terminateExecution(taskId.value, '管理员从任务详情页终止执行')
      confirmingTerminate.value = false
      actionNotice.value = result.lease_revoked
        ? '已终止执行并撤销队列租约。已发出的命令可能已产生部分影响，请核对审计与执行结果。'
        : '已终止执行。队列行已结束或已失效，已发出的命令可能已产生部分影响，请核对审计与执行结果。'
      await load()
    } catch (err) {
      actionError.value = err instanceof ApiError ? err.message : '终止失败'
    } finally {
      terminating.value = false
    }
  }

  function myReportFeedback(
    target: ReportFeedbackTarget,
    index: number,
  ): ReportFeedback | undefined {
    const uid = me.value?.id
    if (!uid) return undefined
    return reportFeedback.value.find((item) => (
      item.target_type === target
      && Number(item.target_index) === index
      && Number(item.created_by) === Number(uid)
    ))
  }

  async function loadReportFeedback(id: string): Promise<void> {
    try {
      const loaded = await fetchReportFeedback(id)
      if (taskId.value === id) reportFeedback.value = loaded.items
    } catch {
      // 反馈是报告阅读的辅助信息；接口失败不影响正文。
    }
  }

  async function sendReportFeedback(payload: {
    targetType: ReportFeedbackTarget
    targetIndex: number
    verdict: ReportFeedbackVerdict
    correctedText: string
    note: string
  }): Promise<void> {
    const id = taskId.value
    if (!id || feedbackSaving.value) return
    const key = `${payload.targetType}-${payload.targetIndex}`
    feedbackSaving.value = key
    feedbackError.value = ''
    try {
      const saved = await submitReportFeedback(
        id,
        payload.targetType,
        payload.targetIndex,
        payload.verdict,
        payload.correctedText,
        payload.note,
      )
      if (taskId.value !== id) return
      reportFeedback.value = [
        ...reportFeedback.value.filter((item) => !(
          Number(item.created_by) === Number(saved.created_by)
          && item.target_type === saved.target_type
          && Number(item.target_index) === Number(saved.target_index)
        )),
        saved,
      ]
    } catch (err) {
      feedbackError.value = err instanceof Error ? err.message : '反馈保存失败'
    } finally {
      if (taskId.value === id) feedbackSaving.value = ''
    }
  }

  async function loadComments(appendOlder = false): Promise<void> {    const requestedId = taskId.value
    const seq = ++commentSeq
    if (!requestedId) return
    commentLoading.value = true
    if (!appendOlder) commentError.value = ''
    try {
      const page = await fetchTaskComments(
        requestedId, appendOlder ? commentsCursor.value : null,
      )
      if (seq !== commentSeq || taskId.value !== requestedId) return
      if (appendOlder) {
        const known = new Set(comments.value.map((item) => item.id))
        const older = page.items.filter((item) => !known.has(item.id))
        comments.value = [...older, ...comments.value]
      } else {
        comments.value = page.items
        commentUnreadNotice.value = 0
        if (page.unread_count > 0) {
          try {
            await markTaskCommentsRead(requestedId)
            commentUnreadNotice.value = page.unread_count
          } catch {
            // 已读失败不影响查看；下次进页面会再算一次。
          }
        }
      }
      commentsHasMore.value = page.has_more
      commentsCursor.value = page.next_cursor
      commentsTruncated.value = page.truncated
    } catch (err) {
      if (seq !== commentSeq || taskId.value !== requestedId) return
      if (!appendOlder) {
        commentError.value = err instanceof ApiError ? err.message : '讨论加载失败'
      }
    } finally {
      if (seq === commentSeq) commentLoading.value = false
    }
  }

  async function loadAssignableUsers(): Promise<void> {
    if (!canAssign.value || !taskId.value) {
      assignableUsers.value = []
      return
    }
    try {
      const page = await fetchAssignableUsers(taskId.value)
      assignableUsers.value = page.items
      if (selectedAssigneeId.value === null && page.items.length === 1) {
        selectedAssigneeId.value = page.items[0].id
      }
    } catch {
      assignableUsers.value = []
    }
  }

  async function assignSelected(): Promise<void> {
    const target = selectedAssigneeId.value
    if (!taskId.value || !target) {
      assignError.value = '请选择要转派的人'
      return
    }
    assigning.value = true
    assignError.value = ''
    actionNotice.value = ''
    try {
      await assignTask(taskId.value, target)
      actionNotice.value = '任务已转派，新的处理人会收到通知。'
      selectedAssigneeId.value = null
      await load()
    } catch (err) {
      assignError.value = err instanceof ApiError ? err.message : '转派失败'
    } finally {
      assigning.value = false
    }
  }

  async function removeComment(item: TaskComment): Promise<void> {
    if (!canDeleteComment(item)) return
    if (!window.confirm('撤回这条讨论？正文会保留在数据库，列表不再显示。')) return
    commentDeletingId.value = item.id
    commentError.value = ''
    try {
      await deleteTaskComment(taskId.value, item.id)
      comments.value = comments.value.filter((entry) => entry.id !== item.id)
    } catch (err) {
      commentError.value = err instanceof ApiError ? err.message : '撤回失败'
    } finally {
      commentDeletingId.value = ''
    }
  }

  async function submitComment(): Promise<void> {
    const requestedId = taskId.value
    const text = commentBody.value.trim()
    if (!text) {
      commentError.value = '评论不能为空'
      return
    }
    if (text.length > 2000) {
      commentError.value = '评论最多 2000 字'
      return
    }
    commentSaving.value = true
    commentError.value = ''
    try {
      const created = await addTaskComment(requestedId, text, commentKind.value)
      if (taskId.value !== requestedId) return
      comments.value.push(created)
      commentBody.value = ''
      commentKind.value = 'discussion'
    } catch (err) {
      commentError.value = err instanceof ApiError ? err.message : '评论提交失败'
    } finally {
      commentSaving.value = false
    }
  }

  async function loadMe(): Promise<void> {
    try {
      me.value = await fetchMe()
    } catch {
      // 身份接口失败不阻断详情页；按钮按未登录处理，后端仍会二次鉴权。
      me.value = null
    }
    void loadAssignableUsers()
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function startPolling() {
    stopPolling()
    const status = task.value?.status
    if (status === 'draft' || status === 'running' || status === 'executing') {
      pollTimer = setInterval(() => { void load(true) }, 2500)
    }
  }

  async function loadLiveTrace(expectedId = taskId.value, seq = loadSeq): Promise<void> {
    const status = task.value?.status
    if (status !== 'draft' && status !== 'running') return
    try {
      const page = await fetchTaskTrace(expectedId, liveTraceCursor)
      if (seq !== loadSeq || taskId.value !== expectedId) return
      if (!page.events.length) return
      for (const event of page.events) {
        const item = event.payload?.item
        if (!item || typeof item !== 'object') continue
        liveTrace.value.push({
          ...item,
          created_at: item.created_at || event.created_at || undefined,
        })
      }
      liveTraceCursor = page.last_event_id
    } catch {
      // 轨迹是过程视图；拉取失败不把任务详情打成错误页，下一轮继续。
    }
  }

  async function load(silent = false) {
    const requestedId = taskId.value
    const seq = ++loadSeq
    if (!silent) {
      loading.value = true
      confirmingWithdraw.value = false
      confirmingTerminate.value = false
      report.value = null
      execution.value = null
      liveTrace.value = []
      liveTraceCursor = 0
      reportFeedback.value = []
      feedbackSaving.value = ''
      feedbackError.value = ''
      comments.value = []
      commentsTruncated.value = false
      commentsHasMore.value = false
      commentsCursor.value = null
      commentUnreadNotice.value = 0
      commentDeletingId.value = ''
      commentBody.value = ''
      commentError.value = ''
      void loadComments()
    }
    error.value = ''
    try {
      const loadedTask = await fetchTask(requestedId)
      if (seq !== loadSeq || taskId.value !== requestedId) return
      task.value = loadedTask
      void loadAssignableUsers()
    } catch (err) {
      if (seq !== loadSeq || taskId.value !== requestedId) return
      if (!silent) error.value = err instanceof Error ? err.message : '排查加载失败'
      // 瞬时失败不要停轮询；只有明确 404 才认为任务已不存在。
      if (!silent || (err instanceof ApiError && err.status === 404)) stopPolling()
      loading.value = false
      return
    }
    if (seq !== loadSeq || taskId.value !== requestedId) return
    await loadLiveTrace(requestedId, seq)
    if (seq !== loadSeq || taskId.value !== requestedId) return
    // 打开报告详情就把这条任务的通知标已读；轮询到 report_ready 时也会补一次，
    // 红点不必等用户再点一次通知。
    void markReportNotificationsRead()
    // 部署任务的报告在部署页「执行报告」里，不在这里拉排查报告/执行结果，
    // 避免两条业务线各拉一次、页面还互相串数据。
    if (task.value?.task_type === 'deployment') {
      loading.value = false
      return
    }
    try {
      const loadedReport = await fetchTaskReport(requestedId)
      if (seq !== loadSeq || taskId.value !== requestedId) return
      report.value = loadedReport
      void loadReportFeedback(requestedId)
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) {
        if (seq === loadSeq && !silent) {
          error.value = err instanceof Error ? err.message : '报告加载失败'
        }
      }
    }
    try {
      const loadedExecution = await fetchExecution(requestedId)
      if (seq !== loadSeq || taskId.value !== requestedId) return
      execution.value = loadedExecution
    } catch (err) {
      // 还没执行时 404 是正常态，不弹错。
      if (!(err instanceof ApiError && err.status === 404)) {
        if (seq === loadSeq && !silent) {
          error.value = err instanceof Error ? err.message : '执行结果加载失败'
        }
      }
    }
    if (seq !== loadSeq || taskId.value !== requestedId) return
    loading.value = false
    startPolling()
  }

  onMounted(() => {
    void load()
    void loadMe()
  })

  // 同一路由组件从一条任务跳到另一条时，onMounted 不会重跑；
  // 必须盯住参数变化重新拉，否则页面会显示上一条任务。
  watch(taskId, () => {
    stopPolling()
    markedNotificationTask = ''
    if (taskId.value) void load()
  })

  onUnmounted(stopPolling)

  return {
    DETAIL_TABS,
    normalizeDetailTab,
    activeTab,
    selectDetailTab,
    task,
    taskId,
    report,
    execution,
    me,
    comments,
    commentsTruncated,
    commentsHasMore,
    commentUnreadNotice,
    commentBody,
    commentKind,
    commentLoading,
    commentSaving,
    commentDeletingId,
    commentError,
    reportFeedback,
    feedbackSaving,
    feedbackError,
    myReportFeedback,
    sendReportFeedback,
    canComment,
    canAssign,
    canDeleteComment,
    assignableUsers,
    selectedAssigneeId,
    assigning,
    assignError,
    loadComments,
    loadAssignableUsers,
    assignSelected,
    removeComment,
    submitComment,
    loading,
    error,
    exporting,
    exportError,
    canceling,
    terminating,
    confirmingWithdraw,
    confirmingTerminate,
    actionError,
    actionNotice,
    markReportNotificationsRead,
    isAdmin,
    isDeployment,
    trace,
    traceIsLive,
    traceLabel,
    isInvestigating,
    canDispose,
    assigneeLabel,
    outcomeLabel,
    statusTone,
    statusBadge,
    aiLabel,
    knowledge,
    knowledgeScore,
    knowledgeVerdictLabel,
    executionResults,
    processCount,
    showDetailTabs,
    nextAction,
    formatTime,
    evidenceAnchor,
    scrollToEvidence,
    formatArgv,
    resultPreview,
    downloadReport,
    goApprovals,
    handleNextAction,
    withdrawTask,
    terminateRun,
    ...detailView,
  }
}
