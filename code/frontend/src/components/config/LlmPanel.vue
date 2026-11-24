<script setup lang="ts">
/**
 * 大模型面板 —— 从 `Config.vue` 拆出来的第二块。
 *
 * 厂商模板、用户配置、新建/编辑、启停、设默认、测试连通都在这里。
 * 「测试连通」的回执单独走 notice（它不是成功也不是错误，是一条明确答复）。
 */

import { onMounted, reactive, ref } from 'vue'

import {
  createLlmConfig,
  deleteLlmConfig,
  fetchLlmConfigs,
  fetchLlmTemplates,
  probeLlmConfig,
  setLlmDefault,
  setLlmEnabled,
  updateLlmConfig,
  type LlmConfig,
  type LlmConfigForm,
  type LlmTemplate,
} from '../../api/llm'
import { fetchSettings, setSetting } from '../../api/config'
import { fetchTokenSummary, type TokenSummary } from '../../api/overview'
import { useAsyncAction } from '../../composables/useAsyncAction'
import ResizableTable from '../ResizableTable.vue'
import RowMoreMenu from '../RowMoreMenu.vue'

const columns = [
  { key: 'name', label: '名称 / 厂商', minWidth: 170 },
  { key: 'model', label: '模型', minWidth: 130 },
  { key: 'endpoint', label: '接口地址', minWidth: 190 },
  { key: 'status', label: '状态', minWidth: 80, resizable: false },
  { key: 'params', label: '参数', minWidth: 130 },
  { key: 'actions', label: '操作', minWidth: 160, headClass: 'config-sticky-actions' },
]

const templates = ref<LlmTemplate[]>([])
const items = ref<LlmConfig[]>([])
const table = ref<{ reset: () => void } | null>(null)
const customWidths = ref(false)
const showForm = ref(false)
const editingId = ref<number | null>(null)
const budgetTask = ref('0')
const budgetDay = ref('0')
const tokenSummary = ref<TokenSummary | null>(null)
// 参数用字符串收输入，提交时才转数字 —— 空字符串表示「用平台默认值」
const form = reactive({
  name: '',
  template_code: 'custom',
  protocol: 'openai_compatible',
  base_url: '',
  model_name: '',
  credential_ref: '',
  timeout_seconds: '',
  max_tokens: '',
  temperature: '',
  input_price_per_1k: '',
  output_price_per_1k: '',
  enabled: false,
  is_default: false,
})

const { busy, error, message, notice, run } = useAsyncAction(reload)

async function reload() {
  try {
    const [tpl, configs, platform, usage] = await Promise.all([
      fetchLlmTemplates(),
      fetchLlmConfigs(),
      fetchSettings(),
      fetchTokenSummary(30),
    ])
    templates.value = tpl.items
    items.value = configs.items
    tokenSummary.value = usage
    budgetTask.value = platform.settings.investigation_token_budget_per_task ?? '0'
    budgetDay.value = platform.settings.investigation_token_budget_per_day ?? '0'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '模型配置加载失败'
  }
}

onMounted(reload)

function startCreate() {
  editingId.value = null
  Object.assign(form, {
    name: '', template_code: 'custom', protocol: 'openai_compatible',
    base_url: '', model_name: '', credential_ref: '',
    timeout_seconds: '', max_tokens: '', temperature: '',
    input_price_per_1k: '', output_price_per_1k: '',
    enabled: false, is_default: false,
  })
  showForm.value = true
}

function startEdit(item: LlmConfig) {
  editingId.value = item.id
  Object.assign(form, {
    name: item.name,
    template_code: item.template_code,
    protocol: item.protocol,
    base_url: item.base_url,
    model_name: item.model_name,
    credential_ref: item.credential_ref,
    timeout_seconds: item.params.timeout_seconds?.toString() ?? '',
    max_tokens: item.params.max_tokens?.toString() ?? '',
    temperature: item.params.temperature?.toString() ?? '',
    input_price_per_1k: item.params.input_price_per_1k?.toString() ?? '',
    output_price_per_1k: item.params.output_price_per_1k?.toString() ?? '',
    enabled: item.enabled,
    is_default: item.is_default,
  })
  showForm.value = true
}

