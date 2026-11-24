<script setup lang="ts">
/** 主机列表主体：加载/空态/可调列宽表格。 */
import { ref } from 'vue'

import type { ExecHost } from '../../../api/hosts'
import ResizableTable from '../../../components/ResizableTable.vue'
import RowMoreMenu from '../../../components/RowMoreMenu.vue'
import {
  HOST_COLUMNS,
  HOST_COLUMN_WIDTHS_STORAGE_KEY,
  HOST_GRID,
} from '../hostsTable'

defineProps<{
  loading: boolean
  hostsCount: number
  filteredHosts: ExecHost[]
  busy: boolean
  canWrite: boolean
}>()

const emit = defineEmits<{
  (event: 'custom-change', value: boolean): void
  (event: 'reset-filters'): void
  (event: 'edit', host: ExecHost): void
  (event: 'toggle', host: ExecHost): void
  (event: 'confirm-key', host: ExecHost): void
  (event: 'delete', host: ExecHost): void
  (event: 'reveal-password', host: ExecHost): void
}>()

const table = ref<{ reset: () => void } | null>(null)

function environmentLabel(host: ExecHost) {
  return [host.os_family, host.distro, host.os_version].filter(Boolean).join(' ') || '未识别'
}

defineExpose({
  reset: () => table.value?.reset(),
})
</script>

<template>
  <div v-if="loading" class="host-state">
    <span class="host-spinner" aria-hidden="true"></span>
    正在读取主机清单…
  </div>
  <div v-else-if="!hostsCount" class="host-empty">
    <strong>还没有远程主机</strong>
    <p>内置 local 主机不在此列表中；新增后会在执行主机里出现。</p>
  </div>
  <div v-else-if="!filteredHosts.length" class="host-empty">
    <strong>没有匹配的主机</strong>
    <p>换个关键词，或清空平台、状态筛选。</p>
    <button class="ghost" type="button" @click="emit('reset-filters')">重置筛选</button>
  </div>
  <div v-else class="host-table-wrap">
    <ResizableTable
      ref="table"
      :columns="HOST_COLUMNS"
      :default-grid="HOST_GRID"
      :storage-key="HOST_COLUMN_WIDTHS_STORAGE_KEY"
      :gap="8"
      :padding-x="0"
      card-class="host-table"
      head-class="table-head"
      @custom-change="emit('custom-change', $event)"
    >
      <div v-for="host in filteredHosts" :key="host.id" class="table-row host-row">
        <div class="host-target" :title="`${host.id} ${host.address}:${host.port}`">
          <span class="host-id">{{ host.id }}</span>
          <span class="host-address mono">{{ host.address }}:{{ host.port }}</span>
        </div>
        <div class="host-environment">
          <span class="host-platform">{{ host.platform || '未填平台' }}</span>
          <span class="host-os-note">{{ environmentLabel(host) }}</span>
        </div>
        <div class="mono" :title="host.user">{{ host.user }}</div>
        <div class="host-credential">
          <span>{{ host.credential_mode === 'password' ? '密码已加密' : '未配置密码' }}</span>
          <span v-if="host.host_key_registered" class="host-key-note">主机密钥已登记</span>
          <button
            v-else
            class="host-key-note is-warning"
            type="button"
            :disabled="busy || !canWrite"
            title="主机公钥未登记，首次连接需要管理员确认"
            @click="emit('confirm-key', host)"
          >
            主机密钥未登记
          </button>
        </div>
        <div>
          <span class="tag" :class="host.enabled ? 'tag-on' : 'tag-off'">
            {{ host.enabled ? '启用' : '停用' }}
          </span>
        </div>
        <div class="host-actions host-sticky-actions">
          <button class="ghost" type="button" :disabled="busy" @click="emit('edit', host)">编辑</button>
          <RowMoreMenu variant="ghost" :disabled="busy || !canWrite">
            <template #default="{ close }">
              <button
                class="row-more-item"
                type="button"
                :disabled="busy || !canWrite || !host.password_set"
                :title="host.password_set ? '输入动态口令后查看完整登录密码' : '这台主机还没有保存密码'"
                @click="close(); emit('reveal-password', host)"
              >
                查看密码
              </button>
              <button
                v-if="!host.host_key_registered"
                class="row-more-item"
                type="button"
                :disabled="busy || !canWrite"
                @click="close(); emit('confirm-key', host)"
              >
                确认主机密钥
              </button>
              <button
                class="row-more-item"
                type="button"
                :disabled="busy || !canWrite"
                @click="close(); emit('toggle', host)"
              >
                {{ host.enabled ? '停用主机' : '启用主机' }}
              </button>
              <button
                class="row-more-item is-danger"
                type="button"
                :disabled="busy || !canWrite"
                title="软删除：从列表隐藏，历史任务、部署和审计记录保留"
                @click="close(); emit('delete', host)"
              >
                删除主机
              </button>
            </template>
          </RowMoreMenu>
        </div>
      </div>
    </ResizableTable>
  </div>
