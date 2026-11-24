import type { ResizableTableColumn } from '../../components/resizableTable'

export const HOST_COLUMN_WIDTHS_STORAGE_KEY = 'ops.hosts.columnWidths.v6'
export const HOST_GRID =
  'minmax(220px, 1.6fr) minmax(170px, 1.1fr) minmax(70px, .5fr) minmax(150px, 1fr) minmax(80px, .55fr) minmax(160px, max-content)'
export const HOST_COLUMNS: ResizableTableColumn[] = [
  { key: 'target', label: '主机 / 地址', minWidth: 220 },
  { key: 'environment', label: '环境', minWidth: 170 },
  { key: 'account', label: '账号', minWidth: 70 },
  { key: 'credential', label: '凭据', minWidth: 150 },
  { key: 'status', label: '状态', minWidth: 80 },
  { key: 'actions', label: '操作', minWidth: 160, headClass: 'host-sticky-actions' },
]