/** 选模板只帮用户「带出」地址和默认模型名，之后仍可随手改。 */
function applyTemplate() {
  const picked = templates.value.find((t) => t.code === form.template_code)
  if (!picked) return
  form.protocol = picked.protocol
  if (!form.base_url) form.base_url = picked.base_url_hint
  if (!form.model_name) form.model_name = picked.model_hint
}

/** 把表单里的字符串参数转成数字；留空的键不提交，用平台默认值。 */
function payload(): LlmConfigForm {
  const params: Record<string, number> = {}
  if (form.timeout_seconds !== '') params.timeout_seconds = Number(form.timeout_seconds)
  if (form.max_tokens !== '') params.max_tokens = Number(form.max_tokens)
  if (form.temperature !== '') params.temperature = Number(form.temperature)
  if (form.input_price_per_1k !== '') params.input_price_per_1k = Number(form.input_price_per_1k)
  if (form.output_price_per_1k !== '') params.output_price_per_1k = Number(form.output_price_per_1k)
  return {
    name: form.name,
    template_code: form.template_code,
    protocol: form.protocol,
    base_url: form.base_url,
    model_name: form.model_name,
    credential_ref: form.credential_ref,
    params,
    enabled: form.enabled,
    is_default: form.is_default,
  }
}

async function submitForm() {
  const ok = editingId.value === null
    ? await run(async () => {
        await createLlmConfig(payload())
      }, `模型配置「${form.name}」已创建（默认停用，需手动启用）`)
    : await run(async () => {
        await updateLlmConfig(editingId.value as number, payload())
      }, `模型配置「${form.name}」已更新`)
  if (ok) showForm.value = false
  if (ok) await refreshUsage()
}

async function saveBudget() {
  const task = Number(budgetTask.value || 0)
  const day = Number(budgetDay.value || 0)
  if (!Number.isInteger(task) || !Number.isInteger(day) || task < 0 || day < 0) {
    error.value = '预算必须填 0 或正整数；0 表示不设预算'
    return
  }
  const ok = await run(async () => {
    await setSetting('investigation_token_budget_per_task', String(task))
    await setSetting('investigation_token_budget_per_day', String(day))
  }, 'Token 预算已保存；超限只发通知，不停止任务')
  if (ok) await refreshUsage()
}

async function refreshUsage() {
  try {
    tokenSummary.value = await fetchTokenSummary(30)
  } catch {
    // 用量刷新失败不影响配置保存本身
  }
}

function numberText(value: number | null | undefined): string {
  return new Intl.NumberFormat('zh-CN').format(value || 0)
}

function costText(value: number | null | undefined): string {
  if (value === null || value === undefined) return '未配置单价'
  return `¥${Number(value).toFixed(2)}`
}

function closeForm() {
  if (!busy.value) showForm.value = false
}

