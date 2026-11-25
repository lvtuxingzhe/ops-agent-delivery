<script setup lang="ts">
/**
 * 值班面板：审批值班负责卡片审批，告警值班只接收告警文本和被 @。
 *
 * 没有值班安排时后台仍回落到全体管理员，这里只负责排班和删除。
 * 值班只影响通知对象，不改变审批权限。
 */

import { computed, onMounted, reactive, ref } from 'vue'

import { listUsers, type AdminUser } from '../../api/admin'
import {
  createOnCallShift,
  deleteOnCallShift,
  fetchOnCallShifts,
  type OnCallShift,
} from '../../api/notifications'
import { useAsyncAction } from '../../composables/useAsyncAction'
import { formatLocalTime } from '../../utils/time'
import ResizableTable from '../ResizableTable.vue'
import FoldToggle from './FoldToggle.vue'

const columns = [
  { key: 'user', label: '值班人', minWidth: 150 },
  { key: 'scope', label: '类型', minWidth: 100, resizable: false },
  { key: 'period', label: '值班时间', minWidth: 260 },
  { key: 'status', label: '状态', minWidth: 90, resizable: false },
  { key: 'note', label: '备注', minWidth: 160 },
  { key: 'actions', label: '操作', minWidth: 90, headClass: 'config-sticky-actions' },
]

const shifts = ref<OnCallShift[]>([])
const users = ref<AdminUser[]>([])
const loaded = ref(false)
const collapsed = ref(true)
const editing = ref(false)
const customWidths = ref(false)
const table = ref<{ reset: () => void } | null>(null)

const selectableUsers = computed(() => {
  if (form.scope === 'approval') {
    return users.value.filter((item) => item.roles.includes('admin'))
  }
  return users.value
})

function localInputValue(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    `${pad(date.getHours())}:${pad(date.getMinutes())}`,
  ].join('T')
}

const form = reactive({
  user_id: 0,
  scope: 'approval' as 'approval' | 'alert',
  starts_at: localInputValue(new Date()),
  ends_at: localInputValue(new Date(Date.now() + 24 * 60 * 60 * 1000)),
  note: '',
})

const { busy, error, message, run } = useAsyncAction(reload)

async function reload() {
  const [list, allUsers] = await Promise.all([
    fetchOnCallShifts(),
    listUsers({ active: true, size: 50 }),
  ])
  shifts.value = list.items
  users.value = allUsers.items
  loaded.value = true
}

onMounted(async () => {
  try {
    await reload()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '值班表加载失败'
  }
})

function openEditor() {
  collapsed.value = false
  form.scope = 'approval'
  form.user_id = selectableUsers.value[0]?.id ?? 0
  const start = new Date()
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000)
  form.starts_at = localInputValue(start)
  form.ends_at = localInputValue(end)
  form.note = ''
  editing.value = true
}

function changeScope() {
  form.user_id = selectableUsers.value[0]?.id ?? 0
}

function closeEditor() {
  if (!busy.value) editing.value = false
}

async function save() {
  if (!form.user_id) {
    error.value = '请选择值班人'
    return
  }
  const ok = await run(async () => {
    await createOnCallShift({
      user_id: Number(form.user_id),
      scope: form.scope,
      starts_at: new Date(form.starts_at).toISOString(),
      ends_at: new Date(form.ends_at).toISOString(),
      note: form.note.trim(),
    })
    await reload()
  }, '值班已保存')
  if (ok) editing.value = false
}

async function remove(item: OnCallShift) {
  if (!window.confirm(`删除 ${item.user_name || '该用户'} 这段值班记录？历史仍保留，但不再参与通知。`)) {
    return
  }
  await run(async () => {
    await deleteOnCallShift(item.id)
    await reload()
  }, `已删除 ${item.user_name || '该值班'} 的值班记录`)
}

