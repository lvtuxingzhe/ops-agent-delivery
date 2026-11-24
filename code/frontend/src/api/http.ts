/**
 * 统一请求出口 —— 前端所有接口调用的必经之路。
 *
 * 统一约束：
 *
 * 1. 只认相对路径 /api。开发期由 Vite 代理转发到后台，
 *    打包进 Electron 后同样走这个前缀，网页和桌面不用各写一套地址。
 * 2. 这里【不提供任意地址请求】。不开放填完整 URL 的入口，
 *    否则「桌面端不能直连生产」这条就守不住了。
 * 3. 登录令牌：存在 sessionStorage —— 关掉窗口/标签页自动消失，
 *    退出登录立刻清掉。不写 localStorage、不写文件：
 *    这就是「桌面端不能本地存长期凭据」的前端落法。
 * 4. 密码类字段只随请求发出去一次，本文件不做任何缓存。
 * 5. 统一在这一层带 X-Client-Kind 来源标记（web / desktop），
 *    供审计记录用。它不参与任何权限判断（后台也不信它做权限）。
 */

/** 后端接口统一前缀，别在组件里另写完整地址。 */
const API_PREFIX = '/api'

/** 会话令牌在 sessionStorage 里的键名。集中一处，退出时好清理。 */
const TOKEN_KEY = 'ops_session_token'

/** 取当前令牌（可能为空 = 未登录）。 */
export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

/** 登录成功后存令牌。 */
export function setToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
}

/** 退出 / 过期时清令牌。这就是「退出即清」的实现。 */
export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
}

/** 判断自己是不是跑在 Electron 壳里（preload 暴露的只读标记）。 */
function isDesktop(): boolean {
  return Boolean(window.desktopBridge)
}

/** 统一的请求头：来源标记 + 已登录时的令牌。 */
function buildHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'X-Client-Kind': isDesktop() ? 'desktop' : 'web',
    ...extra,
  }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

/**
 * 统一的非 2xx 错误。只保留后台给的 detail（已脱敏的话术），
 * 不把响应原文、内部堆栈贴到界面上。
 */
export class ApiError extends Error {
  constructor(public status: number, message: string, public code = '') {
    super(message)
  }
}

async function parseError(response: Response): Promise<never> {
  let detail = `请求失败（HTTP ${response.status}）`
  let code = ''
  try {
    const body = await response.json()
    // FastAPI 的 HTTPException 错误体有两种：detail 是字符串，或者
    // 本平台用户管理接口统一返回的 { code, message } 对象。
    if (body && typeof body.detail === 'string') detail = body.detail
    if (body && body.detail && typeof body.detail === 'object') {
      if (typeof body.detail.message === 'string') detail = body.detail.message
      if (typeof body.detail.code === 'string') code = body.detail.code
    }
  } catch {
    // 响应不是 JSON 就用默认话术，别把原文吐出去
  }
  throw new ApiError(response.status, detail, code)
}

/** 发 GET 请求。 */
export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: 'GET',
    headers: buildHeaders(),
    credentials: 'same-origin',
  })
  if (!response.ok) await parseError(response)
  return (await response.json()) as T
}

/**
 * 打开 SSE 长连接。
 *
 * 原生 EventSource 不能带 Authorization 头，所以这里仍走 fetch，
 * 由调用方读取 response.body 并解析 `text/event-stream`。
 */
export async function getEventStream(path: string, signal?: AbortSignal): Promise<Response> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: 'GET',
    headers: buildHeaders({ Accept: 'text/event-stream' }),
    credentials: 'same-origin',
    signal,
  })
  if (!response.ok) await parseError(response)
  return response
}

/** 下载类 GET：返回二进制与响应头；401 仍走统一错误。 */
export async function getBlobWithHeaders(path: string): Promise<{ blob: Blob; headers: Headers }> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: 'GET',
    headers: buildHeaders(),
    credentials: 'same-origin',
  })
  if (!response.ok) await parseError(response)
  return { blob: await response.blob(), headers: response.headers }
}

/** 下载类 GET：返回二进制而不是 JSON，401 仍走统一错误。 */
export async function getBlob(path: string): Promise<Blob> {
  const { blob } = await getBlobWithHeaders(path)
  return blob
}

/** 发 POST 请求（JSON 体）。登录、发验证码、改配置都走这里。 */
export async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: 'POST',
    headers: buildHeaders({ 'Content-Type': 'application/json' }),
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) await parseError(response)
  return (await response.json()) as T
}

/** 发 multipart POST：上传截图这类场景，不手工设置 Content-Type，浏览器自己带 boundary。 */
export async function postForm<T>(path: string, form: FormData): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: 'POST',
    headers: buildHeaders(),
    credentials: 'same-origin',
    body: form,
  })
  if (!response.ok) await parseError(response)
  return (await response.json()) as T
}

/** 发 PUT 请求（JSON 体）。第 ④ 步配置页改数据源用。 */
export async function put<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: 'PUT',
    headers: buildHeaders({ 'Content-Type': 'application/json' }),
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) await parseError(response)
  return (await response.json()) as T
}

/** 发 PATCH 请求（JSON 体）。用户管理改资料/角色/启停用。 */
export async function patch<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: 'PATCH',
    headers: buildHeaders({ 'Content-Type': 'application/json' }),
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) await parseError(response)
  return (await response.json()) as T
}

/** 发 DELETE 请求。知识库手工条目的删除入口用。 */
export async function del<T>(path: string): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: 'DELETE',
    headers: buildHeaders(),
    credentials: 'same-origin',
  })
  if (!response.ok) await parseError(response)
  return (await response.json()) as T
}

/** 401 时统一跳登录页 —— 会话过期不用每个页面自己判一遍。 */
export async function getOrLogin<T>(path: string): Promise<T | null> {
  try {
    return await get<T>(path)
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      clearToken()
      window.location.hash = '#/'
    }
    return null
  }
}
