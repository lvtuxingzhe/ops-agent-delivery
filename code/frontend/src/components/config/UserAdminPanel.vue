<script setup lang="ts">
/**
 * 配置 → 用户与权限。
 *
 * 权限：本组件只在管理员配置页挂载；后台每个接口仍各自校验 admin，
 * 这里的按钮隐藏/禁用只是体验，不是权限边界。
 *
 * 初始密码 / 重置密码的明文只在弹层里出现一次，不写 localStorage，
 * 不做日志，关闭弹层即从内存丢弃。
 */

import { computed, onMounted, reactive, ref } from 'vue'

import {
  bindPhone,
  createUser,
  deleteUser,
  listRoles,
  listUsers,
  resetPassword,
  resetTotp,
  unbindPhone,
  updateUser,
  type AdminUser,
  type RoleItem,
} from '../../api/admin'
import { useAsyncAction } from '../../composables/useAsyncAction'
import ResizableTable from '../ResizableTable.vue'
import RowMoreMenu from '../RowMoreMenu.vue'

const columns = [
  { key: 'user', label: '用户', minWidth: 180 },
  { key: 'roles', label: '角色', minWidth: 130 },
  { key: 'status', label: '状态', minWidth: 82, resizable: false },
  { key: 'phone', label: '手机号', minWidth: 120 },
  { key: 'credentials', label: '凭据', minWidth: 130 },
  { key: 'actions', label: '操作', minWidth: 220, headClass: 'config-sticky-actions' },
]

const items = ref<AdminUser[]>([])
const table = ref<{ reset: () => void } | null>(null)
const customWidths = ref(false)
const roles = ref<RoleItem[]>([])
const total = ref(0)
const page = ref(1)
const size = 20
const filters = reactive<{ q: string; role: string; active: '' | 'true' | 'false' }>({
  q: '', role: '', active: '',
})

const { busy, error, message, run } = useAsyncAction(reload)

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / size)))

async function loadRoles() {
  try {
    roles.value = await listRoles()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '角色清单加载失败'
  }
}

async function reload() {
  try {
    const result = await listUsers({
      page: page.value,
      size,
      q: filters.q,
      role: filters.role,
      active: filters.active === '' ? null : filters.active === 'true',
    })
    items.value = result.items
    total.value = result.total
  } catch (err) {
    error.value = err instanceof Error ? err.message : '用户列表加载失败'
  }
}

onMounted(async () => {
  await Promise.all([reload(), loadRoles()])
})

function search() {
  page.value = 1
  void run(reload, '已刷新')
}

function changePage(next: number) {
  if (next < 1 || next > pageCount.value) return
  page.value = next
  void run(reload, `已切换到第 ${next} 页`)
}

function roleName(code: string): string {
  return roles.value.find((item) => item.code === code)?.name ?? code
}

function visibleRoles(codes: string[]): string[] {
  return codes.slice(0, 2)
}

function hiddenRoleCount(codes: string[]): number {
  return Math.max(0, codes.length - 2)
}

/* ---------- 新建 / 编辑 ---------- */

const formOpen = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const form = reactive({
  id: 0,
  username: '',
  display_name: '',
  phone: '',
  roles: [] as string[],
  is_active: true,
})

function openCreate() {
  formMode.value = 'create'
  Object.assign(form, {
    id: 0, username: '', display_name: '', phone: '',
    roles: ['investigator'], is_active: true,
  })
  formOpen.value = true
}

function openEdit(user: AdminUser) {
  formMode.value = 'edit'
  Object.assign(form, {
    id: user.id,
    username: user.username,
    display_name: user.display_name,
    phone: '',
    roles: [...user.roles],
    is_active: user.is_active,
  })
  formOpen.value = true
}

async function saveForm() {
  if (formMode.value === 'create') {
    const created = await createUser({
      username: form.username.trim(),
      display_name: form.display_name.trim(),
      roles: form.roles,
      phone: form.phone.trim() || undefined,
    })
    formOpen.value = false
    showSecret('初始密码', created.initial_password,
      `请把密码当面/单独交给 ${created.username}。关闭本弹层后不再显示；交付后应立即重置。`)
    return
  }
  await updateUser(form.id, {
    display_name: form.display_name.trim(),
    roles: form.roles,
    is_active: form.is_active,
  })
  formOpen.value = false
}

