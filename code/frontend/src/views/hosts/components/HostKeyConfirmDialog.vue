<script setup lang="ts">
/** 首次主机公钥确认弹窗：只允许“未登记”确认，已登记后变更不提供继续按钮。 */
import { ref, watch } from 'vue'

import type { ExecHost, HostKeyProbe } from '../../../api/hosts'

const props = defineProps<{
  host: ExecHost | null
  probe: HostKeyProbe | null
  busy: boolean
}>()

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'confirm', fingerprint: string): void
}>()

const checked = ref(false)

watch(() => props.probe, () => {
  checked.value = false
})

function close() {
  if (!props.busy) emit('close')
}
</script>

<template>
  <div v-if="host" class="modal-mask" @click.self="close">
    <section class="modal-card host-key-modal">
      <header>
        <h2>确认主机公钥</h2>
        <button class="ghost" type="button" :disabled="busy" @click="close">关闭</button>
      </header>

      <p class="desc">
        这台主机首次接入。平台只做了 SSH 握手，尚未发送登录密码。
        请先到目标机控制台核对指纹，再决定是否登记。
      </p>

      <dl class="host-key-meta">
        <dt>主机</dt><dd class="mono">{{ host.id }}</dd>
        <dt>地址</dt><dd class="mono">{{ host.address }}:{{ host.port }}</dd>
        <dt>密钥类型</dt><dd class="mono">{{ probe?.key_type || '读取中' }}</dd>
        <dt>当前指纹</dt><dd class="mono fingerprint">{{ probe?.fingerprint || '读取中' }}</dd>
      </dl>

      <p class="desc">
        目标机核对命令：<code>ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</code>
      </p>

      <p v-if="probe?.status === 'mismatch'" class="error">
        这台主机已经登记过，但当前公钥和登记值不一致。不能在这里点确认继续；
        请先排查是重装、换机还是被冒充。
      </p>
      <p v-else-if="probe?.status === 'registered'" class="message">
        当前公钥与登记值一致，不需要重复确认。
      </p>

      <label v-if="probe?.status === 'unregistered'" class="confirm-check">
        <input v-model="checked" type="checkbox" :disabled="busy" />
        <span>我已到目标机控制台核对过上面的指纹，确认这是目标机。</span>
      </label>

      <footer>
        <button class="ghost" type="button" :disabled="busy" @click="close">取消</button>
        <button
          class="primary"
          type="button"
          :disabled="busy || !checked || probe?.status !== 'unregistered'"
          @click="probe && emit('confirm', probe.fingerprint)"
        >
          确认并登记
        </button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(15, 23, 42, 0.45);
}

.modal-card {
  width: min(560px, 100%);
  max-height: 90vh;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface);
  padding: 20px 22px;
  box-shadow: 0 24px 60px -24px rgba(15, 23, 42, 0.55);
}

header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

header h2 {
  margin: 0;
  font-size: calc(17px * var(--font-scale, 1));
}

.host-key-meta {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 7px 12px;
  margin: 14px 0;
  font-size: calc(12px * var(--font-scale, 1));
}

.host-key-meta dt {
  color: var(--muted);
}

.host-key-meta dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}

.fingerprint {
  color: var(--ink);
  font-weight: 600;
}

.confirm-check {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 16px 0;
  color: var(--ink);
  font-size: calc(12px * var(--font-scale, 1));
}

.confirm-check input {
  margin-top: 2px;
}

footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}
</style>
