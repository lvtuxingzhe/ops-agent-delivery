/**
 * 配置页相关的接口封装 —— 第 ④ 步。
 *
 * 和 auth.ts 同一个原则：只描述「调哪个接口、传什么」，不做业务判断。
 * 注意接口里【永远没有】密码字段 —— 数据源只有 credential_ref（引用名），
 * 真实凭据在运行期由环境变量/密钥管理注入，从不经过前端。
 */

import { del, get, post, postForm, put } from './http'

/** 一条数据源配置（后台 datasource_configs 表的一行）。 */
export interface Datasource {
  id: number
  key: string
  name: string
  kind: string
  adapter: string
  endpoint: string
  credential_ref: string
  default_query: string
  index_pattern: string
  readonly: boolean
  enabled: boolean
  health_status: 'unknown' | 'ok' | 'no_data' | 'failed'
  health_detail: string
  health_checked_at: string | null
  created_at: string | null
}

/** 新建/修改数据源时提交的表单（没有密码字段，永远没有）。 */
export interface DatasourceForm {
  name: string
  kind: string
  adapter: string
  endpoint: string
  credential_ref: string
  default_query: string
  index_pattern: string
  enabled?: boolean
}

/** 数据源类型的可选值（和后台服务层白名单保持一致）。 */
export const DATASOURCE_KINDS = [
  'prometheus', 'elk', 'k8s', 'docker',
  'blueking_a', 'blueking_b', 'feishu', 'siyuan', 'obsidian',
] as const

export function fetchDatasources(): Promise<{ items: Datasource[] }> {
  return get<{ items: Datasource[] }>('/config/datasources')
}

export function createDatasource(form: DatasourceForm): Promise<{ item: Datasource }> {
  return post<{ item: Datasource }>('/config/datasources', form)
}

export function updateDatasource(
  id: number | string,
  form: DatasourceForm,
): Promise<{ item: Datasource }> {
  return put<{ item: Datasource }>(`/config/datasources/${id}`, form)
}

/** 删除数据源配置；密钥引用保留，历史任务证据不受影响。 */
export function deleteDatasource(id: number | string): Promise<{ ok: boolean; item: Datasource }> {
  return del<{ ok: boolean; item: Datasource }>(
    `/config/datasources/${encodeURIComponent(String(id))}`,
  )
}


/** 管理员查看数据源完整凭据（Basic 拆成用户名/密码）。 */
export interface RevealedDatasourceCredential {
  key: string
  kind: string
  credential_ref: string
  backend: string
  auth_type: 'basic' | 'bearer'
  username: string
  password: string
  combined: string
  revealed_at: string
}

export function revealDatasourceCredential(
  key: string,
  code: string,
): Promise<{ item: RevealedDatasourceCredential }> {
  return post<{ item: RevealedDatasourceCredential }>(
    `/config/datasources/${encodeURIComponent(key)}/credential/reveal`,
    { code },
  )
}

/** 把数据源密码写入当前凭据后端（OpenBao 或数据库加密）；保存后立即生效。 */
export function saveDatasourceCredential(
  key: string,
  body: { auth_type: 'basic' | 'bearer'; username?: string; password: string; code: string },
): Promise<{ item: {
  key: string
  credential_ref: string
  backend: string
  auth_type: string
  kv_version?: string
  key_version?: number
} }> {
  return put(`/config/datasources/${encodeURIComponent(key)}/credential`, body)
}

export function setDatasourceEnabled(id: number, enabled: boolean): Promise<{ item: Datasource }> {
  return post<{ item: Datasource }>(`/config/datasources/${id}/enabled`, { enabled })
}

export function healthCheckDatasource(id: number): Promise<{ item: Datasource }> {
  return post<{ item: Datasource }>(`/config/datasources/${id}/health-check`)
}

/** 一键巡检所有启用中的真实数据源；样例源由后端跳过。 */
export interface HealthCheckSummary {
  checked: number
  ok: number
  failed: number
  no_data: number
  items: Array<{
    key: string
    name: string
    kind: Datasource['kind']
    health_status: Datasource['health_status']
    health_detail: string
  }>
}

export function healthCheckAllDatasources(): Promise<HealthCheckSummary> {
  return post<HealthCheckSummary>('/config/datasources/health-check')
}

/** 平台开关：键 → 当前值（布尔开关的值是 "true"/"false" 字符串）。 */
export type PlatformSettings = Record<string, string>

export function fetchSettings(): Promise<{ settings: PlatformSettings }> {
  return get<{ settings: PlatformSettings }>('/config/settings')
}

export function setSetting(key: string, value: string): Promise<{ key: string; value: string }> {
  return post<{ key: string; value: string }>(`/config/settings/${key}`, { value })
}

/** 登录页公开配置：背景图、品牌背景、应用名与全局字体。 */
export interface LoginPublicConfig {
  app_name: string
  login_background_url: string
  brand_background_url: string
  ui_font_family: 'noto-sans' | 'noto-serif'
  ui_font_size: string
}

export function fetchLoginConfig(): Promise<LoginPublicConfig> {
  return get<LoginPublicConfig>('/public/login')
}

/** 管理员上传登录背景。返回服务端生成的图片地址。 */
export function uploadLoginBackground(file: File): Promise<{
  url: string
  width: number
  height: number
  bytes: number
}> {
  const form = new FormData()
  form.append('file', file)
  return postForm('/config/login-background', form)
}

export function resetLoginBackground(): Promise<{ ok: boolean }> {
  return post('/config/login-background/reset')
}

/** 管理员上传 OA 左上角品牌块背景；只作用于品牌块。 */
export function uploadBrandBackground(file: File): Promise<{
  url: string
  width: number
  height: number
  bytes: number
}> {
  const form = new FormData()
  form.append('file', file)
  return postForm('/config/brand-background', form)
}

export function resetBrandBackground(): Promise<{ ok: boolean }> {
  return post('/config/brand-background/reset')
}