/* ---------- 一次性明文 ---------- */

const secretOpen = ref(false)
const secretTitle = ref('')
const secretValue = ref('')
const secretHint = ref('')

function showSecret(title: string, value: string, hint: string) {
  secretTitle.value = title
  secretValue.value = value
  secretHint.value = hint
  secretOpen.value = true
}

function closeSecret() {
  secretOpen.value = false
  secretValue.value = ''
}

async function copySecret() {
  try {
    await navigator.clipboard.writeText(secretValue.value)
    message.value = '已复制到剪贴板'
  } catch {
    error.value = '复制失败，请手动选中密码复制'
  }
}

/* ---------- 二次确认 ---------- */

const confirmOpen = ref(false)
const confirmTitle = ref('')
const confirmBody = ref('')
const pending = ref<(() => Promise<void>) | null>(null)

function ask(title: string, body: string, action: () => Promise<void>) {
  confirmTitle.value = title
  confirmBody.value = body
  pending.value = action
  confirmOpen.value = true
}

function closeConfirm() {
  if (!busy.value) confirmOpen.value = false
}

async function runConfirmed() {
  const action = pending.value
  if (!action) return
  const ok = await run(async () => {
    await action()
  }, '操作已完成')
  if (ok) {
    confirmOpen.value = false
    pending.value = null
  }
}

function askResetPassword(user: AdminUser) {
  ask('重置密码', `为 ${user.username} 生成新的 16 位密码？旧密码会立即失效。`, async () => {
    const result = await resetPassword(user.id)
    showSecret('新密码', result.password,
      '密码只显示这一次。请交付后让用户尽快登录并修改；不要发在群里。')
  })
}

function askResetTotp(user: AdminUser) {
  ask('重置动态口令', `解绑 ${user.username} 的 TOTP？解绑后该账号下次登录需要重新绑定。`, async () => {
    await resetTotp(user.id)
  })
}

function askToggle(user: AdminUser) {
  const disabling = user.is_active
  ask(
    disabling ? '停用账号' : '启用账号',
    disabling
      ? `停用 ${user.username}？停用后该账号立即不能登录，历史审计保留。最后一名管理员不能被停用。`
      : `启用 ${user.username}？启用后该账号可以继续登录。`,
    async () => {
      await updateUser(user.id, { is_active: !disabling })
    },
  )
}

function askUnbindPhone(user: AdminUser) {
  ask('解绑手机号', `解绑 ${user.username} 的手机号（${user.phone_masked}）？`, async () => {
    await unbindPhone(user.id)
  })
}

function askDeleteUser(user: AdminUser) {
  ask(
    '删除账号',
    `删除 ${user.username}？删除后该账号从列表隐藏、不能登录，密码/TOTP/手机号清除；历史任务和审计保留，用户名不能再次使用。`,
    async () => {
      await deleteUser(user.id)
    },
  )
}

/* ---------- 手机号 ---------- */

const phoneOpen = ref(false)
const phoneForm = reactive({ id: 0, username: '', phone: '' })

function openPhone(user: AdminUser) {
  Object.assign(phoneForm, {
    id: user.id,
    username: user.username,
    phone: '',
  })
  phoneOpen.value = true
}

