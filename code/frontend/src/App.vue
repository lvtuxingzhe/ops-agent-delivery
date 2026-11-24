<script setup lang="ts">
/**
 * 根组件：登录页不套外壳，其余页面统一走 AppShell。
 *
 * 外壳只负责导航与身份展示；页面权限判断仍由各页面向后台确认。
 */

import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppShell from './components/AppShell.vue'

const route = useRoute()
const isLogin = computed(() => route.name === 'login')
</script>

<template>
  <AppShell v-if="!isLogin">
    <router-view />
  </AppShell>
  <router-view v-else />
</template>
