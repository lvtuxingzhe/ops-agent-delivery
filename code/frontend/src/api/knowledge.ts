/**
 * 知识库接口与前端枚举。
 *
 * 四类来源：deployment（部署经验）/ investigation（排查报告）/
 * manual（手工知识）/ document（上传文档）。
 * 前两类是后端从任务动态聚合的，只读；manual 和 document 支持新增/上传、
 * 编辑、删除。枚举的中文文案在这里集中一份，页面不再各处硬写字符串。
 */

import { del, get, getBlob, post, postForm, put } from './http'

export type KnowledgeKind = 'deployment' | 'investigation' | 'manual' | 'document'

export const KNOWLEDGE_KINDS: Array<{ value: KnowledgeKind; label: string }> = [
  { value: 'deployment', label: '部署经验' },
  { value: 'investigation', label: '排查报告' },
  { value: 'manual', label: '手工知识' },
  { value: 'document', label: '上传文档' },
]

export const KNOWLEDGE_KIND_LABELS: Record<KnowledgeKind, string> = {
  deployment: '部署经验',
  investigation: '排查报告',
  manual: '手工知识',
  document: '上传文档',
}

/** 上传文档的文件类型；与后端 `knowledge_file_service.FILE_TYPES` 一致。 */
export type KnowledgeFileType = 'pdf' | 'text' | 'excel' | 'html' | 'word' | 'markdown'

export const FILE_TYPES: Array<{ value: KnowledgeFileType; label: string }> = [
  { value: 'pdf', label: 'PDF' },
  { value: 'text', label: '文本' },
  { value: 'excel', label: 'Excel' },
  { value: 'html', label: 'HTML' },
  { value: 'word', label: 'Word' },
  { value: 'markdown', label: 'Markdown' },
]

export const FILE_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  FILE_TYPES.map((item) => [item.value, item.label]),
)

/** 前端文件选择器的接受列表；.doc 旧格式后端不支持，明确不收。 */
export const KNOWLEDGE_UPLOAD_ACCEPT =
  '.pdf,.txt,.log,.xlsx,.xlsm,.xls,.csv,.html,.htm,.docx,.md,.markdown'
export const KNOWLEDGE_UPLOAD_MAX_BYTES = 20 * 1024 * 1024

/** 技术域 15 项：8 类业务 + 7 个云/其他；顺序与后端 deployment_taxonomy 一致。 */
export const TECH_DOMAINS: Array<{ value: string; label: string }> = [
  { value: 'middleware', label: '中间件' },
  { value: 'database', label: '数据库' },
  { value: 'kubernetes', label: 'K8S' },
  { value: 'docker', label: 'Docker' },
  { value: 'dependency', label: '依赖组件' },
  { value: 'network', label: '网络' },
  { value: 'security', label: '安全' },
  { value: 'monitor', label: '监控' },
  { value: 'aliyun', label: '阿里云' },
  { value: 'tencent', label: '腾讯云' },
  { value: 'huawei', label: '华为云' },
  { value: 'aws', label: 'AWS' },
  { value: 'azure', label: 'Azure' },
  { value: 'private_cloud', label: '私有云' },
  { value: 'other', label: '其他' },
]

export const TECH_DOMAIN_LABELS: Record<string, string> = Object.fromEntries(
  TECH_DOMAINS.map((item) => [item.value, item.label]),
)

export interface KnowledgeEntry {
  id: string
  kind: KnowledgeKind
  kind_label: string
  title: string
  summary: string
  body: string
  tech_domain: string
  tech_domain_label: string
  tags: string[]
  /** 手工知识的结构化目标；空数组表示未标注，检索时回退正文匹配。 */
  target_hosts: string[]
  target_services: string[]
  source: string
  source_task_id: string
  /** 仅上传文档：原文件名、文件类型、文件大小。 */
  file_name?: string
  file_type?: string
  file_type_label?: string
  file_size?: number
  created_at: string
  updated_at: string
  /** 同簇里被裁掉的相似条数；0 表示没有折叠。 */
  deduped: number
  /** 手工条目/上传文档：当前用户能否编辑/删除。 */
  editable?: boolean
}

export interface KnowledgeListQuery {
  kind?: string
  tech_domain?: string
  file_type?: string
  keyword?: string
  days?: number
  created_from?: string
  created_to?: string
  limit?: number
  offset?: number
}

export interface KnowledgeListResponse {
  items: KnowledgeEntry[]
  total: number
  counts: Record<KnowledgeKind, number>
}

