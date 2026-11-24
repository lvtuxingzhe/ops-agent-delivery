<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  ids: string[]
  evidenceLabel: (id: string) => string
  compact?: boolean
}>()

defineEmits<{ (event: 'select-evidence', id: string): void }>()

const uniqueIds = computed(() => [...new Set(props.ids.map(String).filter(Boolean))])

const groups = computed(() => {
  const counts = new Map<string, number>()
  for (const id of uniqueIds.value) {
    const label = props.evidenceLabel(id)
    const host = label.split(' · ')[0] || '其他'
    counts.set(host, (counts.get(host) ?? 0) + 1)
  }
  return [...counts.entries()].map(([host, count]) => ({ host, count }))
})

const groupText = computed(() => groups.value.length > 1
  ? groups.value.map((item) => `${item.host} ${item.count}`).join(' / ')
  : '')
</script>

<template>
  <div v-if="uniqueIds.length" class="evidence-refs">
    <button
      v-if="uniqueIds.length === 1"
      type="button"
      :class="compact ? 'evidence-inline' : 'evidence-chip'"
      :title="evidenceLabel(uniqueIds[0])"
      @click="$emit('select-evidence', uniqueIds[0])"
    >
      {{ evidenceLabel(uniqueIds[0]) }}
    </button>

    <details v-else class="evidence-refs-details">
      <summary class="evidence-refs-summary">
        <span>证据 {{ uniqueIds.length }} 条</span>
        <span v-if="groupText" class="evidence-refs-groups">{{ groupText }}</span>
      </summary>
      <div class="evidence-refs-list">
        <button
          v-for="eid in uniqueIds"
          :key="eid"
          type="button"
          class="evidence-chip"
          :title="eid"
          @click="$emit('select-evidence', eid)"
        >
          {{ evidenceLabel(eid) }}
        </button>
      </div>
    </details>
  </div>
</template>

<style scoped>
.evidence-refs {
  min-width: 0;
}

.evidence-inline {
  max-width: 220px;
  overflow: hidden;
  padding: 1px 6px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: calc(10px * var(--font-scale, 1));
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evidence-inline:hover {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}

.evidence-refs-details {
  display: inline-block;
  max-width: 100%;
}

.evidence-refs-summary {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 2px 7px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface-soft);
  color: var(--ink-2);
  cursor: pointer;
  font-size: calc(11px * var(--font-scale, 1));
  line-height: 1.5;
  list-style: none;
}

.evidence-refs-summary::-webkit-details-marker {
  display: none;
}

.evidence-refs-summary::before {
  content: "▸";
  color: var(--muted);
  font-size: 10px;
}

.evidence-refs-details[open] .evidence-refs-summary::before {
  content: "▾";
}

.evidence-refs-groups {
  color: var(--muted);
}

.evidence-refs-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 6px;
}
</style>
