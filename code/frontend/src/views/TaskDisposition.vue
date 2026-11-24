<script setup lang="ts">
/**
 * 处置方案页：把“要改机器”从报告页拆出来。
 *
 * 报告只说明问题；这一页由人填写目标、命令和说明，至少一条命令才能提交。
 * 提交后只进入 awaiting_approval，不会直接执行；审批页只审这份方案。
 */

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  fetchDisposition,
  fetchTask,
  fetchTaskReport,
  requestExecution,
  updateDisposition,
  type DispositionDraft,
  type ExecOperation,
  type TaskInfo,
  type TaskReport,
} from '../api/tasks'
import { ApiError } from '../api/http'
import { commandFromArgv, splitCommand } from '../utils/command'
import { formatLocalTime } from '../utils/time'
import HostPicker from '../components/HostPicker.vue'

interface CommandRow {
  commandText: string
  scope: 'read' | 'dangerous'
}

const route = useRoute()
const router = useRouter()
const taskId = computed(() => String(route.params.id || ''))
const task = ref<TaskInfo | null>(null)
const report = ref<TaskReport | null>(null)
const draft = ref<DispositionDraft | null>(null)
const rows = ref<CommandRow[]>([{ commandText: '', scope: 'read' }])
const platform = ref<'linux' | 'windows'>('linux')
const host = ref('local')
const reason = ref('')
const loading = ref(true)
const submitting = ref(false)
const error = ref('')

const hasCommands = computed(() => rows.value.some((row) => row.commandText.trim()))
const canEdit = computed(() => {
  if (!task.value) return false
  if (task.value.status === 'report_ready') return true
  return task.value.status === 'awaiting_approval'
    && !draft.value?.operations?.length
})
const submitLabel = computed(() => (
  task.value?.status === 'awaiting_approval' ? '保存处置方案' : '提交审批'
))

function addRow(): void {
  rows.value.push({ commandText: '', scope: 'read' })
}

function removeRow(index: number): void {
  if (rows.value.length === 1) {
    rows.value[0].commandText = ''
    rows.value[0].scope = 'read'
    return
  }
  rows.value.splice(index, 1)
}