export type KnowledgeDecision = 'admit' | 'review' | 'reject'

export const KNOWLEDGE_DECISION_LABELS: Record<KnowledgeDecision, string> = {
  admit: '已入库',
  review: '待复核',
  reject: '已拒绝',
}

export interface KnowledgeDecisionItem {
  task_id: string
  source_kind: 'deployment' | 'investigation'
  decision: KnowledgeDecision
  effective_decision: KnowledgeDecision
  value_score: number
  reason: string
  signals: string[]
  title: string
  summary: string
  body: string
  tags: string[]
  tech_domain: string
  manual_override: string
  manual_reason: string
  model: string
  decided_at: string
  updated_at: string
  entry_id: string
}

export interface KnowledgeDecisionListResponse {
  items: KnowledgeDecisionItem[]
  total: number
}

/** 治理页：AI 准入结果；普通角色不可见。 */
export function listKnowledgeDecisions(query: {
  decision?: string
  source_kind?: string
  keyword?: string
  limit?: number
  offset?: number
} = {}): Promise<KnowledgeDecisionListResponse> {
  return get(`/knowledge/decisions${buildQuery(query)}`)
}

export function overrideKnowledgeDecision(
  taskId: string,
  payload: { source_kind: string; decision: KnowledgeDecision; reason: string },
): Promise<KnowledgeDecisionItem> {
  return post(`/knowledge/decisions/${encodeURIComponent(taskId)}/override`, payload)
}

export function requeueKnowledgeDecision(
  taskId: string,
  sourceKind: string,
): Promise<{ task_id: string; queued: boolean }> {
  return post(`/knowledge/decisions/${encodeURIComponent(taskId)}/judge`, {
    source_kind: sourceKind,  })
}

export interface ManualKnowledgePayload {
  title: string
  summary: string
  body: string
  tech_domain: string
  tags: string[]
  target_hosts: string[]
  target_services: string[]
}

function buildQuery(query: KnowledgeListQuery): string {
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === '' || value === null) return
    params.set(key, String(value))
  })
  const text = params.toString()
  return text ? `?${text}` : ''
}

export function listKnowledge(query: KnowledgeListQuery = {}): Promise<KnowledgeListResponse> {
  return get(`/knowledge${buildQuery(query)}`)
}

export function fetchKnowledgeEntry(entryId: string): Promise<KnowledgeEntry> {
  return get(`/knowledge/${entryId}`)
}

export function createManualKnowledge(
  payload: ManualKnowledgePayload,
): Promise<KnowledgeEntry> {
  return post('/knowledge', payload)
}

export function updateManualKnowledge(
  entryId: string,
  payload: ManualKnowledgePayload,
): Promise<KnowledgeEntry> {
  return put(`/knowledge/${entryId}`, payload)
}

/** 软删除；后端保留审计痕迹，列表里立刻看不到。 */
export function deleteManualKnowledge(entryId: string): Promise<{ deleted: boolean; id: string }> {
  return del(`/knowledge/${entryId}`)
}

export interface UploadKnowledgeOptions {
  title?: string
  techDomain?: string
}

/** 上传 PDF/文本/Excel/HTML/Word/Markdown；后端抽取文字后返回知识条目。 */
export function uploadKnowledgeDocument(
  file: File,
  options: UploadKnowledgeOptions = {},
): Promise<KnowledgeEntry> {
  const form = new FormData()
  form.append('file', file, file.name)
  form.append('title', options.title ?? '')
  form.append('tech_domain', options.techDomain ?? '')
  return postForm('/knowledge/upload', form)
}

/** 下载上传文档的原文件；浏览器拿到 Blob 后再触发保存。 */
export function downloadKnowledgeFile(entryId: string): Promise<Blob> {
  return getBlob(`/knowledge/${encodeURIComponent(entryId)}/file`)
}

export interface KnowledgeCurationStats {
  total: number
  admitted: number
  review: number
  rejected: number
  admit_ratio: number
  review_ratio: number
  reject_ratio: number
  overrides: number
  override_rate: number
  rejudged: number
  ai_failures: number
  retrieval: {
    window_days: number
    total: number
    hits: number
    hit_rate: number
    avg_top_score: number
    max_top_score: number
  }
}

/** 治理页统计卡：准入分布、改判、重判、AI 失败和近期检索。 */
export function fetchKnowledgeCurationStats(
  days = 7,
): Promise<KnowledgeCurationStats> {
  return get(`/knowledge/curation/stats?days=${days}`)
}
