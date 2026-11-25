<script setup lang="ts">
/**
 * 飞书应用机器人配置：App 凭据、目标群、测试卡片和飞书身份绑定。
 *
 * App Secret 等秘密只写不读；保存后页面只显示“已配置”。
 * 飞书身份用于卡片审批和值班 @；审批按钮仍只认管理员。
 */

import { onMounted, reactive, ref } from 'vue'

import { listUsers, type AdminUser } from '../../api/admin'
import {
  bindFeishuIdentity,
  fetchFeishuApp,
  fetchFeishuChats,
  fetchFeishuIdentities,
  fetchFeishuMembers,
  saveFeishuApp,
  testFeishuApp,
  unbindFeishuIdentity,
  type FeishuAppConfig,
  type FeishuChat,
  type FeishuIdentity,
  type FeishuMember,
} from '../../api/notifications'
import { useAsyncAction } from '../../composables/useAsyncAction'
import FoldToggle from './FoldToggle.vue'

const form = reactive({
  enabled: false,
  app_id: '',
  app_secret: '',
  mode: 'long_connection' as 'long_connection' | 'webhook',
  chat_ids: [] as string[],
  verification_token: '',
  encrypt_key: '',
})
const status = ref<FeishuAppConfig | null>(null)
const chats = ref<FeishuChat[]>([])
const chatIdsText = ref('')
const members = ref<FeishuMember[]>([])
const identities = ref<FeishuIdentity[]>([])
const users = ref<AdminUser[]>([])
const selectedUser = reactive<Record<string, number>>({})
const loaded = ref(false)
const collapsed = ref(true)
const { busy, error, message, run } = useAsyncAction(reload)

function apply(item: FeishuAppConfig) {
  status.value = item
  form.enabled = item.enabled
  form.app_id = item.app_id
  form.mode = item.mode
  form.chat_ids = [...(item.chat_ids || (item.chat_id ? [item.chat_id] : []))]
  chatIdsText.value = form.chat_ids.join(', ')
  form.app_secret = ''
  form.verification_token = ''
  form.encrypt_key = ''
}

function parsedChatIds(): string[] {
  const parts = chatIdsText.value.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean)
  return [...new Set(parts)]
}

function isSelected(chatId: string): boolean {
  return parsedChatIds().includes(chatId)
}

function toggleChat(chatId: string) {
  const current = parsedChatIds()
  const next = current.includes(chatId)
    ? current.filter((item) => item !== chatId)
    : [...current, chatId]
  chatIdsText.value = next.join(', ')
}

async function reload() {
  try {
    const { item } = await fetchFeishuApp()
    apply(item)
    loaded.value = true
  } catch (err) {
    error.value = err instanceof Error ? err.message : '飞书应用配置加载失败'
  }
}

async function loadBindings() {
  const [{ items: bound }, allUsers] = await Promise.all([
    fetchFeishuIdentities(),
    listUsers({ active: true, size: 50 }),
  ])
  identities.value = bound
  users.value = allUsers.items
}

onMounted(() => {
  void reload()
  void loadBindings().catch(() => undefined)
})

async function save() {
  await run(async () => {
    const { item } = await saveFeishuApp({
      enabled: form.enabled,
      app_id: form.app_id.trim(),
      app_secret: form.app_secret.trim(),
      mode: form.mode,
      chat_ids: parsedChatIds(),
      verification_token: form.verification_token.trim(),
      encrypt_key: form.encrypt_key.trim(),
    })
    apply(item)
  }, '飞书应用配置已保存')
}

async function loadChats() {
  await run(async () => {
    const { items } = await fetchFeishuChats()
    chats.value = items
  }, '机器人所在群已读取')
}

async function loadMembers() {
  await run(async () => {
    if (!users.value.length) await loadBindings()
    const { items } = await fetchFeishuMembers()
    members.value = items
    for (const member of items) {
      if (!member.bound && !selectedUser[member.open_id] && users.value[0]) {
        selectedUser[member.open_id] = users.value[0].id
      }
    }
  }, '群成员已读取')
}

async function bindMember(member: FeishuMember) {
  const userId = selectedUser[member.open_id]
  if (!userId) {
    error.value = '先选择一个平台账号'
    return
  }
  await run(async () => {
    await bindFeishuIdentity({
      open_id: member.open_id,
      user_id: userId,
      display_name: member.name,
    })
    await loadBindings()
    await loadMembers()
  }, '飞书身份已绑定')
}

async function unbind(identity: FeishuIdentity) {
  await run(async () => {
    await unbindFeishuIdentity(identity.id)
    await loadBindings()
    if (members.value.length) await loadMembers()
  }, '飞书身份已解绑')
}

async function sendTest() {
  await run(async () => {
    await saveFeishuApp({
      enabled: form.enabled,
      app_id: form.app_id.trim(),
      app_secret: form.app_secret.trim(),
      mode: form.mode,
      chat_ids: parsedChatIds(),
      verification_token: form.verification_token.trim(),
      encrypt_key: form.encrypt_key.trim(),
    })
    const { item } = await fetchFeishuApp()
    apply(item)
    const result = await testFeishuApp()
    if (!result.ok) throw new Error('飞书测试卡片没有发出')
  }, '测试卡片已发到目标群')
}
</script>

