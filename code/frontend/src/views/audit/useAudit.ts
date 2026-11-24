import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import { exportAuditEvents, fetchAuditActors, fetchAuditEvents, type AuditActorOption, type AuditEventItem, type AuditEventPage } from '../../api/audit'
import { ApiError } from '../../api/http'

/**
 * 审计检索页状态与动作。
 *
 * 只读：不提供删除/修改入口，审计记录由数据库权限保证只追加。
 */
export function useAudit() {
  const route = useRoute()
  const filters = reactive({
    task_id: '',
    trace_id: '',
    event_type: '',
    actor: [] as string[],
    q: '',
    since: '',
    until: '',
  })
  const limit = ref(20)
  const offset = ref(0)
  const page = ref<AuditEventPage | null>(null)
  const loading = ref(false)
  const exporting = ref(false)
  const error = ref('')
  const notice = ref('')
  const expandedId = ref<number | null>(null)
  const actorOptions = ref<AuditActorOption[]>([])
  const actorSearch = ref('')
  const actorOpen = ref(false)
  const actorsLoading = ref(false)
  const advancedOpen = ref(false)

  const advancedFilterCount = computed(() => {
    let count = 0
    if (filters.task_id) count += 1
    if (filters.trace_id) count += 1
    if (filters.event_type) count += 1
    if (filters.actor.length) count += 1
    if (filters.since) count += 1
    if (filters.until) count += 1
    return count
  })

  const rows = computed(() => page.value?.items ?? [])
  const total = computed(() => page.value?.total ?? 0)
  const hasMore = computed(() => page.value?.has_more ?? false)
  const currentPage = computed(() => Math.floor(offset.value / limit.value) + 1)
  const filteredActors = computed(() => {
    const query = actorSearch.value.trim().toLowerCase()
    if (!query) return actorOptions.value
    return actorOptions.value.filter((item) => item.actor.toLowerCase().includes(query))
  })

  async function loadActors(): Promise<void> {
    actorsLoading.value = true
    try {
      actorOptions.value = await fetchAuditActors()
    } catch {
      // 操作人列表读不到不影响按其他条件查询。
    } finally {
      actorsLoading.value = false
    }
  }

  function toggleActor(actor: string): void {
    const index = filters.actor.indexOf(actor)
    if (index >= 0) filters.actor.splice(index, 1)
    else filters.actor.push(actor)
  }

  function removeActor(actor: string): void {
    filters.actor = filters.actor.filter((item) => item !== actor)
  }

  function clearActors(): void {
    filters.actor = []
  }

  function openActorMenu(): void {
    actorOpen.value = true
  }

  function closeActorMenu(event: FocusEvent): void {
    const next = event.relatedTarget as Node | null
    if (next && (event.currentTarget as HTMLElement).contains(next)) return
    actorOpen.value = false
  }


  async function load(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      page.value = await fetchAuditEvents({
        ...filters,
        actor: filters.actor.join(','),
        limit: limit.value,
        offset: offset.value,
      })
    } catch (err) {
      page.value = null
      if (err instanceof ApiError) {
        error.value = err.status === 403 ? '当前角色没有审计查询权限。' : err.message
      } else {
        error.value = '审计查询失败，请稍后重试。'
      }
    } finally {
      loading.value = false
    }
  }

  function applyFilters(): void {
    offset.value = 0
    void load()
  }

  async function exportCsv(): Promise<void> {
    if (exporting.value) return
    exporting.value = true
    error.value = ''
    notice.value = ''
    try {
      const { blob, rows, truncated } = await exportAuditEvents({
        ...filters,
        actor: filters.actor.join(','),
      })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
      link.href = url
      link.download = `audit-events-${stamp}.csv`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      notice.value = truncated
        ? `已导出 ${rows} 条审计记录；后端标记为截断，请缩小筛选范围`
        : `已导出 ${rows} 条审计记录`
    } catch (err) {
      if (err instanceof ApiError) {
        error.value = err.status === 403 ? '当前角色没有审计导出权限。' : err.message
      } else {
        error.value = '审计导出失败，请稍后重试。'
      }
    } finally {
      exporting.value = false
    }
  }

  function resetFilters(): void {
    filters.task_id = ''
    filters.trace_id = ''
    filters.event_type = ''
    filters.actor = []
    filters.q = ''
    filters.since = ''
    filters.until = ''
    offset.value = 0
    void load()
  }

  function turnPage(delta: number): void {
    const next = offset.value + delta * limit.value
    if (next < 0 || next >= total.value) return
    offset.value = next
    void load()
  }

  function togglePayload(item: AuditEventItem): void {
    expandedId.value = expandedId.value === item.id ? null : item.id
  }

  function prettyPayload(item: AuditEventItem): string {
    return JSON.stringify(item.payload, null, 2)
  }

  function formatTime(value: string | null): string {
    if (!value) return '—'
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return value
    return date
      .toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hour12: false,
      })
      .replace(/\//g, '-')
  }

  function formatFullTime(value: string | null): string {
    if (!value) return ''
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return value
    return date.toLocaleString('zh-CN', { hour12: false })
  }

  function useTrace(traceId: string): void {
    filters.trace_id = traceId
    applyFilters()
  }

  onMounted(() => {
    // 从任务详情“查审计”跳过来时，带 trace 或 task 条件，直接落到相关流水
    filters.trace_id = String(route.query.trace_id ?? '')
    filters.task_id = String(route.query.task_id ?? '')
    void load()
    void loadActors()
  })

  return {
    advancedFilterCount,
    advancedOpen,
    applyFilters,
    actorOpen,
    actorSearch,
    actorsLoading,
    clearActors,
    closeActorMenu,
    currentPage,
    error,
    expandedId,
    exportCsv,
    exporting,
    filteredActors,
    filters,
    formatFullTime,
    formatTime,
    hasMore,
    limit,
    loading,
    notice,
    offset,
    openActorMenu,
    prettyPayload,
    removeActor,
    resetFilters,
    rows,
    toggleActor,
    togglePayload,
    total,
    turnPage,
    useTrace,
  }
}
