import {
  computed, onMounted, reactive, ref,
  type ComputedRef, type InjectionKey, type Ref,
} from 'vue'

import {
  fetchHostFields,
  listHosts,
  saveHostFields,
  setHostPassword,
  type ExecHost,
  type HostField,
} from '../../../api/hosts'
import { useAsyncAction } from '../../../composables/useAsyncAction'

/**
 * 配置 → 主机配置的唯一状态源。
 *
 * 字段设置和密码管理两个子组件都通过 inject 复用这一份状态，避免各自
 * onMounted 重复拉取、各自保存导致状态不一致。
 */
export interface HostSettingsState {
  hosts: Ref<ExecHost[]>
  fields: Ref<HostField[]>
  passwordInputs: Record<string, string>
  addOpen: Ref<boolean>
  addError: Ref<string>
  draft: {
    key: string
    label: string
    kind: HostField['kind']
    options: string
    required: boolean
    visible: boolean
  }
  visibleFields: ComputedRef<HostField[]>
  hiddenFields: ComputedRef<HostField[]>
  customCount: ComputedRef<number>
  busy: Ref<boolean>
  error: Ref<string>
  message: Ref<string>
  moveField: (key: string, delta: number) => void
  saveFields: () => Promise<void>
  removeField: (key: string) => Promise<void>
  openAdd: () => void
  addField: () => Promise<void>
  savePassword: (host: ExecHost) => Promise<void>
  clearPassword: (host: ExecHost) => Promise<void>
  passwordState: (host: ExecHost) => string
}

export const HOST_SETTINGS_KEY: InjectionKey<HostSettingsState> = Symbol('host-settings')

export function useHostSettings(): HostSettingsState {
  const hosts = ref<ExecHost[]>([])
  const fields = ref<HostField[]>([])
  const passwordInputs = reactive<Record<string, string>>({})
  const addOpen = ref(false)
  const addError = ref('')
  const draft = reactive({
    key: '',
    label: '',
    kind: 'text' as HostField['kind'],
    options: '',
    required: false,
    visible: true,
  })

  const visibleFields = computed(() => fields.value.filter((item) => item.visible))
  const hiddenFields = computed(() => fields.value.filter((item) => !item.visible))
  const customCount = computed(
    () => fields.value.filter((item) => item.key.startsWith('custom_')).length,
  )

  const { busy, error, message, run } = useAsyncAction(reload)

  async function reload() {
    try {
      const [hostItems, fieldItems] = await Promise.all([
        listHosts(),
        fetchHostFields(),
      ])
      hosts.value = hostItems
      fields.value = fieldItems
      for (const host of hostItems) passwordInputs[host.id] ??= ''
    } catch (err) {
      error.value = err instanceof Error ? err.message : '主机配置加载失败'
    }
  }

  onMounted(reload)

  function moveField(key: string, delta: number) {
    const index = fields.value.findIndex((item) => item.key === key)
    const target = index + delta
    if (index < 0 || target < 0 || target >= fields.value.length) return
    const list = [...fields.value]
    const [item] = list.splice(index, 1)
    list.splice(target, 0, item)
    fields.value = list
  }

  async function persist(next: HostField[], okMessage: string) {
    const ok = await run(async () => {
      fields.value = await saveHostFields(next)
    }, okMessage)
    if (!ok) await reload()
    return ok
  }

  async function saveFields() {
    await persist(fields.value, '字段配置已保存；模板和导入解析立即生效')
  }

  async function removeField(key: string) {
    const field = fields.value.find((item) => item.key === key)
    if (!field) return
    if (field.required) {
      error.value = `${field.label} 是核心字段，不能删除`
      return
    }
    if (!window.confirm(`删除字段「${field.label}」？已有主机里的历史值会保留，但模板不再出现。`)) return
    await persist(
      fields.value.filter((item) => item.key !== key),
      `字段「${field.label}」已删除`,
    )
  }

  function openAdd() {
    addError.value = ''
    Object.assign(draft, {
      key: `custom_${Date.now().toString(36)}`,
      label: '',
      kind: 'text' as HostField['kind'],
      options: '',
      required: false,
      visible: true,
    })
    addOpen.value = true
  }

  async function addField() {
    addError.value = ''
    const key = draft.key.trim().toLowerCase()
    const label = draft.label.trim()
    if (!/^custom_[a-z0-9_]{1,40}$/.test(key)) {
      addError.value = '字段标识必须以 custom_ 开头，只能用小写字母、数字、下划线'
      return
    }
    if (fields.value.some((item) => item.key === key)) {
      addError.value = '字段标识已存在'
      return
    }
    if (!label) {
      addError.value = '字段名称不能为空'
      return
    }
    if (fields.value.some((item) => item.label.toLowerCase() === label.toLowerCase())) {
      addError.value = '字段名称已存在'
      return
    }
    const options = draft.kind === 'bool'
      ? ['true', 'false']
      : draft.options.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean)
    if (draft.kind === 'select' && options.length < 2) {
      addError.value = '下拉字段至少填写 2 个选项'
      return
    }
    const next: HostField = {
      key,
      label,
      kind: draft.kind === 'select' && options.length < 2 ? 'text' : draft.kind,
      visible: draft.visible,
      required: draft.required,
      options,
      hint: '自定义字段',
    }
    const list = [...fields.value, next]
    const ok = await persist(list, `字段「${label}」已添加`)
    if (ok) addOpen.value = false
  }

  async function savePassword(host: ExecHost) {
    const password = (passwordInputs[host.id] ?? '').trim()
    if (!password) {
      error.value = '请输入新密码'
      return
    }
    await run(async () => {
      const updated = await setHostPassword(host.id, { password })
      hosts.value = hosts.value.map((item) => (item.id === updated.id ? updated : item))
      passwordInputs[host.id] = ''
    }, `${host.id} 的密码已加密保存`)
  }

  async function clearPassword(host: ExecHost) {
    if (!window.confirm(`清空 ${host.id} 已保存的登录密码？`)) return
    await run(async () => {
      const updated = await setHostPassword(host.id, { clear: true })
      hosts.value = hosts.value.map((item) => (item.id === updated.id ? updated : item))
      passwordInputs[host.id] = ''
    }, `${host.id} 的密码已清空`)
  }

  function passwordState(host: ExecHost): string {
    return host.password_set ? '已配置（加密）' : '未配置'
  }

  return {
    hosts,
    fields,
    passwordInputs,
    addOpen,
    addError,
    draft,
    visibleFields,
    hiddenFields,
    customCount,
    busy,
    error,
    message,
    moveField,
    saveFields,
    removeField,
    openAdd,
    addField,
    savePassword,
    clearPassword,
    passwordState,
  }
}
