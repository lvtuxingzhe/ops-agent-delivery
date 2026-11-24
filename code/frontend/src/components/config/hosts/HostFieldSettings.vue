<script setup lang="ts">
/** 配置 → 主机字段：CMDB 字段的显示、排序、改名、新增、删除。 */
import { computed, inject, onBeforeUnmount, onMounted, ref } from 'vue'

import HostFieldRow from '../HostFieldRow.vue'
import { HOST_SETTINGS_KEY } from './useHostSettings'

const settings = inject(HOST_SETTINGS_KEY)!

const HEADERS = ['字段名称', '字段标识', '类型', '必填', '模板', '排序', '操作']
const MIN_WIDTHS = [220, 140, 160, 58, 54, 68, 48]
const DEFAULT_GRID = 'minmax(220px, 300px) minmax(140px, 1fr) minmax(160px, 1fr) 58px 54px 68px 48px'
const STORAGE_KEY = 'ops.hostfields.columnWidths.v1'

const head = ref<HTMLElement | null>(null)
const widths = ref<number[] | null>(null)
const customized = ref(false)
const gridStyle = computed(() => (
  widths.value ? { '--cmdb-grid': widths.value.map((width) => `${Math.round(width)}px`).join(' ') } : {}
))

let resizeIndex: number | null = null
let resizeStartX = 0
let resizeStartWidths: number[] = []

function fitWidths() {
  if (!customized.value || resizeIndex !== null || !widths.value || !head.value) return
  const container = head.value.parentElement
  if (!container) return
  const available = container.clientWidth - 20 - 8 * (HEADERS.length - 1)
  const total = widths.value.reduce((sum, width) => sum + width, 0)
  if (available <= 0 || total <= 0 || Math.abs(total - available) <= 2) return

  const minimumTotal = MIN_WIDTHS.reduce((sum, width) => sum + width, 0)
  if (available <= minimumTotal) {
    widths.value = widths.value.map((width, index) => Math.max(width, MIN_WIDTHS[index]))
    return
  }

  const scale = available / total
  const next = widths.value.map((width, index) => Math.max(width * scale, MIN_WIDTHS[index]))
  let overflow = next.reduce((sum, width) => sum + width, 0) - available
  if (overflow > 0) {
    const donors = next
      .map((width, index) => ({ index, slack: width - MIN_WIDTHS[index] }))
      .filter((item) => item.slack > 0)
    const totalSlack = donors.reduce((sum, item) => sum + item.slack, 0)
    if (totalSlack > 0) {
      const movement = Math.min(overflow, totalSlack)
      const takes = donors.map((item) => Math.floor((movement * item.slack) / totalSlack))
      let assigned = takes.reduce((sum, take) => sum + take, 0)
      for (let i = 0; assigned < movement; i = (i + 1) % donors.length) {
        if (takes[i] < donors[i].slack) {
          takes[i] += 1
          assigned += 1
        }
      }
      donors.forEach((item, i) => { next[item.index] -= takes[i] })
    }
  }
  widths.value = next
}

function readWidths(): number[] | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed) || parsed.length !== HEADERS.length) return null
    if (!parsed.every((value) => typeof value === 'number' && Number.isFinite(value) && value > 0)) return null
    return parsed.map((value, index) => Math.max(Math.round(value), MIN_WIDTHS[index]))
  } catch {
    return null
  }
}

function persistWidths() {
  if (!widths.value) return
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(widths.value.map((width) => Math.round(width))))
  } catch {
    // 存储不可用时不影响当前页面拖拽。
  }
}

function startResize(event: MouseEvent, index: number) {
  if (index >= HEADERS.length - 1 || !head.value) return
  const cells = Array.from(head.value.children).filter(
    (child): child is HTMLElement => child instanceof HTMLElement,
  )
  if (cells.length !== HEADERS.length) return
  resizeStartWidths = cells.map((cell) => cell.getBoundingClientRect().width)
  widths.value = resizeStartWidths
  resizeIndex = index
  resizeStartX = event.clientX
  document.body.classList.add('col-resizing')
  window.addEventListener('mousemove', onResizeMove)
  window.addEventListener('mouseup', stopResize)
  event.preventDefault()
}

function onResizeMove(event: MouseEvent) {
  if (resizeIndex === null) return
  const index = resizeIndex
  const delta = Math.round(event.clientX - resizeStartX)
  const next = [...resizeStartWidths]
  const others = next
    .map((width, column) => ({
      column,
      slack: column === index ? 0 : width - MIN_WIDTHS[column],
    }))
    .filter((item) => item.slack > 0)
  const totalSlack = others.reduce((sum, item) => sum + item.slack, 0)
  if (!totalSlack) return

  if (delta > 0) {
    const movement = Math.min(delta, totalSlack)
    const takes = others.map((item) => Math.floor((movement * item.slack) / totalSlack))
    let assigned = takes.reduce((sum, take) => sum + take, 0)
    for (let i = 0; assigned < movement; i = (i + 1) % others.length) {
      if (takes[i] < others[i].slack) {
        takes[i] += 1
        assigned += 1
      }
    }
    others.forEach((item, i) => { next[item.column] -= takes[i] })
    next[index] += movement
  } else if (delta < 0) {
    const shrink = Math.min(-delta, resizeStartWidths[index] - MIN_WIDTHS[index], totalSlack)
    if (shrink <= 0) return
    const takes = others.map((item) => Math.floor((shrink * item.slack) / totalSlack))
    let assigned = takes.reduce((sum, take) => sum + take, 0)
    for (let i = 0; assigned < shrink; i = (i + 1) % others.length) {
      if (takes[i] < others[i].slack) {
        takes[i] += 1
        assigned += 1
      }
    }
    others.forEach((item, i) => { next[item.column] += takes[i] })
    next[index] -= shrink
  }

  widths.value = next
}

