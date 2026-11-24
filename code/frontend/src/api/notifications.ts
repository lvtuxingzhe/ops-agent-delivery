/** 站内通知接口 + 飞书/邮件渠道配置。 */

import { get, post, put } from './http'

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

/** 渠道配置里只出现存在标记，永远不出现 webhook / SMTP 密码。 */
export interface FeishuChannel {
  enabled: boolean
  configured: boolean
}

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
  feishu: FeishuChannel
  email: EmailChannel
}

export interface FeishuChannelForm {
  enabled: boolean
  webhook_url?: string
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

export function saveFeishuChannel(form: FeishuChannelForm): Promise<{ item: FeishuChannel }> {
  return put<{ item: FeishuChannel }>('/notifications/channels/feishu', form)
}

export function saveEmailChannel(form: EmailChannelForm): Promise<{ item: EmailChannel }> {
  return put<{ item: EmailChannel }>('/notifications/channels/email', form)
}

export function testNotificationChannel(
  channel: 'feishu' | 'email',
): Promise<{ channel: string; ok: boolean; detail: string }> {
  return post<{ channel: string; ok: boolean; detail: string }>(
    `/notifications/channels/${channel}/test`,
  )
}
