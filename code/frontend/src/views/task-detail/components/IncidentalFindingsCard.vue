<script setup lang="ts">
import type { TaskIncidentalFinding } from '../../../api/tasks'
import EvidenceRefs from './EvidenceRefs.vue'

defineProps<{
  findings: TaskIncidentalFinding[]
  evidenceLabel: (id: string) => string
}>()

defineEmits<{ (event: 'select-evidence', id: string): void }>()

const SEVERITY_LABELS: Record<string, string> = {
  info: '记录',
  warning: '需关注',
  critical: '影响业务',
}

function severityLabel(value: string): string {
  return SEVERITY_LABELS[value] ?? value
}
</script>

<template>
  <section class="incidental-card" aria-label="附带发现">
    <div class="incidental-head">
      <div>
        <h3>附带发现</h3>
        <p class="desc">这些不是标题问的主答案，单独放在这里，避免抢首屏。</p>
      </div>
      <span class="badge badge-muted">{{ findings.length }} 条</span>
    </div>

    <ul class="incidental-list">
      <li v-for="(item, index) in findings" :key="`${item.object}-${item.finding}-${index}`" class="incidental-item">
        <div class="incidental-title">
          <strong>{{ item.object }}</strong>
          <span class="incidental-severity" :class="`is-${item.severity}`">
            {{ severityLabel(item.severity) }}
          </span>
        </div>
        <p>{{ item.finding }}</p>
        <p v-if="item.impact" class="incidental-impact">影响：{{ item.impact }}</p>
        <div v-if="item.evidence_ids?.length" class="incidental-evidence">
          <EvidenceRefs
            :ids="item.evidence_ids"
            :evidence-label="evidenceLabel"
            compact
            @select-evidence="$emit('select-evidence', $event)"
          />
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.incidental-card {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-soft);
  padding: 18px;
}

.incidental-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.incidental-head h3 {
  margin: 2px 0 4px;
}

.incidental-head .desc {
  margin: 0;
}

.incidental-list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.incidental-item {
  padding: 12px;
  border-radius: 10px;
  background: var(--surface);
}

.incidental-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.incidental-severity {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--surface-soft);
  color: var(--muted);
  font-size: 12px;
}

.incidental-severity.is-warning {
  background: var(--warning-soft, #fff7e6);
  color: var(--warning, #b45309);
}

.incidental-severity.is-critical {
  background: var(--danger-soft);
  color: var(--danger);
}

.incidental-item p {
  margin: 8px 0 0;
  line-height: 1.6;
  word-break: break-word;
}

.incidental-impact {
  color: var(--muted);
  font-size: 13px;
}

.incidental-evidence {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
</style>
