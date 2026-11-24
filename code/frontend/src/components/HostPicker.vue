<script setup lang="ts">
/**
 * 可搜索的目标主机选择器。
 *
 * 只做前端选择体验：从执行主机清单里按 ID / 地址 / 平台过滤；
 * 仍允许手填未登记 ID，最终由后端许可与主机清单保障。
 */
import { computed, onMounted, ref } from 'vue'

import { listHosts, type ExecHost } from '../api/hosts'

const props = withDefaults(defineProps<{
  modelValue: string
  label?: string
  placeholder?: string
  hint?: string
  platform?: string
}>(), {
  label: '目标主机',
  placeholder: '搜索主机 ID 或地址',
  hint: '从已登记主机中选择，也可以搜索 ID 或地址；未登记的主机执行时会被拒绝。',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:platform': [value: 'linux' | 'windows']
}>()

interface HostChoice {
  id: string
  platform: string
  address: string
  enabled: boolean
  source: 'local' | 'registered'
}

const hosts = ref<ExecHost[]>([])
const hostsLoading = ref(false)
const menuOpen = ref(false)
const menuTouched = ref(false)

const value = computed({
  get: () => props.modelValue,
  set: (next) => emit('update:modelValue', next),
})

const hostChoices = computed<HostChoice[]>(() => [
  {
    id: 'local',
    platform: 'local',
    address: '本机 / 当前 Worker',
    enabled: true,
    source: 'local',
  },
  ...hosts.value
    .filter((host) => host.id !== 'local')
    .map((host) => ({
      id: host.id,
      platform: host.platform,
      address: host.address,
      enabled: host.enabled,
      source: 'registered' as const,
    })),
])

const filteredHostChoices = computed(() => {
  const query = menuTouched.value ? value.value.trim().toLowerCase() : ''
  if (!query) return hostChoices.value
  return hostChoices.value.filter((host) =>
    [host.id, host.address, host.platform].some((field) =>
      field.toLowerCase().includes(query),
    ),
  )
})

const currentHostLabel = computed(() => {
  const current = hostChoices.value.find((host) => host.id === value.value.trim())
  return current
    ? `${current.id} · ${current.address}`
    : `${value.value.trim() || '未填写'}（未登记）`
})

onMounted(loadHosts)

async function loadHosts() {
  hostsLoading.value = true
  try {
    hosts.value = await listHosts()
  } catch {
    // 主机清单读不到不阻断页面；仍可手填 local 或已登记 ID。
  } finally {
    hostsLoading.value = false
  }
}

function openMenu() {
  menuOpen.value = true
  menuTouched.value = false
}

function filterMenu() {
  menuTouched.value = true
  menuOpen.value = true
}

function closeMenu() {
  menuOpen.value = false
}

function onFocusOut(event: FocusEvent) {
  const next = event.relatedTarget as Node | null
  if (next && (event.currentTarget as HTMLElement).contains(next)) return
  closeMenu()
}

function pickHost(host: HostChoice) {
  if (!host.enabled) return
  value.value = host.id
  if (host.platform === 'linux' || host.platform === 'windows') {
    emit('update:platform', host.platform)
  }
  menuOpen.value = false
  menuTouched.value = false
}
</script>

<template>
  <div class="host-field" @focusout="onFocusOut">
    <span class="host-label">{{ label }}</span>
    <div class="host-combobox">
      <input
        v-model="value"
        type="text"
        autocomplete="off"
        :placeholder="placeholder"
        role="combobox"
        aria-autocomplete="list"
        aria-controls="host-picker-menu"
        :aria-expanded="menuOpen"
        @focus="openMenu"
        @input="filterMenu"
        @keydown.esc="menuOpen = false"
      />
      <div v-if="menuOpen" id="host-picker-menu" class="host-menu">
        <div class="host-menu-head">
          <span>选择目标主机</span>
          <span class="mono">{{ filteredHostChoices.length }} 台</span>
        </div>
        <p v-if="hostsLoading" class="host-menu-empty">正在读取主机清单…</p>
        <button
          v-for="host in filteredHostChoices"
          :key="host.id"
          class="host-option"
          :class="{ 'is-selected': host.id === value.trim() }"
          type="button"
          :disabled="!host.enabled"
          @pointerdown.prevent="pickHost(host)"
          @click="pickHost(host)"
        >
          <span class="host-option-main">
            <strong>{{ host.id }}</strong>
            <small>{{ host.address }}</small>
          </span>
          <span class="host-option-meta">
            <span>{{ host.source === 'local' ? '本机' : host.platform }}</span>
            <span v-if="!host.enabled" class="host-off">已停用</span>
          </span>
        </button>
        <p v-if="!filteredHostChoices.length" class="host-menu-empty">
          没有匹配的主机；可继续输入已登记的 ID。
        </p>
      </div>
    </div>
    <span class="host-hint">当前：{{ currentHostLabel }}</span>
    <span v-if="hint" class="host-hint">{{ hint }}</span>
  </div>
</template>

<style scoped>
.host-field {
  display: flex;
  width: 100%;
  min-width: 0;
  flex-direction: column;
  gap: 6px;
}

.host-label {
  color: var(--muted, #64748b);
  font-size: calc(12px * var(--font-scale, 1));
}

.host-combobox {
  position: relative;
}

.host-combobox input {
  width: 100%;
}

.host-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  left: 0;
  z-index: 20;
  max-height: 280px;
  padding: 6px;
  overflow-y: auto;
  border: 1px solid var(--line, #e2e8f0);
  border-radius: 10px;
  background: var(--surface, #fff);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.16);
}

.host-menu-head {
  display: flex;
  justify-content: space-between;
  padding: 5px 8px 7px;
  color: var(--muted, #64748b);
  font-size: calc(11px * var(--font-scale, 1));
}

.host-option {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--ink, #0f172a);
  cursor: pointer;
  text-align: left;
}

.host-option:hover,
.host-option.is-selected {
  background: var(--surface-soft, #f1f5f9);
}

.host-option:disabled {
  cursor: not-allowed;
  opacity: 0.52;
}

.host-option-main {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.host-option-main strong {
  overflow: hidden;
  font-size: calc(12px * var(--font-scale, 1));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.host-option-main small {
  overflow: hidden;
  color: var(--muted, #64748b);
  font-size: calc(11px * var(--font-scale, 1));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.host-option-meta {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 6px;
  color: var(--muted, #64748b);
  font-size: calc(10px * var(--font-scale, 1));
}

.host-off {
  color: var(--danger, #b91c1c);
}

.host-menu-empty {
  margin: 8px;
  color: var(--muted, #64748b);
  font-size: calc(12px * var(--font-scale, 1));
}

.host-hint {
  color: var(--muted, #64748b);
  font-size: calc(11px * var(--font-scale, 1));
  line-height: 1.55;
}
</style>
