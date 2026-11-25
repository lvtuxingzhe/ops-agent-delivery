<script setup lang="ts">
/**
 * 平台开关面板 —— 从 `Config.vue` 拆出来的第三块。
 *
 * 布尔开关与网段输入分开保存：开关点一下立即生效，网段是自由文本，
 * 两者混在一个「保存」里会互相覆盖（原页面的注释也强调过这一点）。
 */

import { onMounted, ref } from 'vue'

import {
  fetchSettings,
  setSetting,
  type PlatformSettings,
} from '../../api/config'
import { useAsyncAction } from '../../composables/useAsyncAction'
import ResizableTable from '../ResizableTable.vue'
import FoldToggle from './FoldToggle.vue'

const columns = [
  { key: 'setting', label: '配置项', minWidth: 160 },
  { key: 'value', label: '当前值', minWidth: 220 },
  { key: 'status', label: '状态', minWidth: 80, resizable: false },
  { key: 'actions', label: '操作', minWidth: 120, headClass: 'config-sticky-actions' },
]

const settings = ref<PlatformSettings>({})
const table = ref<{ reset: () => void } | null>(null)
const customWidths = ref(false)
const platformCollapsed = ref(false)
const hostKeyCollapsed = ref(true)
const accessCollapsed = ref(true)
const cidrsInput = ref('')
const editing = ref<'cidrs' | 'policy' | 'readonly' | null>(null)
const hostKeyPolicy = ref<'strict' | 'prompt' | 'off'>('prompt')

const hostKeyPolicyLabels: Record<'strict' | 'prompt' | 'off', string> = {
  strict: '严格：只认已登记，未登记直接失败',
  prompt: '确认：首次未登记弹窗确认，已登记后密钥变更仍硬失败',
  off: '关闭：不校验主机密钥，仅测试环境',
}
const policyKey = ref<
  'deployment_command_allowlist'
  | 'deployment_command_denylist'
  | 'investigation_readonly_command_allowlist'
>('deployment_command_allowlist')
const policyInput = ref('')

/** 开关的中文名，页面按这个顺序渲染。 */
const switchLabels: Record<string, string> = {
  auth_password_enabled: '登录方式：账号密码',
  auth_totp_enabled: '登录方式：账号密码 + 动态口令',
  auth_sms_enabled: '登录方式：手机号 + 短信',
  self_approval_enabled: '管理员自审批豁免（默认关闭；开启后管理员可自批，且写入审计）',
  investigation_real_enabled: '真实只读排查（默认关闭；只放开排查取证，不放开部署执行）',
  llm_enabled_by_task: '大模型「按任务开」总开关（默认关；放行还需单模型启用 + 网关白名单/预算）',
  deployment_ai_freeform_enabled: 'AI 自主部署方案（开启后模型生成步骤；命令仍过策略和人工审批）',
  knowledge_ai_summary_enabled: '上传文档 AI 摘要（关闭后改用确定性摘要，不影响上传）',
  knowledge_ai_curation_enabled: '知识库 AI 准入（关闭后硬规则仍拦截，其余转人工复核）',
  knowledge_curation_default_visible: '未判定的旧知识默认可见（回填完成后可关闭）',
}

const { busy, error, message, run } = useAsyncAction(reload)

async function reload() {
  try {
    const st = await fetchSettings()
    settings.value = st.settings
    cidrsInput.value = st.settings.allowed_cidrs ?? ''
    const policy = st.settings.host_key_policy
    hostKeyPolicy.value = policy === 'strict' || policy === 'off' ? policy : 'prompt'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '平台开关加载失败'
  }
}

onMounted(reload)

function toggleSwitch(key: string, current: string) {
  const next = current === 'true' ? 'false' : 'true'
  void run(async () => {
    await setSetting(key, next)
  }, `开关已${next === 'true' ? '开启' : '关闭'}，立即生效（无需改代码）`)
}

async function saveCidrs() {
  const ok = await run(async () => {
    await setSetting('allowed_cidrs', cidrsInput.value.trim())
  }, '网段已保存（留空 = 未配置）')
  if (ok) editing.value = null
}

async function saveHostKeyPolicy(policy: 'strict' | 'prompt' | 'off') {
  if (policy === 'off' && !window.confirm(
    '关闭主机公钥校验后，worker 会接受任何主机公钥，可能被中间人冒充。只在测试环境使用，确定切换？',
  )) return
  await run(async () => {
    await setSetting('host_key_policy', policy)
  }, `主机公钥策略已切换为「${hostKeyPolicyLabels[policy]}」，下一次连接生效`)
}

