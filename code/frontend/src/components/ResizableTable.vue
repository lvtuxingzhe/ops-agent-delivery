<script setup lang="ts">
/**
 * 可拖拽表头列宽的数据表外壳。
 *
 * 只负责表头、列宽测量、拖拽、持久化与双击/按钮重置；
 * 行内容、空态和分页仍由调用方通过默认插槽控制。
 */

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { ResizableTableColumn } from './resizableTable'

const props = withDefaults(
  defineProps<{
    columns: ResizableTableColumn[]
    defaultGrid: string
    storageKey: string
    gap?: number
    /** 表头左右各自的内边距，必须与业务行的水平 padding 一致。 */
    paddingX?: number
    cardClass?: string
    headClass?: string
  }>(),
  {
    gap: 12,
    paddingX: 0,
    cardClass: '',
    headClass: '',
  },
)

const emit = defineEmits<{
  (event: 'custom-change', value: boolean): void
  (event: 'reset'): void
}>()

const card = ref<HTMLElement | null>(null)
const head = ref<HTMLElement | null>(null)
const columnWidths = ref<number[] | null>(null)
let resizeIndex: number | null = null
let resizeStartX = 0
let resizeStartWidths: number[] = []
let resizeObserver: ResizeObserver | null = null

const tableStyle = computed<Record<string, string>>(() => {
  const base: Record<string, string> = {
    '--rt-default': props.defaultGrid,
    '--rt-gap': `${props.gap}px`,
    '--rt-pad-x': `${props.paddingX}px`,
  }
  if (!columnWidths.value) return base
  const width = columnWidths.value.reduce((sum, value) => sum + Math.round(value), 0)
    + props.gap * (props.columns.length - 1)
    + props.paddingX * 2
  return {
    ...base,
    '--rt-grid': columnWidths.value.map((item) => `${Math.round(item)}px`).join(' '),
    'min-width': `${Math.round(width)}px`,
  }
})

function readColumnWidths(): number[] | null {
  try {
    const raw = window.localStorage.getItem(props.storageKey)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed) || parsed.length !== props.columns.length) return null
    if (!parsed.every((value) => typeof value === 'number' && Number.isFinite(value) && value > 0)) return null
    return parsed.map((value, index) => Math.max(Math.round(value), props.columns[index].minWidth))
  } catch {
    return null
  }
}

function persistColumnWidths() {
  if (!columnWidths.value) return
  try {
    window.localStorage.setItem(
      props.storageKey,
      JSON.stringify(columnWidths.value.map((width) => Math.round(width))),
    )
  } catch {
    // 存储不可用时不影响当前页面的拖拽。
  }
}

function clearColumnWidths() {
  try {
    window.localStorage.removeItem(props.storageKey)
  } catch {
    // 同上，存储不可用时忽略。
  }
}

function measureColumnWidths(): number[] | null {
  const element = head.value
  if (!element) return null
  const cells = Array.from(element.children).filter(
    (child): child is HTMLElement => child instanceof HTMLElement,
  )
  if (cells.length !== props.columns.length) return null
  return cells.map((cell) => cell.getBoundingClientRect().width)
}

function startColumnResize(event: MouseEvent, index: number) {
  if (index >= props.columns.length - 1 || props.columns[index].resizable === false) return
  const widths = measureColumnWidths()
  if (!widths) return
  columnWidths.value = widths
  resizeIndex = index
  resizeStartX = event.clientX
  resizeStartWidths = widths
  document.body.classList.add('col-resizing')
  window.addEventListener('mousemove', onColumnResizeMove)
  window.addEventListener('mouseup', stopColumnResize)
  event.preventDefault()
}

