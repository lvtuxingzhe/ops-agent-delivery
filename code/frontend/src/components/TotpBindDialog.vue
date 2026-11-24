<script setup lang="ts">
/**
 * 当前登录账号绑定动态口令。
 *
 * 二维码和密钥只在这一步返回一次；确认成功后由后端把启用状态写入账号。
 * 已绑定的账号不能在这里重复绑定，避免覆盖掉正在使用的口令。
 */
import { ref, watch } from 'vue'

import {
  totpBindConfirm,
  totpBindStart,
  type TotpBindStart,
} from '../api/auth'

const props = defineProps<{
  open: boolean
  enabled: boolean
}>()

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'bound'): void
}>()

const start = ref<TotpBindStart | null>(null)
const code = ref('')
const busy = ref(false)
const error = ref('')

watch(() => props.open, () => {
  // 开关都清空：关闭时立即丢掉二维码里的密钥，避免敏感值留在组件内存里。
  start.value = null
  code.value = ''
  busy.value = false
  error.value = ''
})

async function begin() {
  busy.value = true
  error.value = ''
  try {
    start.value = await totpBindStart()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '动态口令绑定发起失败'
  } finally {
    busy.value = false
  }
}

function digits(event: Event) {
  const input = event.target as HTMLInputElement
  code.value = input.value.replace(/\D/g, '').slice(0, 6)
}

async function confirm() {
  if (code.value.length !== 6 || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await totpBindConfirm(code.value)
    emit('bound')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '动态口令校验失败'
  } finally {
    busy.value = false
  }
}

function close() {
  if (!busy.value) emit('close')
}
</script>

<template>
  <div v-if="open" class="app-modal" @click.self="close">
    <section class="app-modal-card totp-dialog" role="dialog" aria-modal="true">
      <header class="app-modal-head">
        <div>
          <p class="kicker">TOTP</p>
          <h2>{{ enabled ? '动态口令已绑定' : '绑定动态口令' }}</h2>
        </div>
        <button type="button" class="plain" :disabled="busy" @click="close">关闭</button>
      </header>

      <p v-if="error" class="error">{{ error }}</p>

      <template v-if="enabled">
        <p class="desc">
          当前账号已经绑定动态口令，查看密码时会要求输入 6 位数字。
          如需更换设备或重置，请先由管理员解绑后再重新绑定。
        </p>
      </template>

      <template v-else-if="!start">
        <p class="desc">
          绑定后，查看主机密码和数据源密码都需要先输入手机身份验证器里的 6 位动态口令。
          平台和手机 App 共享同一把密钥，不需要联网也能生成。
        </p>
        <div class="totp-actions">
          <button type="button" class="plain" :disabled="busy" @click="close">取消</button>
          <button type="button" class="primary" :disabled="busy" @click="begin">
            {{ busy ? '生成中…' : '开始绑定' }}
          </button>
        </div>
      </template>

      <template v-else>
        <p class="desc">用手机上的身份验证器 App 扫描下面的二维码；扫不到就手动输入密钥。</p>
        <div class="totp-qr" v-html="start.qr_svg"></div>
        <p class="totp-secret">
          手动密钥：<span class="mono">{{ start.secret }}</span>
        </p>
        <p class="desc">添加后会每 30 秒生成一个新口令。输入当前显示的 6 位数字完成绑定。</p>
        <label class="totp-code">动态口令
          <input
            :value="code"
            inputmode="numeric"
            autocomplete="one-time-code"
            maxlength="6"
            placeholder="6 位数字"
            @input="digits"
            @keyup.enter="confirm"
          />
        </label>
        <div class="totp-actions">
          <button type="button" class="plain" :disabled="busy" @click="close">取消</button>
          <button type="button" class="primary" :disabled="busy || code.length !== 6" @click="confirm">
            {{ busy ? '校验中…' : '完成绑定' }}
          </button>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.totp-dialog {
  max-width: 560px;
}

.totp-qr {
  display: flex;
  justify-content: center;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-soft);
}

.totp-qr :deep(svg) {
  width: min(260px, 60vw);
  height: auto;
}

.totp-secret {
  margin: 0;
  color: var(--muted);
  overflow-wrap: anywhere;
}

.totp-code {
  display: grid;
  gap: 6px;
  color: var(--ink);
  font-weight: 600;
}

.totp-code input {
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  letter-spacing: 0.22em;
}

.totp-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
