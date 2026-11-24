/** 可拖拽表格的列定义：列名、最小宽度与可选表头样式。 */
export interface ResizableTableColumn {
  key: string
  label: string
  minWidth: number
  headClass?: string
  align?: 'left' | 'right' | 'center'
  /** 状态、操作这类列固定宽度，不提供拖拽手柄，避免把后续列推走。 */
  resizable?: boolean
}
