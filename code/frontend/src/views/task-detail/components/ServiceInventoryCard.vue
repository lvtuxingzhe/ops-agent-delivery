<script setup lang="ts">
import type { InventoryGroup } from '../useAnswerPresentation'
import EvidenceRefs from './EvidenceRefs.vue'

defineProps<{
  groups: InventoryGroup[]
  evidenceLabel: (id: string) => string
}>()

defineEmits<{ (event: 'select-evidence', id: string): void }>()

const KIND_LABELS: Record<string, string> = {
  systemd: 'systemd',
  container: '容器',
  process: '进程',
  port: '端口',
}

const STATUS_LABELS: Record<string, string> = {
  running: '运行中',
  failed: '失败',
  unknown: '状态未知',
  no_data: '无数据',
}

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

function coverageText(status: string): string {
  if (status === 'unreachable') return '没有连上该主机，不能判断“没有服务”'
  if (status === 'no_data') return '采集到记录但没有可用数据，不能判断“没有服务”'
  return ''
}
</script>

<template>
  <section class="inventory-card" aria-label="服务清单">
    <div class="inventory-head">
      <div>
        <h3>服务清单</h3>
        <p class="desc">按主机分组；每行都挂了本次真实证据，点证据可回看原文。</p>
      </div>
      <span class="badge badge-muted">
        {{ groups.length }} 台 / {{ groups.reduce((sum, group) => sum + group.counts.total, 0) }} 项
      </span>
    </div>

    <article v-for="group in groups" :key="group.host" class="inventory-host">
      <div class="inventory-host-head">
        <div class="inventory-host-title">
          <h4>{{ group.host }}</h4>
          <span class="badge badge-muted">{{ group.counts.total }} 项</span>
          <span v-if="group.counts.failed" class="badge badge-danger">
            {{ group.counts.failed }} 项失败
          </span>
        </div>
        <span class="inventory-running">{{ group.counts.running }} 项运行中</span>
      </div>

      <p v-if="group.coverage && group.coverage.status !== 'ok'" class="inventory-coverage-warning">
        {{ coverageText(group.coverage.status) }}
        <span v-if="group.coverage.reason">：{{ group.coverage.reason }}</span>
      </p>

      <div class="inventory-table-wrap">
        <table class="inventory-table">
          <caption class="sr-only">{{ group.host }} 服务清单</caption>
          <thead>
            <tr>
              <th scope="col">服务</th>
              <th scope="col">来源</th>
              <th scope="col">端口</th>
              <th scope="col">状态</th>
              <th scope="col">证据</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, index) in group.items" :key="`${item.host}-${item.kind}-${item.service}-${index}`">
              <td class="inventory-service">{{ item.service }}</td>
              <td><span class="inventory-kind">{{ kindLabel(item.kind) }}</span></td>
              <td class="inventory-ports">{{ item.ports.length ? item.ports.join(', ') : '—' }}</td>
              <td>
                <span class="inventory-status" :class="`is-${item.status}`">
                  {{ statusLabel(item.status) }}
                </span>
              </td>
              <td>
                <div class="inventory-evidence">
                  <EvidenceRefs
                    :ids="item.evidence_ids"
                    :evidence-label="evidenceLabel"
                    compact
                    @select-evidence="$emit('select-evidence', $event)"
                  />
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>
  </section>
</template>

<style scoped>
.inventory-card {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface);
  padding: 18px;
}

.inventory-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.inventory-head h3 {
  margin: 2px 0 4px;
}

.inventory-head .desc {
  margin: 0;
}

.inventory-host {
  padding: 14px 0 4px;
  border-top: 1px solid var(--line);
}

.inventory-host:first-of-type {
  border-top: 0;
  padding-top: 0;
}

.inventory-host-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.inventory-host-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.inventory-host-title h4 {
  margin: 0;
}

.inventory-running {
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}

.inventory-coverage-warning {
  margin: 0 0 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--warning-soft, #fff7e6);
  color: var(--warning, #b45309);
  font-size: 13px;
}

.inventory-table-wrap {
  overflow-x: auto;
}

.inventory-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.inventory-table th,
.inventory-table td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}

.inventory-table th {
  color: var(--muted);
  font-weight: 600;
  white-space: nowrap;
}

.inventory-service {
  font-weight: 600;
  word-break: break-word;
}

.inventory-kind {
  display: inline-flex;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--surface-soft);
  color: var(--muted);
  font-size: 12px;
}

.inventory-ports {
  color: var(--text-secondary, #64748b);
  white-space: nowrap;
}

.inventory-status {
  display: inline-flex;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--surface-soft);
  color: var(--muted);
  font-size: 12px;
}

.inventory-status.is-running {
  background: var(--success-soft);
  color: var(--success);
}

.inventory-status.is-failed {
  background: var(--danger-soft);
  color: var(--danger);
}

.inventory-status.is-no_data {
  background: var(--warning-soft, #fff7e6);
  color: var(--warning, #b45309);
}

.inventory-evidence {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
