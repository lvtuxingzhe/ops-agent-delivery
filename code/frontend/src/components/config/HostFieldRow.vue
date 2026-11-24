<script setup lang="ts">
/** CMDB 字段行：紧凑表格的一行，避免每个字段一张大卡片。 */
import type { HostField } from '../../api/hosts'

const props = defineProps<{
  field: HostField
  busy: boolean
  first: boolean
  last: boolean
}>()

const emit = defineEmits<{
  (event: 'move', key: string, delta: number): void
  (event: 'remove', key: string): void
}>()

function kindLabel(kind: HostField['kind']): string {
  if (kind === 'select') return '下拉'
  if (kind === 'bool') return '布尔'
  return '文本'
}
</script>

<template>
  <div class="cmdb-row">
    <div class="cmdb-cell cmdb-name">
      <input v-model="field.label" maxlength="24" :disabled="busy" />
    </div>
    <div class="cmdb-cell cmdb-key mono">{{ field.key }}</div>
    <div class="cmdb-cell cmdb-kind-cell">
      <span class="cmdb-kind">{{ kindLabel(field.kind) }}</span>
    </div>
    <div class="cmdb-cell">
      <span :class="['tag', field.required ? 'tag-on' : 'tag-off']">
        {{ field.required ? '必填' : '可选' }}
      </span>
    </div>
    <div class="cmdb-cell cmdb-center">
      <input v-model="field.visible" type="checkbox" :disabled="busy || field.required" />
    </div>
    <div class="cmdb-cell cmdb-order">
      <button type="button" class="icon-btn" :disabled="busy || first" title="上移" @click="emit('move', field.key, -1)">↑</button>
      <button type="button" class="icon-btn" :disabled="busy || last" title="下移" @click="emit('move', field.key, 1)">↓</button>
    </div>
    <div class="cmdb-cell cmdb-action">
      <button
        v-if="!field.required"
        type="button"
        class="link-danger"
        :disabled="busy"
        @click="emit('remove', field.key)"
      >
        删除
      </button>
      <span v-else class="lock-text">锁定</span>
    </div>
  </div>
</template>

<style scoped>
.cmdb-row {
  display: grid;
  grid-template-columns: var(--cmdb-grid, minmax(220px, 300px) minmax(140px, 1fr) minmax(160px, 1fr) 58px 54px 68px 48px);
  align-items: center;
  gap: 8px;
  min-height: 40px;
  padding: 5px 10px;
  border-bottom: 1px solid var(--line);
  font-size: calc(12px * var(--font-scale, 1));
}
.cmdb-cell {
  min-width: 0;
}
.cmdb-name {
  display: block;
}
.cmdb-name input {
  width: 100%;
  min-width: 0;
  padding: 4px 7px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: var(--ink);
  font: inherit;
}
.cmdb-name input:hover,
.cmdb-name input:focus {
  border-color: var(--line);
  background: var(--surface);
}
.cmdb-name small,
.cmdb-cell small {
  color: var(--muted);
  font-size: 10px;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cmdb-key {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
  font-size: 11px;
}
.cmdb-kind-cell {
  display: flex;
  align-items: center;
  min-width: 0;
  overflow: hidden;
}
.cmdb-kind {
  display: inline-block;
  margin-right: 5px;
}
.cmdb-center {
  text-align: center;
}
.cmdb-order {
  display: flex;
  gap: 3px;
}
.icon-btn {
  width: 26px;
  height: 24px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: var(--surface);
  color: var(--ink-2);
  cursor: pointer;
  font: inherit;
  line-height: 1;
}
.icon-btn:disabled {
  opacity: 0.35;
  cursor: default;
}
.link-danger {
  border: 0;
  background: transparent;
  color: var(--danger, #b42318);
  cursor: pointer;
  font: inherit;
  padding: 2px;
}
.link-danger:disabled {
  opacity: 0.4;
  cursor: default;
}
.lock-text {
  color: var(--muted);
  font-size: 11px;
}
</style>