function parseOperations(): ExecOperation[] {
  const operations: ExecOperation[] = []
  for (const [index, row] of rows.value.entries()) {
    if (!row.commandText.trim()) continue
    try {
      operations.push({
        argv: splitCommand(row.commandText.trim()),
        scope: row.scope,
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : '命令格式不对'
      throw new Error(`第 ${index + 1} 行：${message}`)
    }
  }
  if (!operations.length) throw new Error('至少填写一条可执行的命令')
  return operations
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    task.value = await fetchTask(taskId.value)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '任务加载失败'
    loading.value = false
    return
  }
  try {
    report.value = await fetchTaskReport(taskId.value)
  } catch {
    // 报告还没写入数据库时不阻断；处置仍要等状态到位。
  }
  try {
    draft.value = await fetchDisposition(taskId.value)
  } catch {
    draft.value = null
  }
  if (draft.value?.operations?.length) {
    rows.value = draft.value.operations.map((operation) => ({
      commandText: commandFromArgv(operation.argv),
      scope: operation.scope,
    }))
    if (draft.value.platform === 'windows' || draft.value.platform === 'linux') {
      platform.value = draft.value.platform
    }
    host.value = draft.value.host || 'local'
    reason.value = draft.value.reason || ''
  }
  loading.value = false
}

async function submit(): Promise<void> {
  if (!task.value || !canEdit.value) {
    error.value = '只有报告已生成、或待审批但还没有命令的排查，才能提交处置方案'
    return
  }
  let operations: ExecOperation[]
  try {
    operations = parseOperations()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '命令格式不对'
    return
  }
  submitting.value = true
  error.value = ''
  try {
    if (task.value.status === 'awaiting_approval') {
      await updateDisposition(
        taskId.value,
        reason.value.trim(),
        operations,
        platform.value,
        host.value.trim() || 'local',
      )
    } else {
      await requestExecution(
        taskId.value,
        reason.value.trim(),
        operations,
        platform.value,
        host.value.trim() || 'local',
      )
    }
    await router.push({ name: 'task-detail', params: { id: taskId.value } })
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : '提交处置方案失败'
  } finally {
    submitting.value = false
  }
}

onMounted(() => { void load() })
watch(taskId, () => { void load() })
</script>

<template>
  <div class="page-stack disposition-page">
    <section class="card">
      <div class="disposition-head">
        <div>
          <p class="eyebrow">DISPOSITION</p>
          <h2>创建处置方案</h2>
          <p class="desc">
            报告只说明问题。这里由人填写要执行的命令；提交后进入审批，批准前不会执行。
          </p>
        </div>
        <span class="badge badge-muted">待提交</span>
      </div>

      <p v-if="loading" class="desc">加载中…</p>
      <p v-if="error" class="form-error">{{ error }}</p>

      <div v-if="task" class="context-grid">
        <div class="context-item">
          <span class="context-label">排查任务</span>
          <strong>{{ task.title }}</strong>
          <code class="context-id">{{ task.id }}</code>
        </div>
        <div class="context-item">
          <span class="context-label">报告判断</span>
          <strong>{{ report?.headline || '报告尚未加载' }}</strong>
          <small v-if="report?.verdict">判断：{{ report.verdict }}</small>
        </div>
      </div>
    </section>

    <section v-if="task && canEdit" class="card">
      <div class="section-head">
        <div>
          <h3>执行目标</h3>
          <p class="section-hint">命令会按这里的平台和主机执行；主机填 local 或平台里登记的主机 ID。</p>
        </div>
      </div>
      <div class="form-grid">
        <label class="field">
          <span>平台</span>
          <select v-model="platform">
            <option value="linux">Linux</option>
            <option value="windows">Windows</option>
          </select>
        </label>
        <HostPicker
          v-model="host"
          :platform="platform"
          label="主机 ID"
          hint="从已登记主机中选择，也可以搜索 ID 或地址；未登记的主机执行时会被拒绝。"
          @update:platform="platform = $event"
        />
      </div>
    </section>

    <section v-if="task && canEdit" class="card">
      <div class="section-head">
        <div>
          <h3>命令清单</h3>
          <p class="section-hint">
            一条命令一行；不要管道、重定向、&& 或 ; 组合，平台只执行 argv。
          </p>
        </div>
        <button class="ghost" type="button" @click="addRow">+ 添加命令</button>
      </div>

      <div class="command-list">
        <div v-for="(row, index) in rows" :key="index" class="command-row">
          <span class="command-index">{{ index + 1 }}</span>
          <input
            v-model="row.commandText"
            class="command-input"
            type="text"
            placeholder="例如：systemctl status nginx"
          />
          <select v-model="row.scope" class="command-scope">
            <option value="read">只读</option>
            <option value="dangerous">变更</option>
          </select>
          <button class="ghost command-remove" type="button" @click="removeRow(index)">删除</button>
        </div>
      </div>

      <label class="field reason-field">
        <span>处置说明（可选）</span>
        <textarea
          v-model="reason"
          rows="3"
          placeholder="为什么需要处置、影响范围，审批人会看到这段说明。"
        />
      </label>

      <p v-if="error" class="form-error">{{ error }}</p>

      <div class="submit-row">
        <button
          class="primary"
          type="button"
          :disabled="!hasCommands || submitting"
          @click="submit"
        >
          {{ submitting ? '提交中…' : submitLabel }}
        </button>
        <router-link class="back-link" :to="{ name: 'task-detail', params: { id: taskId } }">
          返回报告
        </router-link>
        <span class="submit-hint">至少一条命令才能提交；批准前不会执行。</span>
      </div>
    </section>

    <section v-else-if="task && !loading" class="card">
      <p class="desc">
        当前状态：{{ task.status_text || task.status }}。这条任务已提交过处置方案；如要修改，请回到审批页由管理员处理。
      </p>
      <router-link class="back-link" :to="{ name: 'task-detail', params: { id: taskId } }">
        返回报告
      </router-link>
    </section>
  </div>
</template>

<style scoped>
.disposition-head,
.section-head,
.submit-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.disposition-head {
  margin-bottom: 16px;
}

.disposition-head h2,
.section-head h3 {
  margin: 0 0 4px;
}

.eyebrow {
  margin: 0 0 6px;
  color: var(--muted);
  font-family: var(--mono);
  font-size: calc(10px * var(--font-scale, 1));
  letter-spacing: 0.12em;
}

.desc,
.section-hint {
  margin: 0;
  color: var(--muted);
  font-size: calc(12px * var(--font-scale, 1));
  line-height: 1.7;
}

.context-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 10px;
}

.context-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface-soft);
}

.context-label {
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
}

.context-item strong {
  overflow-wrap: anywhere;
  font-size: calc(13px * var(--font-scale, 1));
  line-height: 1.55;
}

.context-item small,
.context-id {
  overflow-wrap: anywhere;
  color: var(--muted);
  font-family: var(--mono);
  font-size: calc(10px * var(--font-scale, 1));
}

.form-grid {
  display: grid;
  grid-template-columns: minmax(0, 180px) minmax(0, 1fr);
  gap: 12px;
  margin-top: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  color: var(--muted);
  font-size: calc(12px * var(--font-scale, 1));
}

.field input,
.field select,
.field textarea,
.command-input {
  width: 100%;
  min-width: 0;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  font: inherit;
}

.reason-field {
  margin-top: 12px;
}

.command-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}

.command-row {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) 76px auto;
  align-items: center;
  gap: 8px;
}

.command-index {
  color: var(--muted);
  font-family: var(--mono);
  font-size: calc(10px * var(--font-scale, 1));
  text-align: right;
}

.command-scope {
  padding: 8px 6px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  font-size: calc(12px * var(--font-scale, 1));
}

.command-remove {
  white-space: nowrap;
}

.form-error {
  margin: 12px 0 0;
  color: var(--danger);
  font-size: calc(12px * var(--font-scale, 1));
}

.submit-row {
  align-items: center;
  margin-top: 16px;
}

.submit-hint {
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
  line-height: 1.6;
}

.back-link {
  color: var(--accent-ink);
  font-size: calc(12px * var(--font-scale, 1));
  text-decoration: none;
}

.back-link:hover {
  text-decoration: underline;
}

@media (max-width: 640px) {
  .context-grid,
  .form-grid {
    grid-template-columns: 1fr;
  }

  .command-row {
    grid-template-columns: 16px minmax(0, 1fr);
  }

  .command-scope,
  .command-remove {
    grid-column: 2;
    justify-self: start;
  }

  .submit-row {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
