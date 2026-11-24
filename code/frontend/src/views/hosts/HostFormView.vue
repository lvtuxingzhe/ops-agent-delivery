<script setup lang="ts">
/**
 * 主机表单页：/hosts/new 与 /hosts/:id/edit。
 *
 * 密码在这里录入，但只以密文形式写入数据库：提交时后端用 Fernet 加密，
 * 编辑时页面只显示「已配置/未配置」，不回显任何密码内容。SSH 主机公钥
 * 不在本表单录入，保存后到主机列表点「确认密钥」由管理员核对登记。
 */

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { type MeInfo } from '../../api/auth'
import {
  createHost,
  fetchHostFields,
  listHosts,
  updateHost,
  type ExecHostInput,
  type HostField,
} from '../../api/hosts'
import { ApiError, getOrLogin } from '../../api/http'

const route = useRoute()
const router = useRouter()
const me = ref<MeInfo | null>(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const hostIdInput = ref('')
const password = ref('')
const clearPassword = ref(false)
const passwordConfigured = ref(false)
const customDefs = ref<HostField[]>([])
const customFields = reactive<Record<string, string>>({})

const mode = computed(() => (route.name === 'host-edit' ? 'edit' : 'create'))
const editingId = computed(() => (mode.value === 'edit' ? String(route.params.id || '') : ''))

const form = reactive<ExecHostInput>({
  platform: 'linux',
  os_family: 'unknown',
  distro: '',
  os_version: '',
  package_manager: 'unknown',
  service_manager: '',
  address: '',
  port: 22,
  user: '',
  enabled: false,
  custom_fields: {},
})

const canWrite = computed(() => (me.value?.roles ?? []).includes('admin'))
const osFamilyOptions = computed(() => (
  form.platform === 'windows' ? ['windows'] : ['unknown', 'debian', 'rhel']
))
const packageManagerOptions = computed(() => (
  form.platform === 'windows'
    ? ['none', 'unknown']
    : ['unknown', 'apt', 'dnf', 'yum', 'none']
))

watch(() => form.platform, (platform) => {
  if (platform === 'windows') {
    form.os_family = 'windows'
    form.package_manager = 'none'
    return
  }
  if (form.os_family === 'windows') form.os_family = 'unknown'
  if (form.package_manager === 'none') form.package_manager = 'unknown'
})

function resetForm() {
  hostIdInput.value = ''
  password.value = ''
  clearPassword.value = false
  passwordConfigured.value = false
  for (const key of Object.keys(customFields)) delete customFields[key]
  for (const item of customDefs.value) customFields[item.key] = ''
  Object.assign(form, {
    platform: 'linux',
    os_family: 'unknown',
    distro: '',
    os_version: '',
    package_manager: 'unknown',
    service_manager: '',
    address: '',
    port: 22,
    user: '',
      enabled: false,
  })
}

function fillForm(host: ExecHostInput & { password_set?: boolean }) {
  password.value = ''
  clearPassword.value = false
  passwordConfigured.value = Boolean(host.password_set)
  Object.assign(form, {
    platform: host.platform,
    os_family: host.os_family || (host.platform === 'windows' ? 'windows' : 'unknown'),
    distro: host.distro || '',
    os_version: host.os_version || '',
    package_manager: host.package_manager || (host.platform === 'windows' ? 'none' : 'unknown'),
    service_manager: host.service_manager || '',
    address: host.address,
    port: host.port,
    user: host.user,
    enabled: host.enabled,
  })
  for (const item of customDefs.value) {
    customFields[item.key] = host.custom_fields?.[item.key] ?? ''
  }
}

async function loadExisting() {
  if (!editingId.value) {
    resetForm()
    loading.value = false
    return
  }
  try {
    const hosts = await listHosts()
    const host = hosts.find((item) => item.id === editingId.value)
    if (!host) {
      error.value = `主机 ${editingId.value} 不存在`
      return
    }
    fillForm(host)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '主机详情加载失败'
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!canWrite.value) {
    error.value = '当前角色没有主机写权限（需要 admin）。'
    return
  }
  const id = editingId.value || hostIdInput.value.trim().toLowerCase()
  if (!id) {
    error.value = '新建主机时必须填主机 ID'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const body: ExecHostInput = {
      ...form,
      port: Number(form.port),
      password: password.value,
      clear_password: clearPassword.value,
      custom_fields: { ...customFields },
    }
    if (editingId.value) {
      await updateHost(id, body)
    } else {
      await createHost(id, body)
    }
    await router.push({
      name: 'hosts',
      query: { notice: editingId.value ? `已保存 ${id}` : `已新建 ${id}；启用前请确认已配置登录密码` },
    })
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '主机保存失败'
  } finally {
    busy.value = false
  }
}

function cancel() {
  void router.push({ name: 'hosts' })
}

onMounted(async () => {
  me.value = await getOrLogin<MeInfo>('/auth/me')
  try {
    customDefs.value = (await fetchHostFields()).filter(
      (item) => item.key.startsWith('custom_') && item.visible !== false,
    )
  } catch {
    customDefs.value = []
  }
  await loadExisting()
})
</script>

<template>
  <div class="page-stack">
    <p v-if="error" class="error">{{ error }}</p>

    <section class="card">
      <p v-if="!canWrite" class="notice">当前账号为只读，主机字段不可修改。</p>

      <div v-if="loading" class="desc">加载中…</div>
      <template v-else>
        <div class="form-grid">
          <div class="form-section">主机与操作系统</div>
          <label v-if="!editingId" class="field">
            <span>主机 ID</span>
            <input v-model="hostIdInput" type="text" placeholder="例如 vm-150" :disabled="!canWrite" />
          </label>
          <label class="field">
            <span>平台</span>
            <select v-model="form.platform" :disabled="!canWrite">
              <option value="linux">linux</option>
              <option value="windows">windows</option>
            </select>
          </label>
          <label class="field">
            <span>操作系统（决定能跑 apt 还是 dnf/yum）</span>
            <select v-model="form.os_family" :disabled="!canWrite">
              <option v-for="item in osFamilyOptions" :key="item" :value="item">{{ item }}</option>
            </select>
          </label>
          <label class="field">
            <span>系统名称</span>
            <input v-model="form.distro" type="text" placeholder="centos / ubuntu / rocky" :disabled="!canWrite" />
          </label>
          <label class="field">
            <span>系统版本</span>
            <input v-model="form.os_version" type="text" placeholder="7.9 / 22.04" :disabled="!canWrite" />
          </label>
          <label class="field">
            <span>包管理器</span>
            <select v-model="form.package_manager" :disabled="!canWrite">
              <option v-for="item in packageManagerOptions" :key="item" :value="item">{{ item }}</option>
            </select>
          </label>
          <label class="field">
            <span>服务管理器</span>
            <input v-model="form.service_manager" type="text" placeholder="systemd / 空" :disabled="!canWrite" />
          </label>
          <div class="form-section">SSH 连接</div>
          <label class="field">
            <span>地址</span>
            <input v-model="form.address" type="text" placeholder="10.0.0.150" :disabled="!canWrite" />
          </label>
          <label class="field">
            <span>端口</span>
            <input v-model.number="form.port" type="number" min="1" max="65535" :disabled="!canWrite" />
          </label>
          <label class="field">
            <span>账号</span>
            <input v-model="form.user" type="text" placeholder="root" :disabled="!canWrite" />
          </label>
          <p class="desc inline-note fingerprint-note">
            保存后在主机列表点“确认密钥”。平台只做 SSH 握手取公钥，管理员核对
            <code>ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</code> 的指纹后才写入登记文件。
          </p>
          <label class="field">
            <span>登录密码</span>
            <input
              v-model="password"
              type="password"
              autocomplete="new-password"
              :placeholder="editingId ? '留空则保留当前密码' : '输入 SSH 登录密码'"
              :disabled="!canWrite || clearPassword"
            />
          </label>
          <div class="field password-state">
            <span>密码状态</span>
            <div class="password-state-row">
              <span
                class="tag"
                :class="(passwordConfigured && !clearPassword) || password ? 'tag-on' : 'tag-off'"
              >
                {{ clearPassword ? '保存后清除' : password ? '待保存新密码' : passwordConfigured ? '已配置（加密保存）' : '未配置' }}
              </span>
              <button
                v-if="editingId && passwordConfigured && !clearPassword"
                class="ghost danger-text"
                type="button"
                :disabled="!canWrite || busy"
                @click="clearPassword = true; password = ''"
              >
                清除密码
              </button>
              <button
                v-if="clearPassword"
                class="ghost"
                type="button"
                :disabled="busy"
                @click="clearPassword = false"
              >
                撤销
              </button>
            </div>
          </div>
          <template v-if="customDefs.length">
            <div class="form-section">扩展字段（CMDB）</div>
            <label v-for="item in customDefs" :key="item.key" class="field">
              <span>{{ item.label }}<b v-if="item.required" class="required-mark">*</b></span>
              <select
                v-if="item.kind === 'select' || item.kind === 'bool'"
                v-model="customFields[item.key]"
                :disabled="!canWrite"
              >
                <option value="">（空）</option>
                <option v-for="option in item.options" :key="option" :value="option">{{ option }}</option>
              </select>
              <input
                v-else
                v-model="customFields[item.key]"
                type="text"
                :placeholder="item.hint || '自定义字段'"
                :disabled="!canWrite"
              />
            </label>
          </template>
        </div>

        <label class="check-row">
          <input v-model="form.enabled" type="checkbox" :disabled="!canWrite" />
          <span>启用这台主机（启用前必须已配置登录密码）</span>
        </label>

        <div class="form-foot">
          <button class="primary" type="button" :disabled="busy || !canWrite" @click="save">
            {{ busy ? '保存中…' : editingId ? '保存修改' : '创建主机' }}
          </button>
          <button class="ghost" type="button" :disabled="busy" @click="cancel">返回列表</button>
          <span v-if="editingId" class="desc inline-note">正在编辑已有主机；ID 不可改。</span>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
}

.form-section {
  grid-column: 1 / -1;
  padding-top: 6px;
  color: var(--ink-3);
  font-size: calc(11px * var(--font-scale, 1));
  font-weight: 650;
  letter-spacing: 0.12em;
}

.form-section:not(:first-child) {
  margin-top: 4px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.field.span-2 {
  grid-column: 1 / -1;
}

.fingerprint-note {
  grid-column: 1 / -1;
  margin: -2px 0 0;
  line-height: 1.55;
}

.password-state-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 34px;
  flex-wrap: wrap;
}

.danger-text {
  color: var(--danger, #b42318);
}

.required-mark {
  margin-left: 3px;
  color: var(--danger, #b42318);
  font-weight: 600;
}

.check-row {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  margin-top: 16px;
  padding: 9px 12px;
  color: var(--ink-2);
  background: var(--surface-soft);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  font-size: calc(12px * var(--font-scale, 1));
  cursor: pointer;
}

.form-foot {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 16px;
}

.inline-note {
  margin: 0;
}

@media (max-width: 640px) {
  .form-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

