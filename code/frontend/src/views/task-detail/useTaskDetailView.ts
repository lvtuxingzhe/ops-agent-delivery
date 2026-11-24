/**
 * 排查详情的报告展示派生状态。
 *
 * 只把 `report` 整理成模板可直接使用的字段，不发请求、不碰路由、不管理轮询。
 * `useTaskDetail.ts` 仍负责数据加载和动作编排。
 */

import { computed, ref, type Ref } from 'vue'

import type { TaskReport } from '../../api/tasks'
import { formatLocalTime } from '../../utils/time'
import { evidenceProbeLabel, evidenceTargetLabel, groupEvidence } from './evidencePresentation'
import {
  formatRootCauseMarkdown,
  formatSolutionMarkdown,
  parseSolutionSteps,
  renderMarkdown,
  type SolutionStep,
} from './markdown'

export interface ReportConclusion {
  text?: string
  evidence_ids?: string[]
  data_status?: string
}

export function useTaskDetailView(report: Ref<TaskReport | null>) {
  const conclusions = computed<ReportConclusion[]>(() => {
    const items = report.value?.conclusions ?? []
    return items.map((item) => ({
      text: String(item.text ?? item.conclusion ?? JSON.stringify(item)),
      evidence_ids: Array.isArray(item.evidence_ids) ? item.evidence_ids.map(String) : [],
      data_status: String(item.data_status ?? ''),
    }))
  })

  const hasReportContent = computed(() => {
    const current = report.value
    if (!current) return false
    return Boolean(
      current.headline?.trim()
        || current.summary?.trim()
        || current.conclusions?.length
        || current.evidences?.length
        || current.unknowns?.length,
    )
  })

  const evidenceById = computed(() => {
    return new Map((report.value?.evidences ?? []).map((item) => [item.id, item]))
  })

  const realEvidences = computed(() =>
    (report.value?.evidences ?? []).filter((item) => !item.fake),
  )

  const evidenceGroups = computed(() => groupEvidence(
    realEvidences.value,
    (item) => item.source === 'ssh'
      ? `host:${evidenceTargetLabel(item.target)}`
      : `source:${item.source}`,
    (item) => item.source === 'ssh'
      ? evidenceTargetLabel(item.target) || 'SSH 其他'
      : sourceName(item.source),
  ))

  const citedEvidenceIds = computed(() =>
    new Set(
      (report.value?.conclusions ?? []).flatMap((item) => item.evidence_ids ?? []),
    ),
  )

  const citedRealEvidences = computed(() =>
    realEvidences.value.filter((item) => citedEvidenceIds.value.has(item.id)),
  )

  const fakeEvidenceCount = computed(() =>
    (report.value?.evidences ?? []).filter((item) => item.fake).length,
  )

  const sourceNames: Record<string, string> = {
    prometheus: 'Prometheus',
    elk: 'ELK',
    k8s: 'K8s',
    docker: '容器',
    ssh: 'SSH',
    manual_screenshot: '截图',
  }

  function sourceName(source: string): string {
    return sourceNames[source] ?? source
  }

  function dataStatusLabel(status: string): string {
    if (status === 'ok') return '可用'
    if (status === 'no_data') return '无数据'
    if (status === 'failed') return '采集失败'
    if (status === 'unknown') return '待确认'
    return status || '—'
  }

  function hypothesisStatusLabel(status: string): string {
    if (status === 'supported') return '证据支持'
    if (status === 'rejected') return '已排除'
    if (status === 'inconclusive') return '证据不足'
    return status || '待确认'
  }

  function sourceLabelForEvidence(ids?: string[]): string {
    const labels = [
      ...new Set(
        (ids ?? [])
          .map((id) => evidenceById.value.get(id)?.source)
          .filter((source): source is string => Boolean(source))
          .map(sourceName),
      ),
    ]
    return labels.slice(0, 2).join(' · ') || '报告结论'
  }

  function evidenceLabel(id: string): string {
    const item = evidenceById.value.get(id)
    if (!item) return id
    const host = evidenceTargetLabel(item.target)
    if (host) {
      const probe = evidenceProbeLabel(item.id, item.target)
      return `${host} · ${probe || '记录'}`
    }
    return `${sourceName(item.source)} · ${formatLocalTime(item.observed_at, true).slice(11, 16)}`
  }

  const findingGroups = computed(() => {
    const items = conclusions.value.map((item, index) => {
      const evidence = (item.evidence_ids ?? [])
        .map((id) => evidenceById.value.get(id))
        .filter((value): value is NonNullable<typeof value> => Boolean(value))
      const text = item.text ?? ''
      const fakeOnly = evidence.length > 0 && evidence.every((entry) => entry.fake)
      const hasMissing = evidence.some((entry) => entry.data_status !== 'ok')
      let tone: 'confirmed' | 'needs' | 'untrusted' = 'confirmed'
      if (fakeOnly || /演练数据|fake=true/i.test(text)) {
        tone = 'untrusted'
      } else if (hasMissing || /无数据|no_data|未探测|不确定|未知|未覆盖|没有其他|暂不能判断/i.test(text)) {
        tone = 'needs'
      }
      return {
        ...item,
        index,
        tone,
        sourceLabel: sourceLabelForEvidence(item.evidence_ids),
      }
    })
    return [
      { key: 'confirmed', label: '已核实', description: '有可采信数据支撑，可作为判断依据。' },
      { key: 'needs', label: '还缺数据', description: '数据缺失或无法确认，不能单独下结论。' },
      { key: 'untrusted', label: '未采用', description: '演练或示例数据，不参与判断。' },
    ].map((group) => ({ ...group, items: items.filter((item) => item.tone === group.key) }))
  })

  const visibleFindingGroups = computed(() =>
    findingGroups.value.filter((group) => group.key !== 'untrusted'),
  )

  const untrustedCount = computed(() =>
    findingGroups.value.find((group) => group.key === 'untrusted')?.items.length ?? 0,
  )

  const factCards = computed(() => {
    const evidences = report.value?.evidences ?? []
    const allReal = evidences.filter((item) => !item.fake && item.data_status === 'ok').length
    const citedReal = citedRealEvidences.value.filter((item) => item.data_status === 'ok').length
    const fake = evidences.filter((item) => item.fake).length
    const unknown = report.value?.unknowns.length ?? 0
    const totalConclusions = report.value?.conclusions_count ?? 0
    const confirmed = findingGroups.value.find((group) => group.key === 'confirmed')?.items.length ?? 0
    const cards = [
      {
        label: '结论引用证据',
        value: String(citedReal),
        unit: '条',
        hint: `共采集 ${allReal} 条`,
        tone: 'success',
      },
      { label: '演练数据已排除', value: String(fake), unit: '条', hint: '不参与判断', tone: 'muted' },
      { label: '还缺数据', value: String(unknown), unit: '项', hint: '补后才能判断', tone: 'warn' },
      {
        label: 'AI 结论',
        value: String(totalConclusions),
        unit: '条',
        hint: report.value?.verdict === 'insufficient'
          ? `已核实 ${confirmed} 条间接事实`
          : `已核实 ${confirmed} 条`,
        tone: 'info',
      },
    ]
    // 真实任务没有演练数据时，不拿 “排除 0 条” 占一格；有旧演练证据时才展示。
    return cards.filter((card) => card.label !== '演练数据已排除' || fake > 0)
  })

  const summaryPoints = computed(() => {
    const summary = (report.value?.summary ?? '').trim()
    if (!summary) return []
    // 一句话一段，保留句末标点；比一整块长段落更适合扫读。
    const points = summary
      .split(/(?<=[。！？；])/u)
      .map((item) => item.trim())
      .filter(Boolean)
    return points.length ? points : [summary]
  })

  const solutionStepCount = computed(() => {
    const lines = (report.value?.solution ?? '').split(/\r?\n/)
    return lines.filter((line) => /^\d+[.、]/.test(line.trim())).length
  })

  const solutionHtml = computed(() =>
    renderMarkdown(formatSolutionMarkdown(report.value?.solution ?? '')),
  )
  const solutionSteps = computed(() => parseSolutionSteps(report.value?.solution ?? ''))
  const copiedSolutionStep = ref(0)
  const solutionCount = computed(() => solutionSteps.value.length || solutionStepCount.value)
  const evidenceCount = computed(() => conclusions.value.length)

  async function copySolutionCommands(step: SolutionStep): Promise<void> {
    if (!step.commands.length) return
    try {
      await navigator.clipboard.writeText(step.commands.join('\n'))
      copiedSolutionStep.value = step.index
      window.setTimeout(() => {
        if (copiedSolutionStep.value === step.index) copiedSolutionStep.value = 0
      }, 1600)
    } catch {
      copiedSolutionStep.value = 0
    }
  }

  const rootCauseHtml = computed(() =>
    renderMarkdown(formatRootCauseMarkdown(report.value?.root_cause ?? '')),
  )

  const rootVerification = computed(() => {
    const current = report.value
    if (!current) return null
    if (!current.root_cause && current.verdict !== 'anomaly') return null
    const hypotheses = current.hypotheses ?? []
    const supported = hypotheses.filter((item) => item.status === 'supported' && item.evidence_ids.length > 0)
    const rejected = hypotheses.filter((item) => item.status === 'rejected')
    const inconclusive = hypotheses.filter((item) => item.status === 'inconclusive')
    const evidenceIds = Array.from(
      new Set(
        current.root_cause_evidence_ids?.length
          ? current.root_cause_evidence_ids
          : supported.flatMap((item) => item.evidence_ids),
      ),
    )
    const human = current.root_cause_source === 'human'
    return {
      human,
      note: current.root_cause_note ?? '',
      verified: human || (current.root_cause_verified ?? Boolean(current.root_cause && supported.length)),
      supported: supported.length,
      rejected: rejected.length,
      inconclusive: inconclusive.length,
      evidenceIds: human ? [] : evidenceIds,
    }
  })

  const verdictText = computed(() => {
    const verdict = report.value?.verdict
    if (verdict === 'no_anomaly') return '范围内未发现异常'
    if (verdict === 'anomaly') return '发现异常'
    if (verdict === 'insufficient') return '证据不足'
    return ''
  })

  const verdictTone = computed(() => {
    const verdict = report.value?.verdict
    if (verdict === 'no_anomaly') return 'badge-success'
    if (verdict === 'anomaly') return 'badge-danger'
    if (verdict === 'insufficient') return 'badge-warn'
    return 'badge-muted'
  })

  const hasHeadline = computed(() => Boolean(report.value?.headline?.trim()))

  return {
    conclusions,
    hasReportContent,
    evidenceById,
    realEvidences,
    evidenceGroups,
    citedEvidenceIds,
    citedRealEvidences,
    fakeEvidenceCount,
    sourceName,
    dataStatusLabel,
    hypothesisStatusLabel,
    sourceLabelForEvidence,
    evidenceLabel,
    findingGroups,
    visibleFindingGroups,
    untrustedCount,
    factCards,
    summaryPoints,
    solutionStepCount,
    solutionHtml,
    solutionSteps,
    copiedSolutionStep,
    solutionCount,
    evidenceCount,
    copySolutionCommands,
    rootCauseHtml,
    rootVerification,
    verdictText,
    verdictTone,
    hasHeadline,
  }
}
