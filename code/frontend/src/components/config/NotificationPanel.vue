<script setup lang="ts">
/**
 * 通知渠道面板：飞书群机器人 webhook + SMTP 邮件。
 *
 * 秘密字段只写不读：保存后后台只返回「已配置」，页面不再回显 webhook URL
 * 或 SMTP 密码。输入框留空 = 保留原值，方便只改启停或只测连通。
 */

import { onMounted, reactive, ref } from 'vue'

import {
  fetchNotificationChannels,
  saveEmailChannel,
  saveFeishuChannel,
  testNotificationChannel,
} from '../../api/notifications'
import { useAsyncAction } from '../../composables/useAsyncAction'
import ResizableTable from '../ResizableTable.vue'

const columns = [
  { key: 'channel', label: '渠道', minWidth: 150 },
  { key: 'enabled', label: '启用', minWidth: 64 },
  { key: 'target', label: '目标 / 参数', minWidth: 240 },
  { key: 'status', label: '配置状态', minWidth: 100, resizable: false },
  { key: 'actions', label: '操作', minWidth: 210, headClass: 'config-sticky-actions' },
]

const feishu = reactive({
  enabled: false,
  configured: false,
  webhook_url: '',
})
const email = reactive({
  enabled: false,
  configured: false,
  smtp_host: '',
  smtp_port: 587,
  smtp_user: '',
  password: '',
  password_set: false,
  from_addr: '',
  to_addrs: '',
  use_tls: true,
})
const loaded = ref(false)
const table = ref<{ reset: () => void } | null>(null)
const customWidths = ref(false)
const editingChannel = ref<'feishu' | 'email' | null>(null)

const { busy, error, message, run } = useAsyncAction(reload)

async function reload() {
  try {
    const { channels } = await fetchNotificationChannels()
    feishu.enabled = channels.feishu.enabled
    feishu.configured = channels.feishu.configured
    feishu.webhook_url = ''
    email.enabled = channels.email.enabled
    email.configured = channels.email.configured
    email.smtp_host = channels.email.smtp_host
    email.smtp_port = channels.email.smtp_port
    email.smtp_user = channels.email.smtp_user
    email.password = ''
    email.password_set = channels.email.password_set
    email.from_addr = channels.email.from_addr
    email.to_addrs = channels.email.to_addrs.join(', ')
    email.use_tls = channels.email.use_tls
    loaded.value = true
  } catch (err) {
    error.value = err instanceof Error ? err.message : '通知渠道加载失败'
  }
}

onMounted(reload)

async function saveFeishu() {
  const ok = await run(async () => {
    await saveFeishuChannel({
      enabled: feishu.enabled,
      webhook_url: feishu.webhook_url.trim(),
    })
    await reload()
  }, '飞书渠道已保存')
  if (ok) editingChannel.value = null
}

async function saveEmail() {
  const ok = await run(async () => {
    await saveEmailChannel({
      enabled: email.enabled,
      smtp_host: email.smtp_host.trim(),
      smtp_port: Number(email.smtp_port) || 587,
      smtp_user: email.smtp_user.trim(),
      password: email.password,
      from_addr: email.from_addr.trim(),
      to_addrs: email.to_addrs.trim(),
      use_tls: email.use_tls,
    })
    await reload()
  }, '邮件渠道已保存')
  if (ok) editingChannel.value = null
}

function closeEditor() {
  if (!busy.value) editingChannel.value = null
}

function testFeishu() {
  void run(async () => {
    await saveFeishuChannel({
      enabled: feishu.enabled,
      webhook_url: feishu.webhook_url.trim(),
    })
    await reload()
    const result = await testNotificationChannel('feishu')
    if (!result.ok) throw new Error(`飞书测试失败：${result.detail}`)
  }, '飞书测试消息已发送')
}

