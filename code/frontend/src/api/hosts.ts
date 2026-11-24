/** 执行主机接口：只读写连接元数据，密码明文不落页面。 */
import { del, get, getBlob, post, postForm, put } from './http'

export interface ExecHost {
  id: string
  platform: string
  os_family: string
  distro: string
  os_version: string
  package_manager: string
  service_manager: string
  address: string
  port: number
  user: string
  credential_mode: 'password' | 'none'
  password_set: boolean
  password_source: 'encrypted' | 'none'
  /** 主机公钥登记状态：registered / unregistered / mismatch / file_missing / file_invalid。 */
  host_key_status?: string
  host_key_registered?: boolean
  host_key_fingerprints?: Array<{ key_type: string; fingerprint: string }>
  host_key_fingerprint?: string
  host_key_type?: string
  host_key_pinned?: boolean
  enabled: boolean
  custom_fields: Record<string, string>
}

export interface ExecHostInput {
  platform: string
  os_family: string
  distro: string
  os_version: string
  package_manager: string
  service_manager: string
  address: string
  port: number
  user: string
  /** 本次提交的明文密码；留空表示保留已有密码。 */
  password?: string
  /** 勾选后清空已保存密码。 */
  clear_password?: boolean
  enabled: boolean
  /** CMDB 自定义字段；key 为 custom_ 开头。 */
  custom_fields: Record<string, string>
}

interface HostListResponse {
  items: ExecHost[]
}

interface HostItemResponse {
  item: ExecHost
}

export async function listHosts(): Promise<ExecHost[]> {
  const data = await get<HostListResponse>('/hosts')
  return data.items
}

export async function createHost(hostId: string, body: ExecHostInput): Promise<ExecHost> {
  const data = await post<HostItemResponse>(`/hosts?host_id=${encodeURIComponent(hostId)}`, body)
  return data.item
}

export async function updateHost(hostId: string, body: ExecHostInput): Promise<ExecHost> {
  const data = await put<HostItemResponse>(`/hosts/${encodeURIComponent(hostId)}`, body)
  return data.item
}

export async function setHostEnabled(hostId: string, enabled: boolean): Promise<ExecHost> {
  const data = await post<HostItemResponse>(
    `/hosts/${encodeURIComponent(hostId)}/enabled`,
    { enabled },
  )
  return data.item
}

export interface HostKeyProbe {
  host_id: string
  address: string
  port: number
  status: 'registered' | 'unregistered' | 'mismatch'
  key_type: string
  public_key: string
  fingerprint: string
  stored: Array<{ key_type: string; fingerprint: string }>
}

/** 只做 SSH 握手取公钥；不发送密码。 */
export async function probeHostKey(hostId: string): Promise<HostKeyProbe> {
  const data = await post<{ item: HostKeyProbe }>(
    `/hosts/${encodeURIComponent(hostId)}/host-key/probe`,
    {},
  )
  return data.item
}

/** 管理员确认首次指纹后写入 known_hosts。 */
export async function confirmHostKey(hostId: string, fingerprint: string): Promise<ExecHost> {
  const data = await post<HostItemResponse>(
    `/hosts/${encodeURIComponent(hostId)}/host-key/confirm`,
    { fingerprint },
  )
  return data.item
}

/** 软删除：立即停用并从主机列表隐藏；配置、审计和历史引用保留。 */
export async function deleteHost(hostId: string): Promise<void> {
  await del<{ item: { id: string; deleted: boolean; deleted_at: string } }>(
    `/hosts/${encodeURIComponent(hostId)}`,
  )
}

export interface RevealedHostPassword {
  id: string
  user: string
  password: string
  password_source: string
  revealed_at: string
}

/** 管理员输入 6 位动态口令后查看完整登录密码；后端会记审计。 */
export async function revealHostPassword(
  hostId: string,
  code: string,
): Promise<RevealedHostPassword> {
  const data = await post<{ item: RevealedHostPassword }>(
    `/hosts/${encodeURIComponent(hostId)}/password/reveal`,
    { code },
  )
  return data.item
}

export interface HostField {
  key: string
  label: string
  kind: 'text' | 'select' | 'bool'
  visible: boolean
  required: boolean
  options: string[]
  hint: string
}

interface HostFieldsResponse {
  fields: HostField[]
}

/** 读取导入模板字段配置；管理员可见。 */
export async function fetchHostFields(): Promise<HostField[]> {
  const data = await get<HostFieldsResponse>('/hosts/field-schema')
  return data.fields
}

/** 保存字段名、显示与顺序；模板下载和导入解析立即生效。 */
export async function saveHostFields(fields: HostField[]): Promise<HostField[]> {
  const data = await put<HostFieldsResponse>('/hosts/field-schema', {
    fields: fields.map(({
      key, label, kind, visible, required, options,
    }) => ({
      key, label, kind, visible, required, options,
    })),
  })
  return data.fields
}

/** 集中管理密码：只提交新密码或清空标记，不读取旧密码。 */
export async function setHostPassword(
  hostId: string,
  body: { password?: string; clear?: boolean },
): Promise<ExecHost> {
  const data = await post<HostItemResponse>(
    `/hosts/${encodeURIComponent(hostId)}/password`,
    body,
  )
  return data.item
}

export interface HostImportError {
  row: number
  host_id: string
  error: string
}

export interface HostImportResult {
  created: number
  skipped: number
  failed: number
  errors: HostImportError[]
}

/** 上传 xlsx；重复主机由后台跳过，不回滚其他成功行。 */
export async function importHosts(file: File): Promise<HostImportResult> {
  const form = new FormData()
  form.append('file', file)
  return postForm<HostImportResult>('/hosts/import', form)
}

/** 下载后台生成的 xlsx 模板。 */
export function downloadHostTemplate(): Promise<Blob> {
  return getBlob('/hosts/import-template')
}
