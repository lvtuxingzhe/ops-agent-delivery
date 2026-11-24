<script setup lang="ts">
/**
 * 部署详情里的知识入口。
 *
 * 详情页不再内嵌“历史部署经验”列表 —— 那会让同一条知识在多个页面重复出现，
 * 也看不到全局去重后的结果。这里只给一条可点击的入口，跳到独立知识库页，
 * 带上 kind=deployment 和目标主机做预筛。
 */

import { computed } from 'vue'

const props = defineProps<{ count: number; keyword?: string }>()

const to = computed(() => ({
  name: 'knowledge',
  query: {
    kind: 'deployment',
    ...(props.keyword ? { keyword: props.keyword } : {}),
  },
}))
</script>

<template>
  <RouterLink class="knowledge-shortcut" :to="to">
    <span>
      相关知识 <strong>{{ count }}</strong> 条
    </span>
    <span class="go">去知识库查看 →</span>
  </RouterLink>
</template>

<style scoped>
.knowledge-shortcut {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border: 1px solid var(--accent-line);
  border-radius: var(--radius);
  background: var(--accent-soft);
  color: var(--accent-ink);
  text-decoration: none;
  font-size: calc(13px * var(--font-scale, 1));
  font-weight: 600;
}

.knowledge-shortcut:hover {
  border-color: var(--accent);
  background: #e0efff;
}

.knowledge-shortcut strong {
  font-weight: 700;
}

.knowledge-shortcut .go {
  white-space: nowrap;
}
</style>