function onColumnResizeMove(event: MouseEvent) {
  if (resizeIndex === null) return
  const index = resizeIndex
  const delta = Math.round(event.clientX - resizeStartX)
  const next = [...resizeStartWidths]

  if (delta > 0) {
    const donors = next
      .map((width, column) => ({
        column,
        slack: column === index ? 0 : width - props.columns[column].minWidth,
      }))
      .filter((item) => item.slack > 0)
    const totalSlack = donors.reduce((sum, item) => sum + item.slack, 0)
    if (!totalSlack) return

    const movement = Math.min(delta, totalSlack)
    const takes = donors.map((item) => Math.floor((movement * item.slack) / totalSlack))
    let assigned = takes.reduce((sum, take) => sum + take, 0)
    for (let i = 0; assigned < movement; i = (i + 1) % donors.length) {
      const donor = donors[i]
      if (takes[i] < donor.slack) {
        takes[i] += 1
        assigned += 1
      }
    }
    donors.forEach((item, i) => {
      next[item.column] -= takes[i]
    })
    next[index] += movement
  } else if (delta < 0) {
    const shrink = Math.min(-delta, resizeStartWidths[index] - props.columns[index].minWidth)
    const donors = next
      .map((width, column) => ({
        column,
        slack: column === index ? 0 : width - props.columns[column].minWidth,
      }))
      .filter((item) => item.slack > 0)
    const totalSlack = donors.reduce((sum, item) => sum + item.slack, 0)
    if (!totalSlack) return
    const growth = Math.min(shrink, totalSlack)
    const takes = donors.map((item) => Math.floor((growth * item.slack) / totalSlack))
    let assigned = takes.reduce((sum, take) => sum + take, 0)
    for (let i = 0; assigned < growth; i = (i + 1) % donors.length) {
      const donor = donors[i]
      if (takes[i] < donor.slack) {
        takes[i] += 1
        assigned += 1
      }
    }
    donors.forEach((item, i) => {
      next[item.column] += takes[i]
    })
    next[index] -= growth
  }

  columnWidths.value = next
}

function stopColumnResize() {
  const wasResizing = resizeIndex !== null
  resizeIndex = null
  document.body.classList.remove('col-resizing')
  window.removeEventListener('mousemove', onColumnResizeMove)
  window.removeEventListener('mouseup', stopColumnResize)
  if (!wasResizing) return
  persistColumnWidths()
  emit('custom-change', Boolean(columnWidths.value))
}

function resetColumnWidths() {
  stopColumnResize()
  columnWidths.value = null
  clearColumnWidths()
  emit('custom-change', false)
  emit('reset')
  window.requestAnimationFrame(() => fitColumnWidths())
}

function fitColumnWidths() {
  if (!columnWidths.value || resizeIndex !== null || !card.value) return
  const available = availableWidth() - props.paddingX * 2 - props.gap * (props.columns.length - 1)
  const total = columnWidths.value.reduce((sum, width) => sum + width, 0)
  if (available <= 0 || total <= 0 || Math.abs(total - available) <= 2) return

  const minimumTotal = props.columns.reduce((sum, column) => sum + column.minWidth, 0)
  if (available <= minimumTotal) {
    columnWidths.value = columnWidths.value.map(
      (width, index) => Math.max(width, props.columns[index].minWidth),
    )
    return
  }

  const scale = available / total
  const next = columnWidths.value.map(
    (width, index) => Math.max(width * scale, props.columns[index].minWidth),
  )
  let overflow = next.reduce((sum, width) => sum + width, 0) - available

  if (overflow > 0) {
    const donors = next
      .map((width, index) => ({
        index,
        slack: width - props.columns[index].minWidth,
      }))
      .filter((item) => item.slack > 0)
    const totalSlack = donors.reduce((sum, item) => sum + item.slack, 0)
    if (totalSlack > 0) {
      const movement = Math.min(overflow, totalSlack)
      const takes = donors.map((item) => Math.floor((movement * item.slack) / totalSlack))
      let assigned = takes.reduce((sum, take) => sum + take, 0)
      for (let i = 0; assigned < movement; i = (i + 1) % donors.length) {
        const donor = donors[i]
        if (takes[i] < donor.slack) {
          takes[i] += 1
          assigned += 1
        }
      }
      donors.forEach((item, i) => {
        next[item.index] -= takes[i]
      })
      overflow = next.reduce((sum, width) => sum + width, 0) - available
    }
  }

  if (overflow > 0) {
    // 所有列都已到最小宽度：保持列宽，由外层滚动容器承接横向溢出。
    columnWidths.value = next
    return
  }
  columnWidths.value = next
}

