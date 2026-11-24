/**
 * 报告主答案的展示层整理。
 *
 * 只做“怎么排、怎么兜底”，不负责拉数据：
 * - answer_mode 优先读后端结论；旧报告没有该字段时按标题兜底。
 * - inventory / incidental_findings 做分组和排序，空数据回退到 conclusions。
 * - 避免把这段逻辑继续塞进 useTaskDetail.ts。
 */

import { computed, type Ref } from 'vue'

import type {
  TaskHostCoverage,
  TaskIncidentalFinding,
  TaskInfo,
  TaskInventoryItem,
  TaskReport,
} from '../../api/tasks'

export interface ReportConclusion {
  text: string
  evidence_ids: string[]
  data_status?: string
}

export interface InventoryGroup {
  host: string
  items: TaskInventoryItem[]
  coverage?: TaskHostCoverage
  counts: { total: number; running: number; failed: number }
}

const INVENTORY_RE = /有哪些服务|有哪些端口|服务清单|列一下|盘点|跑了什么|装了哪些|监听端口|进程列表|服务列表/
const DIAGNOSIS_RE = /为什么|失败原因|根因|排查|报错|异常|挂了|不可用/
const ANOMALY_RE = /failed|失败|未运行|异常|报错|冲突|不可用|退出|未启用|not running/i
const KIND_ORDER: Record<string, number> = { systemd: 0, container: 1, process: 2, port: 3 }

function normalizeInventoryItem(row: TaskInventoryItem): TaskInventoryItem {
  return {
    host: String(row.host || ''),
    service: String(row.service || ''),
    kind: String(row.kind || 'process'),
    ports: Array.isArray(row.ports) ? row.ports.map(String) : [],
    status: String(row.status || 'unknown'),
    evidence_ids: Array.isArray(row.evidence_ids) ? row.evidence_ids.map(String) : [],
  }
}

function normalizeAnswerText(value: string): string {
  return value.replace(/[\s。；，、！？：:]/g, '')
}

/** 长结论按句号/分号拆成编号点；整句过长时再按逗号兜底。 */
function splitHeadlinePoints(value: string): string[] {
  const text = value.trim()
  if (!text) return []
  const sentences = text.split(/(?<=[。！？；\n])/u).map((item) => item.trim()).filter(Boolean)
  if (sentences.length > 1) return sentences
  if (text.length <= 90) return [text]
  const clauses = text.split(/(?<=[，,])/u).map((item) => item.trim()).filter(Boolean)
  return clauses.length > 1 ? clauses : [text]
}