async function savePhone() {
  await bindPhone(phoneForm.id, phoneForm.phone.trim())
  phoneOpen.value = false
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="message" class="message">{{ message }}</p>

  <section class="card">
    <div class="admin-head">
      <div>
        <p class="kicker">ACCESS CONTROL</p>
        <h2>用户与权限</h2>
      </div>
      <div class="card-actions">
        <button v-if="customWidths" type="button" class="plain" @click="table?.reset()">重置列宽</button>
        <button type="button" class="primary" :disabled="busy" @click="openCreate">新建用户</button>
      </div>
    </div>

    <div class="admin-filters">
      <label class="field">
        <span>搜索</span>
        <input v-model="filters.q" placeholder="用户名 / 显示名" :disabled="busy" @keyup.enter="search" />
      </label>
      <label class="field">
        <span>角色</span>
        <select v-model="filters.role" :disabled="busy" @change="search">
          <option value="">全部角色</option>
          <option v-for="role in roles" :key="role.code" :value="role.code">{{ role.name }}</option>
        </select>
      </label>
      <label class="field">
        <span>状态</span>
        <select v-model="filters.active" :disabled="busy" @change="search">
          <option value="">全部</option>
          <option value="true">启用中</option>
          <option value="false">已停用</option>
        </select>
      </label>
      <button type="button" class="plain" :disabled="busy" @click="search">刷新</button>
    </div>

    <ResizableTable
      ref="table"
      class="user-table"
      :columns="columns"
      default-grid="minmax(180px, 1.5fr) minmax(130px, .9fr) 82px minmax(120px, .7fr) 130px 220px"
      storage-key="ops.config.users.columnWidths.v4"
      :gap="10"
      :padding-x="4"
      head-class="config-rt-head"
      @custom-change="customWidths = $event"
    >
      <div v-for="user in items" :key="user.id" class="user-row">
        <div class="user-main">
          <strong>{{ user.username }}</strong>
        </div>
        <div class="role-tags">
          <span v-for="role in visibleRoles(user.roles)" :key="role" class="role-tag">{{ roleName(role) }}</span>
          <span v-if="hiddenRoleCount(user.roles)" class="role-tag" :title="user.roles.map(roleName).join('、')">
            +{{ hiddenRoleCount(user.roles) }}
          </span>
        </div>
        <div>
          <span class="status-dot" :class="{ on: user.is_active }"></span>
          {{ user.is_active ? '启用中' : '已停用' }}
        </div>
        <div class="mono phone-cell">{{ user.phone_bound ? user.phone_masked : '未绑定' }}</div>
        <div class="credential">
          <span>{{ user.has_password ? '密码已设' : '密码未设' }} · {{ user.totp_enabled ? 'TOTP 已绑' : 'TOTP 未绑' }}</span>
        </div>
        <div class="user-actions config-sticky-actions">
          <button type="button" class="plain" :disabled="busy" @click="openEdit(user)">编辑</button>
          <RowMoreMenu :disabled="busy">
            <template #default="{ close }">
              <button type="button" class="row-more-item" :disabled="busy"
                      @click="close(); askResetPassword(user)">
                重置密码
              </button>
              <button type="button" class="row-more-item" :disabled="busy"
                      @click="close(); askResetTotp(user)">
                重置 TOTP
              </button>
              <button type="button" class="row-more-item" :disabled="busy"
                      @click="close(); openPhone(user)">
                {{ user.phone_bound ? '换绑手机号' : '绑定手机号' }}
              </button>
              <button v-if="user.phone_bound" type="button" class="row-more-item is-danger" :disabled="busy"
                      @click="close(); askUnbindPhone(user)">
                解绑手机号
              </button>
              <button type="button" class="row-more-item is-danger" :disabled="busy"
                      @click="close(); askDeleteUser(user)">
                删除账号
              </button>
              <button type="button" class="row-more-item" :class="{ 'is-danger': user.is_active }"
                      :disabled="busy" @click="close(); askToggle(user)">
                {{ user.is_active ? '停用账号' : '启用账号' }}
              </button>
            </template>
          </RowMoreMenu>
        </div>
      </div>
      <p v-if="!items.length" class="desc empty">没有匹配的用户。</p>
    </ResizableTable>

    <div class="admin-pager">
      <span>共 {{ total }} 个用户，第 {{ page }} / {{ pageCount }} 页</span>
      <div>
        <button type="button" class="plain" :disabled="busy || page <= 1" @click="changePage(page - 1)">上一页</button>
        <button type="button" class="plain" :disabled="busy || page >= pageCount" @click="changePage(page + 1)">下一页</button>
      </div>
    </div>
  </section>

  <!-- 新建 / 编辑 -->
  <div v-if="formOpen" class="config-modal" @click.self="formOpen = false">
    <section class="config-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div>
          <p class="kicker">USER ACCOUNT</p>
          <h2>{{ formMode === 'create' ? '新建用户' : `编辑 ${form.username}` }}</h2>
        </div>
        <button type="button" class="plain" :disabled="busy" @click="formOpen = false">关闭</button>
      </header>
      <div class="form-grid admin-form">
        <label class="field">
          <span>用户名</span>
          <input
            v-model="form.username"
            placeholder="例如：ops_alice"
            maxlength="32"
            :disabled="busy || formMode === 'edit'"
          />
        </label>
        <label class="field">
          <span>显示名</span>
          <input v-model="form.display_name" maxlength="64" placeholder="例如：Alice（运维）" :disabled="busy" />
        </label>
        <label v-if="formMode === 'create'" class="field">
          <span>手机号（可选）</span>
          <input v-model="form.phone" maxlength="11" placeholder="登记后短信登录仍保持关闭" :disabled="busy" />
        </label>
        <label v-if="formMode === 'edit'" class="check-line">
          <input v-model="form.is_active" type="checkbox" :disabled="busy" /> 账号启用中
        </label>
        <div class="span-2 field">
          <span>角色（至少一个）</span>
          <div class="role-checks">
            <label v-for="role in roles" :key="role.code" class="check-line">
              <input v-model="form.roles" type="checkbox" :value="role.code" :disabled="busy" />
              {{ role.name }}
            </label>
          </div>
        </div>
      </div>
      <footer class="config-editor-foot">
        <button type="button" class="plain" :disabled="busy" @click="formOpen = false">取消</button>
        <button type="button" class="primary" :disabled="busy" @click="run(saveForm, formMode === 'create' ? '用户已创建' : '用户已更新')">
          {{ formMode === 'create' ? '创建用户' : '保存修改' }}
        </button>
      </footer>
    </section>
  </div>

  <!-- 一次明文 -->
  <div v-if="secretOpen" class="config-modal" @click.self="closeSecret">
    <section class="config-editor secret-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div><p class="kicker">ONE-TIME SECRET</p><h2>{{ secretTitle }}</h2></div>
        <button type="button" class="plain" @click="closeSecret">关闭</button>
      </header>
      <p class="desc">{{ secretHint }}</p>
      <div class="secret-box mono">{{ secretValue }}</div>
      <footer class="config-editor-foot">
        <button type="button" class="plain" @click="closeSecret">我已记下，关闭</button>
        <button type="button" class="primary" @click="copySecret">复制密码</button>
      </footer>
    </section>
  </div>

  <!-- 二次确认 -->
  <div v-if="confirmOpen" class="config-modal" @click.self="closeConfirm">
    <section class="config-editor confirm-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div><p class="kicker">PLEASE CONFIRM</p><h2>{{ confirmTitle }}</h2></div>
      </header>
      <p class="desc">{{ confirmBody }}</p>
      <footer class="config-editor-foot">
        <button type="button" class="plain" :disabled="busy" @click="confirmOpen = false">取消</button>
        <button type="button" class="primary" :disabled="busy" @click="runConfirmed">确认</button>
      </footer>
    </section>
  </div>

  <!-- 手机号 -->
  <div v-if="phoneOpen" class="config-modal" @click.self="phoneOpen = false">
    <section class="config-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div><p class="kicker">PHONE BINDING</p><h2>绑定手机号 · {{ phoneForm.username }}</h2></div>
        <button type="button" class="plain" :disabled="busy" @click="phoneOpen = false">关闭</button>
      </header>
      <label class="field">
        <span>手机号</span>
        <input v-model="phoneForm.phone" maxlength="11" placeholder="11 位中国大陆手机号" :disabled="busy" />
      </label>
      <footer class="config-editor-foot">
        <button type="button" class="plain" :disabled="busy" @click="phoneOpen = false">取消</button>
        <button type="button" class="primary" :disabled="busy" @click="run(savePhone, '手机号已绑定')">保存</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.admin-head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
}
.admin-head h2 {
  margin: 2px 0 0;
}
.admin-filters {
  display: grid;
  width: 100%;
  max-width: 920px;
  grid-template-columns: minmax(260px, 1fr) 180px 160px auto;
  gap: 10px;
  align-items: end;
  justify-content: start;
  margin: 14px 0 0;
}
.admin-filters .field {
  margin: 0;
}
.user-table {
  width: 100%;
  max-width: 100%;
  min-width: 860px;
  margin-top: 14px;
  border-top: 1px solid var(--line);
  overflow-x: auto;
}
.user-row {
  display: grid;
  grid-template-columns: var(--rt-grid, var(--rt-default));
  gap: var(--rt-gap, 10px);
  align-items: center;
  min-height: 60px;
  padding: 9px var(--rt-pad-x, 4px);
  border-bottom: 1px solid var(--line);
}
.user-head {
  min-height: 44px;
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
  font-weight: 650;
  letter-spacing: .03em;
}
.user-head > span:last-child {
  text-align: right;
}
.user-row {
  color: var(--ink-2);
  font-size: calc(12px * var(--font-scale, 1));
}
.user-main {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.user-main strong {
  overflow: hidden;
  color: var(--ink);
  font-size: calc(13px * var(--font-scale, 1));
  text-overflow: ellipsis;
  white-space: nowrap;
}
.user-main small {
  overflow: hidden;
  color: var(--muted);
  font-size: calc(10px * var(--font-scale, 1));
  text-overflow: ellipsis;
  white-space: nowrap;
}
.role-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.role-tag {
  padding: 3px 7px;
  color: var(--ink-2);
  font-size: calc(10px * var(--font-scale, 1));
  background: var(--surface-2, #f3f5f7);
  border: 1px solid var(--line);
  border-radius: 999px;
}
.status-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 5px;
  vertical-align: 1px;
  background: var(--muted);
  border-radius: 50%;
}
.status-dot.on {
  background: var(--ok, #1f9d55);
}
.credential {
  display: flex;
  flex-direction: column;
  gap: 3px;
  color: var(--muted);
  font-size: calc(10px * var(--font-scale, 1));
}
.user-actions {
  display: flex;
  flex-wrap: nowrap;
  gap: 4px;
  align-items: center;
  justify-content: flex-start;
}
.user-actions .plain {
  min-height: 28px;
  padding: 4px 7px;
  font-size: calc(11px * var(--font-scale, 1));
}
.user-actions :deep(.row-more-trigger) {
  min-height: 28px;
  padding: 4px 7px;
  font-size: calc(11px * var(--font-scale, 1));
}
.plain.text-danger {
  color: var(--danger, #b42318);
}
.empty {
  padding: 18px 4px;
}
.admin-pager {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
}
.admin-pager > div {
  display: flex;
  gap: 6px;
}
.admin-form {
  margin-top: 10px;
}
.role-checks {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  padding-top: 6px;
}
.secret-box {
  margin: 12px 0;
  padding: 14px 16px;
  overflow-x: auto;
  color: var(--ink);
  font-size: calc(18px * var(--font-scale, 1));
  font-weight: 650;
  letter-spacing: .06em;
  background: var(--surface-2, #f3f5f7);
  border: 1px dashed var(--line);
  border-radius: 8px;
  user-select: all;
}
.secret-editor,
.confirm-editor {
  max-width: 560px;
}
@media (max-width: 980px) {
  .admin-filters {
    grid-template-columns: 1fr 1fr;
  }
  .admin-filters .plain {
    grid-column: span 2;
  }
}

/* 配置页在窄窗口里只有约 600px 内容宽；七列横向滚动会让操作列看不见。
   这里改成两列信息卡：用户/角色/状态/手机号在上，凭据/创建时间/操作在下。 */
@media (max-width: 1180px) {
  .user-table {
    min-width: 0;
  }
  .user-head {
    display: none;
  }
  .user-row {
    grid-template-columns: minmax(140px, 1.2fr) minmax(120px, 1fr);
    gap: 7px 14px;
    min-width: 0;
    padding: 14px 4px;
  }
  .user-main {
    grid-column: 1;
  }
  .role-tags {
    grid-column: 2;
  }
  .user-row > :nth-child(3) {
    grid-column: 1;
    grid-row: 2;
  }
  .user-row > .phone-cell {
    grid-column: 2;
    grid-row: 2;
  }
  .user-row > .credential {
    grid-column: 1 / -1;
    flex-direction: row;
    gap: 12px;
  }
  .user-row > .user-actions {
    grid-column: 1 / -1;
    justify-content: flex-start;
  }
}
</style>
