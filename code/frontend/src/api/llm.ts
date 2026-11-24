/**
 * 通用大模型配置接口封装。
 *
 * 只描述调哪个接口、传什么，不做业务判断。
 * 这里没有密钥字段；credential_ref 只是密钥引用名，真实密钥不经过前端。
 * 默认态不会发起真实调用；管理员打开开关且域名在白名单内时才走调用链。
 */

import { ApiError, del, get, post, put } from './http'

/** 厂商预置模板（存在库里，管理员可在配置页改）。 */
export interface LlmTemplate {
  id: number
  code: string
  display_name: string
  protocol: string
  base_url_hint: string
  model_hint: string
  note: string
  enabled: boolean
}

/** 用户自己配的一条模型。注意没有任何密钥字段。 */
export interface LlmConfig {
  id: number
  name: string
  template_code: string
  protocol: string
  base_url: string
  model_name: string
  credential_ref: string
  params: Record<string, number>
  enabled: boolean
  is_default: boolean
  health_status: string
  health_detail: string
  health_checked_at: string | null
  created_at: string | null
}

/** 新建/修改时提交的表单。 */
export interface LlmConfigForm {
  name: string
  template_code: string
  protocol: string
  base_url: string
  model_name: string
  credential_ref: string
  params: Record<string, number>
  enabled?: boolean
  is_default?: boolean
}

export function fetchLlmTemplates(): Promise<{ items: LlmTemplate[] }> {
  return get<{ items: LlmTemplate[] }>('/config/llm/templates')
}

export function fetchLlmConfigs(): Promise<{ items: LlmConfig[] }> {
  return get<{ items: LlmConfig[] }>('/config/llm/configs')
}

export function createLlmConfig(form: LlmConfigForm): Promise<{ item: LlmConfig }> {
  return post<{ item: LlmConfig }>('/config/llm/configs', form)
}

export function updateLlmConfig(id: number, form: LlmConfigForm): Promise<{ item: LlmConfig }> {
  return put<{ item: LlmConfig }>(`/config/llm/configs/${id}`, form)
}

/** 删除模型配置；默认模型不能直接删，需先换默认。 */
export function deleteLlmConfig(id: number | string): Promise<{ ok: boolean; item: LlmConfig }> {
  return del<{ ok: boolean; item: LlmConfig }>(
    `/config/llm/configs/${encodeURIComponent(String(id))}`,
  )
}

export function setLlmEnabled(id: number, enabled: boolean): Promise<{ item: LlmConfig }> {
  return post<{ item: LlmConfig }>(`/config/llm/configs/${id}/enabled`, { enabled })
}

export function setLlmDefault(id: number): Promise<{ item: LlmConfig }> {
  return post<{ item: LlmConfig }>(`/config/llm/configs/${id}/default`)
}

/** 探测结果：默认关时是 403 人话回执；放行时带归纳状态与是否降级。 */
export interface LlmProbeResult {
  allowed: boolean
  status: string
  detail: string
  reason: string
  used_model: boolean
  degraded: boolean
  data_quality: string
}

/** 「测试连通」：把后台的明确回执交回页面，不自行判断成不成功。 */
export async function probeLlmConfig(id: number): Promise<string> {
  try {
    const result = await post<{ result: LlmProbeResult }>(
      `/config/llm/configs/${id}/probe`,
    )
    const r = result.result
    if (r.degraded) return `已降级：${r.detail}（本次由规则引擎生成，未使用云端模型）`
    return r.allowed ? `调用成功：${r.detail}` : r.detail
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) return err.message
    throw err
  }
}
