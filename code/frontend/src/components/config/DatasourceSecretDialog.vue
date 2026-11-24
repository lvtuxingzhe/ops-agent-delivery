<script setup lang="ts">
/** 设置数据源密码：管理员输入新凭据和 6 位动态口令，直接写入 OpenBao。 */
import { reactive, computed, watch } from 'vue'

const props = defineProps<{
  open: boolean
  target: string
  credentialRef: string
  busy?: boolean
  error?: string
}>()

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'confirm', payload: {
    auth_type: 'basic' | 'bearer'
    username: string
    password: string
    code: string
  }): void
}>()

const form = reactive({
  auth_type: 'basic' as 'basic' | 'bearer',
  username: '',
  password: '',
  code: '',
})

const canSubmit = computed(() => {
  if (props.busy) return false
  if (form.code.length !== 6) return false
  if (!form.password) return false
  if (form.auth_type === 'basic' && !form.username) return false
  return true
})

watch(() => props.open, () => {
  // 打开和关闭都重置：关闭时立即清掉密码和动态口令，不留在组件内存里。
  form.auth_type = 'basic'
  form.username = ''
  form.password = ''
  form.code = ''
})

function digits(event: Event) {
  const input = event.target as HTMLInputElement
  form.code = input.value.replace(/\D/g, '').slice(0, 6)
}

function close() {
  if (!props.busy) emit('close')
}

function submit() {
  if (!canSubmit.value) return
  emit('confirm', { ...form })
}
</script>

<template>
  <div v-if="open" class="app-modal" @click.self="close">
    <section class="app-modal-card credential-dialog" role="dialog" aria-modal="true">
      <header class="app-modal-head">
        <div>
          <p class="kicker">CREDENTIAL</p>
          <h2>设置数据源密码</h2>
        </div>
        <button type="button" class="plain" :disabled="busy" @click="close">关闭</button>
      </header>

      <p v-if="error" class="error">{{ error }}</p>

      <form class="credential-form" @submit.prevent="submit">
        <label>认证方式
          <select v-model="form.auth_type">
            <option value="basic">Basic（用户名 + 密码）</option>
            <option value="bearer">Bearer（只有令牌 / 密码）</option>
          </select>
        </label>
        <label v-if="form.auth_type === 'basic'">用户名
          <input v-model="form.username" autocomplete="off" placeholder="如 reader" />
        </label>
        <label>{{ form.auth_type === 'basic' ? '密码' : '令牌 / 密码' }}
          <input v-model="form.password" type="password" autocomplete="new-password" placeholder="保存后不再回显" />
        </label>
        <label>动态口令
          <input
            :value="form.code"
            inputmode="numeric"
            autocomplete="one-time-code"
            maxlength="6"
            placeholder="6 位数字"
            @input="digits"
          />
        </label>
        <div class="credential-actions">
          <button type="button" class="plain" :disabled="busy" @click="close">取消</button>
          <button type="submit" class="primary" :disabled="!canSubmit">
            {{ busy ? '写入中…' : '保存凭据' }}
          </button>
        </div>
      </form>
    </section>
  </div>
</template>

<style scoped>
.credential-dialog {
  max-width: 520px;
}

.credential-form {
  display: grid;
  gap: 12px;
}

.credential-form label {
  display: grid;
  gap: 6px;
  color: var(--ink);
  font-weight: 600;
}

.credential-form input,
.credential-form select {
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink);
  font: inherit;
}

.credential-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
