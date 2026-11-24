<script setup lang="ts">
/**
 * 数据源状态条：工作台和新建排查页共用。
 *
 * 父页面已经拿到 overview 时直接传入，避免重复请求；没有传时组件自己拉一次。
 * 状态条只做只读提示，点“查看配置”进入配置页。
 */

import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { fetchOverview, type Overview } from '../api/overview'

const props = defineProps<{ overview?: Overview | null }>()
const router = useRouter()
const loaded = ref<Overview | null>(null)
const failed = ref(false)

const data = computed(() => (props.overview !== undefined ? props.overview : loaded.value))

onMounted(async () => {
  if (props.overview !== undefined) return
  try {
    loaded.value = await fetchOverview()
  } catch {
    failed.value = true
  }
})
</script>

<template>
  <div v-if="data" class="status-strip">
    <span class="status-item">
      <span class="status-dot ok" aria-hidden="true" />
      数据源 {{ data.datasources.enabled }} 个启用
    </span>
    <span class="status-item">正常 {{ data.datasources.health.ok ?? 0 }}</span>
    <span class="status-item">不通过 {{ data.datasources.health.failed ?? 0 }}</span>
    <span class="status-item">未检查 {{ data.datasources.health.unknown ?? 0 }}</span>
    <span class="status-spacer" />
    <button class="plain status-link" type="button" @click="router.push('/config')">查看配置</button>
  </div>
  <p v-else-if="failed" class="status-failed">数据源状态读取失败，仍可提交排查。</p>
</template>

<style scoped>
.status-strip {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 18px;
  padding: 0 2px;
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
}

.status-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.status-dot.ok {
  background: #10b981;
}

.status-spacer {
  flex: 1;
}

.status-link {
  min-height: 28px;
  padding: 3px 8px;
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: calc(11px * var(--font-scale, 1));
}

.status-failed {
  margin: 0;
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
}
</style>
