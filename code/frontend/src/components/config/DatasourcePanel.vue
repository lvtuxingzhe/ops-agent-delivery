<script setup lang="ts">
/**
 * 数据源面板 —— 从 `Config.vue` 拆出来的第一块。
 *
 * 只管数据源：列表、新建/编辑、启停、健康检查。管理员判定由父页面做，
 * 这里被渲染出来就等于「已确认是管理员」。
 */

import { onMounted, reactive, ref } from 'vue'

import {
  createDatasource,
  DATASOURCE_KINDS,
  deleteDatasource,
  fetchDatasources,
  healthCheckAllDatasources,
  healthCheckDatasource,
  revealDatasourceCredential,
  saveDatasourceCredential,
  setDatasourceEnabled,
  updateDatasource,
  type Datasource,
  type HealthCheckSummary,
} from '../../api/config'
import { useAsyncAction } from '../../composables/useAsyncAction'
import PasswordRevealDialog, { type RevealField } from '../PasswordRevealDialog.vue'
import ResizableTable from '../ResizableTable.vue'
import RowMoreMenu from '../RowMoreMenu.vue'
import DatasourceSecretDialog from './DatasourceSecretDialog.vue'

const columns = [
  { key: 'name', label: '名称', minWidth: 180 },
  { key: 'kind', label: '类型', minWidth: 70 },
  { key: 'endpoint', label: '地址 / 默认查询', minWidth: 200 },
  { key: 'status', label: '状态', minWidth: 110, resizable: false },
  { key: 'actions', label: '操作', minWidth: 230, headClass: 'config-sticky-actions' },
]

const items = ref<Datasource[]>([])
const table = ref<{ reset: () => void } | null>(null)
const customWidths = ref(false)
const batch = ref<HealthCheckSummary | null>(null)
const editingId = ref<number | null>(null)
const showForm = ref(false)
const form = reactive({
  name: '',
  kind: 'prometheus',
  adapter: 'self',
  endpoint: '',
  credential_ref: '',
  default_query: '',
  index_pattern: '',
  enabled: true,
})

/** 健康检查状态 → 页面上的人话。注意 no_data 就叫「无数据」，不叫正常。 */
const healthText: Record<Datasource['health_status'], string> = {
  unknown: '未检查',
  ok: '正常',
  no_data: '无数据（未探测）',
  failed: '不通过',
}

const { busy, error, message, run } = useAsyncAction(reload)

const revealItem = ref<Datasource | null>(null)
const revealFields = ref<RevealField[]>([])
const revealBusy = ref(false)
const revealError = ref('')
const secretItem = ref<Datasource | null>(null)
const secretBusy = ref(false)
const secretError = ref('')

async function reload() {
  try {
    items.value = (await fetchDatasources()).items
  } catch (err) {
    error.value = err instanceof Error ? err.message : '数据源加载失败'
  }
}

onMounted(reload)

function startCreate() {
  editingId.value = null
  Object.assign(form, {
    name: '', kind: 'prometheus', adapter: 'self',
    endpoint: '', credential_ref: '', default_query: '', index_pattern: '',
    enabled: true,
  })
  showForm.value = true
}

function startEdit(item: Datasource) {
  editingId.value = item.id
  Object.assign(form, {
    name: item.name, kind: item.kind, adapter: item.adapter,
    endpoint: item.endpoint, credential_ref: item.credential_ref,
    default_query: item.default_query ?? '',
    index_pattern: item.index_pattern ?? '',
    enabled: item.enabled,
  })
  showForm.value = true
}

async function checkAll() {
  batch.value = await healthCheckAllDatasources()
}

async function submitForm() {
  const ok = editingId.value === null
    ? await run(async () => {
        await createDatasource({ ...form })
      }, `数据源「${form.name}」已创建`)
    : await run(async () => {
        await updateDatasource(editingId.value as number, { ...form })
      }, `数据源「${form.name}」已更新`)
  if (ok) showForm.value = false
}

function closeForm() {
  if (!busy.value) showForm.value = false
}

function openReveal(item: Datasource) {
  revealItem.value = item
  revealFields.value = []
  revealError.value = ''
}

