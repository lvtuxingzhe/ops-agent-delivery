/**
 * 用户管理接口封装 —— 只描述「调哪个接口、传什么」。
 *
 * 权限判断不在这里：后台每个接口都要求 admin。前端拿到 403 时
 * 原样展示后台话术，不把按钮藏起来当成权限控制。
 */

import {
  del, get, patch, post, put,
} from './http'

export interface AdminUser {
  id: number
  username: string
  display_name: string
  roles: string[]
  is_active: boolean
  has_password: boolean
  totp_enabled: boolean
  phone_bound: boolean
  phone_masked: string
  created_at: string
}

export interface UserList {
  items: AdminUser[]
  total: number
  page: number
  size: number
}

export interface RoleItem {
  code: string
  name: string
  can_self_approve: boolean
}

export interface CreatedUser {
  id: number
  username: string
  display_name: string
  roles: string[]
  initial_password: string
  phone_masked: string
}

export function listUsers(params: {
  page?: number
  size?: number
  q?: string
  role?: string
  active?: boolean | null
} = {}): Promise<UserList> {
  const search = new URLSearchParams()
  search.set('page', String(params.page ?? 1))
  search.set('size', String(params.size ?? 20))
  if (params.q?.trim()) search.set('q', params.q.trim())
  if (params.role) search.set('role', params.role)
  if (params.active !== undefined && params.active !== null) {
    search.set('active', params.active ? 'true' : 'false')
  }
  return get<UserList>(`/admin/users?${search.toString()}`)
}

export function createUser(body: {
  username: string
  display_name?: string
  roles: string[]
  phone?: string
}): Promise<CreatedUser> {
  return post<CreatedUser>('/admin/users', body)
}

export function updateUser(userId: number, body: {
  display_name?: string
  roles?: string[]
  is_active?: boolean
}): Promise<AdminUser> {
  return patch<AdminUser>(`/admin/users/${userId}`, body)
}

export function resetPassword(userId: number): Promise<{ password: string }> {
  return post<{ password: string }>(`/admin/users/${userId}/reset-password`)
}

/** 软删除用户：页面隐藏、不能登录，历史任务和审计保留。 */
export function deleteUser(userId: number): Promise<{
  id: number
  username: string
  deleted: boolean
}> {
  return del<{ id: number; username: string; deleted: boolean }>(
    `/admin/users/${userId}`,
  )
}

export function resetTotp(userId: number): Promise<{ totp_enabled: boolean }> {
  return post<{ totp_enabled: boolean }>(`/admin/users/${userId}/reset-totp`)
}

export function bindPhone(userId: number, phone: string): Promise<{ phone_masked: string }> {
  return put<{ phone_masked: string }>(`/admin/users/${userId}/phone`, { phone })
}

export function unbindPhone(userId: number): Promise<{ phone_masked: string }> {
  return del<{ phone_masked: string }>(`/admin/users/${userId}/phone`)
}

export function listRoles(): Promise<RoleItem[]> {
  return get<RoleItem[]>('/admin/roles')
}
