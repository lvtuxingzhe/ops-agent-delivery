/**
 * 部署模板库的状态与动作。
 *
 * 页面只做呈现；模板筛选、编辑器状态、校验/保存/发布、AI 草稿与
 * 从任务沉淀都收在这里。权限仍以接口返回的 can_edit / can_publish 为准。
 */

import { computed, onMounted, reactive, ref } from 'vue'

import { fetchMe, type MeInfo } from '../../api/auth'
import {
  cloneDeployTemplate,
  createDeployTemplate,
  createTemplateFromTask,
  deleteDeployTemplate,
  DEPLOY_TYPES,
  DEPLOY_TYPE_LABELS,
  disableDeployTemplate,
  fetchTemplate,
  generateAiTemplate,
  listTemplateHistory,
  listTemplateLibrary,
  publishDeployTemplate,
  updateDeployTemplate,
  validateDeployTemplate,
  type AiTraceItem,
  type DeployTemplate,
  type TemplateHistoryItem,
  type TemplateStatus,
} from '../../api/deployments'
import { useAsyncAction } from '../../composables/useAsyncAction'
import {
  TEMPLATE_COLUMNS,
  TEMPLATE_COLUMN_WIDTHS_STORAGE_KEY,
  TEMPLATE_GRID,
  blankEditor,
  blankRow,
  editorFromTemplate,
  formatTime,
  payloadFromEditor,
  riskText,
  sourceText,
  statusClass,
  statusText,
  type CommandStage,
  type EditorState,
} from './model'

