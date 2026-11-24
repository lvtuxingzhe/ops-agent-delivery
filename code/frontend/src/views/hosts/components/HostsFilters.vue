<script setup lang="ts">
/** 主机列表筛选栏：搜索、平台多选、状态多选、列宽重置。 */
import MultiSelect from '../../../components/MultiSelect.vue'

const query = defineModel<string>('query', { required: true })
const platforms = defineModel<string[]>('platforms', { required: true })
const statuses = defineModel<string[]>('statuses', { required: true })

defineProps<{
  filtersActive: boolean
  hasCustomWidths: boolean
  platformOptions: { value: string; label: string }[]
}>()

const emit = defineEmits<{
  (event: 'reset'): void
  (event: 'reset-widths'): void
}>()

const statusSelectOptions = [
  { value: 'enabled', label: '启用' },
  { value: 'disabled', label: '停用' },
]
</script>

<template>
  <div class="host-filters">
    <input
      v-model="query"
      class="host-search"
      type="search"
      placeholder="搜索主机/账号"
    />
    <div class="host-filter-group">
      <span>平台</span>
      <MultiSelect
        v-model="platforms"
        :options="platformOptions"
        aria-label="平台"
        placeholder="全部平台"
      />
    </div>
    <div class="host-filter-group">
      <span>状态</span>
      <MultiSelect
        v-model="statuses"
        :options="statusSelectOptions"
        aria-label="状态"
        placeholder="全部状态"
      />
    </div>
    <button v-if="filtersActive" class="ghost host-filter-reset" type="button" @click="emit('reset')">
      重置筛选
    </button>
    <div class="host-column-tools">
      <button class="ghost host-reset" type="button" :disabled="!hasCustomWidths" @click="emit('reset-widths')">
        重置列宽
      </button>
    </div>
  </div>
</template>

<style scoped>
.host-filters {
  --filter-h: 36px;
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 8px 12px;
  margin: 12px 0 20px;
}

.host-search {
  flex: 0 0 var(--filter-search-w);
  width: var(--filter-search-w);
  min-width: var(--filter-search-w);
  max-width: var(--filter-search-w);
  height: var(--filter-h);
  min-height: var(--filter-h);
  padding: 0 12px;
}

.host-filter-group {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: calc(12px * var(--font-scale, 1));
}

.host-filter-group :deep(.multi-select) {
  width: var(--filter-w);
  min-width: var(--filter-w);
}

.host-filter-group :deep(.multi-trigger) {
  height: var(--filter-h);
  min-height: var(--filter-h);
}

.host-column-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.host-filter-reset {
  min-height: 28px;
  padding: 0 9px;
  font-size: calc(11px * var(--font-scale, 1));
}

.host-reset {
  min-height: 28px;
  padding: 0 9px;
  font-size: calc(11px * var(--font-scale, 1));
}

.host-reset:disabled {
  opacity: .45;
  cursor: default;
}

@media (max-width: 760px) {
  .host-filters {
    flex-wrap: wrap;
  }

  .host-search {
    flex-basis: 100%;
    max-width: none;
  }

  .host-column-tools {
    width: 100%;
    justify-content: flex-end;
    margin-left: 0;
  }
}

@media (max-width: 480px) {
  .host-filter-group {
    flex: 1 1 100%;
  }

  .host-filter-group :deep(.multi-select) {
    width: 100%;
    min-width: 0;
  }
}
</style>