/** 「测试连通」：默认关时拿 403 人话回执，放行时拿归纳状态；都只显示人话。 */
async function probe(item: LlmConfig) {
  busy.value = true
  error.value = ''
  message.value = ''
  notice.value = ''
  try {
    notice.value = await probeLlmConfig(item.id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '测试失败'
  } finally {
    busy.value = false
  }
}

async function deleteItem(item: LlmConfig) {
  if (item.is_default) {
    error.value = '默认模型不能直接删除，请先把另一条模型设为默认'
    return
  }
  if (!window.confirm(
    `删除模型配置「${item.name}」？\n\n历史报告里的模型名和价格快照仍保留。`,
  )) return
  await run(() => deleteLlmConfig(item.id), `模型配置「${item.name}」已删除`)
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="message" class="message">{{ message }}</p>
  <p v-if="notice" class="notice">{{ notice }}</p>

  <section v-if="tokenSummary" class="card token-config-card">
    <div class="card-head">
      <div>
        <h2>Token 预算与用量</h2>
      </div>
      <span :class="['tag', tokenSummary.budget.daily_exceeded ? 'tag-warn' : 'tag-on']">
        {{ tokenSummary.budget.daily_exceeded ? '今日已超限' : '预算正常' }}
      </span>
    </div>

    <div class="token-config-metrics">
      <div><span>今日</span><strong>{{ numberText(tokenSummary.today.total_tokens) }}</strong><small>token / {{ tokenSummary.today.tasks }} 个任务</small></div>
      <div><span>本月</span><strong>{{ numberText(tokenSummary.month.total_tokens) }}</strong><small>token / {{ tokenSummary.month.tasks }} 个任务</small></div>
      <div><span>今日估算</span><strong>{{ costText(tokenSummary.today.cost) }}</strong><small>{{ tokenSummary.today.unpriced_tasks ? '部分任务缺单价' : '按模型单价（元 / 千 token）' }}</small></div>
      <div><span>单任务超限</span><strong>{{ tokenSummary.budget.per_task_exceeded }}</strong><small>本月累计</small></div>
    </div>

    <div class="token-budget-form">
      <label>单任务预算（token）
        <input v-model="budgetTask" inputmode="numeric" placeholder="0 = 不限制" />
      </label>
      <label>每日预算（token）
        <input v-model="budgetDay" inputmode="numeric" placeholder="0 = 不限制" />
      </label>
      <button type="button" class="primary" :disabled="busy" @click="saveBudget">保存预算</button>
    </div>
  </section>

  <section class="card">
    <div class="card-head">
      <h2>通用大模型接入</h2>
      <div class="card-actions">
        <button v-if="customWidths" type="button" class="plain" @click="table?.reset()">重置列宽</button>
        <button type="button" class="primary" @click="startCreate">新建模型配置</button>
      </div>
    </div>

    <ResizableTable
      ref="table"
      v-if="items.length"
      :columns="columns"
      default-grid="minmax(170px, 300px) minmax(130px, 150px) minmax(190px, 1fr) 80px minmax(130px, 220px) 160px"
      storage-key="ops.config.llm.columnWidths.v4"
      :gap="10"
      :padding-x="4"
      card-class="config-record-table llm-table"
      head-class="config-rt-head"
      @custom-change="customWidths = $event"
    >
      <div v-for="item in items" :key="item.id" class="record-row">
        <div class="record-main">
          <strong>{{ item.name }}</strong>
        </div>
        <div class="mono record-model">{{ item.model_name }}</div>
        <div class="record-endpoint">
          <span class="mono" :title="item.base_url">{{ item.base_url }}</span>
        </div>
        <div class="record-tags">
          <span v-if="item.is_default" class="tag tag-on">默认</span>
          <span :class="['tag', item.enabled ? 'tag-on' : 'tag-off']">
            {{ item.enabled ? '启用' : '停用' }}
          </span>
        </div>
        <div class="record-params">
          <span
            class="param-line"
            :title="`超时 ${item.params.timeout_seconds ?? '默认'}s · 上限 ${item.params.max_tokens ?? '默认'} · 温度 ${item.params.temperature ?? '默认'} · 输入单价 ${item.params.input_price_per_1k ?? '未填'} / 输出单价 ${item.params.output_price_per_1k ?? '未填'} 元 / 千 token`"
          >
            超时 {{ item.params.timeout_seconds ?? '默认' }}s ·
            上限 {{ item.params.max_tokens ?? '默认' }} ·
            温度 {{ item.params.temperature ?? '默认' }} ·
            单价 {{ item.params.input_price_per_1k ?? '未填' }} / {{ item.params.output_price_per_1k ?? '未填' }} 元 / 千 token
          </span>
        </div>
        <div class="record-actions config-sticky-actions">
          <button type="button" class="plain" :disabled="busy" @click="startEdit(item)">
            编辑
          </button>
          <RowMoreMenu :disabled="busy" v-slot="{ close }">
            <button type="button" class="row-more-item" :disabled="busy"
                    @click="close(); run(() => setLlmEnabled(item.id, !item.enabled), '已保存')">
              {{ item.enabled ? '停用' : '启用' }}
            </button>
            <button type="button" class="row-more-item" :disabled="busy || item.is_default"
                    @click="close(); run(() => setLlmDefault(item.id), '已设为默认模型')">
              设为默认
            </button>
            <button type="button" class="row-more-item" :disabled="busy"
                    @click="close(); probe(item)">
              测试连通
            </button>
            <button type="button" class="row-more-item is-danger"
                    :disabled="busy || item.is_default"
                    :title="item.is_default ? '先设其他模型为默认，再删除' : '删除模型配置'"
                    @click="close(); deleteItem(item)">
              删除模型
            </button>
          </RowMoreMenu>
        </div>
      </div>
    </ResizableTable>
    <p v-else class="empty">（还没有模型配置，点「新建模型配置」加一个）</p>
  </section>

  <div v-if="showForm" class="config-modal" @click.self="closeForm">
    <section class="config-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div>
          <p class="kicker">MODEL CONFIG</p>
          <h2>{{ editingId === null ? '新建模型配置' : '编辑模型配置' }}</h2>
        </div>
        <button type="button" class="plain" :disabled="busy" @click="closeForm">关闭</button>
      </header>
      <div class="form-grid">
        <label>名称<input v-model="form.name" placeholder="如：默认-DeepSeek" /></label>
        <label>厂商模板
          <select v-model="form.template_code" @change="applyTemplate">
            <option v-for="t in templates" :key="t.code" :value="t.code">
              {{ t.display_name }}{{ t.base_url_hint ? '' : '（需自填地址）' }}
            </option>
          </select>
        </label>
        <label>协议
          <select v-model="form.protocol">
            <option value="openai_compatible">统一兼容协议</option>
            <option value="native_claude">Claude 原生（第一期仅登记）</option>
            <option value="native_gemini">Gemini 原生（第一期仅登记）</option>
          </select>
        </label>
        <label class="wide">接口地址（选模板后自动带出，可改成内网网关 / 自建端点）
          <input v-model="form.base_url" placeholder="例：http://127.0.0.1:8000/v1 或你的内网网关地址" />
        </label>
        <label>模型名
          <input v-model="form.model_name" placeholder="如 deepseek-chat" />
        </label>
        <label>凭据引用（只填编号，不是密钥）
          <input v-model="form.credential_ref" placeholder="如 LLM_KEY_DEEPSEEK（可留空）" />
        </label>
        <label>超时（秒，选填）<input v-model="form.timeout_seconds" placeholder="平台默认" /></label>
        <label>最大返回长度（选填）<input v-model="form.max_tokens" placeholder="平台默认" /></label>
        <label>温度（0～2，选填）<input v-model="form.temperature" placeholder="平台默认" /></label>
        <label>输入单价（元 / 千 token，选填）
          <input v-model="form.input_price_per_1k" inputmode="decimal" placeholder="留空 = 不估算金额" />
        </label>
        <label>输出单价（元 / 千 token，选填）
          <input v-model="form.output_price_per_1k" inputmode="decimal" placeholder="留空 = 不估算金额" />
        </label>
        <label></label>
        <label class="check">
          <input v-model="form.enabled" type="checkbox" /> 保存后立即启用（默认停用）
        </label>
        <label class="check">
          <input v-model="form.is_default" type="checkbox" /> 设为默认模型
        </label>
      </div>
      <footer class="config-editor-foot">
        <button type="button" class="plain" :disabled="busy" @click="closeForm">取消</button>
        <button type="button" class="primary" :disabled="busy" @click="submitForm">保存</button>
      </footer>
    </section>
  </div>
</template>
