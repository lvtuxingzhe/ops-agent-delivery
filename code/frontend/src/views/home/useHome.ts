/**
 * 工作台状态与数据逻辑。
 *
 * 页面只负责渲染；角色权限、任务计数、最近排查和管理员开关都收在这里。
 */

import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { setSelfApprovalSwitch, type MeInfo } from '../../api/auth'
import { getOrLogin } from '../../api/http'
import { fetchOverview, fetchTokenSummary, type TokenSummary } from '../../api/overview'
import { listTasks, type TaskInfo } from '../../api/tasks'
import { formatLocalTime } from '../../utils/time'

export function useHome() {
  const router = useRouter()
  const me = ref<MeInfo | null>(null)
  const recent = ref<TaskInfo[]>([])
  const investigationCounts = ref({ awaiting: 0, running: 0, total: 0 })
  const deploymentCounts = ref({ awaiting: 0, running: 0, total: 0 })
  const tokens = ref<TokenSummary | null>(null)
  const error = ref('')
  const switchBusy = ref(false)

  const isAdmin = computed(() => (me.value?.roles ?? []).includes('admin'))
  const canAudit = computed(() => {
    const roles = me.value?.roles ?? []
    return roles.includes('admin') || roles.includes('auditor')
  })
  const canSubmit = computed(() => {
    const roles = me.value?.roles ?? []
    return roles.includes('admin') || roles.includes('investigator') || roles.includes('oncall')
  })
  const canDeploy = computed(() => {
    const roles = me.value?.roles ?? []
    return roles.includes('admin') || roles.includes('investigator')
  })
  const pendingTotal = computed(
    () => investigationCounts.value.awaiting + deploymentCounts.value.awaiting,
  )
  const taskTotal = computed(
    () => investigationCounts.value.total + deploymentCounts.value.total,
  )
  const investigationShare = computed(() => {
    if (!taskTotal.value) return 50
    return Math.round((investigationCounts.value.total / taskTotal.value) * 100)
  })
  const deploymentShare = computed(() => 100 - investigationShare.value)

  const tokenDays = computed(() => {
    const rows = tokens.value?.days ?? []
    const max = Math.max(1, ...rows.map((item) => item.total_tokens))
    return rows.map((item) => ({
      ...item,
      height: Math.max(4, Math.round((item.total_tokens / max) * 100)),
    }))
  })
  const tokenBudgetWarning = computed(() => {
    if (!tokens.value) return ''
    if (tokens.value.budget.daily_exceeded) return '今日 Token 已超过预算'
    if (tokens.value.budget.per_task_exceeded > 0) {
      return `本月有 ${tokens.value.budget.per_task_exceeded} 个任务超过单任务预算`
    }
    return ''
  })

  const dateText = computed(() => {
    const now = new Date()
    const week = ['日', '一', '二', '三', '四', '五', '六'][now.getDay()]
    return `${now.getFullYear()} 年 ${now.getMonth() + 1} 月 ${now.getDate()} 日 · 星期${week}`
  })

  onMounted(async () => {
    try {
      me.value = await getOrLogin<MeInfo>('/auth/me')
      const [overviewData, recentList, tokenData] = await Promise.all([
        fetchOverview(),
        listTasks(undefined, 1, 5, undefined, { taskType: 'investigation' }),
        fetchTokenSummary(30),
      ])
      recent.value = recentList.items
      tokens.value = tokenData
      const investigation = overviewData.tasks.by_type.investigation
      const deployment = overviewData.tasks.by_type.deployment
      investigationCounts.value = {
        awaiting: investigation.awaiting_approval,
        running: investigation.running,
        total: investigation.total,
      }
      deploymentCounts.value = {
        awaiting: deployment.awaiting_approval,
        running: deployment.running,
        total: deployment.total,
      }
    } catch (err) {
      error.value = err instanceof Error ? err.message : '工作台加载失败'
    }
  })

  async function handleSwitch(next: boolean) {
    switchBusy.value = true
    error.value = ''
    try {
      await setSelfApprovalSwitch(next)
      if (me.value) me.value.self_approval_switch = next
    } catch (err) {
      error.value = err instanceof Error ? err.message : '切换失败'
    } finally {
      switchBusy.value = false
    }
  }

  function formatTime(iso: string | null): string {
    return iso ? formatLocalTime(iso).slice(5, 16) : '—'
  }

  function formatTokens(value: number): string {
    return new Intl.NumberFormat('zh-CN').format(value || 0)
  }

  function formatCost(value: number | null | undefined): string {
    if (value === null || value === undefined) return '未配置单价'
    return `¥${Number(value).toFixed(2)}`
  }

  function formatUnitPrice(value: number | null | undefined): string {
    if (value === null || value === undefined) return '未填'
    return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 6 }).format(value)
  }

  function statusClass(status: string): string {
    if (status === 'awaiting_approval') return 'badge-warn'
    if (status === 'running' || status === 'executing') return 'badge-info'
    if (status === 'report_ready') return 'badge-success'
    if (status === 'closed') return 'badge-muted'
    return 'badge-muted'
  }

  function statusText(task: TaskInfo): string {
    const labels: Record<string, string> = {
      draft: '已提交',
      running: '排查中',
      report_ready: '报告已生成',
      awaiting_approval: '处置待审批',
      executing: '命令执行中',
      closed: '排查已结束',
    }
    return labels[task.status] ?? task.status_text
  }

  function openTask(task: TaskInfo) {
    router.push(`/tasks/${task.id}`)
  }

  return {
    router,
    me,
    recent,
    investigationCounts,
    deploymentCounts,
    tokens,
    error,
    switchBusy,
    isAdmin,
    canAudit,
    canSubmit,
    canDeploy,
    pendingTotal,
    taskTotal,
    investigationShare,
    deploymentShare,
    tokenDays,
    tokenBudgetWarning,
    dateText,
    handleSwitch,
    formatTime,
    formatTokens,
    formatCost,
    formatUnitPrice,
    statusClass,
    statusText,
    openTask,
  }
}
