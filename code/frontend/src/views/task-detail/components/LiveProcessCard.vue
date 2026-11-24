<script setup lang="ts">
import { computed } from 'vue'

import type { TaskTraceItem } from '../../../api/tasks'

const props = defineProps<{
  items: TaskTraceItem[]
  total: number
}>()

defineEmits<{ (event: 'open-process'): void }>()

const KIND_LABELS: Record<string, string> = {
  thinking: '思考',
  tool_call: '调用工具',
  tool_result: '工具回包',
  text: '模型说明',
  step: '步骤',
  error: '失败',
}

const latestItems = computed(() => props.items.slice(-4))

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

function itemText(item: TaskTraceItem): string {
  return String(item.text || item.name || item.output || '').replace(/\s+/g, ' ').trim()
}
</script>

<template>
  <section class="card live-process-card" aria-label="实时排查过程">
    <div class="live-process-head">
      <div>
        <span class="live-process-title">
          <span class="live-process-dot" aria-hidden="true" />
          {{ total ? 'AI 正在排查' : 'AI 排查准备中' }}
        </span>
        <p class="desc">
          {{ total
            ? '过程会随每一步实时刷新，完成后可在「过程与参考」查看完整记录。'
            : '任务已进入队列，正在等待 Agent 启动。' }}
        </p>
      </div>
      <span class="badge badge-info">{{ total }} 步</span>
    </div>

    <ol v-if="latestItems.length" class="live-process-list">
      <li v-for="item in latestItems" :key="item.index" class="live-process-item">
        <span class="live-process-index">{{ item.index }}</span>
        <div class="live-process-main">
          <span class="live-process-kind">{{ kindLabel(item.kind) }}</span>
          <p>{{ itemText(item) || '已完成一步，暂无文字说明' }}</p>
        </div>
      </li>
    </ol>
    <p v-else class="live-process-empty">正在等待 Agent 返回第一步…</p>

    <button
      v-if="total"
      type="button"
      class="ghost live-process-link"
      @click="$emit('open-process')"
    >
      查看完整过程
    </button>
  </section>
</template>

<style scoped>
.live-process-card {
  border-color: color-mix(in srgb, var(--accent) 28%, var(--line));
  background: linear-gradient(180deg, var(--accent-soft), var(--surface) 38%);
}

.live-process-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.live-process-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--ink);
  font-size: calc(16px * var(--font-scale, 1));
  font-weight: 650;
}

.live-process-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--accent);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 16%, transparent);
  animation: live-pulse 1.6s ease-in-out infinite;
}

.live-process-head .desc {
  margin: 5px 0 0;
}

.live-process-list {
  display: grid;
  gap: 8px;
  margin: 14px 0 0;
  padding: 0;
  list-style: none;
}

.live-process-item {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: color-mix(in srgb, var(--surface) 82%, transparent);
}

.live-process-index {
  display: grid;
  width: 26px;
  height: 26px;
  place-items: center;
  border-radius: 7px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: calc(11px * var(--font-scale, 1));
  font-weight: 700;
}

.live-process-main {
  min-width: 0;
}

.live-process-kind {
  color: var(--accent);
  font-size: calc(11px * var(--font-scale, 1));
  font-weight: 650;
}

.live-process-main p {
  margin: 2px 0 0;
  color: var(--ink-2);
  font-size: calc(12px * var(--font-scale, 1));
  line-height: 1.55;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.live-process-empty {
  margin: 14px 0 0;
  color: var(--muted);
  font-size: calc(12px * var(--font-scale, 1));
}

.live-process-link {
  margin-top: 10px;
  padding-left: 0;
  color: var(--accent);
}

@keyframes live-pulse {
  0%, 100% { opacity: 0.55; transform: scale(0.82); }
  50% { opacity: 1; transform: scale(1); }
}
</style>
