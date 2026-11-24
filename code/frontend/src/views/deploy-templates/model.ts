import type {
  DeployOperation,
  DeployTemplate,
  TemplateCommandInput,
  TemplatePayload,
  TemplateStatus,
} from '../../api/deployments'
import { formatLocalTime } from '../../utils/time'
import type { ResizableTableColumn } from '../../components/resizableTable'

export const TEMPLATE_COLUMN_WIDTHS_STORAGE_KEY = 'ops.deployTemplates.columnWidths.v1'
export const TEMPLATE_GRID =
  'minmax(220px, 1.7fr) 78px minmax(120px, 1.1fr) 46px 90px 100px minmax(170px, 1.05fr)'
export const TEMPLATE_COLUMNS: ResizableTableColumn[] = [
  { key: 'template', label: '模板', minWidth: 220 },
  { key: 'status', label: '状态', minWidth: 64 },
  { key: 'platform', label: '平台 / 主机', minWidth: 110 },
  { key: 'version', label: '版本', minWidth: 46 },
  { key: 'usage', label: '使用', minWidth: 70 },
  { key: 'updated_at', label: '更新', minWidth: 80 },
  { key: 'actions', label: '操作', minWidth: 120, align: 'right' },
]

export interface CommandRow {
  command: string
  scope: 'read' | 'dangerous'
  exitCode: string
  stdout: string
}

export type CommandStage = 'precheck' | 'operations' | 'verify' | 'rollback' | 'postRollback'

export interface EditorState {
  id: string | null
  status: TemplateStatus
  name: string
  description: string
  platform: 'linux' | 'windows'
  osFamily: string
  packageManager: string
  deployType: string
  defaultHost: string
  ttlMinutes: number
  precheck: CommandRow[]
  operations: CommandRow[]
  verify: CommandRow[]
  rollback: CommandRow[]
  postRollback: CommandRow[]
}

export function blankRow(scope: 'read' | 'dangerous' = 'dangerous', verify = false): CommandRow {
  return {
    command: '',
    scope: verify ? 'read' : scope,
    exitCode: verify ? '0' : '',
    stdout: '',
  }
}

export function blankEditor(): EditorState {
  return {
    id: null,
    status: 'draft',
    name: '',
    description: '',
    platform: 'linux',
    osFamily: '',
    packageManager: '',
    deployType: '',
    defaultHost: 'local',
    ttlMinutes: 30,
    precheck: [],
    operations: [blankRow('dangerous')],
    verify: [blankRow('read', true)],
    rollback: [blankRow('dangerous')],
    postRollback: [],
  }
}

export function rowsFromOps(ops: DeployOperation[], verify = false, optional = false): CommandRow[] {
  const rows = ops.map((op) => ({
    command: op.command || op.argv.join(' '),
    scope: op.scope,
    exitCode: verify ? String(op.expect?.exit_code ?? 0) : '',
    stdout: verify ? String(op.expect?.stdout_contains ?? '') : '',
  }))
  if (rows.length) return rows
  return optional ? [] : [blankRow(verify ? 'read' : 'dangerous', verify)]
}

export function editorFromTemplate(item: DeployTemplate): EditorState {
  return {
    id: item.id,
    status: item.status ?? 'draft',
    name: item.name,
    description: item.description,
    platform: item.platform === 'windows' ? 'windows' : 'linux',
    osFamily: item.os_family || 'unknown',
    packageManager: item.package_manager || 'unknown',
    deployType: item.deploy_type || '',
    defaultHost: item.default_host,
    ttlMinutes: item.ttl_minutes,
    precheck: rowsFromOps(item.precheck ?? [], true, true),
    operations: rowsFromOps(item.operations),
    verify: rowsFromOps(item.verify, true),
    rollback: rowsFromOps(item.rollback),
    postRollback: rowsFromOps(item.post_rollback ?? [], true, true),
  }
}

export function rowsToPayload(rows: CommandRow[], verify = false): TemplateCommandInput[] {
  return rows
    .filter((row) => row.command.trim())
    .map((row) => {
      const item: TemplateCommandInput = {
        command: row.command.trim(),
        scope: row.scope,
      }
      if (verify) {
        const exit = row.exitCode.trim()
        item.expect = {
          exit_code: exit ? Number(exit) : 0,
          ...(row.stdout.trim() ? { stdout_contains: row.stdout.trim() } : {}),
        }
      }
      return item
    })
}

export function payloadFromEditor(editor: EditorState): TemplatePayload {
  return {
    name: editor.name.trim(),
    description: editor.description.trim(),
    platform: editor.platform,
    os_family: editor.osFamily,
    package_manager: editor.packageManager,
    deploy_type: editor.deployType || undefined,
    default_host: editor.defaultHost.trim(),
    ttl_minutes: Number(editor.ttlMinutes),
    precheck: rowsToPayload(editor.precheck, true),
    operations: rowsToPayload(editor.operations),
    verify: rowsToPayload(editor.verify, true),
    rollback: rowsToPayload(editor.rollback),
    post_rollback: rowsToPayload(editor.postRollback, true),
  }
}

export function statusText(status?: string): string {
  return ({ draft: '草稿', published: '已发布', disabled: '已停用' } as Record<string, string>)[status || ''] || status || '—'
}

export function statusClass(status?: string): string {
  if (status === 'published') return 'badge-success'
  if (status === 'draft') return 'badge-warn'
  return 'badge-muted'
}

export function riskText(risk?: string): string {
  return ({ low: '低', medium: '中', high: '高' } as Record<string, string>)[risk || ''] || '—'
}

export function formatTime(iso?: string | null): string {
  return formatLocalTime(iso)
}

export function sourceText(source?: string): string {
  return ({ manual: '手写', ai: 'AI 草稿', clone: '克隆', from_task: '任务沉淀', seed: '种子' } as Record<string, string>)[source || ''] || source || '—'
}