function closeReveal() {
  revealItem.value = null
  revealFields.value = []
  revealError.value = ''
  revealBusy.value = false
}

async function confirmReveal(code: string) {
  if (!revealItem.value) return
  revealBusy.value = true
  revealError.value = ''
  try {
    const { item } = await revealDatasourceCredential(revealItem.value.key, code)
    revealFields.value = item.auth_type === 'basic'
      ? [
          { label: '用户名', value: item.username },
          { label: '密码', value: item.password },
          { label: '完整凭据', value: item.combined },
        ]
      : [{ label: '令牌 / 密码', value: item.password }]
    message.value = `数据源「${revealItem.value.name}」的凭据已显示，30 秒后自动隐藏`
  } catch (err) {
    revealError.value = err instanceof Error ? err.message : '凭据查看失败'
  } finally {
    revealBusy.value = false
  }
}

function openSecret(item: Datasource) {
  secretItem.value = item
  secretError.value = ''
}

async function deleteItem(item: Datasource) {
  if (!window.confirm(
    `删除数据源「${item.name}」？\n\n只删除配置，不删除密钥引用；历史任务里的证据仍可查看。`,
  )) return
  await run(() => deleteDatasource(item.key), `数据源「${item.name}」已删除`)
}

function closeSecret() {
  if (!secretBusy.value) {
    secretItem.value = null
    secretError.value = ''
  }
}