/**
 * 表格宽度不能超过父级卡片的实际内容宽度。
 *
 * 只看 `card.clientWidth` 会形成循环：表格自己因为历史列宽被撑到 1200，
 * 再按 1200 去“适配”，永远不会缩回卡片内，最后一列就被推到卡片外。
 * 所以优先用父容器的内容宽度（去掉 padding）作为上限。
 */
function availableWidth(): number {
  const element = card.value
  if (!element) return 0
  let width = element.clientWidth
  const parent = element.parentElement
  if (parent) {
    const style = getComputedStyle(parent)
    const inner = parent.clientWidth
      - Number.parseFloat(style.paddingLeft || '0')
      - Number.parseFloat(style.paddingRight || '0')
    if (inner > 0) width = Math.min(width, inner)
  }
  return width
}

watch(columnWidths, () => {
  if (resizeIndex === null) fitColumnWidths()
}, { flush: 'post' })

onMounted(() => {
  const stored = readColumnWidths()
  if (stored) {
    columnWidths.value = stored
    emit('custom-change', true)
    nextTick(() => {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => fitColumnWidths())
      })
    })
  }
  if (card.value && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => fitColumnWidths())
    resizeObserver.observe(card.value)
    if (card.value.parentElement) resizeObserver.observe(card.value.parentElement)
  }
})

onBeforeUnmount(() => {
  resizeIndex = null
  document.body.classList.remove('col-resizing')
  window.removeEventListener('mousemove', onColumnResizeMove)
  window.removeEventListener('mouseup', stopColumnResize)
  resizeObserver?.disconnect()
})

defineExpose({ reset: resetColumnWidths })
</script>

<template>
  <section ref="card" class="resizable-table" :class="cardClass" :style="tableStyle">
    <div ref="head" class="rt-head" :class="headClass">
      <span
        v-for="(column, index) in columns"
        :key="column.key"
        :class="column.headClass"
        :style="column.align ? { textAlign: column.align } : undefined"
      >
        {{ column.label }}
        <span
          v-if="index < columns.length - 1 && column.resizable !== false"
          class="col-resizer"
          title="拖动调整列宽，双击恢复默认"
          @dblclick="resetColumnWidths"
          @mousedown="startColumnResize($event, index)"
        />
      </span>
    </div>
    <slot />
  </section>
</template>

<style scoped>
.rt-head {
  display: grid;
  grid-template-columns: var(--rt-grid, var(--rt-default));
  gap: var(--rt-gap, 12px);
  padding: 10px var(--rt-pad-x, 0);
  border-bottom: 1px solid var(--line);
  background: var(--surface-soft);
  color: var(--ink-2);
  font-size: calc(10px * var(--font-scale, 1));
  font-weight: 650;
  letter-spacing: 0.02em;
}

.rt-head > span {
  position: relative;
  white-space: nowrap;
}

.col-resizer {
  position: absolute;
  top: 50%;
  right: -10px;
  z-index: 4;
  width: 20px;
  height: 32px;
  transform: translateY(-50%);
  cursor: col-resize;
  touch-action: none;
}

.col-resizer::after {
  content: "";
  position: absolute;
  top: 2px;
  bottom: 2px;
  left: 9px;
  width: 3px;
  border-radius: 2px;
  background: var(--line-strong);
  opacity: 0.7;
  transition: opacity 0.15s ease, background 0.15s ease;
}

.col-resizer:hover {
  background: rgba(37, 99, 235, 0.08);
}

.col-resizer:hover::after {
  background: var(--accent);
  opacity: 0.9;
}

:global(body.col-resizing) {
  cursor: col-resize;
  user-select: none;
}

@media (max-width: 980px) {
  .col-resizer {
    display: none;
  }
}

@media (max-width: 760px) {
  .rt-head {
    display: none;
  }
}
</style>
