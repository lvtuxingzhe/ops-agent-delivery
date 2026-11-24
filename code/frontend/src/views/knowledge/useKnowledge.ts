/**
 * 知识库页状态与动作。页面只保留模板渲染。
 */

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { fetchMe, type MeInfo } from '../../api/auth'
import {
  createManualKnowledge,
  deleteManualKnowledge,
  downloadKnowledgeFile,
  fetchKnowledgeEntry,
  FILE_TYPES,
  KNOWLEDGE_KINDS,
  KNOWLEDGE_UPLOAD_ACCEPT,
  KNOWLEDGE_UPLOAD_MAX_BYTES,
  listKnowledge,
  TECH_DOMAINS,
  uploadKnowledgeDocument,
  updateManualKnowledge,
  type KnowledgeEntry,
  type KnowledgeKind,
} from '../../api/knowledge'
import { downloadCsv, exportStamp, MAX_EXPORT_ROWS, type CsvColumn } from '../../utils/csv'
import { formatLocalTime } from '../../utils/time'

export function useKnowledge() {
const route = useRoute()
const me = ref<MeInfo | null>(null)
const loading = ref(false)
const exporting = ref(false)
const error = ref('')
const notice = ref('')

const entries = ref<KnowledgeEntry[]>([])
const total = ref(0)
const counts = ref<Record<KnowledgeKind, number>>({
  deployment: 0,
  investigation: 0,
  manual: 0,
  document: 0,
})
const offset = ref(0)
const pageSize = 50

const filters = ref({ kind: '', techDomain: '', fileType: '', range: '', keyword: '' })
const appliedKeyword = ref('')

const detail = ref<KnowledgeEntry | null>(null)
const detailLoading = ref(false)
const deleting = ref(false)
const confirmDelete = ref(false)
const downloading = ref(false)

const editor = ref<{
  id: string | null
  title: string
  summary: string
  body: string
  techDomain: string
  tags: string
  targetHosts: string
  targetServices: string
} | null>(null)
const bodyLength = computed(() => editor.value?.body.length ?? 0)

/**
 * 自动知识正文按“现象 / 根因 / 处理步骤 / 结论”拆块。
 * 只拆自动条目；手工知识和上传文档保持整段正文，不猜结构。
 */
type BodyBlock = { title: string; lines: string[] }
const BODY_HEAD_RE =
  /^(现象|判断|摘要|根因|处理步骤|初步方案|方案|结论|结果|目标|关键处理|原因码|原因)：(.*)$/
const bodyBlocks = computed<BodyBlock[]>(() => {
  if (!detail.value || detail.value.kind === 'manual' || detail.value.kind === 'document') {
    return []
  }
  const lines = (detail.value.body || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  if (!lines.length) return []
  const blocks: BodyBlock[] = []
  for (const line of lines) {
    const match = line.match(BODY_HEAD_RE)
    if (match) {
      blocks.push({ title: match[1], lines: match[2] ? [match[2]] : [] })
      continue
    }
    if (!blocks.length) blocks.push({ title: '', lines: [] })
    blocks[blocks.length - 1].lines.push(line)
  }
  return blocks
})
const lineClass = (line: string) => ({
  'body-step': /^\d+[.、]/.test(line),
  'body-bullet': line.startsWith('-'),
})
const saving = ref(false)

const upload = ref<{
  file: File | null
  title: string
  techDomain: string
} | null>(null)
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const canWrite = computed(() => {
  const roles = me.value?.roles ?? []
  return roles.includes('admin') || roles.includes('investigator')
})
const canGovern = computed(() => {
  const roles = me.value?.roles ?? []
  return roles.includes('admin') || roles.includes('auditor')
})

const hasMore = computed(() => entries.value.length < total.value)

const activeFilters = computed(
  () => Boolean(
    filters.value.kind || filters.value.techDomain || filters.value.fileType
      || filters.value.range || appliedKeyword.value,
  ),
)

onMounted(async () => {
  const kind = String(route.query.kind || '')
  if (KNOWLEDGE_KINDS.some((item) => item.value === kind)) filters.value.kind = kind
  const keyword = String(route.query.keyword || '')
  if (keyword) {
    filters.value.keyword = keyword
    appliedKeyword.value = keyword
  }
  try {
    me.value = await fetchMe()
  } catch {
    // 读写权限最终由接口保障
  }
  await load()
})

function timeRangeToDays(): number {
  if (filters.value.range === '7d') return 7
  if (filters.value.range === '30d') return 30
  if (filters.value.range === '90d') return 90
  return 0
}

function query(extra: Record<string, string | number> = {}) {
  return {
    kind: filters.value.kind || undefined,
    tech_domain: filters.value.techDomain || undefined,
    file_type: filters.value.fileType || undefined,
    keyword: appliedKeyword.value || undefined,
    days: timeRangeToDays() || undefined,
    limit: pageSize,
    ...extra,
  }
}

async function load(append = false) {
  loading.value = true
  error.value = ''
  try {
    const nextOffset = append ? offset.value : 0
    const resp = await listKnowledge(query({ offset: nextOffset }))
    entries.value = append ? [...entries.value, ...resp.items] : resp.items
    total.value = resp.total
    counts.value = resp.counts
    offset.value = nextOffset
    // 选中的条目被新筛选排除了，就收起详情，避免右侧还挂着一条列表里没有的知识。
    if (detail.value && !entries.value.some((item) => item.id === detail.value?.id)) {
      detail.value = null
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '知识库加载失败'
  } finally {
    loading.value = false
  }
}

async function applyFilters() {
  appliedKeyword.value = filters.value.keyword.trim()
  await load()
}

async function clearFilters() {
  filters.value = { kind: '', techDomain: '', fileType: '', range: '', keyword: '' }
  appliedKeyword.value = ''
  await load()
}

function filterByKind(kind: KnowledgeKind) {
  filters.value.kind = filters.value.kind === kind ? '' : kind
  void load()
}

async function loadMore() {
  offset.value += pageSize
  await load(true)
}

const exportColumns: CsvColumn<KnowledgeEntry>[] = [
  { label: '知识 ID', value: (item) => item.id },
  { label: '类型', value: (item) => item.kind_label || item.kind },
  { label: '标题', value: (item) => item.title },
  { label: '摘要', value: (item) => item.summary },
  { label: '技术域', value: (item) => item.tech_domain_label || item.tech_domain },
  { label: '标签', value: (item) => (item.tags || []).join(' / ') },
  { label: '目标主机', value: (item) => (item.target_hosts || []).join(' / ') },
  { label: '目标服务', value: (item) => (item.target_services || []).join(' / ') },
  { label: '来源', value: (item) => item.source },
  { label: '来源任务', value: (item) => item.source_task_id },
  { label: '文件名', value: (item) => item.file_name || '' },
  { label: '文件类型', value: (item) => item.file_type_label || item.file_type || '' },
  { label: '更新时间', value: (item) => formatLocalTime(item.updated_at) },
  { label: '创建时间', value: (item) => formatLocalTime(item.created_at) },
]

/** 按当前筛选导出全量知识条目；列表是 offset 分页，逐页拉取。 */
async function exportKnowledge() {
  if (exporting.value) return
  exporting.value = true
  error.value = ''
  notice.value = ''
  try {
    const all: KnowledgeEntry[] = []
    let nextOffset = 0
    let serverTotal = 0
    // 按服务端 total 拉全；50,000 条只是前端安全上限，触发时明确提示。
    while (all.length < MAX_EXPORT_ROWS) {
      const result = await listKnowledge(query({ offset: nextOffset }))
      serverTotal = result.total
      all.push(...result.items)
      if (all.length >= serverTotal || result.items.length === 0) break
      nextOffset += pageSize
    }
    if (!all.length) {
      error.value = '当前筛选没有可导出的知识条目'
      return
    }
    downloadCsv(`知识库-${exportStamp()}.csv`, exportColumns, all)
    notice.value = all.length < serverTotal
      ? `已导出 ${all.length} 条，筛选共 ${serverTotal} 条；达到 ${MAX_EXPORT_ROWS} 条安全上限`
      : `已导出 ${all.length} 条知识`
  } catch (err) {
    error.value = err instanceof Error ? err.message : '导出失败'
  } finally {
    exporting.value = false
  }
}

async function openDetail(entry: KnowledgeEntry) {
  detailLoading.value = true
  confirmDelete.value = false
  error.value = ''
  try {
    detail.value = await fetchKnowledgeEntry(entry.id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '知识详情加载失败'
  } finally {
    detailLoading.value = false
  }
}

function openCreate() {
  editor.value = {
    id: null,
    title: '',
    summary: '',
    body: '',
    techDomain: 'other',
    tags: '',
    targetHosts: '',
    targetServices: '',
  }
  error.value = ''
  notice.value = ''
}

function openUpload() {
  upload.value = { file: null, title: '', techDomain: 'other' }
  error.value = ''
  notice.value = ''
}

function closeUpload() {
  upload.value = null
  uploading.value = false
}

function fileStem(filename: string): string {
  const index = filename.lastIndexOf('.')
  return (index > 0 ? filename.slice(0, index) : filename).trim()
}

function acceptUploadFile(file: File | null | undefined) {
  if (!upload.value) return
  if (!file) return
  if (file.size > KNOWLEDGE_UPLOAD_MAX_BYTES) {
    error.value = '文件超过 20MB，请压缩后再传'
    return
  }
  upload.value.file = file
  if (!upload.value.title.trim()) upload.value.title = fileStem(file.name)
  error.value = ''
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  acceptUploadFile(input.files?.[0])
}

function onFileDrop(event: DragEvent) {
  event.preventDefault()
  acceptUploadFile(event.dataTransfer?.files?.[0])
}

async function submitUpload() {
  const current = upload.value
  if (!current?.file) {
    error.value = '请选择要上传的文档'
    return
  }
  uploading.value = true
  error.value = ''
  notice.value = ''
  try {
    const saved = await uploadKnowledgeDocument(current.file, {
      title: current.title.trim(),
      techDomain: current.techDomain,
    })
    upload.value = null
    detail.value = saved
    notice.value = '文档已上传并进入知识库，可直接按文件类型筛选'
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '文档上传失败'
  } finally {
    uploading.value = false
  }
}

async function downloadFile() {
  const entry = detail.value
  if (!entry?.file_type) return
  downloading.value = true
  error.value = ''
  try {
    const blob = await downloadKnowledgeFile(entry.id)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = entry.file_name || 'document'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '原文件下载失败'
  } finally {
    downloading.value = false
  }
}

function formatBytes(bytes?: number): string {
  if (!bytes || bytes <= 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function openEdit(entry: KnowledgeEntry) {
  editor.value = {
    id: entry.id,
    title: entry.title,
    summary: entry.summary,
    body: entry.body,
    techDomain: entry.tech_domain || 'other',
    tags: (entry.tags ?? []).join(', '),
    targetHosts: (entry.target_hosts ?? []).join(', '),
    targetServices: (entry.target_services ?? []).join(', '),
  }
  error.value = ''
  notice.value = ''
}

function splitCommaList(value: string): string[] {
  return value
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

async function saveManual() {
  const current = editor.value
  if (!current) return
  const title = current.title.trim()
  if (!title) {
    error.value = '请填写知识标题'
    return
  }
  saving.value = true
  error.value = ''
  notice.value = ''
  const payload = {
    title,
    summary: current.summary.trim(),
    body: current.body,
    tech_domain: current.techDomain,
    tags: splitCommaList(current.tags),
    target_hosts: splitCommaList(current.targetHosts),
    target_services: splitCommaList(current.targetServices),
  }
  try {
    const saved = current.id
      ? await updateManualKnowledge(current.id, payload)
      : await createManualKnowledge(payload)
    editor.value = null
    notice.value = current.id ? '知识已更新' : '知识已新增'
    detail.value = saved
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '知识保存失败'
  } finally {
    saving.value = false
  }
}

async function removeManual() {
  const entry = detail.value
  if (!entry) return
  deleting.value = true
  error.value = ''
  notice.value = ''
  try {
    await deleteManualKnowledge(entry.id)
    confirmDelete.value = false
    detail.value = null
    notice.value = '知识已删除（软删除，审计仍可追溯）'
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '知识删除失败'
  } finally {
    deleting.value = false
  }
}

function kindBadgeClass(kind: KnowledgeKind): string {
  if (kind === 'deployment') return 'badge-info'
  if (kind === 'investigation') return 'badge-warn'
  if (kind === 'document') return 'badge-muted'
  return 'badge-success'
}

function summaryText(entry: KnowledgeEntry): string {
  return entry.summary || entry.body.slice(0, 160) || '这条知识没有摘要。'
}

  return {
    route,
    me,
    loading,
    exporting,
    error,
    notice,
    entries,
    total,
    counts,
    offset,
    pageSize,
    filters,
    appliedKeyword,
    detail,
    detailLoading,
    deleting,
    confirmDelete,
    downloading,
    editor,
    bodyLength,
    BODY_HEAD_RE,
    bodyBlocks,
    lineClass,
    saving,
    upload,
    uploading,
    fileInput,
    canWrite,
    canGovern,
    hasMore,
    activeFilters,
    timeRangeToDays,
    query,
    load,
    applyFilters,
    clearFilters,
    filterByKind,
    loadMore,
    exportColumns,
    exportKnowledge,
    openDetail,
    openCreate,
    openUpload,
    closeUpload,
    fileStem,
    acceptUploadFile,
    onFileChange,
    onFileDrop,
    submitUpload,
    downloadFile,
    formatBytes,
    openEdit,
    saveManual,
    removeManual,
    kindBadgeClass,
    summaryText,
  }
}