export function useDeployTemplates() {
  const me = ref<MeInfo | null>(null)
  const items = ref<DeployTemplate[]>([])
  const loading = ref(true)
  const search = ref('')
  const statusFilter = ref<'all' | TemplateStatus>('all')
  const templateTable = ref<{ reset: () => void } | null>(null)
  const hasCustomTemplateWidths = ref(false)
  const editor = ref<EditorState | null>(null)
  const history = ref<{ item: DeployTemplate; rows: TemplateHistoryItem[] } | null>(null)
  const aiOpen = ref(false)
  const aiTrace = ref<AiTraceItem[]>([])
  const fromTaskOpen = ref(false)
  const fromTaskId = ref('')
  const fromTaskBusy = ref(false)
  const ai = reactive({ goal: '', platform: 'linux' as 'linux' | 'windows', host: 'local', commands: '' })

  const { busy, error, message, run } = useAsyncAction(load)

  const isAdmin = computed(() => (me.value?.roles ?? []).includes('admin'))
  const canCreate = computed(() => {
    const roles = me.value?.roles ?? []
    return roles.includes('admin') || roles.includes('investigator')
  })

  const filtered = computed(() => {
    const keyword = search.value.trim().toLowerCase()
    return items.value.filter((item) => {
      if (statusFilter.value !== 'all' && item.status !== statusFilter.value) return false
      if (!keyword) return true
      return [item.id, item.name, item.description].join(' ').toLowerCase().includes(keyword)
    })
  })

  const stats = computed(() => {
    const published = items.value.filter((item) => item.status === 'published')
    return {
      published: published.length,
      draft: items.value.filter((item) => item.status === 'draft').length,
      disabled: items.value.filter((item) => item.status === 'disabled').length,
      usage: items.value.reduce((sum, item) => sum + Number(item.usage_count ?? 0), 0),
      success: items.value.reduce((sum, item) => sum + Number(item.success_count ?? 0), 0),
    }
  })

  onMounted(async () => {
    try {
      me.value = await fetchMe()
    } catch {
      // 页面权限由接口保障
    }
    await load()
  })

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const resp = await listTemplateLibrary()
      items.value = resp.items
    } catch (err) {
      error.value = err instanceof Error ? err.message : '模板库加载失败'
    } finally {
      loading.value = false
    }
  }

  function openCreate() {
    aiTrace.value = []
    editor.value = blankEditor()
  }

  async function openEdit(item: DeployTemplate) {
    aiTrace.value = []
    try {
      const detail = await fetchTemplate(item.id)
      editor.value = editorFromTemplate(detail)
      aiTrace.value = detail.ai_trace ?? []
    } catch (err) {
      error.value = err instanceof Error ? err.message : '模板详情加载失败'
    }
  }

  function addRow(kind: CommandStage) {
    if (!editor.value) return
    const readOnly = ['precheck', 'verify', 'postRollback'].includes(kind)
    editor.value[kind].push(blankRow(readOnly ? 'read' : 'dangerous', readOnly))
  }

  function removeRow(kind: CommandStage, index: number) {
    if (!editor.value) return
    const optional = ['precheck', 'postRollback'].includes(kind)
    if (!optional && editor.value[kind].length <= 1) return
    if (index >= 0 && index < editor.value[kind].length) {
      editor.value[kind].splice(index, 1)
    }
  }

  async function checkTemplate() {
    if (!editor.value) return
    busy.value = true
    error.value = ''
    message.value = ''
    try {
      const result = await validateDeployTemplate(payloadFromEditor(editor.value))
      message.value = `校验通过：${result.command_count} 条命令，风险 ${result.risk}。${result.warnings[0] ?? ''}`
    } catch (err) {
      error.value = err instanceof Error ? err.message : '校验失败'
    } finally {
      busy.value = false
    }
  }

  async function saveTemplate(publishAfterSave = false) {
    if (!editor.value) return
    if (!editor.value.name.trim()) {
      error.value = '模板名称必填'
      return
    }
    busy.value = true
    error.value = ''
    message.value = ''
    try {
      const payload = payloadFromEditor(editor.value)
      let saved: DeployTemplate
      if (editor.value.id) {
        const resp = await updateDeployTemplate(editor.value.id, payload)
        saved = resp.item
      } else {
        const resp = await createDeployTemplate(payload)
        saved = resp.item
      }
      if (publishAfterSave && isAdmin.value && saved.status !== 'published') {
        const resp = await publishDeployTemplate(saved.id)
        saved = resp.item
      }
      editor.value = null
      message.value = saved.status === 'published'
        ? '模板已发布，部署页现在可以选到它。'
        : '草稿已保存；管理员发布后团队才能使用。'
      await load()
    } catch (err) {
      error.value = err instanceof Error ? err.message : '保存模板失败'
    } finally {
      busy.value = false
    }
  }

  async function cloneItem(item: DeployTemplate) {
    await run(async () => {
      const resp = await cloneDeployTemplate(item.id)
      editor.value = editorFromTemplate(resp.item)
    }, '已克隆成草稿，可以继续编辑。')
  }

  async function publishItem(item: DeployTemplate) {
    await run(() => publishDeployTemplate(item.id), `已发布 v${item.version}。`)
  }

  async function disableItem(item: DeployTemplate) {
    await run(() => disableDeployTemplate(item.id), '已停用；已创建但未执行的方案会在执行前被复验拦下。')
  }

  async function deleteItem(item: DeployTemplate) {
    const confirmed = window.confirm(
      `删除模板「${item.name}」？\n\n` +
      '删除后模板会从模板库隐藏，后续不能再发布或用于新建部署；' +
      '已经创建的部署任务和版本历史会保留，便于审计回溯。',
    )
    if (!confirmed) return
    await run(
      () => deleteDeployTemplate(item.id),
      '模板已删除；已有部署任务和版本历史仍保留。',
    )
  }

  async function showHistory(item: DeployTemplate) {
    try {
      const resp = await listTemplateHistory(item.id)
      history.value = { item, rows: resp.items }
    } catch (err) {
      error.value = err instanceof Error ? err.message : '版本历史加载失败'
    }
  }

  async function generateAi() {
    if (!ai.goal.trim()) {
      error.value = '先写清楚要部署什么'
      return
    }
    busy.value = true
    error.value = ''
    message.value = ''
    try {
      const resp = await generateAiTemplate({
        goal: ai.goal.trim(),
        platform: ai.platform,
        host: ai.host.trim() || 'local',
        pasted_commands: ai.commands.trim(),
      })
      aiOpen.value = false
      aiTrace.value = resp.trace ?? []
      editor.value = editorFromTemplate(resp.item)
      message.value = `AI 草稿已生成（${resp.ai.model_name}），请先逐条核对命令再发布。`
      await load()
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'AI 生成失败'
    } finally {
      busy.value = false
    }
  }

  async function saveFromTask() {
    if (!fromTaskId.value.trim()) return
    fromTaskBusy.value = true
    error.value = ''
    try {
      const resp = await createTemplateFromTask(fromTaskId.value.trim())
      fromTaskOpen.value = false
      fromTaskId.value = ''
      await openEdit(resp.item)
      message.value = '已从成功任务生成草稿；命令仍需人工核对后再发布。'
      await load()
    } catch (err) {
      error.value = err instanceof Error ? err.message : '从任务沉淀失败'
    } finally {
      fromTaskBusy.value = false
    }
  }

  function resetTemplateWidths() {
    templateTable.value?.reset()
  }

  function closeEditor() {
    if (!busy.value) editor.value = null
  }

  return {
    DEPLOY_TYPES,
    DEPLOY_TYPE_LABELS,
    TEMPLATE_COLUMNS,
    TEMPLATE_GRID,
    TEMPLATE_COLUMN_WIDTHS_STORAGE_KEY,
    ai,
    aiOpen,
    aiTrace,
    addRow,
    busy,
    canCreate,
    checkTemplate,
    closeEditor,
    cloneItem,
    deleteItem,
    disableItem,
    editor,
    error,
    filtered,
    formatTime,
    fromTaskBusy,
    fromTaskId,
    fromTaskOpen,
    generateAi,
    hasCustomTemplateWidths,
    history,
    isAdmin,
    items,
    load,
    loading,
    me,
    message,
    openCreate,
    openEdit,
    publishItem,
    removeRow,
    resetTemplateWidths,
    riskText,
    saveFromTask,
    saveTemplate,
    search,
    showHistory,
    sourceText,
    stats,
    statusClass,
    statusFilter,
    statusText,
    templateTable,
  }
}