<template>
  <section v-if="loaded" class="card">
    <div class="card-head">
      <div>
        <h2>飞书应用（卡片审批）</h2>
        <p class="desc">用应用机器人发审批卡片和事件文本；关闭后不再发送任何飞书通知。</p>
      </div>
      <FoldToggle
        :expanded="!collapsed"
        controls="feishu-app-body"
        @toggle="collapsed = !collapsed"
      />
    </div>
    <div v-show="!collapsed" id="feishu-app-body">
      <div class="form-grid">
        <label class="check">
          <input v-model="form.enabled" type="checkbox" />
          启用飞书应用卡片
        </label>
        <label>
          App ID
          <input v-model="form.app_id" autocomplete="off" placeholder="cli_xxxxxxxxxxxx" />
        </label>
        <label>
          App Secret
          <input
            v-model="form.app_secret"
            type="password"
            autocomplete="off"
            :placeholder="status?.app_secret_set ? '已配置，留空表示不修改' : '应用后台的 App Secret'"
          />
        </label>
        <label>
          连接方式
          <select v-model="form.mode">
            <option value="long_connection">长连接（不需要公网回调）</option>
          </select>
        </label>
        <label class="wide">
          目标群 chat_id（多个用逗号分隔）
          <input
            v-model="chatIdsText"
            autocomplete="off"
            placeholder="oc_xxxxxxxxxxxxxxxx, oc_yyyyyyyyyyyyyyyy"
          />
        </label>
        <div v-if="chats.length" class="wide chat-list">
          <button
            v-for="chat in chats"
            :key="chat.chat_id"
            type="button"
            :class="['plain', isSelected(chat.chat_id) ? 'chat-selected' : '']"
            @click="toggleChat(chat.chat_id)"
          >
            {{ isSelected(chat.chat_id) ? '✓ ' : '' }}{{ chat.name || chat.chat_id }}
          </button>
        </div>
      </div>
      <p class="desc">告警、排查和部署文本会发到上面选中的群，并 @ 当前值班人；未绑定飞书的人只显示名字。</p>
      <p class="desc">
        当前状态：{{ status?.enabled ? '已启用' : '未启用' }}；
        App Secret {{ status?.app_secret_set ? '已配置' : '未配置' }}；
        长连接 {{ status?.worker_running ? '已连接' : '未连接' }}
        <span v-if="status?.worker_error">（{{ status.worker_error }}）</span>；
        最近测试 {{ status?.last_test_at || '未测试' }}
        <span v-if="status?.last_test_detail">（{{ status.last_test_detail }}）</span>。
      </p>
      <div class="page-actions">
        <button type="button" class="plain" :disabled="busy" @click="loadChats">读取机器人所在群</button>
        <button type="button" class="plain" :disabled="busy" @click="save">保存</button>
        <button type="button" class="primary" :disabled="busy" @click="sendTest">发测试卡片</button>
      </div>

      <div class="binding-block">
        <div class="binding-head">
          <div>
            <h3>飞书身份绑定</h3>
            <p class="desc">可绑定任意启用用户，用于值班 @；只有绑定到管理员账号的人，点卡片“批准 / 驳回”才会生效。</p>
          </div>
          <button type="button" class="plain" :disabled="busy" @click="loadMembers">读取群成员</button>
        </div>

        <div v-if="identities.length" class="binding-list">
          <div v-for="identity in identities" :key="identity.id" class="binding-row">
            <div>
              <strong>{{ identity.user_name || identity.username }}</strong>
              <span class="desc">{{ identity.feishu_name || identity.open_id }}</span>
            </div>
            <div class="binding-actions">
              <span class="tag tag-on">已绑定</span>
              <button type="button" class="plain" :disabled="busy" @click="unbind(identity)">解绑</button>
            </div>
          </div>
        </div>
        <p v-else class="desc">还没有绑定飞书身份。先读取群成员，再把飞书成员绑到平台账号。</p>

        <div v-if="members.length" class="binding-list">
          <div v-for="member in members" :key="member.open_id" class="binding-row">
            <div>
              <strong>{{ member.name || '未命名成员' }}</strong>
              <span class="desc mono">{{ member.open_id }}</span>
            </div>
            <div v-if="member.bound" class="binding-actions">
              <span class="tag tag-on">已绑定 {{ member.user_name || member.user_id }}</span>
            </div>
            <div v-else class="binding-actions">
              <select v-model="selectedUser[member.open_id]">
                <option v-for="user in users" :key="user.id" :value="user.id">
                  {{ user.display_name || user.username }}
                </option>
              </select>
              <button type="button" class="plain" :disabled="busy" @click="bindMember(member)">绑定</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.chat-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chat-list .plain {
  font-size: calc(12px * var(--font-scale, 1));
}

.chat-selected {
  border-color: var(--primary, #3370ff);
  color: var(--primary, #3370ff);
  background: var(--primary-soft, #f0f5ff);
}

.binding-block {
  margin-top: 18px;
  border-top: 1px solid var(--border);
  padding-top: 14px;
}

.binding-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.binding-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.binding-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-soft);
}

.binding-row strong {
  display: block;
}

.binding-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.binding-actions select {
  min-width: 140px;
}
</style>
