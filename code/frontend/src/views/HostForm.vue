<script setup lang="ts">
/** 路由入口薄包装；表单实现与 scoped 样式都在 HostFormView。 */
import HostFormView from './hosts/HostFormView.vue'
</script>

<template>
  <HostFormView v-bind="$attrs" />
</template>