function periodText(item: OnCallShift): string {
  return `${formatLocalTime(item.starts_at, true)} 至 ${formatLocalTime(item.ends_at, true)}`
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="message" class="message">{{ message }}</p>

  <section v-if="loaded" class="card">
    <div class="card-head">
      <div>
        <h2>审批与告警值班</h2>
        <p class="desc">
          审批值班只允许管理员，负责卡片审批；告警值班可选任意启用用户，只接收告警文本和被 @。值班只影响通知对象，不改变审批权限。
        </p>
      </div>
      <div class="card-actions">
        <FoldToggle
          :expanded="!collapsed"
          controls="oncall-body"
          @toggle="collapsed = !collapsed"
        />
        <button v-if="customWidths" type="button" class="plain" @click="table?.reset()">重置列宽</button>
        <button type="button" class="primary" :disabled="busy" @click="openEditor">新增值班</button>
      </div>
    </div>
    <div v-show="!collapsed" id="oncall-body">
    <ResizableTable
      ref="table"
      :columns="columns"
      default-grid="minmax(150px, .9fr) 100px minmax(260px, 1.8fr) 100px minmax(160px, 1.1fr) 100px"
      storage-key="ops.config.oncall.columnWidths.v1"
      :gap="10"
      :padding-x="4"
      card-class="config-record-table oncall-table"
      head-class="config-rt-head"
      @custom-change="customWidths = $event"
    >
      <div v-for="item in shifts" :key="item.id" class="record-row oncall-row">
        <div class="record-main">
          <strong>{{ item.user_name || `用户 ${item.user_id}` }}</strong>
        </div>
        <div>
          <span :class="['tag', item.scope === 'alert' ? 'tag-off' : 'tag-on']">
            {{ item.scope_name || (item.scope === 'alert' ? '告警值班' : '审批值班') }}
          </span>
        </div>
        <div>{{ periodText(item) }}</div>
        <div>
          <span :class="['tag', item.is_current ? 'tag-on' : 'tag-off']">
            {{ item.is_current ? '值班中' : (new Date(item.ends_at).getTime() < Date.now() ? '已结束' : '未来') }}
          </span>
        </div>
        <div class="record-endpoint">{{ item.note || '—' }}</div>
        <div class="record-actions config-sticky-actions">
          <button type="button" class="plain" :disabled="busy || item.is_current" @click="remove(item)">
            删除
          </button>
        </div>
      </div>
      <p v-if="!shifts.length" class="desc empty-row">还没有值班安排，审批通知会发给全体管理员。</p>
    </ResizableTable>
    </div>
  </section>

  <div v-if="editing" class="config-modal" @click.self="closeEditor">
    <section class="config-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div><p class="kicker">ONCALL</p><h2>新增值班</h2></div>
        <button type="button" class="plain" :disabled="busy" @click="closeEditor">关闭</button>
      </header>
      <div class="form-grid">
        <label class="wide">
          值班类型
          <select v-model="form.scope" :disabled="busy" @change="changeScope">
            <option value="approval">审批值班（仅管理员）</option>
            <option value="alert">告警值班（任意启用用户）</option>
          </select>
        </label>
        <label class="wide">
          值班人
          <select v-model.number="form.user_id" :disabled="busy">
            <option v-for="item in selectableUsers" :key="item.id" :value="item.id">
              {{ item.display_name || item.username }}
            </option>
          </select>
        </label>
        <label>
          开始时间
          <input v-model="form.starts_at" type="datetime-local" :disabled="busy" />
        </label>
        <label>
          结束时间
          <input v-model="form.ends_at" type="datetime-local" :disabled="busy" />
        </label>
        <label class="wide">
          备注（可不填）
          <input v-model="form.note" maxlength="200" placeholder="例如：夜间值班，处理告警审批" />
        </label>
      </div>
      <p class="desc">
        审批值班只能选管理员；告警值班可选任意启用用户。同一时段可以多人值班，当前值班记录不能删除，结束后再清理。
      </p>
      <footer class="config-editor-foot">
        <button type="button" class="plain" :disabled="busy" @click="closeEditor">取消</button>
        <button type="button" class="primary" :disabled="busy" @click="save">保存</button>
      </footer>
    </section>
  </div>
</template>
