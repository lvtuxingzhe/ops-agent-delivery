<script setup lang="ts">
/**
 * 执行主机列表页。
 *
 * 列表只展示与启停；新增/编辑跳独立表单页 /hosts/new、/hosts/:id/edit，
 * 避免列表下方再压一整块表单。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { type MeInfo } from '../../api/auth'
import {
  confirmHostKey,
  deleteHost,
  downloadHostTemplate,
  fetchHostFields,
  importHosts,
  listHosts,
  probeHostKey,
  revealHostPassword,
  setHostEnabled,
  type ExecHost,
  type HostField,
  type HostImportError,
  type HostKeyProbe,
} from '../../api/hosts'
import { ApiError, getOrLogin } from '../../api/http'
import { downloadCsv, exportStamp, type CsvColumn } from '../../utils/csv'
import PasswordRevealDialog, { type RevealField } from '../../components/PasswordRevealDialog.vue'
import HostKeyConfirmDialog from './components/HostKeyConfirmDialog.vue'
import HostsFilters from './components/HostsFilters.vue'
import HostsImportErrors from './components/HostsImportErrors.vue'
import HostsTable from './components/HostsTable.vue'
import HostsToolbar from './components/HostsToolbar.vue'

const route = useRoute()
const router = useRouter()
const me = ref<MeInfo | null>(null)
const hosts = ref<ExecHost[]>([])
const hostFields = ref<HostField[]>([])
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const importErrors = ref<HostImportError[]>([])
const exporting = ref(false)
const keyHost = ref<ExecHost | null>(null)
const keyProbe = ref<HostKeyProbe | null>(null)
const keyBusy = ref(false)
const revealHost = ref<ExecHost | null>(null)
const revealFields = ref<RevealField[]>([])
const revealBusy = ref(false)
const revealError = ref('')

const canWrite = computed(() => (me.value?.roles ?? []).includes('admin'))
const enabledCount = computed(() => hosts.value.filter((host) => host.enabled).length)
const hostTable = ref<{ reset: () => void } | null>(null)
const hasCustomHostWidths = ref(false)
const hostQuery = ref('')
const platformFilters = ref<string[]>([])
const statusFilters = ref<string[]>([])
const platformOptions = computed(() => {
  const values = hosts.value.map((host) => host.platform).filter(Boolean)
  return Array.from(new Set(values)).sort((a, b) => a.localeCompare(b))
})
const platformSelectOptions = computed(() =>
  platformOptions.value.map((value) => ({ value, label: value })),
)
const hostFiltersActive = computed(
  () => Boolean(hostQuery.value.trim() || platformFilters.value.length || statusFilters.value.length),
)
const filteredHosts = computed(() => {
  const query = hostQuery.value.trim().toLowerCase()
  return hosts.value.filter((host) => {
    if (query) {
      const haystack = `${host.id} ${host.address} ${host.user}`.toLowerCase()
      if (!haystack.includes(query)) return false
    }
    if (platformFilters.value.length && !platformFilters.value.includes(host.platform)) return false
    if (statusFilters.value.length) {
      const status = host.enabled ? 'enabled' : 'disabled'
      if (!statusFilters.value.includes(status)) return false
    }
    return true
  })
})

function resetHostFilters() {
  hostQuery.value = ''
  platformFilters.value = []
  statusFilters.value = []
}

function resetHostWidths() {
  hostTable.value?.reset()
}

async function load() {
  loading.value = true
  try {
    hosts.value = await listHosts()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '主机清单加载失败'
  } finally {
    loading.value = false
  }
}

async function toggle(host: ExecHost) {
  if (!canWrite.value) {
    error.value = '当前角色没有主机写权限（需要 admin）。'
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await setHostEnabled(host.id, !host.enabled)
    notice.value = `${host.id} 已${host.enabled ? '停用' : '启用'}`
    await load()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '主机启停失败'
  } finally {
    busy.value = false
  }
}

async function deleteHostAction(host: ExecHost) {
  if (!canWrite.value) {
    error.value = '当前角色没有主机删除权限（需要 admin）。'
    return
  }
  const warning = host.enabled ? '该主机当前启用中，删除会立即停用。\n\n' : ''
  if (!window.confirm(
    `删除主机「${host.id}」？\n\n${warning}` +
    '这是软删除：主机立即从列表隐藏，历史任务、部署和审计记录保留。',
  )) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await deleteHost(host.id)
    notice.value = `${host.id} 已软删除，历史记录仍保留`
    keyHost.value = null
    keyProbe.value = null
    await load()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '主机删除失败'
  } finally {
    busy.value = false
  }
}

function createHost() {
  void router.push({ name: 'host-new' })
}

function openPasswordReveal(host: ExecHost) {
  if (!canWrite.value) {
    error.value = '当前角色没有查看密码权限（需要 admin）。'
    return
  }
  revealHost.value = host
  revealFields.value = []
  revealError.value = ''
}

function closePasswordReveal() {
  revealHost.value = null
  revealFields.value = []
  revealError.value = ''
  revealBusy.value = false
}

async function confirmPasswordReveal(code: string) {
  if (!revealHost.value) return
  revealBusy.value = true
  revealError.value = ''
  try {
    const item = await revealHostPassword(revealHost.value.id, code)
    revealFields.value = [
      { label: '登录用户', value: item.user || '（未填写）' },
      { label: '登录密码', value: item.password },
      { label: '密码来源', value: item.password_source === 'encrypted' ? '加密保存' : item.password_source },
    ]
    notice.value = `${revealHost.value.id} 的密码已显示，30 秒后自动隐藏`
  } catch (err) {
    revealError.value = err instanceof ApiError ? err.message : '密码查看失败'
  } finally {
    revealBusy.value = false
  }
}

function editHost(host: ExecHost) {
  void router.push({ name: 'host-edit', params: { id: host.id } })
}

async function openHostKey(host: ExecHost) {
  keyHost.value = host
  keyProbe.value = null
  keyBusy.value = true
  error.value = ''
  notice.value = ''
  try {
    keyProbe.value = await probeHostKey(host.id)
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '主机公钥探测失败'
    keyHost.value = null
  } finally {
    keyBusy.value = false
  }
}

async function confirmHostKeyAction(fingerprint: string) {
  if (!keyHost.value) return
  keyBusy.value = true
  error.value = ''
  try {
    await confirmHostKey(keyHost.value.id, fingerprint)
    notice.value = `${keyHost.value.id} 的主机公钥已登记`
    keyHost.value = null
    keyProbe.value = null
    await load()
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '主机公钥登记失败'
  } finally {
    keyBusy.value = false
  }
}

const exportColumns = computed<CsvColumn<ExecHost>[]>(() => [
  { label: '主机 ID', value: (host) => host.id },
  { label: '平台', value: (host) => host.platform },
  { label: '操作系统族', value: (host) => host.os_family },
  { label: '发行版', value: (host) => host.distro },
  { label: '版本', value: (host) => host.os_version },
  { label: '包管理器', value: (host) => host.package_manager },
  { label: '服务管理器', value: (host) => host.service_manager },
  { label: '地址', value: (host) => host.address },
  { label: '端口', value: (host) => host.port },
  { label: '账号', value: (host) => host.user },
  { label: '密码', value: (host) => host.password_set ? '已配置' : '未配置' },
  { label: '状态', value: (host) => host.enabled ? '启用' : '停用' },
  ...hostFields.value
    .filter((field) => field.key.startsWith('custom_') && field.visible !== false)
    .map((field) => ({
      label: field.label,
      value: (host: ExecHost) => host.custom_fields?.[field.key] ?? '',
    })),
])

/** 按当前筛选结果导出主机清单，不导出未命中的行。 */
function exportHosts() {
  if (!filteredHosts.value.length) {
    error.value = '当前筛选没有可导出的主机'
    return
  }
  exporting.value = true
  try {
    downloadCsv(`主机清单-${exportStamp()}.csv`, exportColumns.value, filteredHosts.value)
    notice.value = `已导出 ${filteredHosts.value.length} 台主机`
  } finally {
    exporting.value = false
  }
}

