<script setup lang="ts">
/**
 * 顶栏通知铃铛：只读当前用户收件箱。
 *
 * 每 30 秒刷新一次未读数；打开面板时拉最近 20 条。
 * 通知写失败不影响业务，这里也只在接口可用时显示，不阻塞顶栏。
 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  fetchNotifications,
  markAllNotificationsRead,
  type NotificationItem,
} from '../api/notifications'
import { formatLocalTime } from '../utils/time'

const router = useRouter()
const items = ref<NotificationItem[]>([])
const unread = ref(0)
const open = ref(false)
const loading = ref(false)
const error = ref('')
let timer: ReturnType<typeof setInterval> | null = null
let closeTimer: ReturnType<typeof setTimeout> | null = null
const AUTO_CLOSE_MS = 4_000

function clearCloseTimer(): void {
  if (closeTimer) {
    clearTimeout(closeTimer)
    closeTimer = null
  }
}

function scheduleAutoClose(): void {
  clearCloseTimer()
  closeTimer = setTimeout(() => {
    open.value = false
    closeTimer = null
  }, AUTO_CLOSE_MS)
}

const unreadLabel = computed(() => (unread.value > 99 ? '99+' : String(unread.value)))
const historyLabel = computed(() => {
  if (!items.value.length) return '暂无通知'
  if (!unread.value) return `共 ${items.value.length} 条，均已读`
  return `${unread.value} 条未读 · 共 ${items.value.length} 条`
})

async function load(): Promise<void> {
  try {
    const result = await fetchNotifications()
    items.value = result.items
    unread.value = result.unread_count
  } catch {
    // 顶栏不能因为通知接口失败而显示错误；业务页自己会处理 401
  }
}

async function toggle(): Promise<void> {
  open.value = !open.value
  if (!open.value) {
    clearCloseTimer()
    return
  }
  scheduleAutoClose()
  loading.value = true
  error.value = ''
  try {
    const result = await fetchNotifications()
    items.value = result.items
    unread.value = result.unread_count
  } catch (err) {
    error.value = err instanceof Error ? err.message : '通知加载失败'
  } finally {
    loading.value = false
  }
}

function close(): void {
  clearCloseTimer()
  open.value = false
}

async function openItem(item: NotificationItem): Promise<void> {
  // 打开报告详情只负责跳转；是否已读由用户显式操作，避免“看过报告”
  // 就把通知红点清掉，导致通知和报告阅读状态混在一起。
  close()
  if (item.task_id == null) return
  if (item.type === 'task.approval_timeout') {
    router.push({ path: '/approvals', query: { task: item.task_id } })
    return
  }
  if (item.type.startsWith('deployment.')) {
    router.push({ path: '/deployments', query: { tab: 'records', task: item.task_id } })
    return
  }
  router.push(`/tasks/${item.task_id}`)
}

async function readAll(): Promise<void> {
  try {
    const result = await markAllNotificationsRead()
    items.value = items.value.map((item) => ({
      ...item,
      read_at: item.read_at || new Date().toISOString(),
    }))
    unread.value = 0
    error.value = ''
    if (result.updated === 0) return
  } catch (err) {
    error.value = err instanceof Error ? err.message : '全部已读失败'
  }
}

function timeText(iso: string | null): string {
  if (!iso) return ''
  return formatLocalTime(iso).slice(5, 16)
}

function refreshFromDetail(): void {
  void load()
}

onMounted(() => {
  void load()
  timer = setInterval(() => void load(), 30_000)
  window.addEventListener('ops:notifications-read', refreshFromDetail)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  clearCloseTimer()
  window.removeEventListener('ops:notifications-read', refreshFromDetail)
})
</script>

<template>
  <div class="notification-wrap">
    <button
      class="bell"
      type="button"
      :aria-expanded="open"
      aria-label="通知"
      @click="toggle"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
        <path d="M10 21h4" />
      </svg>
      <span v-if="unread" class="badge-dot">{{ unreadLabel }}</span>
    </button>

    <div v-if="open" class="backdrop" @click="close" />

    <section v-if="open" class="panel" aria-label="通知列表">
      <header class="panel-head">
        <div>
          <strong>通知</strong>
          <small>{{ historyLabel }}</small>
        </div>
        <button class="text-btn" type="button" :disabled="!unread" @click="readAll">
          全部已读
        </button>
      </header>

      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="loading" class="empty">加载中…</p>
      <div v-else-if="items.length" class="list">
        <div
          v-for="item in items"
          :key="item.id"
          class="item-row"
          :class="{ unread: !item.read_at }"
        >
          <button class="item" type="button" @click="openItem(item)">
            <span class="item-top">
              <strong>{{ item.title }}</strong>
              <span class="item-meta">
                <span v-if="item.read_at" class="read-tag">已读</span>
                <small>{{ timeText(item.created_at) }}</small>
              </span>
            </span>
            <span class="item-body">{{ item.body }}</span>
          </button>
        </div>
      </div>
      <p v-else class="empty">还没有通知。</p>
    </section>
  </div>
</template>

<style scoped>
.notification-wrap {
  position: relative;
}
.bell {
  position: relative;
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
}
.bell:hover { color: var(--ink); }
.bell svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.7; }
.badge-dot {
  position: absolute;
  top: -6px;
  right: -7px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 999px;
  background: #dc2626;
  color: #fff;
  font-size: calc(10px * var(--font-scale, 1));
  line-height: 17px;
  text-align: center;
  font-weight: 700;
}
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
}
.panel {
  position: absolute;
  z-index: 41;
  top: 44px;
  right: 0;
  width: 340px;
  max-height: 440px;
  overflow: auto;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface);
  box-shadow: 0 18px 44px rgba(15, 23, 42, 0.16);
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 2px 4px 10px;
  border-bottom: 1px solid var(--line);
}
.panel-head strong { display: block; font-size: calc(14px * var(--font-scale, 1)); }
.panel-head small { display: block; margin-top: 2px; color: var(--muted-2); font-size: calc(11px * var(--font-scale, 1)); }
.text-btn {
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: calc(12px * var(--font-scale, 1));
  cursor: pointer;
}
.text-btn:disabled { color: var(--muted-2); cursor: default; }
.list { display: grid; gap: 2px; padding-top: 8px; }
.item-row {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  border-radius: 8px;
}
.item-row:hover { background: var(--surface-soft); }
.item-row.unread { background: rgba(37, 99, 235, 0.06); }
.item {
  display: grid;
  flex: 1;
  min-width: 0;
  gap: 4px;
  width: 100%;
  padding: 9px 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.item-top { display: flex; justify-content: space-between; gap: 10px; }
.item-top strong { color: var(--ink); font-size: calc(13px * var(--font-scale, 1)); font-weight: 600; }
.item-meta { display: inline-flex; align-items: center; gap: 6px; flex: none; }
.item-meta small { color: var(--muted-2); font-size: calc(11px * var(--font-scale, 1)); white-space: nowrap; }
.read-tag {
  padding: 1px 5px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--muted-2);
  font-size: calc(10px * var(--font-scale, 1));
  line-height: 1.5;
  white-space: nowrap;
}
.item-body { color: var(--muted); font-size: calc(12px * var(--font-scale, 1)); line-height: 1.5; }
.empty { padding: 18px 4px; color: var(--muted-2); font-size: calc(12px * var(--font-scale, 1)); text-align: center; }
.error { padding: 8px 4px; color: #b91c1c; font-size: calc(12px * var(--font-scale, 1)); }
</style>