function stopResize() {
  const wasResizing = resizeIndex !== null
  resizeIndex = null
  document.body.classList.remove('col-resizing')
  window.removeEventListener('mousemove', onResizeMove)
  window.removeEventListener('mouseup', stopResize)
  if (!wasResizing) return
  customized.value = true
  persistWidths()
}

function resetWidths() {
  widths.value = null
  customized.value = false
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // 同上。
  }
}

const {
  visibleFields, hiddenFields, customCount, busy,
  moveField, removeField, saveFields, openAdd,
} = settings

onMounted(() => {
  const stored = readWidths()
  if (stored) {
    widths.value = stored
    customized.value = true
    window.requestAnimationFrame(fitWidths)
  }
  window.addEventListener('resize', fitWidths)
})

onBeforeUnmount(() => {
  resizeIndex = null
  document.body.classList.remove('col-resizing')
  window.removeEventListener('mousemove', onResizeMove)
  window.removeEventListener('mouseup', stopResize)
  window.removeEventListener('resize', fitWidths)
})
</script>

<template>
  <section class="card">
    <header class="panel-head">
      <div>
        <h2>主机配置</h2>
      </div>
      <div class="panel-actions">
        <button v-if="customized" type="button" class="plain" :disabled="busy" @click="resetWidths">重置列宽</button>
        <button type="button" class="primary" :disabled="busy" @click="openAdd">新增字段</button>
      </div>
    </header>

    <div class="cmdb-summary">
      <span><b>{{ visibleFields.length }}</b> 个字段显示在模板</span>
      <span><b>{{ hiddenFields.length }}</b> 个隐藏</span>
      <span><b>{{ customCount }}</b> 个自定义</span>
    </div>

    <div class="cmdb-table" :style="gridStyle">
      <div ref="head" class="cmdb-head">
        <span v-for="(label, index) in HEADERS" :key="label">
          {{ label }}
          <span
            v-if="index < HEADERS.length - 1"
            class="cmdb-resizer"
            title="拖动调整列宽"
            @mousedown="startResize($event, index)"
          />
        </span>
      </div>
      <HostFieldRow
        v-for="(field, index) in visibleFields"
        :key="field.key"
        :field="field"
        :busy="busy"
        :first="index === 0"
        :last="index === visibleFields.length - 1"
        @move="moveField"
        @remove="removeField"
      />
      <p v-if="!visibleFields.length" class="empty-row">没有显示字段；请从隐藏字段中勾选。</p>
    </div>

    <details v-if="hiddenFields.length" class="hidden-fields">
      <summary>未显示字段（{{ hiddenFields.length }}）</summary>
      <div class="cmdb-table" :style="gridStyle">
        <HostFieldRow
          v-for="(field, index) in hiddenFields"
          :key="field.key"
          :field="field"
          :busy="busy"
          :first="index === 0"
          :last="index === hiddenFields.length - 1"
          @move="moveField"
          @remove="removeField"
        />
      </div>
    </details>

    <div class="panel-foot">
      <button type="button" class="primary" :disabled="busy" @click="saveFields">保存字段配置</button>
    </div>
  </section>
</template>

<style scoped>
.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}
.panel-head h2 {
  margin-bottom: 4px;
}
.panel-head .primary {
  flex: 0 0 auto;
}
.panel-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cmdb-summary {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  padding: 8px 2px 10px;
  color: var(--muted);
  font-size: calc(12px * var(--font-scale, 1));
}
.cmdb-summary b {
  color: var(--ink);
  font-weight: 600;
}
.cmdb-table {
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
}
.cmdb-head {
  display: grid;
  grid-template-columns: var(--cmdb-grid, minmax(220px, 300px) minmax(140px, 1fr) minmax(160px, 1fr) 58px 54px 68px 48px);
  gap: 8px;
  min-width: 760px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-soft);
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
}
.cmdb-head > span {
  position: relative;
  white-space: nowrap;
}
.cmdb-resizer {
  position: absolute;
  top: 50%;
  right: -8px;
  z-index: 2;
  width: 16px;
  height: 26px;
  transform: translateY(-50%);
  cursor: col-resize;
  touch-action: none;
}
.cmdb-resizer::after {
  content: "";
  position: absolute;
  top: 2px;
  bottom: 2px;
  left: 7px;
  width: 2px;
  border-radius: 2px;
  background: var(--line-strong);
  opacity: 0.65;
}
.cmdb-resizer:hover::after {
  background: var(--accent);
  opacity: 0.9;
}
@media (max-width: 980px) {
  .cmdb-resizer {
    display: none;
  }
}
.cmdb-table :deep(.cmdb-row) {
  min-width: 760px;
}
.hidden-fields {
  margin-top: 10px;
}
.hidden-fields summary {
  cursor: pointer;
  color: var(--ink-2);
  font-size: calc(12px * var(--font-scale, 1));
  padding: 6px 2px;
}
.empty-row {
  margin: 0;
  padding: 14px;
  color: var(--muted);
  font-size: 12px;
}
.panel-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 12px;
}
@media (max-width: 720px) {
  .panel-foot {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
