/**
 * 知识治理页状态与动作。页面只保留模板渲染。
 */

import { computed, onMounted, ref } from 'vue'

import { fetchMe, type MeInfo } from '../../api/auth'
import {
  KNOWLEDGE_DECISION_LABELS,
  fetchKnowledgeCurationStats,
  listKnowledgeDecisions,
  overrideKnowledgeDecision,
  requeueKnowledgeDecision,
  type KnowledgeCurationStats,
  type KnowledgeDecision,
  type KnowledgeDecisionItem,
} from '../../api/knowledge'
import { formatLocalTime } from '../../utils/time'

export function useKnowledgeGovernance() {
const me = ref<MeInfo | null>(null)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const items = ref<KnowledgeDecisionItem[]>([])
const total = ref(0)
const offset = ref(0)
const pageSize = 50
const detail = ref<KnowledgeDecisionItem | null>(null)

const filters = ref<{ decision: string; sourceKind: string; keyword: string }>({
  decision: 'review',
  sourceKind: '',
  keyword: '',
})

const overrideForm = ref<{ decision: KnowledgeDecision; reason: string }>({
  decision: 'review',
  reason: '',
})
const saving = ref(false)
const requeueing = ref(false)
const stats = ref<KnowledgeCurationStats | null>(null)
const statsLoading = ref(false)

const canOverride = computed(() => (me.value?.roles ?? []).includes('admin'))
const canApprove = computed(() => (me.value?.roles ?? []).includes('admin'))
const hasMore = computed(() => items.value.length < total.value)

function query() {
  return {
    decision: filters.value.decision || undefined,
    source_kind: filters.value.sourceKind || undefined,
    keyword: filters.value.keyword.trim() || undefined,
    limit: pageSize,
    offset: offset.value,
  }
}

async function load(append = false) {
  loading.value = true
  error.value = ''
  offset.value = append ? offset.value : 0
  try {
    const resp = await listKnowledgeDecisions(query())
    items.value = append ? [...items.value, ...resp.items] : resp.items
    total.value = resp.total
  } catch (err) {
    error.value = err instanceof Error ? err.message : '准入记录加载失败'
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  statsLoading.value = true
  try {
    stats.value = await fetchKnowledgeCurationStats(7)
  } catch {
    // 统计卡失败不影响准入列表，保留上一次结果。
  } finally {
    statsLoading.value = false
  }
}

async function applyFilters() {
  offset.value = 0
  await load()
}

async function loadMore() {
  offset.value += pageSize
  await load(true)
}

function openDetail(item: KnowledgeDecisionItem) {
  detail.value = item
  overrideForm.value = { decision: item.effective_decision, reason: '' }
}

async function submitOverride() {
  const item = detail.value
  const form = overrideForm.value
  if (!item) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    await overrideKnowledgeDecision(item.task_id, {
      source_kind: item.source_kind,
      decision: form.decision,
      reason: form.reason.trim(),
    })
    notice.value = '改判已保存，知识库列表会按新决策生效'
    detail.value = null
    await Promise.all([load(), loadStats()])
  } catch (err) {
    error.value = err instanceof Error ? err.message : '改判失败'
  } finally {
    saving.value = false
  }
}

async function requeue(item: KnowledgeDecisionItem) {
  requeueing.value = true
  error.value = ''
  notice.value = ''
  try {
    await requeueKnowledgeDecision(item.task_id, item.source_kind)
    notice.value = '已重新入队，等待 worker 重判'
    await loadStats()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '重新入队失败'
  } finally {
    requeueing.value = false
  }
}

function decisionClass(value: string): string {
  if (value === 'admit') return 'badge-success'
  if (value === 'review') return 'badge-warn'
  return 'badge-danger'
}

function decisionLabel(value: string): string {
  return KNOWLEDGE_DECISION_LABELS[value as KnowledgeDecision] ?? value
}

onMounted(async () => {
  try {
    me.value = await fetchMe()
  } catch {
    // 权限最终由接口保障
  }
  await load()
  await loadStats()
})

  return {
    me,
    loading,
    error,
    notice,
    items,
    total,
    offset,
    pageSize,
    detail,
    filters,
    overrideForm,
    saving,
    requeueing,
    stats,
    statsLoading,
    canOverride,
    canApprove,
    hasMore,
    query,
    load,
    applyFilters,
    loadMore,
    openDetail,
    submitOverride,
    requeue,
    decisionClass,
    decisionLabel,
  }
}
