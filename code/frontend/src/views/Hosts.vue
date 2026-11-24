<script setup lang="ts">
/** 路由入口薄包装；页面实现与样式都在 HostListView，避免 scoped 样式跨组件失效。 */
import HostListView from './hosts/HostListView.vue'
</script>

<template>
  <HostListView v-bind="$attrs" />
</template>