async function confirmSecret(payload: {
  auth_type: 'basic' | 'bearer'; username: string; password: string; code: string
}) {
  if (!secretItem.value) return
  secretBusy.value = true
  secretError.value = ''
  try {
    await saveDatasourceCredential(secretItem.value.key, payload)
    message.value = `数据源「${secretItem.value.name}」的密码已保存，无需重启服务`
    secretItem.value = null
    await reload()
  } catch (err) {
    secretError.value = err instanceof Error ? err.message : '凭据写入失败'
  } finally {
    secretBusy.value = false
  }
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="message" class="message">{{ message }}</p>

  <section class="card">
    <div class="card-head">
      <h2>数据源</h2>
      <div class="card-actions">
        <button v-if="customWidths" type="button" class="plain" @click="table?.reset()">重置列宽</button>
        <button type="button" class="plain" :disabled="busy"
                @click="run(checkAll, '批量巡检完成')">
          一键巡检
        </button>
        <button type="button" class="primary" @click="startCreate">新建数据源</button>
      </div>
    </div>
    <p v-if="batch" class="health-note">
      本轮检查 {{ batch.checked }} 个：正常 {{ batch.ok }}、不通过 {{ batch.failed }}、无数据 {{ batch.no_data }}。
      样例源不参与巡检。
    </p>
    <ResizableTable
      ref="table"
      v-if="items.length"
      :columns="columns"
      default-grid="minmax(180px, 1.4fr) 80px minmax(200px, 1.8fr) 120px 230px"
      storage-key="ops.config.datasources.columnWidths.v4"
      :gap="10"
      :padding-x="4"
      card-class="config-record-table ds-table"
      head-class="config-rt-head"
      @custom-change="customWidths = $event"
    >
      <div v-for="item in items" :key="item.id" class="record-row">
        <div class="record-main">
          <strong>{{ item.name }}</strong>
        </div>
        <div><span class="record-kind">{{ item.kind }}</span></div>
        <div class="record-endpoint">
          <span class="mono" :title="item.endpoint">{{ item.endpoint }}</span>
        </div>
        <div class="ds-status">
          <span :class="['tag', item.enabled ? 'tag-on' : 'tag-off']">
            {{ item.enabled ? '启用' : '停用' }}
          </span>
          <span :class="['tag', `health-${item.health_status}`]" :title="item.health_detail">
            {{ healthText[item.health_status] }}
          </span>
        </div>
        <div class="record-actions config-sticky-actions">
          <button type="button" class="plain" :disabled="busy"
                  @click="run(() => healthCheckDatasource(item.id), '健康检查已登记')">
            检查
          </button>
          <button type="button" class="plain" :disabled="busy" @click="startEdit(item)">
            编辑
          </button>
          <RowMoreMenu :disabled="busy">
            <template #default="{ close }">
              <button type="button" class="row-more-item" :disabled="busy"
                      @click="close(); run(() => setDatasourceEnabled(item.id, !item.enabled), '已保存')">
                {{ item.enabled ? '停用数据源' : '启用数据源' }}
              </button>
              <button type="button" class="row-more-item"
                      :disabled="busy || !item.credential_ref"
                      :title="item.credential_ref ? '输入动态口令后查看完整凭据' : '先配置凭据引用'"
                      @click="close(); openReveal(item)">
                查看凭据
              </button>
              <button type="button" class="row-more-item"
                      :disabled="busy || !item.credential_ref"
                      :title="item.credential_ref ? '保存凭据，无需重启服务' : '先配置凭据引用'"
                      @click="close(); openSecret(item)">
                设置凭据
              </button>
              <button type="button" class="row-more-item is-danger" :disabled="busy"
                      @click="close(); deleteItem(item)">
                删除数据源
              </button>
            </template>
          </RowMoreMenu>
        </div>
      </div>
    </ResizableTable>
    <p v-else class="empty">（还没有数据源，点「新建数据源」加一个）</p>
  </section>

  <div v-if="showForm" class="config-modal" @click.self="closeForm">
    <section class="config-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div>
          <p class="kicker">DATASOURCE</p>
          <h2>{{ editingId === null ? '新建数据源' : '编辑数据源' }}</h2>
        </div>
        <button type="button" class="plain" :disabled="busy" @click="closeForm">关闭</button>
      </header>
      <div class="form-grid">
        <label>名称<input v-model="form.name" placeholder="如：生产-Prometheus" /></label>
        <label>类型
          <select v-model="form.kind">
            <option v-for="kind in DATASOURCE_KINDS" :key="kind" :value="kind">{{ kind }}</option>
          </select>
        </label>
        <label>接法
          <select v-model="form.adapter">
            <option value="self">self（自研适配器）</option>
            <option value="mcp">mcp（插件层）</option>
          </select>
        </label>
        <label class="wide">地址{{ form.kind === 'obsidian' ? '（本机目录，如 D:/notes/obsidian）' : '' }}
          <input v-model="form.endpoint" placeholder="例：http://127.0.0.1:9090" />
        </label>
        <label class="wide">凭据引用（名字，不是密码；如 CRED_PROM_MAIN）
          <input v-model="form.credential_ref" placeholder="可留空" />
        </label>
        <label class="wide">默认查询（Prometheus 填 PromQL；ELK 填 query_string；可留空）
          <textarea v-model="form.default_query" rows="3"
                    placeholder="Prometheus 例：100 - (avg(rate(node_cpu_seconds_total{mode=&quot;idle&quot;}[5m])) * 100)&#10;ELK 例：level:ERROR"></textarea>
        </label>
        <label>默认索引（仅 ELK；如 filebeat-*，留空查全部）
          <input v-model="form.index_pattern" placeholder="可留空" />
        </label>
        <label class="check"><input v-model="form.enabled" type="checkbox" /> 保存后立即启用</label>
      </div>
      <footer class="config-editor-foot">
        <button type="button" class="plain" :disabled="busy" @click="closeForm">取消</button>
        <button type="button" class="primary" :disabled="busy" @click="submitForm">保存</button>
      </footer>
    </section>
  </div>

  <PasswordRevealDialog
    :open="Boolean(revealItem)"
    title="查看数据源完整凭据"
    :target="revealItem ? `${revealItem.name} · ${revealItem.endpoint} · ${revealItem.credential_ref}` : ''"
    hint="Basic 凭据会拆成用户名和密码；查看动作会写入审计。"
    :busy="revealBusy"
    :error="revealError"
    :fields="revealFields"
    @close="closeReveal"
    @confirm="confirmReveal"
  />

  <DatasourceSecretDialog
    :open="Boolean(secretItem)"
    :target="secretItem ? `${secretItem.name} · ${secretItem.endpoint}` : ''"
    :credential-ref="secretItem?.credential_ref || ''"
    :busy="secretBusy"
    :error="secretError"
    @close="closeSecret"
    @confirm="confirmSecret"
  />
</template>
