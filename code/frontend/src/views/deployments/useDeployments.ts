/**
 * 部署页状态与动作。页面只负责模板渲染，所有接口调用、轮询和权限判断都在这里。
 */

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchMe, type MeInfo } from '../../api/auth'
import {
  approveDeploymentPlan,
  assignDeploymentApprover,
  confirmDeploymentPlan,
  createAiDeployment,
  createDeployment,
  DEPLOY_RESULTS,
  DEPLOY_TYPES,
  exportDeploymentReport,
  fetchDeployment,
  fetchDeploymentTrace,
  streamDeploymentTrace,
  listDeploymentApprovers,
  listDeployments,
  listDeployTemplates,
  type DeploymentApprover,
  type DeploymentTraceEvent,
  type DeploymentTraceState,
  type DeployListItem,
  type DeployTask,
  type DeployTemplate,
} from '../../api/deployments'
import { formatLocalTime } from '../../utils/time'
import { downloadCsv, exportStamp, MAX_EXPORT_ROWS, type CsvColumn } from '../../utils/csv'
import {
  executionGroupDuration,
  executionGroupOrder,
  executionGroupStatusText,
  executionLineText,
  executionPhaseText,
  executionRoleText,
  groupExecutionTrace,
  type ExecutionTraceGroup,
} from './executionTrace'

