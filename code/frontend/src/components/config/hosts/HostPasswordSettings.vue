<script setup lang="ts">
/** 配置 → 登录密码：集中改密/清空，不读取旧密码。 */
import { inject } from 'vue'

import { HOST_SETTINGS_KEY } from './useHostSettings'

const settings = inject(HOST_SETTINGS_KEY)!
const {
  hosts, passwordInputs, busy, savePassword, clearPassword, passwordState,
} = settings
</script>

<template>
  <section class="card">
    <header class="panel-head">
      <div>
        <h2>登录密码</h2>
      </div>
    </header>
    <div class="password-list">
      <div v-for="host in hosts" :key="host.id" class="password-row">
        <div class="password-host">
          <strong>{{ host.id }}</strong>
          <small>{{ host.address }}:{{ host.port }} · {{ host.user }} · {{ host.enabled ? '启用' : '停用' }}</small>
        </div>
        <span :class="['tag', host.password_set ? 'tag-on' : 'tag-off']">{{ passwordState(host) }}</span>
        <input
          v-model="passwordInputs[host.id]"
          type="password"
          autocomplete="new-password"
          placeholder="输入新密码"
          :disabled="busy"
        />
        <button type="button" class="plain" :disabled="busy" @click="savePassword(host)">保存</button>
        <button type="button" class="plain" :disabled="busy || !host.password_set" @click="clearPassword(host)">清空</button>
      </div>
      <p v-if="!hosts.length" class="desc">还没有主机；先到「主机」页新增或导入。</p>
    </div>
  </section>
</template>

<style scoped>
.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}
.panel-head h2 {
  margin-bottom: 4px;
}
.password-list {
  display: grid;
  gap: 0;
  border-top: 1px solid var(--line);
}
.password-row {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) 100px 170px 58px 48px;
  align-items: center;
  gap: 8px;
  padding: 9px 2px;
  border-bottom: 1px solid var(--line);
  font-size: calc(12px * var(--font-scale, 1));
}
.password-host {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.password-host small {
  color: var(--muted);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.password-row input {
  min-width: 0;
  width: 100%;
}
@media (max-width: 720px) {
  .password-row {
    grid-template-columns: 1fr auto;
  }
  .password-row input {
    grid-column: 1 / -1;
  }
}
</style>