function testEmail() {
  void run(async () => {
    await saveEmailChannel({
      enabled: email.enabled,
      smtp_host: email.smtp_host.trim(),
      smtp_port: Number(email.smtp_port) || 587,
      smtp_user: email.smtp_user.trim(),
      password: email.password,
      from_addr: email.from_addr.trim(),
      to_addrs: email.to_addrs.trim(),
      use_tls: email.use_tls,
    })
    await reload()
    const result = await testNotificationChannel('email')
    if (!result.ok) throw new Error(`邮件测试失败：${result.detail}`)
  }, '邮件测试消息已发送')
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="message" class="message">{{ message }}</p>

  <section v-if="loaded" class="card">
    <div class="card-head">
      <div>
        <h2>通知渠道</h2>
      </div>
      <div class="card-actions">
        <button v-if="customWidths" type="button" class="plain" @click="table?.reset()">重置列宽</button>
      </div>
    </div>
    <ResizableTable
      ref="table"
      :columns="columns"
      default-grid="minmax(160px, 1.1fr) 70px minmax(260px, 1.8fr) 110px 210px"
      storage-key="ops.config.notification.columnWidths.v4"
      :gap="10"
      :padding-x="4"
      card-class="config-record-table notification-table"
      head-class="config-rt-head"
      @custom-change="customWidths = $event"
    >
      <div class="record-row notification-row">
        <div class="record-main">
          <strong>飞书通知</strong>
        </div>
        <div><span :class="['tag', feishu.enabled ? 'tag-on' : 'tag-off']">{{ feishu.enabled ? '启用' : '停用' }}</span></div>
        <div class="record-endpoint">
          <span class="mono">open.feishu.cn / bot webhook</span>
        </div>
        <div><span :class="['tag', feishu.configured ? 'tag-on' : 'tag-off']">{{ feishu.configured ? '可发送' : '未配置' }}</span></div>
        <div class="record-actions config-sticky-actions">
          <button type="button" class="plain" :disabled="busy" @click="editingChannel = 'feishu'">编辑</button>
          <button type="button" class="plain" :disabled="busy || !feishu.configured" @click="testFeishu">测试</button>
        </div>
      </div>
      <div class="record-row notification-row">
        <div class="record-main">
          <strong>邮件通知</strong>
        </div>
        <div><span :class="['tag', email.enabled ? 'tag-on' : 'tag-off']">{{ email.enabled ? '启用' : '停用' }}</span></div>
        <div class="record-endpoint">
          <span class="mono">{{ email.smtp_host || '未配置 SMTP 主机' }}{{ email.smtp_port ? `:${email.smtp_port}` : '' }}</span>
        </div>
        <div><span :class="['tag', email.configured ? 'tag-on' : 'tag-off']">{{ email.configured ? '参数已配置' : '未配置' }}</span></div>
        <div class="record-actions config-sticky-actions">
          <button type="button" class="plain" :disabled="busy" @click="editingChannel = 'email'">编辑</button>
          <button type="button" class="plain" :disabled="busy || !email.configured" @click="testEmail">测试</button>
        </div>
      </div>
    </ResizableTable>
  </section>

  <div v-if="editingChannel === 'feishu'" class="config-modal" @click.self="closeEditor">
    <section class="config-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div><p class="kicker">FEISHU</p><h2>编辑飞书通知</h2></div>
        <button type="button" class="plain" :disabled="busy" @click="closeEditor">关闭</button>
      </header>
      <div class="form-grid">
        <label class="check">
          <input v-model="feishu.enabled" type="checkbox" />
          启用飞书通知
        </label>
        <label class="wide">
          webhook 地址
          <input
            v-model="feishu.webhook_url"
            type="password"
            autocomplete="off"
            :placeholder="feishu.configured ? '已配置，留空表示不修改' : 'https://open.feishu.cn/open-apis/bot/v2/hook/...'"
          />
        </label>
      </div>
      <p class="desc">
        当前状态：{{ feishu.configured ? 'webhook 已配置' : 'webhook 未配置' }}；
        {{ feishu.enabled ? '通知已启用' : '通知已停用' }}。
      </p>
      <footer class="config-editor-foot">
        <button type="button" class="plain" :disabled="busy" @click="closeEditor">取消</button>
        <button type="button" class="primary" :disabled="busy" @click="saveFeishu">保存</button>
      </footer>
    </section>
  </div>

  <div v-if="editingChannel === 'email'" class="config-modal" @click.self="closeEditor">
    <section class="config-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div><p class="kicker">SMTP</p><h2>编辑邮件通知</h2></div>
        <button type="button" class="plain" :disabled="busy" @click="closeEditor">关闭</button>
      </header>
      <div class="form-grid">
        <label class="check">
          <input v-model="email.enabled" type="checkbox" />
          启用邮件通知
        </label>
        <label>
          SMTP 主机
          <input v-model="email.smtp_host" placeholder="smtp.example.com" />
        </label>
        <label>
          端口
          <input v-model.number="email.smtp_port" type="number" min="1" max="65535" />
        </label>
        <label>
          登录账号（可空）
          <input v-model="email.smtp_user" autocomplete="off" placeholder="ops@example.com" />
        </label>
        <label>
          SMTP 密码
          <input
            v-model="email.password"
            type="password"
            autocomplete="off"
            :placeholder="email.password_set ? '已配置，留空表示不修改' : '没有密码可留空（内网中继）'"
          />
        </label>
        <label>
          发件人
          <input v-model="email.from_addr" placeholder="ops-agent@example.com" />
        </label>
        <label class="wide">
          收件人（逗号分隔）
          <input v-model="email.to_addrs" placeholder="oncall@example.com, sre@example.com" />
        </label>
        <label class="check">
          <input v-model="email.use_tls" type="checkbox" />
          使用 STARTTLS（465 端口自动走 SSL，不需要勾）
        </label>
      </div>
      <p class="desc">
        当前状态：{{ email.configured ? '邮件参数已配置' : '邮件参数未配置' }}；
        {{ email.enabled ? '通知已启用' : '通知已停用' }}；
        密码{{ email.password_set ? '已配置' : '未配置' }}。
      </p>
      <footer class="config-editor-foot">
        <button type="button" class="plain" :disabled="busy" @click="closeEditor">取消</button>
        <button type="button" class="primary" :disabled="busy" @click="saveEmail">保存</button>
      </footer>
    </section>
  </div>
</template>