export function useDeployments() {
const me = ref<MeInfo | null>(null)
const route = useRoute()
const router = useRouter()
const activeTab = ref<'create' | 'records' | 'templates'>('create')

watch(() => route.query.tab, (tab) => {
  const value = String(tab || '')
  activeTab.value = value === 'records' || value === 'templates' ? value : 'create'
  if (value === 'records' && !selected.value) {
    const item = tasks.value.find((task) => task.plan_hash) ?? tasks.value[0]
    if (item) void openTask(item.id)
  }
})

watch(() => route.query.task, async (task) => {
  const id = String(task || '')
  if (!id || id === selected.value?.id) return
  activeTab.value = 'records'
  await openTask(id, false)
})
const templates = ref<DeployTemplate[]>([])
const tasks = ref<DeployListItem[]>([])
const tasksTotal = ref(0)
/**
 * 部署记录只做三项筛选：时间、部署类型、部署结果。
 * 类型下拉固定 8 项、没有“其他”；结果下拉 4 项，全部由空值表示。
 */
const filters = ref({ range: '', deployType: '', result: '' })
const exporting = ref(false)
const selected = ref<DeployTask | null>(null)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const token = ref('')
const approvalReason = ref('')
const stepUpCode = ref('')
const approvers = ref<DeploymentApprover[]>([])
const selectedApproverId = ref<number | null>(null)
const routingReason = ref('')
const exportingReport = ref(false)
const busy = ref(false)
const form = ref({ template_id: '', host: '', title: '', description: '', deployType: '' })
const aiForm = ref({
  goal: '', host: '', platform: 'linux' as 'linux' | 'windows',
  title: '', mode: 'freeform' as 'template' | 'freeform', deployType: '',
})
const aiBusy = ref(false)
const traceEvents = ref<DeploymentTraceEvent[]>([])
const traceLoading = ref(false)
const traceMode = ref<'idle' | 'sse' | 'polling'>('idle')
const traceClock = ref(Date.now())
const traceViewport = ref<HTMLElement | null>(null)
const tracePinnedToBottom = ref(true)
let traceTimer: number | null = null
let traceClockTimer: number | null = null
let traceCursor = 0
let traceStreamController: AbortController | null = null
const traceIds = new Set<number>()

const canSubmit = computed(() => {
  const roles = me.value?.roles ?? []
  return roles.includes('admin') || roles.includes('investigator')
})
const isAdmin = computed(() => (me.value?.roles ?? []).includes('admin'))
const isSelfSubmitted = computed(
  () => Boolean(selected.value && me.value && String(selected.value.submitted_by) === String(me.value.id)),
)
const canSelfApproveNow = computed(
  () => Boolean(me.value?.self_approval_switch && me.value?.can_self_approve),
)
const separationBlocked = computed(
  () => Boolean(selected.value
    && selected.value.status === 'awaiting_approval'
    && isSelfSubmitted.value
    && !canSelfApproveNow.value),
)
const routing = computed(() => {
  const value = (selected.value?.approval as Record<string, unknown> | undefined)?.routing
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
})
const assignedApproverId = computed(() => {
  const value = routing.value.assigned_to
  return value === null || value === undefined || value === '' ? null : Number(value)
})
const assignedApproverName = computed(
  () => String(routing.value.assigned_to_name || ''),
)
const isAssignedApprover = computed(
  () => Boolean(me.value && assignedApproverId.value !== null
    && Number(me.value.id) === assignedApproverId.value),
)
const canAssignApprover = computed(
  () => Boolean(selected.value
    && selected.value.status === 'awaiting_approval'
    && (isAdmin.value || isSelfSubmitted.value)),
)
const currentTemplate = computed(
  () => templates.value.find((item) => item.id === form.value.template_id) ?? null,
)
const plan = computed(() => selected.value?.plan ?? null)
const planExpired = computed(() => {
  const raw = plan.value?.expires_at
  if (!raw) return false
  const expiresAt = Date.parse(raw)
  return Number.isFinite(expiresAt) && expiresAt <= Date.now()
})
const planConfirmed = computed(() => {
  const confirmation = (selected.value?.approval as Record<string, unknown> | undefined)?.confirmation
  if (!confirmation || typeof confirmation !== 'object') return false
  const status = (confirmation as Record<string, unknown>).status
  return status === 'confirmed' || status === 'approved'
})

function approvalTimestamp(key: string): string {
  const approval = selected.value?.approval
  if (!approval || typeof approval !== 'object') return ''
  const confirmation = (approval as Record<string, unknown>).confirmation
  if (!confirmation || typeof confirmation !== 'object') return ''
  const value = (confirmation as Record<string, unknown>)[key]
  return typeof value === 'string' ? value : ''
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

const approvalFlow = computed(() => {
  const task = selected.value
  if (!task) return null
  const confirmedAt = approvalTimestamp('confirmed_at')
  const approvedAt = approvalTimestamp('approved_at')
  return {
    confirmedAt,
    approvedAt,
    waiting: task.status === 'awaiting_approval'
      ? elapsedText(task.created_at)
      : '',
    submitToApprove: approvedAt ? elapsedText(task.created_at, approvedAt) : '',
  }
})

const execution = computed(() => {
  const value = selected.value?.approval?.exec
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
})
const executionResults = computed(() => (execution.value.results as unknown[]) ?? [])
const deploymentKnowledge = computed(() => selected.value?.deployment_knowledge ?? [])

const filtersActive = computed(
  () => Boolean(filters.value.range || filters.value.deployType || filters.value.result),
)

const precheckResults = computed(() => (execution.value.precheck_results as unknown[]) ?? [])
const verifyResults = computed(() => (execution.value.verify_results as unknown[]) ?? [])
const rollbackResults = computed(() => (execution.value.rollback_results as unknown[]) ?? [])
const postRollbackResults = computed(() => (execution.value.post_rollback_results as unknown[]) ?? [])
const failure = computed(() => {
  const value = selected.value?.failure ?? execution.value.failure
  if (!value || typeof value !== 'object') return null
  const item = value as Record<string, unknown>
  if (item.code === 'not_allowedlist') {
    return { ...item, code: 'not_allowlisted', message: '命令不在白名单内，执行器拒绝执行' }
  }
  return item
})
const reasonLabel = computed(() => {
  const code = String(selected.value?.reason_code || '')
  return code === 'not_allowedlist' ? 'not_allowlisted' : code
})
const aiPlanning = computed(() => {
  const current = selected.value
  if (!current) return false
  return current.status === 'running' && current.stage === 'ai_plan'
})
const planTrace = computed(() =>
  traceEvents.value.filter((item) => item.phase === 'plan' || item.kind === 'thinking'),
)
const executionTraceAll = computed(() =>
  traceEvents.value.filter((item) => item.phase !== 'plan'),
)
const executionTraceTotal = computed(() => executionTraceAll.value.length)
const executionTrace = computed(() => executionTraceAll.value.slice(-300))
const executionTraceHidden = computed(
  () => Math.max(0, executionTraceTotal.value - executionTrace.value.length),
)
const executionLogGroups = computed(() => groupExecutionTrace(executionTrace.value))

watch(() => executionTrace.value.length, () => {
  if (tracePinnedToBottom.value) void scrollTraceToBottom()
})
const planProcess = computed(() => {
  const current = plan.value
  if (!current || !Array.isArray(current.operations)) return []
  const aiFreeform = current.plan_kind === 'ai_freeform'
  return [
    {
      title: aiFreeform ? 'AI 现场规划' : '读取已发布模板',
      detail: aiFreeform
        ? '由 AI 生成五阶段方案，审批后执行'
        : `${current.template_id || '—'} · v${current.template_version || 0}`,
    },
    {
      title: '解析目标主机',
      detail: `${current.host} · ${current.platform} · 目标机 OS ${current.host_os_family || 'unknown'}`,
    },
    {
      title: '操作系统兼容校验',
      detail: `模板 OS ${current.os_family || 'unknown'} · 包管理器 ${current.package_manager || 'unknown'}`,
    },
    {
      title: '编排变更、验证与回滚',
      detail: `变更前检查 ${current.precheck?.length ?? 0} 条 · 变更 ${current.operations.length} 条 · 验证 ${current.verify?.length ?? 0} 条 · 回滚 ${current.rollback?.length ?? 0} 条 · 回滚后验证 ${current.post_rollback?.length ?? 0} 条`,
    },
    {
      title: '计算方案指纹',
      detail: selected.value?.plan_hash || '—',
    },
    {
      title: '等待人工确认',
      detail: `当前状态：${selected.value?.status || '—'}`,
    },
  ]
})

onMounted(async () => {
  try {
    me.value = await fetchMe()
  } catch {
    // 权限最终由接口保障
  }
  await loadApprovers()
  await refresh()
  const tabFromRoute = String(route.query.tab || '')
  if (tabFromRoute === 'records' || tabFromRoute === 'templates') {
    activeTab.value = tabFromRoute
  }
  const taskFromRoute = String(route.query.task || '')
  if (taskFromRoute) {
    activeTab.value = 'records'
    await openTask(taskFromRoute)
  } else if (activeTab.value === 'records' && !selected.value) {
    const item = tasks.value.find((task) => task.plan_hash) ?? tasks.value[0]
    if (item) await openTask(item.id)
  }
})

async function loadApprovers() {
  try {
    approvers.value = (await listDeploymentApprovers()).items
  } catch {
    // 非管理员/排查员看不到审批人列表，保持空列表即可。
    approvers.value = []
  }
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const [templateResp, taskResp] = await Promise.all([
      listDeployTemplates(),
      listDeployments(taskQuery()),
    ])
    templates.value = templateResp.items
    tasks.value = taskResp.items
    tasksTotal.value = taskResp.total
    if (!form.value.template_id && templates.value.length) {
      form.value.template_id = templates.value[0].id
      form.value.host = templates.value[0].default_host
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '部署数据加载失败'
  } finally {
    loading.value = false
  }
}

function chooseTemplate(id: string) {
  form.value.template_id = id
  const template = templates.value.find((item) => item.id === id)
  form.value.host = template?.default_host ?? ''
}

async function submit() {
  if (!form.value.template_id) return
  if (!form.value.deployType) {
    error.value = '请选择部署类型后再生成方案。'
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const task = await createDeployment({
      template_id: form.value.template_id,
      host: form.value.host.trim() || undefined,
      title: form.value.title.trim() || undefined,
      description: form.value.description.trim() || undefined,
      deploy_type: form.value.deployType,
    })
    selected.value = task
    notice.value = '方案已生成。请先核对指纹和命令，再确认方案。'
    await refreshTasks()
    activeTab.value = 'records'
    await router.push({ name: 'deployments', query: { tab: 'records', task: task.id } })
  } catch (err) {
    error.value = err instanceof Error ? err.message : '创建部署任务失败'
  } finally {
    busy.value = false
  }
}

async function refreshTasks() {
  try {
    const resp = await listDeployments(taskQuery())
    tasks.value = resp.items
    tasksTotal.value = resp.total
  } catch {
    // 列表刷新失败不覆盖主错误
  }
}

/** 时间筛选用本地自然日，和后端接收的 ISO 时间对齐。 */
function deploymentTimeBounds(): { created_from?: string; created_to?: string } {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (filters.value.range === 'today') {
    return {
      created_from: startOfToday.toISOString(),
      created_to: new Date(startOfToday.getTime() + 86400000).toISOString(),
    }
  }
  if (filters.value.range === '7d') {
    return { created_from: new Date(now.getTime() - 7 * 86400000).toISOString() }
  }
  if (filters.value.range === '30d') {
    return { created_from: new Date(now.getTime() - 30 * 86400000).toISOString() }
  }
  return {}
}

function taskQuery() {
  return {
    limit: 50,
    ...deploymentTimeBounds(),
    deploy_type: filters.value.deployType || undefined,
    result: filters.value.result || undefined,
  }
}

async function applyFilters() {
  await refreshTasks()
}

const deployTypeLabels: Record<string, string> = Object.fromEntries(
  DEPLOY_TYPES.map((item) => [item.value, item.label]),
)
const deployResultLabels: Record<string, string> = Object.fromEntries(
  DEPLOY_RESULTS.map((item) => [item.value, item.label]),
)

const exportColumns: CsvColumn<DeployListItem>[] = [
  { label: '任务 ID', value: (item) => item.id },
  { label: '标题', value: (item) => item.title },
  { label: '部署类型', value: (item) => item.deploy_type_label || deployTypeLabels[item.deploy_type] || item.deploy_type || '未分类' },
  { label: '状态', value: (item) => statusText(item) },
  { label: '结果', value: (item) => item.outcome || deployResultLabels[item.status] || item.status },
  { label: '模板', value: (item) => item.template_id },
  { label: '方案指纹', value: (item) => item.plan_hash },
  { label: '创建时间', value: (item) => item.created_at ? formatLocalTime(item.created_at) : '' },
  { label: '关闭时间', value: (item) => item.closed_at ? formatLocalTime(item.closed_at) : '' },
]

/** 部署列表接口单次最多 200 条；按 offset 逐页拉全，避免只导出前 200 条。 */
async function exportDeployments() {
  if (exporting.value) return
  exporting.value = true
  error.value = ''
  notice.value = ''
  try {
    const all: DeployListItem[] = []
    let offset = 0
    let serverTotal = 0
    // 50,000 条只是前端安全上限，触发时明确提示。
    while (all.length < MAX_EXPORT_ROWS) {
      const resp = await listDeployments({ ...taskQuery(), limit: 200, offset })
      serverTotal = resp.total
      all.push(...resp.items)
      if (all.length >= serverTotal || resp.items.length === 0) break
      offset += 200
    }
    if (!all.length) {
      error.value = '当前筛选没有可导出的部署记录'
      return
    }
    downloadCsv(`部署记录-${exportStamp()}.csv`, exportColumns, all)
    notice.value = all.length < serverTotal
      ? `已导出 ${all.length} 条，筛选共 ${serverTotal} 条；达到 ${MAX_EXPORT_ROWS} 条安全上限`
      : `已导出 ${all.length} 条部署记录`
  } catch (err) {
    error.value = err instanceof Error ? err.message : '导出失败'
  } finally {
    exporting.value = false
  }
}

async function clearFilters() {
  filters.value = { range: '', deployType: '', result: '' }
  await refreshTasks()
}

function switchTab(tab: 'create' | 'records' | 'templates') {
  activeTab.value = tab
  void router.replace({
    name: 'deployments',
    query: tab === 'create' ? {} : { tab },
  })
}

async function openTask(id: string, focusConfirm = false) {
  const requestedId = id
  stopTrace()
  error.value = ''
  notice.value = ''
  token.value = ''
  approvalReason.value = ''
  selected.value = null
  traceEvents.value = []
  traceIds.clear()
  traceCursor = 0
  traceLoading.value = false
  tracePinnedToBottom.value = true
  traceMode.value = 'idle'
  try {
    const loaded = await fetchDeployment(requestedId)
    if (loaded.id !== requestedId) return
    selected.value = loaded
    const routingValue = (loaded.approval as Record<string, unknown> | undefined)?.routing
    const assigned = routingValue && typeof routingValue === 'object'
      ? Number((routingValue as Record<string, unknown>).assigned_to)
      : NaN
    selectedApproverId.value = Number.isFinite(assigned)
      ? assigned
      : (approvers.value.find((item) => String(item.id) !== String(loaded.submitted_by))?.id ?? null)
    routingReason.value = ''
    if (route.query.task !== requestedId) {
      void router.replace({ name: 'deployments', query: { tab: 'records', task: requestedId } })
    }
    await refreshTrace(requestedId)
    if (selected.value?.id !== requestedId) return
    if (isActiveStatus(selected.value.status)) startTraceStream()
    await nextTick()
    await scrollTraceToBottom()
    if (focusConfirm && selected.value.status === 'awaiting_approval') {
      const focusTaskId = selected.value.id
      await nextTick()
      // 方案详情是异步渲染的，等一帧再定位，避免确认区还没挂上就被跳过。
      window.setTimeout(() => {
        if (selected.value?.id !== focusTaskId || selected.value.status !== 'awaiting_approval') return
        document.getElementById('deployment-confirm')?.scrollIntoView({ block: 'center' })
      }, 120)
    }
  } catch (err) {
    if (selected.value?.id === requestedId) selected.value = null
    error.value = err instanceof Error ? err.message : '部署详情加载失败'
  }
}

function isActiveStatus(status: string | undefined): boolean {
  return status === 'running' || status === 'executing'
}

async function scrollTraceToBottom() {
  await nextTick()
  const viewport = traceViewport.value
  if (viewport) viewport.scrollTop = viewport.scrollHeight
}

async function scrollTraceToLatest() {
  tracePinnedToBottom.value = true
  await scrollTraceToBottom()
}

function onTraceScroll() {
  const viewport = traceViewport.value
  if (!viewport) return
  const distance = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight
  tracePinnedToBottom.value = distance < 24
}

function executionGroupDurationText(group: ExecutionTraceGroup): string {
  return executionGroupDuration(group, traceClock.value)
}

function startTraceClock() {
  stopTraceClock()
  traceClock.value = Date.now()
  traceClockTimer = window.setInterval(() => {
    traceClock.value = Date.now()
  }, 1000)
}

function stopTraceClock() {
  if (traceClockTimer !== null) {
    window.clearInterval(traceClockTimer)
    traceClockTimer = null
  }
  traceClock.value = Date.now()
}

function stopTracePolling() {
  if (traceTimer !== null) {
    window.clearInterval(traceTimer)
    traceTimer = null
  }
  if (traceMode.value === 'polling') traceMode.value = 'idle'
}

function stopTraceStream() {
  if (traceStreamController) {
    traceStreamController.abort()
    traceStreamController = null
  }
  if (traceMode.value === 'sse') traceMode.value = 'idle'
}

function stopTrace() {
  stopTraceStream()
  stopTracePolling()
  stopTraceClock()
  traceMode.value = 'idle'
}

function appendTraceEvent(event: DeploymentTraceEvent) {
  if (traceIds.has(event.event_id)) return
  traceIds.add(event.event_id)
  traceEvents.value.push(event)
  if (event.event_id > traceCursor) traceCursor = event.event_id
}

async function applyTraceState(taskId: string, state: DeploymentTraceState) {
  if (selected.value?.id !== taskId) return
  traceCursor = Math.max(traceCursor, Number(state.next_after_event_id || 0))
  if (state.status === selected.value.status && state.outcome === selected.value.outcome) return
  try {
    const refreshed = await fetchDeployment(taskId)
    if (selected.value?.id !== taskId) return
    selected.value = refreshed
    if (!isActiveStatus(refreshed.status)) {
      stopTraceStream()
      stopTracePolling()
      stopTraceClock()
      traceMode.value = 'idle'
    }
  } catch {
    // 状态刷新失败不影响已经收到的日志；流本身会在任务结束时关闭。
  }
}

function startTracePolling() {
  stopTraceStream()
  stopTracePolling()
  startTraceClock()
  traceMode.value = 'polling'
  traceTimer = window.setInterval(() => {
    void refreshTrace()
  }, 1000)
}

function startTraceStream() {
  stopTracePolling()
  stopTraceStream()
  const taskId = selected.value?.id
  if (!taskId || !isActiveStatus(selected.value?.status)) return
  const controller = new AbortController()
  traceStreamController = controller
  startTraceClock()
  traceMode.value = 'sse'
  void (async () => {
    try {
      await streamDeploymentTrace(taskId, traceCursor, {
        onTrace: (event) => {
          if (selected.value?.id !== taskId) return
          appendTraceEvent(event)
        },
        onState: (state) => {
          if (selected.value?.id !== taskId) return
          void applyTraceState(taskId, state)
        },
      }, controller.signal)
      if (controller.signal.aborted || selected.value?.id !== taskId) return
      await refreshTrace(taskId)
      if (selected.value?.id !== taskId) return
      if (isActiveStatus(selected.value.status)) startTracePolling()
      else traceMode.value = 'idle'
    } catch {
      if (controller.signal.aborted || selected.value?.id !== taskId) return
      // SSE 被代理断开或浏览器不支持流式读取时，退回原轮询。
      startTracePolling()
    } finally {
      if (traceStreamController === controller) traceStreamController = null
    }
  })()
}

async function refreshTrace(expectedId?: string) {
  const taskId = expectedId || selected.value?.id
  if (!taskId || traceLoading.value) return
  traceLoading.value = true
  try {
    const resp = await fetchDeploymentTrace(taskId, traceCursor, 200)
    if (selected.value?.id !== taskId) return
    for (const event of resp.events) appendTraceEvent(event)
    traceCursor = Math.max(traceCursor, resp.next_after_event_id)
    if (resp.status !== selected.value.status || resp.outcome !== selected.value.outcome) {
      const refreshed = await fetchDeployment(taskId)
      if (selected.value?.id !== taskId) return
      selected.value = refreshed
    }
    if (!isActiveStatus(resp.status)) {
      stopTracePolling()
      stopTraceStream()
      stopTraceClock()
      traceMode.value = 'idle'
    }
  } catch {
    // 轮询失败不覆盖主错误；下一轮继续
  } finally {
    traceLoading.value = false
  }
}

async function submitAiPlan() {
  const goal = aiForm.value.goal.trim()
  if (!goal) {
    error.value = '请先描述要部署什么'
    return
  }
  if (!aiForm.value.deployType) {
    error.value = '请选择部署类型后再让 AI 规划。'
    return
  }
  aiBusy.value = true
  error.value = ''
  notice.value = ''
  try {
    const task = await createAiDeployment({
      goal,
      host: aiForm.value.host.trim() || undefined,
      platform: aiForm.value.platform,
      title: aiForm.value.title.trim() || undefined,
      mode: aiForm.value.mode,
      deploy_type: aiForm.value.deployType,
    })
    notice.value = aiForm.value.mode === 'freeform'
      ? 'AI 规划已入队，正在生成结构化变更方案。'
      : 'AI 规划已入队，正在读取主机元数据并选择模板。'
    await refreshTasks()
    await openTask(task.id)
    activeTab.value = 'records'
    await router.push({ name: 'deployments', query: { tab: 'records', task: task.id } })
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'AI 规划创建失败'
  } finally {
    aiBusy.value = false
  }
}

onUnmounted(stopTrace)

async function confirmPlan() {
  if (!selected.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await confirmDeploymentPlan(selected.value.id, selected.value.plan_hash)
    token.value = result.token
    notice.value = '方案已确认。一次性令牌只显示这一次，请交给审批人。'
    selected.value = await fetchDeployment(selected.value.id)
    await refreshTasks()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '确认方案失败'
  } finally {
    busy.value = false
  }
}

async function assignApprover() {
  if (!selected.value || !selectedApproverId.value) {
    error.value = '请选择一位管理员作为审批人'
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await assignDeploymentApprover(
      selected.value.id,
      selectedApproverId.value,
      routingReason.value.trim(),
    )
    notice.value = '审批人已更新；只有被指派的管理员可以用一次性令牌批准。'
    routingReason.value = ''
    selected.value = await fetchDeployment(selected.value.id)
  } catch (err) {
    const raw = err instanceof Error ? err.message : '指派审批人失败'
    error.value = raw.includes('cross_person_required')
      ? '跨人审批：审批人不能是提交人。'
      : raw.includes('approver_not_available')
        ? '指定用户不在启用中的管理员列表里。'
        : raw
  } finally {
    busy.value = false
  }
}

async function exportReport(format: 'md' | 'pdf' | 'docx' = 'md') {
  if (!selected.value || exportingReport.value) return
  exportingReport.value = true
  error.value = ''
  notice.value = ''
  try {
    const blob = await exportDeploymentReport(selected.value.id, format)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `deployment-report-${selected.value.id}.${format === 'md' ? 'md' : format}`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    notice.value = '部署报告已导出'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '部署报告导出失败'
  } finally {
    exportingReport.value = false
  }
}

async function decide(decision: 'APPROVE' | 'REJECT') {
  if (!selected.value) return
  if (decision === 'APPROVE' && !token.value.trim()) {
    error.value = '批准并执行需要一次性确认令牌；驳回不需要令牌'
    return
  }
  if (decision === 'APPROVE' && !stepUpCode.value.trim()) {
    error.value = '批准部署需要输入当前账号的 6 位动态口令'
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await approveDeploymentPlan(
      selected.value.id,
      selected.value.plan_hash,
      token.value.trim(),
      decision,
      approvalReason.value.trim(),
      stepUpCode.value.trim(),
    )
    token.value = ''
    approvalReason.value = ''
    stepUpCode.value = ''
    const taskId = selected.value.id
    notice.value = decision === 'APPROVE'
      ? '已批准，执行已入队；正在连接实时日志。'
      : '已驳回，任务已结束。'
    selected.value = await fetchDeployment(taskId)
    if (selected.value?.id !== taskId) return
    await refreshTasks()
    if (decision === 'APPROVE') {
      // 审批前 SSE 在 awaiting_approval 时已经关闭，审批后必须重新接上，
      // 否则执行阶段只能等下一次手动刷新才能看到日志。
      await refreshTrace(taskId)
      if (selected.value?.id !== taskId) return
      if (isActiveStatus(selected.value.status)) {
        startTraceStream()
        notice.value = '已批准，执行已入队；实时日志已连接，结果会自动更新。'
      } else {
        notice.value = '已批准，执行已结束；结果已刷新。'
      }
    }
  } catch (err) {
    const raw = err instanceof Error ? err.message : '审批部署任务失败'
    error.value = raw.includes('self_approval_not_allowed')
      ? '职责分离：不能审批自己提交的部署任务。请让其他管理员审批；确需自批，请管理员在配置页开启“自审批豁免”。'
      : raw.includes('approver_not_assigned')
        ? '这条部署已指派给其他管理员；请由被指派管理员批准，或先重新指派审批人。'
        : raw
  } finally {
    busy.value = false
  }
}

function formatTime(iso: string | null | undefined): string {
  return formatLocalTime(iso)
}

function statusText(item: DeployListItem): string {
  const labels: Record<string, string> = {
    running: 'AI 规划中',
    awaiting_approval: '待确认',
    executing: '执行中',
    closed: '已结束',
  }
  return labels[item.status] ?? item.status
}

function statusClass(item: DeployListItem): string {
  if (item.status === 'awaiting_approval') return 'badge-warn'
  if (item.status === 'executing') return 'badge-info'
  return 'badge-muted'
}

function resultOk(item: unknown): boolean {
  const row = item as Record<string, unknown>
  if (typeof row?.expect_ok === 'boolean') return row.expect_ok
  return Boolean(row?.ok)
}

function resultReason(item: unknown): string {
  const row = item as Record<string, unknown>
  if (row?.reason) return String(row.reason)
  if (typeof row?.expect_ok === 'boolean' && !row.expect_ok) return '结果与预期不一致'
  return row?.ok ? '成功' : '失败'
}

function stageText(stage?: unknown): string {
  const key = stage ? String(stage) : ''
  const map: Record<string, string> = {
    precheck: '变更前检查',
    apply: '变更执行',
    verify: '业务验证',
    rollback: '失败回滚',
    post_rollback: '回滚后验证',
    ai_plan: 'AI 规划',
  }
  return key ? map[key] || key : '—'
}

function traceDetail(event: DeploymentTraceEvent): string {
  const payload = event.payload ?? {}
  const text = String(payload.detail || payload.line || payload.command || '')
  return text.slice(0, 300)
}

  return {
    me,
    route,
    router,
    activeTab,
    templates,
    tasks,
    tasksTotal,
    filters,
    exporting,
    selected,
    loading,
    error,
    notice,
    token,
    approvalReason,
    stepUpCode,
    approvers,
    selectedApproverId,
    routingReason,
    routing,
    assignedApproverId,
    assignedApproverName,
    isAssignedApprover,
    canAssignApprover,
    exportingReport,
    assignApprover,
    exportReport,
    busy,
    form,
    aiForm,
    aiBusy,
    traceEvents,
    traceLoading,
    traceMode,
    canSubmit,
    isAdmin,
    isSelfSubmitted,
    canSelfApproveNow,
    separationBlocked,
    currentTemplate,
    plan,
    planExpired,
    planConfirmed,
    approvalTimestamp,
    elapsedText,
    approvalFlow,
    execution,
    executionResults,
    deploymentKnowledge,
    filtersActive,
    precheckResults,
    verifyResults,
    rollbackResults,
    postRollbackResults,
    failure,
    reasonLabel,
    aiPlanning,
    planTrace,
    executionTrace,
    executionTraceTotal,
    executionTraceHidden,
    executionLogGroups,
    traceViewport,
    tracePinnedToBottom,
    onTraceScroll,
    scrollTraceToLatest,
    executionGroupDurationText,
    executionGroupOrder,
    executionGroupStatusText,
    executionLineText,
    executionPhaseText,
    executionRoleText,
    planProcess,
    refresh,
    chooseTemplate,
    submit,
    refreshTasks,
    deploymentTimeBounds,
    taskQuery,
    applyFilters,
    deployTypeLabels,
    deployResultLabels,
    exportColumns,
    exportDeployments,
    clearFilters,
    switchTab,
    openTask,
    isActiveStatus,
    stopTracePolling,
    startTracePolling,
    startTraceStream,
    refreshTrace,
    submitAiPlan,
    confirmPlan,
    decide,
    formatTime,
    statusText,
    statusClass,
    resultOk,
    resultReason,
    stageText,
    traceDetail,
  }
}