async function downloadTemplate() {
  busy.value = true
  error.value = ''
  try {
    const blob = await downloadHostTemplate()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = '主机导入模板.xlsx'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '模板下载失败'
  } finally {
    busy.value = false
  }
}

/** 导入 xlsx：重复主机后端跳过；失败行在下方单独列出。 */
async function handleImportFile(file: File) {
  if (!file.name.toLowerCase().endsWith('.xlsx')) {
    error.value = '只支持 .xlsx 文件，请先下载模板填写'
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  importErrors.value = []
  try {
    const result = await importHosts(file)
    importErrors.value = result.errors
    notice.value = `导入完成：新增 ${result.created} 台，跳过 ${result.skipped} 台，失败 ${result.failed} 行`
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '主机导入失败'
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  me.value = await getOrLogin<MeInfo>('/auth/me')
  notice.value = String(route.query.notice || '')
  try {
    hostFields.value = await fetchHostFields()
  } catch {
    hostFields.value = []
  }
  await load()
})
</script>

<template>
  <div class="page-stack">
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <section class="card host-panel">
      <HostsToolbar
        :hosts-count="hosts.length"
        :filtered-count="filteredHosts.length"
        :enabled-count="enabledCount"
        :filters-active="hostFiltersActive"
        :loading="loading"
        :busy="busy"
        :exporting="exporting"
        :can-write="canWrite"
        @download-template="downloadTemplate"
        @import-file="handleImportFile"
        @export="exportHosts"
        @create="createHost"
      />

      <HostsImportErrors :errors="importErrors" @close="importErrors = []" />

      <HostsFilters
        v-model:query="hostQuery"
        v-model:platforms="platformFilters"
        v-model:statuses="statusFilters"
        :filters-active="hostFiltersActive"
        :has-custom-widths="hasCustomHostWidths"
        :platform-options="platformSelectOptions"
        @reset="resetHostFilters"
        @reset-widths="resetHostWidths"
      />

      <HostsTable
        ref="hostTable"
        :loading="loading"
        :hosts-count="hosts.length"
        :filtered-hosts="filteredHosts"
        :busy="busy"
        :can-write="canWrite"
        @custom-change="hasCustomHostWidths = $event"
        @reset-filters="resetHostFilters"
        @edit="editHost"
        @toggle="toggle"
        @confirm-key="openHostKey"
        @delete="deleteHostAction"
        @reveal-password="openPasswordReveal"
      />
    </section>

    <HostKeyConfirmDialog
      :host="keyHost"
      :probe="keyProbe"
      :busy="keyBusy"
      @close="keyHost = null; keyProbe = null"
      @confirm="confirmHostKeyAction"
    />

    <PasswordRevealDialog
      :open="Boolean(revealHost)"
      title="查看主机登录密码"
      :target="revealHost ? `${revealHost.id} · ${revealHost.address}:${revealHost.port} · ${revealHost.user}` : ''"
      hint="主机密码由服务端密钥解密，查看动作会写入审计。"
      :busy="revealBusy"
      :error="revealError"
      :fields="revealFields"
      @close="closePasswordReveal"
      @confirm="confirmPasswordReveal"
    />
  </div>
</template>

<style scoped>
.host-panel {
  padding: 20px 24px 24px;
}
</style>
