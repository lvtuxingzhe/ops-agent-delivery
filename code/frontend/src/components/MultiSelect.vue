<script setup lang="ts">
/**
 * 紧凑多选下拉框。
 *
 * 筛选栏里不再把二三个选项摊成 chips；统一收成一个下拉，
 * 既保留同字段多选，也不让筛选项抢走列表的视觉重心。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

interface MultiSelectOption {
  value: string
  label: string
}

const props = withDefaults(
  defineProps<{
    modelValue: string[]
    options: MultiSelectOption[]
    placeholder?: string
    ariaLabel?: string
  }>(),
  {
    placeholder: '全部',
    ariaLabel: '多选筛选',
  },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: string[]): void
}>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)

const summary = computed(() => {
  if (!props.modelValue.length) return props.placeholder
  if (props.modelValue.length === 1) {
    const option = props.options.find((item) => item.value === props.modelValue[0])
    return option?.label ?? props.modelValue[0]
  }
  return `已选 ${props.modelValue.length} 项`
})

function toggle(value: string): void {
  const next = props.modelValue.includes(value)
    ? props.modelValue.filter((item) => item !== value)
    : [...props.modelValue, value]
  emit('update:modelValue', next)
}

function clear(): void {
  emit('update:modelValue', [])
}

function handleOutside(event: MouseEvent): void {
  if (!open.value) return
  if (root.value && !root.value.contains(event.target as Node)) open.value = false
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') open.value = false
}

onMounted(() => {
  document.addEventListener('mousedown', handleOutside)
  document.addEventListener('keydown', handleKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', handleOutside)
  document.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <div ref="root" class="multi-select">
    <button
      class="multi-trigger"
      type="button"
      :aria-label="ariaLabel"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span class="multi-summary">{{ summary }}</span>
      <span class="multi-caret" aria-hidden="true">⌄</span>
    </button>
    <div v-if="open" class="multi-menu" role="listbox" :aria-label="ariaLabel">
      <div class="multi-menu-head">
        <span>{{ ariaLabel }}</span>
        <button v-if="modelValue.length" type="button" @click="clear">清空</button>
      </div>
      <label v-for="option in options" :key="option.value" class="multi-option">
        <input
          type="checkbox"
          :checked="modelValue.includes(option.value)"
          @change="toggle(option.value)"
        />
        <span>{{ option.label }}</span>
      </label>
      <p v-if="!options.length" class="multi-empty">暂无可选项</p>
    </div>
  </div>
</template>

<style scoped>
.multi-select {
  position: relative;
  width: var(--filter-w, 220px);
  min-width: var(--filter-w, 220px);
}

.multi-trigger {
  display: flex;
  width: 100%;
  min-height: 40px;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--ink-2);
  cursor: pointer;
  font-size: calc(13px * var(--font-scale, 1));
  text-align: left;
}

.multi-trigger:hover {
  border-color: var(--line-strong);
}

.multi-trigger:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.multi-summary {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.multi-caret {
  flex: 0 0 auto;
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
  transform: translateY(-1px);
}

.multi-menu {
  position: absolute;
  z-index: 30;
  top: calc(100% + 4px);
  left: 0;
  width: max(100%, 178px);
  padding: 6px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.12);
}

.multi-menu-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 7px 6px;
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
}

.multi-menu-head button {
  border: 0;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
  font-size: calc(11px * var(--font-scale, 1));
}

.multi-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-radius: 7px;
  color: var(--ink-2);
  cursor: pointer;
  font-size: calc(12px * var(--font-scale, 1));
}

.multi-option:hover {
  background: var(--surface-soft);
}

.multi-option input {
  width: 14px;
  height: 14px;
  accent-color: var(--accent);
}

.multi-empty {
  margin: 4px 7px 6px;
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
}
</style>
