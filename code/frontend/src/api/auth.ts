/**
 * 认证相关的接口封装 —— 只描述「调哪个接口、传什么」，不做业务判断。
 * 业务流程（发码 → 登录 → 拉身份）写在组件里，这样每个页面看得懂全貌。
 */

import { get, post } from './http'

/** 后台返回的登录方式开关。 */
export interface LoginMethods {
  password: boolean
  totp: boolean
  sms: boolean
}

/** 登录成功后的返回。 */
export interface LoginResult {
  token: string
  token_type: string
  expires_in_minutes: number
  user: { id: number; username: string; display_name: string; roles: string[] }
}

/** 当前会话身份（/auth/me）。 */
export interface MeInfo {
  id: number
  username: string
  display_name: string
  roles: string[]
  can_self_approve: boolean
  self_approval_switch: boolean
  totp_enabled: boolean
}

/** 自审批清单的一条记录。 */
export interface SelfApprovalItem {
  id: number
  task_id: string
  step_up_method: string | null
  created_at: string | null
  plan_fingerprint: string
  submitter: string
}

/** 短信发送结果（测试通道会带 dev_code，真实通道没有）。 */
export interface SmsSendResult {
  sent: boolean
  ttl_seconds: number
  dev_code?: string
  dev_note?: string
}

/** TOTP 绑定起点返回（二维码只露这一次）。 */
export interface TotpBindStart {
  secret: string
  otpauth_uri: string
  qr_svg: string
}

export function fetchLoginMethods(): Promise<LoginMethods> {
  return get<LoginMethods>('/auth/methods')
}

export function login(body: {
  method: string
  username?: string
  password?: string
  totp_code?: string
  phone?: string
  sms_code?: string
}): Promise<LoginResult> {
  return post<LoginResult>('/auth/login', body)
}

export function sendSmsCode(phone: string): Promise<SmsSendResult> {
  return post<SmsSendResult>('/auth/sms/send', { phone })
}

export function fetchMe(): Promise<MeInfo> {
  return get<MeInfo>('/auth/me')
}

export function fetchSelfApprovals(): Promise<{ items: SelfApprovalItem[] }> {
  return get<{ items: SelfApprovalItem[] }>('/auth/self-approvals')
}

export function setSelfApprovalSwitch(enabled: boolean): Promise<{ enabled: boolean }> {
  return post<{ enabled: boolean }>('/auth/self-approval-switch', { enabled })
}

export function logout(): Promise<{ logged_out: boolean }> {
  return post<{ logged_out: boolean }>('/auth/logout')
}

export function totpBindStart(): Promise<TotpBindStart> {
  return post<TotpBindStart>('/auth/totp/bind/start')
}

export function totpBindConfirm(code: string): Promise<{ bound: boolean }> {
  return post<{ bound: boolean }>('/auth/totp/bind/confirm', { code })
}
