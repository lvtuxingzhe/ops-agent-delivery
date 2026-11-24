<script setup lang="ts">
import type { ReportConclusion } from '../useAnswerPresentation'
import EvidenceRefs from './EvidenceRefs.vue'

defineProps<{
  title: string
  description?: string
  points: ReportConclusion[]
  evidenceLabel: (id: string) => string
}>()

defineEmits<{ (event: 'select-evidence', id: string): void }>()
</script>

<template>
  <div class="conclusion-points-panel">
    <div class="conclusion-points-head">
      <div>
        <h3>{{ title }}</h3>
        <p v-if="description" class="desc">{{ description }}</p>
      </div>
      <span class="badge badge-muted">{{ points.length }} 条</span>
    </div>
    <ol class="conclusion-points">
      <li v-for="(item, index) in points" :key="`${index}-${item.text}`" class="conclusion-points-item">
        <span class="conclusion-points-index" aria-hidden="true">{{ index + 1 }}</span>
        <div class="conclusion-points-main">
          <p>{{ item.text }}</p>
          <div v-if="item.evidence_ids?.length" class="evidence-links">
            <EvidenceRefs
              :ids="item.evidence_ids"
              :evidence-label="evidenceLabel"
              compact
              @select-evidence="$emit('select-evidence', $event)"
            />
          </div>
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.conclusion-points-panel {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface);
  padding: 18px;
}

.conclusion-points-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.conclusion-points-head h3 {
  margin: 2px 0 4px;
}

.conclusion-points-head .desc {
  margin: 0;
}

.conclusion-points {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.conclusion-points-item {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  gap: 10px;
  padding: 12px;
  border-radius: 10px;
  background: var(--surface-soft);
}

.conclusion-points-index {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 700;
  font-size: 12px;
}

.conclusion-points-main p {
  margin: 0 0 8px;
  line-height: 1.6;
  word-break: break-word;
}

.evidence-links {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
