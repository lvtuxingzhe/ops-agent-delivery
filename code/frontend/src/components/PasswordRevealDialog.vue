<script setup lang="ts">
/**
 * 查看敏感值前的动态口令弹窗。
 *
 * 只负责收集 6 位口令和展示调用方给的结果；取数、鉴权、审计都在页面/API 层。
 * 结果出现后 30 秒自动关闭并清空，避免密码一直挂在屏幕上。
 */
import { computed, ref, watch } from 'vue'

export interface RevealField {
  label: string
  value: string
}

const props = defineProps<{
  open: boolean
  title: string
  target: string
  hint?: string
  busy?: boolean
  error?: string
  fields?: RevealField[]
}>()

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'confirm', code: string): void
}>()

const code = ref('')
const seconds = ref(30)
const copied = ref('')
let timer: number | null = null

const hasResult = computed(() => Boolean(props.fields?.length))
const canSubmit = computed(() => code.value.length === 6 && !props.busy)

function stopTimer() {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

function startTimer() {
  stopTimer()
  seconds.value = 30
  timer = window.setInterval(() => {
    seconds.value -= 1
    if (seconds.value <= 0) {
      stopTimer()
      emit('close')
    }
  }, 1000)
}

watch(() => props.fields, (fields) => {
  if (fields?.length) startTimer()
  else {
    stopTimer()
    seconds.value = 30
  }
})

watch(() => props.open, (open) => {
  if (!open) {
    code.value = ''
    copied.value = ''
    stopTimer()
    seconds.value = 30
  }
})

function submit() {
  if (!canSubmit.value) return
  emit('confirm', code.value)
}

function close() {
  if (props.busy) return
  emit('close')
}

function onlyDigits(event: Event) {
  const input = event.target as HTMLInputElement
  code.value = input.value.replace(/\D/g, '').slice(0, 6)
}

async function copy(label: string, value: string) {
  try {
    await navigator.clipboard.writeText(value)
    copied.value = label
    window.setTimeout(() => {
      if (copied.value === label) copied.value = ''
    }, 1500)
  } catch {
    copied.value = ''
  }
}
</script>

<template>
  <div v-if="open" class="app-modal" @click.self="close">
    <section class="app-modal-card reveal-dialog" role="dialog" aria-modal="true">
      <header class="app-modal-head">
        <div>
          <p class="kicker">STEP-UP</p>
          <h2>{{ title }}</h2>
        </div>
        <button type="button" class="plain" :disabled="busy" @click="close">关闭</button>
      </header>

      <p class="desc">
        目标：<span class="mono">{{ target }}</span>。查看前请输入当前登录账号的 6 位动态口令。
        <span v-if="hint">{{ hint }}</span>
      </p>

      <p v-if="error" class="error">{{ error }}</p>

      <template v-if="hasResult">
        <div class="reveal-fields">
          <div v-for="field in fields" :key="field.label" class="reveal-field">
            <label>{{ field.label }}</label>
            <div class="reveal-value">
              <span class="mono">{{ field.value }}</span>
              <button type="button" class="plain" @click="copy(field.label, field.value)">
                {{ copied === field.label ? '已复制' : '复制' }}
              </button>
            </div>
          </div>
        </div>
        <p class="reveal-countdown">
          {{ seconds }} 秒后自动隐藏。关闭弹窗会立即清空。
        </p>
      </template>

      <form v-else class="reveal-form" @submit.prevent="submit">
        <label>
          动态口令
          <input
            :value="code"
            inputmode="numeric"
            autocomplete="one-time-code"
            maxlength="6"
            placeholder="6 位数字"
            @input="onlyDigits"
          />
        </label>
        <div class="reveal-actions">
          <button type="button" class="plain" :disabled="busy" @click="close">取消</button>
          <button type="submit" class="primary" :disabled="!canSubmit">
            {{ busy ? '校验中…' : '查看完整密码' }}
          </button>
        </div>
      </form>
    </section>
  </div>
</template>

<style scoped>
.reveal-dialog {
  max-width: 560px;
}

.reveal-form {
  display: grid;
  gap: 14px;
}

.reveal-form label {
  display: grid;
  gap: 6px;
  color: var(--ink);
  font-weight: 600;
}

.reveal-form input {
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  letter-spacing: 0.22em;
}

.reveal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.reveal-fields {
  display: grid;
  gap: 10px;
}

.reveal-field {
  display: grid;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-soft);
}

.reveal-field label {
  color: var(--muted);
  font-size: calc(12px * var(--font-scale, 1));
}

.reveal-value {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.reveal-value .mono {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--ink);
  font-size: calc(13px * var(--font-scale, 1));
}

.reveal-countdown {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: calc(12px * var(--font-scale, 1));
}
</style>
