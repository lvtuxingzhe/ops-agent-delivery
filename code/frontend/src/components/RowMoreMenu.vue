<script setup lang="ts">
/**
 * 表格行内的“更多操作”菜单。
 *
 * 只负责打开/关闭、定位和点击外部关闭；具体菜单项由调用方通过插槽提供。
 * 浮层挂到 body，避免被表格横向滚动容器裁掉。
 */
import { nextTick, onBeforeUnmount, ref } from 'vue'

withDefaults(defineProps<{
  label?: string
  variant?: 'plain' | 'ghost'
  disabled?: boolean
}>(), {
  label: '更多操作',
  variant: 'plain',
  disabled: false,
})

const open = ref(false)
const trigger = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLElement | null>(null)
const menuStyle = ref<Record<string, string>>({})

function placeMenu() {
  const triggerRect = trigger.value?.getBoundingClientRect()
  if (!triggerRect) return
  const menuWidth = menu.value?.offsetWidth ?? 176
  const left = Math.min(
    Math.max(12, triggerRect.right - menuWidth),
    Math.max(12, window.innerWidth - menuWidth - 12),
  )
  const estimatedHeight = menu.value?.offsetHeight ?? 0
  const below = triggerRect.bottom + 6
  const above = triggerRect.top - estimatedHeight - 6
  const top = below + estimatedHeight > window.innerHeight && above > 12 ? above : below
  menuStyle.value = {
    top: `${Math.round(top)}px`,
    left: `${Math.round(left)}px`,
  }
}

function addListeners() {
  document.addEventListener('mousedown', onDocumentMouseDown, true)
  document.addEventListener('keydown', onKeyDown, true)
  window.addEventListener('resize', close, true)
  window.addEventListener('scroll', close, true)
}

function removeListeners() {
  document.removeEventListener('mousedown', onDocumentMouseDown, true)
  document.removeEventListener('keydown', onKeyDown, true)
  window.removeEventListener('resize', close, true)
  window.removeEventListener('scroll', close, true)
}

function onDocumentMouseDown(event: MouseEvent) {
  const target = event.target
  if (!(target instanceof Node)) return
  if (menu.value?.contains(target) || trigger.value?.contains(target)) return
  close()
}

function onKeyDown(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
}

function openMenu() {
  if (open.value) return
  open.value = true
  addListeners()
  void nextTick(() => {
    placeMenu()
    void nextTick(placeMenu)
  })
}

function close() {
  if (!open.value) return
  open.value = false
  removeListeners()
}

function toggle() {
  if (open.value) {
    close()
  } else {
    openMenu()
  }
}

onBeforeUnmount(removeListeners)

defineExpose({ close })
</script>

<template>
  <span class="row-more">
    <button
      ref="trigger"
      type="button"
      :class="variant"
      class="row-more-trigger"
      :disabled="disabled"
      aria-haspopup="menu"
      :aria-expanded="open"
      @click.stop="toggle"
      @keydown.esc.stop="close"
    >
      {{ label }}
    </button>
    <Teleport to="body">
      <div
        v-if="open"
        ref="menu"
        class="row-more-menu"
        :style="menuStyle"
        role="menu"
        @keydown.esc.stop="close"
      >
        <slot :close="close" />
      </div>
    </Teleport>
  </span>
</template>

<style scoped>
.row-more {
  display: inline-flex;
  flex: 0 0 auto;
  min-width: 0;
}

.row-more-trigger {
  white-space: nowrap;
}

.row-more-menu {
  position: fixed;
  z-index: 80;
  display: flex;
  min-width: 168px;
  max-width: 240px;
  flex-direction: column;
  gap: 2px;
  padding: 5px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface);
  box-shadow: 0 14px 38px rgba(15, 23, 42, 0.18);
}

.row-more-menu :deep(.row-more-item) {
  width: 100%;
  min-height: 32px;
  padding: 6px 9px;
  border: 0;
  border-radius: 7px;
  color: var(--ink-2);
  background: transparent;
  font: inherit;
  font-size: calc(12px * var(--font-scale, 1));
  text-align: left;
  cursor: pointer;
}

.row-more-menu :deep(.row-more-item:hover:not(:disabled)) {
  color: var(--ink);
  background: var(--surface-soft);
}

.row-more-menu :deep(.row-more-item:disabled) {
  color: var(--muted);
  cursor: default;
  opacity: 0.55;
}

.row-more-menu :deep(.row-more-item.is-danger) {
  color: var(--danger);
}

.row-more-menu :deep(.row-more-item.is-danger:hover:not(:disabled)) {
  background: #fef2f2;
}
</style>
