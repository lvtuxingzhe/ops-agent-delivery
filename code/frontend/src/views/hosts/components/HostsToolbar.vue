<script setup lang="ts">
/** 主机列表顶部：标题、统计与操作按钮。 */
import { ref } from 'vue'

const props = defineProps<{
  hostsCount: number
  filteredCount: number
  enabledCount: number
  filtersActive: boolean
  loading: boolean
  busy: boolean
  exporting: boolean
  canWrite: boolean
}>()

const emit = defineEmits<{
  (event: 'download-template'): void
  (event: 'import-file', file: File): void
  (event: 'export'): void
  (event: 'create'): void
}>()

const importInput = ref<HTMLInputElement | null>(null)

function chooseImportFile() {
  importInput.value?.click()
}

function onImportChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  input.value = ''
  if (file) emit('import-file', file)
}
</script>

<template>
  <div class="card-head">
    <div>
      <div class="host-title-row">
        <span class="badge badge-muted">
          {{ filtersActive ? `${filteredCount} / ${hostsCount}` : hostsCount }} 台
        </span>
        <span v-if="!loading" class="host-overview">
          {{ enabledCount }} 启用 · {{ hostsCount - enabledCount }} 停用
        </span>
      </div>
    </div>
    <div class="host-head-actions">
      <input
        ref="importInput"
        class="host-import-input"
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        @change="onImportChange"
      />
      <button class="ghost" type="button" :disabled="busy || !canWrite" @click="emit('download-template')">下载模板</button>
      <button class="ghost" type="button" :disabled="busy || !canWrite" @click="chooseImportFile">
        {{ busy ? '处理中…' : '导入 Excel' }}
      </button>
      <button class="ghost" type="button" :disabled="loading || exporting || !filteredCount" @click="emit('export')">
        {{ exporting ? '导出中…' : '导出 CSV' }}
      </button>
      <button class="primary" type="button" :disabled="busy || !canWrite" @click="emit('create')">新增主机</button>
    </div>
  </div>
</template>

<style scoped>
.host-head-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.host-import-input {
  display: none;
}

.host-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 5px;
}

.host-title-row h2 {
  margin: 0;
  font-size: calc(20px * var(--font-scale, 1));
  font-weight: 650;
  letter-spacing: -0.02em;
}

.host-overview {
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
}

@media (max-width: 760px) {
  .host-title-row {
    flex-wrap: wrap;
    gap: 6px 8px;
  }
}
</style>
