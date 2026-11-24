<script setup lang="ts">
/** 导入失败明细；只展示后端返回的前 50 条。 */
import type { HostImportError } from '../../../api/hosts'

defineProps<{ errors: HostImportError[] }>()
const emit = defineEmits<{ (event: 'close'): void }>()
</script>

<template>
  <div v-if="errors.length" class="host-import-errors">
    <div class="host-import-errors-head">
      <strong>导入失败明细</strong>
      <button type="button" class="plain" @click="emit('close')">关闭</button>
    </div>
    <p v-for="item in errors" :key="`${item.row}-${item.host_id}`">
      第 {{ item.row }} 行<span v-if="item.host_id">（{{ item.host_id }}）</span>：{{ item.error }}
    </p>
  </div>
</template>

<style scoped>
.host-import-errors {
  margin: 0 0 14px;
  padding: 12px 14px;
  border: 1px solid rgba(220, 38, 38, .25);
  border-radius: 10px;
  background: var(--danger-soft);
  color: var(--danger);
  font-size: calc(12px * var(--font-scale, 1));
}

.host-import-errors-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.host-import-errors p {
  margin: 3px 0;
}
</style>
