<script setup lang="ts">
/**
 * 配置卡片的折叠按钮。
 *
 * 只负责「展开 / 收起」这一个动作，卡片标题和正文仍由各自面板渲染。
 * 用 aria-expanded 和 aria-controls 把状态告诉辅助技术，避免只看颜色。
 */

defineProps<{
  expanded: boolean
  controls: string
}>()

defineEmits<{
  toggle: []
}>()
</script>

<template>
  <button
    type="button"
    class="plain fold-toggle"
    :aria-expanded="expanded"
    :aria-controls="controls"
    @click="$emit('toggle')"
  >
    {{ expanded ? '收起' : '展开' }}
    <span class="fold-chevron" :class="{ collapsed: !expanded }" aria-hidden="true"></span>
  </button>
</template>

<style scoped>
.fold-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}

.fold-chevron {
  width: 0;
  height: 0;
  border-top: 5px solid currentColor;
  border-right: 4px solid transparent;
  border-left: 4px solid transparent;
  transition: transform 0.18s ease;
}

.fold-chevron.collapsed {
  transform: rotate(-90deg);
}
</style>
