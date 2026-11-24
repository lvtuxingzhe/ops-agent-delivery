/**
 * 排查列表页状态与数据逻辑。
 *
 * 页面只负责渲染；筛选、分页、导出和列宽状态都收在这里，
 * 保持与拆开前的行为一致。
 */

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import type { MeInfo } from '../../api/auth'
import { getOrLogin } from '../../api/http'
import { fetchOverview, type Overview } from '../../api/overview'
import { listTasks, type TaskInfo } from '../../api/tasks'
import ResizableTable from '../../components/ResizableTable.vue'
import type { ResizableTableColumn } from '../../components/resizableTable'
import { downloadCsv, exportStamp, MAX_EXPORT_ROWS, type CsvColumn } from '../../utils/csv'
import { formatLocalTime } from '../../utils/time'

export function useTasks() {
  const router = useRouter()
  const me = ref<MeInfo | null>(null)
  const overview = ref<Overview | null>(null)
  const tasks = ref<TaskInfo[]>([])
  const total = ref(0)
  const page = ref(1)
  const size = 20
  const status = ref('')
  const assignee = ref('')
  const category = ref('')
  const timeRange = ref('')
  const keyword = ref('')
  const loading = ref(true)
  const exporting = ref(false)
  const error = ref('')

  /** 列宽拖动由公共 ResizableTable 承载；只有桌面六列表格启用。 */
  const COLUMN_WIDTHS_STORAGE_KEY = 'ops.tasks.columnWidths.v1'
  const DEFAULT_TASK_GRID =
    'minmax(0, 34fr) minmax(96px, 12fr) minmax(104px, 15fr) minmax(112px, 16fr) minmax(112px, 14fr) minmax(72px, 9fr)'
  const TASK_COLUMNS: ResizableTableColumn[] = [
    { key: 'task', label: '任务', minWidth: 220 },
    { key: 'type', label: '类型', minWidth: 80 },
    { key: 'assignee', label: '处理人', minWidth: 88 },
    { key: 'status', label: '状态', minWidth: 92 },
    { key: 'created_at', label: '创建时间', minWidth: 100, headClass: 'time-head' },
    { key: 'action', label: '操作', minWidth: 64, headClass: 'action-head' },
  ]
  const tableRef = ref<InstanceType<typeof ResizableTable> | null>(null)
  const hasCustomWidths = ref(false)
  const clearNotice = ref('')
  let clearNoticeTimer: number | null = null

  function showClearNotice(text: string) {
    clearNotice.value = text
    if (clearNoticeTimer !== null) window.clearTimeout(clearNoticeTimer)
    clearNoticeTimer = window.setTimeout(() => {
      clearNotice.value = ''
      clearNoticeTimer = null
    }, 1800)
  }

  const categoryOptions = [
    { value: '', label: '全部类型' },
    { value: 'host', label: '主机' },
    { value: 'middleware', label: '中间件' },
    { value: 'database', label: '数据库' },
    { value: 'k8s', label: 'K8S' },
    { value: 'docker', label: 'DOCKER' },
    { value: 'other', label: '其他' },
  ]

  const statusOptions = [
    { value: '', label: '全部' },
    { value: 'draft', label: '已提交' },
    { value: 'running', label: '进行中' },
    { value: 'report_ready', label: '报告已生成' },
    { value: 'awaiting_approval', label: '处置待审批' },
    { value: 'executing', label: '命令执行中' },
    { value: 'closed', label: '排查已结束' },
  ]

  const assigneeOptions = [
    { value: '', label: '全部' },
    { value: 'me', label: '我认领的' },
  ]

  const timeOptions = [
    { value: '', label: '全部时间' },
    { value: 'today', label: '今天' },
    { value: '7d', label: '近 7 天' },
    { value: '30d', label: '近 30 天' },
  ]

  /** 时间筛选用本地自然日，避免“今天”被 UTC 切成昨天。 */
  function timeBounds(): { createdAfter?: string; createdBefore?: string } {
    const now = new Date()
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    if (timeRange.value === 'today') {
      return { createdAfter: startOfToday.toISOString(), createdBefore: new Date(startOfToday.getTime() + 86400000).toISOString() }
    }
    if (timeRange.value === '7d') {
      return { createdAfter: new Date(now.getTime() - 7 * 86400000).toISOString() }
    }
    if (timeRange.value === '30d') {
      return { createdAfter: new Date(now.getTime() - 30 * 86400000).toISOString() }
    }
    return {}
  }

  const canSubmit = computed(() => {
    const roles = me.value?.roles ?? []
    return roles.includes('admin') || roles.includes('investigator')
  })

  const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size)))

  const investigationCounts = computed(() => overview.value?.tasks.by_type.investigation ?? null)

  const allTotal = computed(() => investigationCounts.value?.total ?? total.value)

  const activeFilterCount = computed(
    () =>
      [status.value, assignee.value, category.value, timeRange.value, keyword.value.trim()]
        .filter(Boolean).length,
  )

  const clearLabel = computed(() =>
    keyword.value.trim() && activeFilterCount.value === 1 ? '清空搜索' : '清空筛选',
  )

  const summaryItems = computed(() => {
    const counts = investigationCounts.value
    if (!counts) return []
    return [
      { label: '待审批', value: counts.awaiting_approval, tone: 'warn' },
      { label: '进行中', value: counts.running, tone: 'info' },
      { label: '报告已生成', value: counts.report_ready, tone: 'success' },
      { label: '已结束', value: counts.by_status.closed ?? 0, tone: 'muted' },
    ]
  })

  onMounted(async () => {
    try {
      me.value = await getOrLogin<MeInfo>('/auth/me')
    } catch {
      // 列表本身仍可读；权限错误由列表接口返回。
    }
    await refreshAll()
  })

  async function loadOverview() {
    try {
      overview.value = await fetchOverview()
    } catch {
      // 概览是辅助信息；失败时列表照常工作，不阻断页面。
    }
  }

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const result = await listTasks(
        status.value || undefined,
        page.value,
        size,
        assignee.value || undefined,
        {
          taskType: 'investigation',
          category: category.value || undefined,
          q: keyword.value.trim() || undefined,
          ...timeBounds(),
        },
      )
      tasks.value = result.items
      total.value = result.total
    } catch (err) {
      error.value = err instanceof Error ? err.message : '排查列表加载失败'
    } finally {
      loading.value = false
    }
  }

  async function refreshAll() {
    await Promise.all([loadOverview(), load()])
  }

  async function changeFilter() {
    page.value = 1
    await load()
  }

  async function clearFilters() {
    const label = clearLabel.value
    status.value = ''
    assignee.value = ''
    category.value = ''
    timeRange.value = ''
    keyword.value = ''
    showClearNotice(label === '清空搜索' ? '已清空搜索' : '已清空筛选')
    await changeFilter()
  }

  async function goPage(next: number) {
    if (next < 1 || next > totalPages.value) return
    page.value = next
    await load()
  }

  const statusLabels: Record<string, string> = Object.fromEntries(
    statusOptions.filter((item) => item.value).map((item) => [item.value, item.label]),
  )
  const categoryLabels: Record<string, string> = Object.fromEntries(
    categoryOptions.filter((item) => item.value).map((item) => [item.value, item.label]),
  )

  const exportColumns: CsvColumn<TaskInfo>[] = [
    { label: '任务 ID', value: (item) => item.id },
    { label: '标题', value: (item) => item.title },
    { label: '类型', value: (item) => categoryLabels[item.category ?? ''] ?? item.category ?? '' },
    { label: '状态', value: (item) => item.status_text || statusLabels[item.status] || item.status },
    { label: '处理人', value: (item) => assigneeName(item) },
    { label: '提交人', value: (item) => item.submitted_by_name || item.submitted_by },
    { label: '创建时间', value: (item) => formatTime(item.created_at) },
    { label: '关闭时间', value: (item) => formatTime(item.closed_at) },
    { label: '结果', value: (item) => item.outcome ?? '' },
  ]

  /** 按当前筛选导出全量，不只导当前页；接口上限 50，逐页拉取。 */
  async function exportTasks() {
    if (exporting.value) return
    exporting.value = true
    error.value = ''
    try {
      const filters = {
        taskType: 'investigation' as const,
        category: category.value || undefined,
        q: keyword.value.trim() || undefined,
        ...timeBounds(),
      }
      const all: TaskInfo[] = []
      let currentPage = 1
      let serverTotal = 0
      // 按服务端 total 拉全；50,000 条只是前端安全上限，触发时明确提示。
      while (all.length < MAX_EXPORT_ROWS) {
        const result = await listTasks(
          status.value || undefined,
          currentPage,
          50,
          assignee.value || undefined,
          filters,
        )
        serverTotal = result.total
        all.push(...result.items)
        if (all.length >= serverTotal || result.items.length === 0) break
        currentPage += 1
      }
      if (!all.length) {
        error.value = '当前筛选没有可导出的排查任务'
        return
      }
      downloadCsv(`排查任务-${exportStamp()}.csv`, exportColumns, all)
      if (all.length < serverTotal) {
        showClearNotice(`已导出 ${all.length} 条，筛选共 ${serverTotal} 条；达到 ${MAX_EXPORT_ROWS} 条安全上限`)
      } else {
        showClearNotice(`已导出 ${all.length} 条排查任务`)
      }
    } catch (err) {
      error.value = err instanceof Error ? err.message : '导出失败'
    } finally {
      exporting.value = false
    }
  }

  function formatTime(iso: string | null): string {
    return formatLocalTime(iso)
  }

  function shortTime(iso: string | null): string {
    const full = formatTime(iso)
    return full === '—' ? full : full.slice(5)
  }

  function assigneeName(task: TaskInfo): string {
    return task.assignee?.display_name || task.assignee?.username || '未认领'
  }

  function statusClass(item: TaskInfo): string {
    if (item.status === 'awaiting_approval') return 'badge-warn'
    if (item.status === 'running' || item.status === 'executing') return 'badge-info'
    if (item.status === 'report_ready') return 'badge-success'
    return 'badge-muted'
  }

  function statusTone(item: TaskInfo): string {
    if (item.status === 'awaiting_approval') return 'warn'
    if (item.status === 'running' || item.status === 'executing') return 'info'
    if (item.status === 'report_ready') return 'success'
    return 'muted'
  }

  function statusText(item: TaskInfo): string {
    const labels: Record<string, string> = {
      draft: '已提交',
      running: '排查中',
      report_ready: '报告已生成',
      awaiting_approval: '处置待审批',
      executing: '命令执行中',
      closed: '排查已结束',
    }
    return labels[item.status] ?? item.status_text
  }

  function openTask(task: TaskInfo) {
    router.push(`/tasks/${task.id}`)
  }

  function resetColumnWidths() {
    tableRef.value?.reset()
  }

  onBeforeUnmount(() => {
    if (clearNoticeTimer !== null) {
      window.clearTimeout(clearNoticeTimer)
      clearNoticeTimer = null
    }
  })

  return {
    router,
    tasks,
    total,
    page,
    status,
    assignee,
    category,
    timeRange,
    keyword,
    loading,
    exporting,
    error,
    TASK_COLUMNS,
    DEFAULT_TASK_GRID,
    COLUMN_WIDTHS_STORAGE_KEY,
    tableRef,
    hasCustomWidths,
    clearNotice,
    categoryOptions,
    statusOptions,
    assigneeOptions,
    timeOptions,
    canSubmit,
    totalPages,
    investigationCounts,
    allTotal,
    activeFilterCount,
    clearLabel,
    summaryItems,
    refreshAll,
    changeFilter,
    clearFilters,
    goPage,
    exportTasks,
    formatTime,
    shortTime,
    assigneeName,
    statusClass,
    statusTone,
    statusText,
    openTask,
    resetColumnWidths,
  }
}