function openPolicy(key: 'deployment_command_allowlist' | 'deployment_command_denylist') {
  policyKey.value = key
  policyInput.value = settings.value[key] ?? ''
  error.value = ''
  editing.value = 'policy'
}

function openReadonlyPolicy() {
  policyKey.value = 'investigation_readonly_command_allowlist'
  policyInput.value = settings.value.investigation_readonly_command_allowlist ?? ''
  error.value = ''
  editing.value = 'readonly'
}

async function savePolicy() {
  const ok = await run(async () => {
    await setSetting(policyKey.value, policyInput.value.trim())
  }, editing.value === 'readonly'
    ? '只读命令清单已保存；下一次新建排查任务生效'
    : '命令策略已保存；下一次生成方案和审批立即生效')
  if (ok) editing.value = null
}

function policyTitle(): string {
  if (policyKey.value === 'deployment_command_allowlist') return '部署命令允许清单'
  if (policyKey.value === 'deployment_command_denylist') return '部署命令禁止清单'
  return '排查只读命令允许清单'
}

function closeEditor() {
  if (!busy.value) editing.value = null
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="message" class="message">{{ message }}</p>

  <section class="card">
    <div class="card-head">
      <h2>平台开关</h2>
      <div class="card-actions">
        <FoldToggle
          :expanded="!platformCollapsed"
          controls="platform-switches-body"
          @toggle="platformCollapsed = !platformCollapsed"
        />
      </div>
    </div>
    <div v-show="!platformCollapsed" id="platform-switches-body">
    <ul class="switches">
      <li v-for="(label, key) in switchLabels" :key="key">
        <span>{{ label }}</span>
        <button
          type="button"
          class="switch"
          :class="{ on: settings[key] === 'true' }"
          :disabled="busy"
          @click="toggleSwitch(key, settings[key])"
        >
          {{ settings[key] === 'true' ? '已开启（点击关闭）' : '已关闭（点击开启）' }}
        </button>
      </li>
    </ul>
    </div>
  </section>

  <section class="card">
    <div class="card-head">
      <h2>主机公钥校验</h2>
      <div class="card-actions">
        <FoldToggle
          :expanded="!hostKeyCollapsed"
          controls="host-key-body"
          @toggle="hostKeyCollapsed = !hostKeyCollapsed"
        />
      </div>
    </div>
    <div v-show="!hostKeyCollapsed" id="host-key-body">
    <ul class="switches">
      <li v-for="policy in (['prompt', 'strict', 'off'] as const)" :key="policy">
        <span>{{ hostKeyPolicyLabels[policy] }}</span>
        <button
          type="button"
          class="switch"
          :class="{ on: hostKeyPolicy === policy, danger: policy === 'off' && hostKeyPolicy === policy }"
          :disabled="busy"
          @click="saveHostKeyPolicy(policy)"
        >
          {{ hostKeyPolicy === policy ? '当前策略' : '切换' }}
        </button>
      </li>
    </ul>
    <p v-if="hostKeyPolicy === 'off'" class="error">
      主机公钥校验已关闭：worker 会接受任何主机公钥，只在测试环境使用。
    </p>
    </div>
  </section>

  <section class="card">
    <div class="card-head">
      <h2>登录与访问</h2>
      <div class="card-actions">
        <FoldToggle
          :expanded="!accessCollapsed"
          controls="login-access-body"
          @toggle="accessCollapsed = !accessCollapsed"
        />
        <button v-if="customWidths" type="button" class="plain" @click="table?.reset()">重置列宽</button>
      </div>
    </div>
    <div v-show="!accessCollapsed" id="login-access-body">
    <ResizableTable
      ref="table"
      :columns="columns"
      default-grid="minmax(180px, 1.1fr) minmax(260px, 1.8fr) 90px 120px"
      storage-key="ops.config.settings.columnWidths.v4"
      :gap="10"
      :padding-x="4"
      card-class="config-record-table settings-table"
      head-class="config-rt-head"
      @custom-change="customWidths = $event"
    >
      <div class="record-row">
        <div class="record-main">
          <strong>允许排查触达的网段</strong>
        </div>
        <div class="record-endpoint">
          <span class="mono">{{ settings.allowed_cidrs || '未配置（留空）' }}</span>
        </div>
        <div>
          <span :class="['tag', settings.allowed_cidrs ? 'tag-on' : 'tag-off']">
            {{ settings.allowed_cidrs ? '已配置' : '未配置' }}
          </span>
        </div>
        <div class="record-actions config-sticky-actions">
          <button type="button" class="plain" :disabled="busy" @click="editing = 'cidrs'">编辑</button>
        </div>
      </div>
      <div class="record-row">
        <div class="record-main">
          <strong>部署命令允许清单</strong>
        </div>
        <div class="record-endpoint">
          <span class="mono policy-preview">{{ settings.deployment_command_allowlist || '空（不允许任何部署命令）' }}</span>
        </div>
        <div>
          <span :class="['tag', settings.deployment_command_allowlist ? 'tag-on' : 'tag-off']">
            {{ settings.deployment_command_allowlist ? '已配置' : '未配置' }}
          </span>
        </div>
        <div class="record-actions config-sticky-actions">
          <button type="button" class="plain" :disabled="busy" @click="openPolicy('deployment_command_allowlist')">编辑</button>
        </div>
      </div>
      <div class="record-row">
        <div class="record-main">
          <strong>排查只读命令允许清单</strong>
        </div>
        <div class="record-endpoint">
          <span class="mono policy-preview">{{ settings.investigation_readonly_command_allowlist || '空（不允许任何排查命令）' }}</span>
        </div>
        <div>
          <span :class="['tag', settings.investigation_readonly_command_allowlist ? 'tag-on' : 'tag-off']">
            {{ settings.investigation_readonly_command_allowlist ? '已配置' : '未配置' }}
          </span>
        </div>
        <div class="record-actions config-sticky-actions">
          <button type="button" class="plain" :disabled="busy" @click="openReadonlyPolicy">编辑</button>
        </div>
      </div>
      <div class="record-row">
        <div class="record-main">
          <strong>部署命令禁止清单</strong>
        </div>
        <div class="record-endpoint">
          <span class="mono policy-preview">{{ settings.deployment_command_denylist || '未配置' }}</span>
        </div>
        <div>
          <span :class="['tag', settings.deployment_command_denylist ? 'tag-warn' : 'tag-off']">
            {{ settings.deployment_command_denylist ? '生效中' : '未配置' }}
          </span>
        </div>
        <div class="record-actions config-sticky-actions">
          <button type="button" class="plain" :disabled="busy" @click="openPolicy('deployment_command_denylist')">编辑</button>
        </div>
      </div>
    </ResizableTable>
    </div>
  </section>

  <div v-if="editing === 'cidrs'" class="config-modal" @click.self="closeEditor">
    <section class="config-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div><p class="kicker">ALLOWED CIDRS</p><h2>编辑允许触达的网段</h2></div>
        <button type="button" class="plain" :disabled="busy" @click="closeEditor">关闭</button>
      </header>
      <div class="form-grid">
        <label class="wide">允许触达的网段
          <input v-model="cidrsInput" class="cidrs-input" placeholder="198.51.100.0/24, 203.0.113.0/24" />
        </label>
      </div>
      <footer class="config-editor-foot">
        <button type="button" class="plain" :disabled="busy" @click="closeEditor">取消</button>
        <button type="button" class="primary" :disabled="busy" @click="saveCidrs">保存网段</button>
      </footer>
    </section>
  </div>

  <div v-if="editing === 'policy' || editing === 'readonly'" class="config-modal" @click.self="closeEditor">
    <section class="config-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div><p class="kicker">COMMAND POLICY</p><h2>{{ policyTitle() }}</h2></div>
        <button type="button" class="plain" :disabled="busy" @click="closeEditor">关闭</button>
      </header>
      <div class="form-grid">
        <label class="wide">
          <span>命令名</span>
          <textarea
            v-model="policyInput"
            rows="6"
            class="policy-input"
            :placeholder="editing === 'readonly' ? 'ps, df, grep' : 'apt-get, systemctl, sed'"
          />
        </label>
      </div>
      <footer class="config-editor-foot">
        <button type="button" class="plain" :disabled="busy" @click="closeEditor">取消</button>
        <button type="button" class="primary" :disabled="busy" @click="savePolicy">保存策略</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.policy-preview {
  display: inline-block;
  max-width: 420px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.policy-input {
  width: 100%;
  min-height: 132px;
  resize: vertical;
  font-family: var(--mono);
  font-size: calc(12px * var(--font-scale, 1));
  line-height: 1.7;
}

.muted {
  color: var(--muted);
}
</style>