export function useAnswerPresentation(
  task: Ref<TaskInfo | null>,
  report: Ref<TaskReport | null>,
) {
  const answerMode = computed<'inventory' | 'diagnosis' | 'general'>(() => {
    const mode = report.value?.answer_mode
    if (mode === 'inventory' || mode === 'diagnosis' || mode === 'general') return mode
    const text = task.value?.title ?? ''
    if (INVENTORY_RE.test(text) && !DIAGNOSIS_RE.test(text)) return 'inventory'
    return 'general'
  })

  const isInventoryTask = computed(() => answerMode.value === 'inventory')

  const primaryAnswer = computed(() => {
    const explicit = String(report.value?.primary_answer || '').trim()
    if (explicit) return explicit
    // 旧报告没有 primary_answer：清单类任务用 summary 里第一句“在讲结果”的话兜底，
    // 跳过“用户要的是/本次取证”这类过程说明；不改历史 JSONB。
    if (!isInventoryTask.value) return ''
    const sentences = String(report.value?.summary || '')
      .split(/(?<=[。！？\n])/u)
      .map((item) => item.trim())
      .filter(Boolean)
    return sentences.find((item) => (
      /运行|服务|端口|进程|安装|监听|容器/.test(item)
      && !/用户要|本次|采集|取证|检查|排查/.test(item)
    )) || sentences[0] || ''
  })

  const displayHeadline = computed(() => {
    const raw = String(report.value?.headline || '').trim()
    if (isInventoryTask.value && !report.value?.answer_mode && primaryAnswer.value) {
      return primaryAnswer.value.split(/[。！？\n]/u)[0]?.trim() || raw
    }
    return raw
  })

  // 首屏长结论按 1. 2. 排列；短结论仍保持一段话。
  const headlinePoints = computed(() => splitHeadlinePoints(displayHeadline.value))

  // 旧报告把同一条答案同时放进 headline 和 summary，避免“先回答”再抄一遍。
  const primaryAnswerIsHeadline = computed(() => {
    const headline = normalizeAnswerText(displayHeadline.value)
    const primary = normalizeAnswerText(primaryAnswer.value)
    return Boolean(headline && primary && headline === primary)
  })

  const hasDisplayHeadline = computed(() => Boolean(displayHeadline.value))

  const hasPrimaryAnswer = computed(() => Boolean(primaryAnswer.value))

  const inventoryGroups = computed<InventoryGroup[]>(() => {
    const rows = (report.value?.inventory ?? []).map(normalizeInventoryItem).filter(
      (row) => row.host && row.service,
    )
    if (!rows.length) return []

    const order = (report.value?.host_coverage ?? []).map((item) => item.host)
    const groups = new Map<string, InventoryGroup>()
    for (const row of rows) {
      const group = groups.get(row.host) ?? {
        host: row.host,
        items: [],
        coverage: (report.value?.host_coverage ?? []).find((item) => item.host === row.host),
        counts: { total: 0, running: 0, failed: 0 },
      }
      group.items.push(row)
      groups.set(row.host, group)
    }

    return [...groups.values()]
      .sort((left, right) => {
        const leftIndex = order.indexOf(left.host)
        const rightIndex = order.indexOf(right.host)
        if (leftIndex >= 0 || rightIndex >= 0) {
          return (leftIndex >= 0 ? leftIndex : 999) - (rightIndex >= 0 ? rightIndex : 999)
        }
        return left.host.localeCompare(right.host, 'zh-CN')
      })
      .map((group) => {
        const items = [...group.items].sort((left, right) => {
          const leftRank = KIND_ORDER[left.kind] ?? 99
          const rightRank = KIND_ORDER[right.kind] ?? 99
          return leftRank - rightRank || left.service.localeCompare(right.service, 'zh-CN')
        })
        return {
          ...group,
          items,
          counts: {
            total: items.length,
            running: items.filter((item) => item.status === 'running').length,
            failed: items.filter((item) => item.status === 'failed').length,
          },
        }
      })
  })

  const hostCoverage = computed<TaskHostCoverage[]>(() => report.value?.host_coverage ?? [])

  const coverageIssues = computed(() =>
    hostCoverage.value.filter((item) => item.status !== 'ok'),
  )

  const incidentalFindings = computed<TaskIncidentalFinding[]>(() => {
    const rows = report.value?.incidental_findings ?? []
    if (rows.length) return rows
    // 旧报告没有独立槽位：清单类任务把 root_cause 降级为附带发现，避免它继续占首屏。
    if (isInventoryTask.value && report.value?.root_cause && report.value?.verdict === 'anomaly') {
      return [{
        object: '异常线索',
        finding: report.value.root_cause.slice(0, 300),
        severity: 'warning',
        evidence_ids: report.value.root_cause_evidence_ids ?? [],
      }]
    }
    return []
  })

  // 旧报告把 root_cause 当附带发现展示时，不再重复渲染“根因判断”卡片。
  const rootCauseIsIncidentalFallback = computed(
    () => isInventoryTask.value
      && !report.value?.incidental_findings?.length
      && Boolean(report.value?.root_cause)
      && report.value?.verdict === 'anomaly',
  )

  const orderedConclusions = computed<ReportConclusion[]>(() => {
    const items: ReportConclusion[] = (report.value?.conclusions ?? []).map((row) => ({
      text: String(row.text ?? row.conclusion ?? ''),
      evidence_ids: Array.isArray(row.evidence_ids) ? row.evidence_ids.map(String) : [],
      data_status: String(row.data_status ?? ''),
    }))
    if (!isInventoryTask.value) return items
    return [...items].sort((left, right) => {
      const leftAnomaly = ANOMALY_RE.test(left.text) ? 1 : 0
      const rightAnomaly = ANOMALY_RE.test(right.text) ? 1 : 0
      return leftAnomaly - rightAnomaly
    })
  })

  const hasStructuredInventory = computed(() => inventoryGroups.value.length > 0)

  return {
    answerMode,
    isInventoryTask,
    primaryAnswer,
    displayHeadline,
    headlinePoints,
    primaryAnswerIsHeadline,
    hasDisplayHeadline,
    hasPrimaryAnswer,
    inventoryGroups,
    hostCoverage,
    coverageIssues,
    incidentalFindings,
    rootCauseIsIncidentalFallback,
    orderedConclusions,
    hasStructuredInventory,
  }
}