</template>

<style scoped>
.host-table-wrap {
  width: 100%;
  overflow-x: auto;
  overscroll-behavior-x: contain;
}

.host-table {
  display: flex;
  width: 100%;
  min-width: 0;
  flex-direction: column;
}

.host-table :deep(.rt-head) {
  min-height: 44px;
  align-items: center;
  font-size: calc(11px * var(--font-scale, 1));
}

/* 表头不再显示常驻列分隔竖线；保留拖拽热区，悬停时才提示。 */
.host-table :deep(.col-resizer::after) {
  opacity: 0;
  background: transparent;
}

.host-table :deep(.col-resizer:hover::after) {
  opacity: 0.9;
  background: var(--accent);
}

.host-row {
  display: grid;
  grid-template-columns: var(--rt-grid, var(--rt-default));
  gap: var(--rt-gap, 8px);
  align-items: center;
  min-height: 64px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
  font-size: calc(12px * var(--font-scale, 1));
}

.host-row:last-child {
  border-bottom: 0;
}

.host-row > div {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.host-target,
.host-environment,
.host-credential {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.host-id {
  color: var(--ink);
  font-weight: 600;
}

.host-address,
.host-os-note,
.host-key-note {
  overflow: hidden;
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.host-platform {
  color: var(--ink-2);
}

.host-key-note {
  display: inline-flex;
  width: max-content;
  max-width: 100%;
  align-items: center;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: default;
}

.host-key-note.is-warning {
  color: var(--warn);
  cursor: pointer;
}

.host-key-note.is-warning:hover:not(:disabled) {
  text-decoration: underline;
}

.host-key-note:disabled {
  cursor: default;
}

.host-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  min-height: 160px;
  border: 1px dashed var(--line);
  border-radius: 10px;
  background: var(--surface-soft);
  color: var(--muted);
  font-size: calc(12px * var(--font-scale, 1));
}

.host-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--line);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: host-spin 0.7s linear infinite;
}

@keyframes host-spin {
  to { transform: rotate(360deg); }
}

.host-empty {
  display: flex;
  min-height: 160px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px dashed var(--line);
  border-radius: 10px;
  background: var(--surface-soft);
  text-align: center;
}

.host-empty strong {
  color: var(--ink);
  font-size: calc(15px * var(--font-scale, 1));
}

.host-empty p {
  margin: 0;
  color: var(--muted);
  font-size: calc(12px * var(--font-scale, 1));
}

.host-table td {
  white-space: nowrap;
}

.host-row > .host-actions {
  display: flex;
  flex-wrap: nowrap;
  gap: 6px;
  align-items: center;
  justify-content: flex-start;
  overflow: visible;
  text-overflow: clip;
  white-space: nowrap;
}

/* 操作列固定在右侧：表格横向滚动时仍可直接编辑/打开更多操作。 */
:deep(.rt-head .host-sticky-actions),
.host-row .host-sticky-actions {
  position: sticky;
  right: 0;
  z-index: 2;
  padding-left: 10px;
  box-shadow: -10px 0 12px -14px rgba(15, 23, 42, 0.45);
}

:deep(.rt-head .host-sticky-actions) {
  z-index: 3;
  background: var(--surface-soft);
  text-align: left;
}

.host-row .host-sticky-actions {
  background: var(--surface);
}

.host-actions .ghost {
  min-height: 30px;
  padding: 0 10px;
}

.host-actions :deep(.row-more-trigger) {
  min-height: 30px;
  padding: 0 10px;
}

@media (max-width: 760px) {
  .host-table {
    min-width: 0;
  }

  .host-row {
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas:
      "target actions"
      "status status";
    gap: 8px 10px;
    min-height: 0;
    padding: 12px 0;
  }

  .host-row > :nth-child(1) { grid-area: target; }
  .host-row > :nth-child(5) { grid-area: status; justify-self: start; }
  .host-row > :nth-child(6) { grid-area: actions; }

  .host-row > :nth-child(2),
  .host-row > :nth-child(3),
  .host-row > :nth-child(4) {
    display: none;
  }

  .host-row > .host-actions {
    justify-content: flex-end;
  }
}
</style>
