/** 站内通知接口 + 飞书/邮件渠道配置。 */

import { del, get, post, put } from './http'

export interface NotificationItem {
  id: number
  type: string
  title: string
  body: string
  task_id: string | null
  read_at: string | null
  created_at: string | null
}

export interface NotificationList {
  items: NotificationItem[]
  unread_count: number
}

export function fetchNotifications(unreadOnly = false): Promise<NotificationList> {
  const params = new URLSearchParams({
    unread_only: unreadOnly ? 'true' : 'false',
    limit: '20',
  })
  return get<NotificationList>(`/notifications?${params.toString()}`)
}

export function markNotificationRead(id: number): Promise<{ id: number; read_at: string | null }> {
  return post<{ id: number; read_at: string | null }>(`/notifications/${id}/read`)
}

export function markAllNotificationsRead(): Promise<{ updated: number }> {
  return post<{ updated: number }>('/notifications/read-all')
}

export function markTaskNotificationsRead(taskId: string): Promise<{ updated: number }> {
  return post<{ updated: number }>(`/notifications/tasks/${encodeURIComponent(taskId)}/read`)
}

/** 渠道配置里只出现存在标记，永远不出现 SMTP 密码。 */
export interface EmailChannel {
  enabled: boolean
  configured: boolean
  smtp_host: string
  smtp_port: number
  smtp_user: string
  from_addr: string
  to_addrs: string[]
  use_tls: boolean
  password_set: boolean
}

export interface NotificationChannels {
  email: EmailChannel
}

export interface EmailChannelForm {
  enabled: boolean
  smtp_host: string
  smtp_port: number
  smtp_user: string
  password?: string
  from_addr: string
  to_addrs: string
  use_tls: boolean
}

export function fetchNotificationChannels(): Promise<{ channels: NotificationChannels }> {
  return get<{ channels: NotificationChannels }>('/notifications/channels')
}

export function saveEmailChannel(form: EmailChannelForm): Promise<{ item: EmailChannel }> {
  return put<{ item: EmailChannel }>('/notifications/channels/email', form)
}

export function testNotificationChannel(
  channel: 'email',
): Promise<{ channel: string; ok: boolean; detail: string }> {
  return post<{ channel: string; ok: boolean; detail: string }>(
    `/notifications/channels/${channel}/test`,
  )
}

/** 飞书应用机器人：秘密字段只回“是否已配置”，不回显值。 */
export interface FeishuAppConfig {
  enabled: boolean
  app_id: string
  mode: 'long_connection' | 'webhook'
  chat_id: string
  chat_ids: string[]
  app_secret_set: boolean
  verification_token_set: boolean
  encrypt_key_set: boolean
  last_test_at: string
  last_test_ok: boolean
  last_test_detail: string
  worker_running: boolean
  worker_pid: number
  worker_started_at: string
  worker_error: string
}

export interface FeishuAppForm {
  enabled: boolean
  app_id: string
  app_secret?: string
  mode: 'long_connection' | 'webhook'
  chat_id?: string
  chat_ids: string[]
  verification_token?: string
  encrypt_key?: string
}

export interface FeishuChat {
  chat_id: string
  name: string
}

export function fetchFeishuApp(): Promise<{ item: FeishuAppConfig }> {
  return get<{ item: FeishuAppConfig }>('/notifications/channels/feishu-app')
}

export function saveFeishuApp(form: FeishuAppForm): Promise<{ item: FeishuAppConfig }> {
  return put<{ item: FeishuAppConfig }>('/notifications/channels/feishu-app', form)
}

export function fetchFeishuChats(): Promise<{ items: FeishuChat[] }> {
  return get<{ items: FeishuChat[] }>('/notifications/channels/feishu-app/chats')
}

export function testFeishuApp(): Promise<{
  ok: boolean
  message_id: string
  message_ids: string[]
}> {
  return post<{ ok: boolean; message_id: string; message_ids: string[] }>(
    '/notifications/channels/feishu-app/test',
  )
}

export interface FeishuMember {
  open_id: string
  name: string
  bound: boolean
  user_id: number
  user_name: string
}

export interface FeishuIdentity {
  id: string
  open_id: string
  user_id: number
  feishu_name: string
  username: string
  user_name: string
  created_at: string
}

export function fetchFeishuMembers(): Promise<{ items: FeishuMember[] }> {
  return get<{ items: FeishuMember[] }>('/notifications/channels/feishu-app/members')
}

export function fetchFeishuIdentities(): Promise<{ items: FeishuIdentity[] }> {
  return get<{ items: FeishuIdentity[] }>('/notifications/channels/feishu-app/identities')
}

export function bindFeishuIdentity(body: {
  open_id: string
  user_id: number
  display_name?: string
}): Promise<FeishuIdentity> {
  return post<FeishuIdentity>('/notifications/channels/feishu-app/identities', body)
}

export function unbindFeishuIdentity(identityId: string): Promise<{ revoked: boolean }> {
  return del<{ revoked: boolean }>(
    `/notifications/channels/feishu-app/identities/${identityId}`,
  )
}

/** 告警自动建单预检结果：不真正建任务。 */
export interface AlertPreviewResult {  would_create: boolean
  reason: string
  reason_text: string
  host_registered: boolean
  host_token: string
  enabled: boolean
  allow_hosts: string[]
  allow_alertnames: string[]
}

export function previewAlertPolicy(form: {
  host: string
  alertname: string
  severity: string
}): Promise<AlertPreviewResult> {
  return post<AlertPreviewResult>('/notifications/alerts/preview', form)
}

export interface OnCallShift {
  id: string
  user_id: number
  user_name: string
  scope: 'approval' | 'alert'
  scope_name: string
  starts_at: string
  ends_at: string
  note: string
  is_current: boolean
}

export interface OnCallShiftForm {
  user_id: number
  scope: 'approval' | 'alert'
  starts_at: string
  ends_at: string
  note?: string
}

export function fetchOnCallShifts(): Promise<{ items: OnCallShift[] }> {
  return get<{ items: OnCallShift[] }>('/notifications/oncall')
}

export function createOnCallShift(form: OnCallShiftForm): Promise<OnCallShift> {
  return post<OnCallShift>('/notifications/oncall', form)
}

export function deleteOnCallShift(shiftId: string): Promise<{
  id: string
  deleted: boolean
  already: boolean
}> {
  return del<{ id: string; deleted: boolean; already: boolean }>(
    `/notifications/oncall/${shiftId}`,
  )
}
