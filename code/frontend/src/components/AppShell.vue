<script setup lang="ts">
/**
 * 全站外壳：左侧导航 + 顶部身份区。
 *
 * 只负责“框”，不接管页面业务。页面仍各自拉 /auth/me 做权限判断；
 * 这里拉一次只为了显示当前用户和退出按钮，接口失败不阻塞页面。
 */

import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchMe, logout, type MeInfo } from '../api/auth'
import { clearToken } from '../api/http'
import { syncAppearance } from '../utils/appearance'
import NotificationBell from './NotificationBell.vue'
import TotpBindDialog from './TotpBindDialog.vue'

const route = useRoute()
const router = useRouter()
const me = ref<MeInfo | null>(null)
const brandBackground = ref('')
const brandStyle = computed(() => {
  const url = brandBackground.value
  if (!url) return {}
  // 后端只允许 http(s) 或本站 /api/assets/；这里再转义引号，防止样式串断。
  return { backgroundImage: `url("${url.replace(/"/g, '%22')}")` }
})

const titles: Record<string, { title: string; kicker: string }> = {
  home: { title: '工作台', kicker: 'WORKSPACE' },
  tasks: { title: '排查', kicker: 'INVESTIGATIONS' },
  'task-new': { title: '新建排查', kicker: 'NEW INVESTIGATION' },
  'task-detail': { title: '排查详情', kicker: 'INVESTIGATION' },
  deployments: { title: '部署', kicker: 'DEPLOYMENTS' },
  knowledge: { title: '知识库', kicker: 'KNOWLEDGE' },
  'knowledge-governance': { title: '知识治理', kicker: 'KNOWLEDGE GOVERNANCE' },
  approvals: { title: '审批', kicker: 'APPROVALS' },
  audit: { title: '审计', kicker: 'AUDIT' },
  hosts: { title: '主机', kicker: 'HOSTS' },
  'host-new': { title: '新增主机', kicker: 'NEW HOST' },
  'host-edit': { title: '编辑主机', kicker: 'EDIT HOST' },
  config: { title: '配置', kicker: 'SETTINGS' },
}

const page = computed(() => titles[String(route.name)] ?? { title: '工作台', kicker: 'WORKSPACE' })
const isTaskList = computed(() => String(route.name ?? '') === 'tasks')
const isWorkspacePage = computed(() =>
  new Set([
    'tasks',
    'task-new',
    'task-detail',
    'approvals',
    'audit',
    'knowledge',
    'knowledge-governance',
    'hosts',
    'host-new',
    'host-edit',
    'config',
  ]).has(String(route.name ?? '')),
)
// 排查详情和新建排查都属于“排查”这一组；主机表单属于“主机”；路由不会自动高亮父级。
const activeSection = computed(() => {
  const name = String(route.name ?? '')
  if (name === 'task-detail' || name === 'task-new' || name === 'tasks') return 'tasks'
  if (name === 'host-new' || name === 'host-edit') return 'hosts'
  return name
})

const isAdmin = computed(() => (me.value?.roles ?? []).includes('admin'))
const canDeploy = computed(() => {
  const roles = me.value?.roles ?? []
  return roles.includes('admin') || roles.includes('investigator')
})
const canAudit = computed(() => {
  const roles = me.value?.roles ?? []
  return roles.includes('admin') || roles.includes('auditor')
})
// 知识库读权限与任务详情一致：admin / investigator / auditor / oncall 都能看。
const canReadKnowledge = computed(() => {
  const roles = me.value?.roles ?? []
  return ['admin', 'investigator', 'auditor', 'oncall'].some((role) => roles.includes(role))
})

const roleText = computed(() => {
  if (!me.value) return '加载中…'
  const labels: Record<string, string> = {
    admin: '管理员',
    auditor: '审计员',
    investigator: '排查员',
    oncall: '值班工程师',
    viewer: '只读',
  }
  return (me.value.roles ?? []).map((role) => labels[role] ?? role).join(' / ') || '—'
})

const initial = computed(() => {
  const name = me.value?.display_name || me.value?.username || '?'
  return name.slice(0, 1).toUpperCase()
})

const totpOpen = ref(false)

function totpBound() {
  if (me.value) me.value.totp_enabled = true
  totpOpen.value = false
}

onMounted(async () => {
  // 外观配置是公开视觉资源，失败不影响身份和业务页。
  void syncAppearance().then((config) => {
    brandBackground.value = config?.brand_background_url || ''
  })
  try {
    me.value = await fetchMe()
  } catch {
    // 顶栏身份显示失败不阻塞业务页；401 由路由守卫/业务请求处理
  }
})

async function handleLogout() {
  try {
    await logout()
  } catch {
    // 退出接口失败也清本地令牌，别把用户困住
  }
  clearToken()
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand" :class="{ 'has-image': brandBackground }" :style="brandStyle">
        <span class="brand-mark">OA</span>
        <div class="brand-text">
          <strong>运维 Agent</strong>
          <small>OPS CONTROL PLANE</small>
        </div>
      </div>

      <nav class="nav" aria-label="主导航">
        <RouterLink class="nav-item" to="/home" :class="{ 'is-active': activeSection === 'home' }">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 11 12 4l8 7v8a1 1 0 0 1-1 1h-5v-6H10v6H5a1 1 0 0 1-1-1z" />
          </svg>
          <span>工作台</span>
        </RouterLink>
        <RouterLink class="nav-item" to="/tasks" :class="{ 'is-active': activeSection === 'tasks' }">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 3h9l3 3v15H6z" />
            <path d="M9 10h6M9 14h6M9 18h4" />
          </svg>
          <span>排查</span>
        </RouterLink>
        <RouterLink v-if="canDeploy" class="nav-item" to="/deployments" :class="{ 'is-active': activeSection === 'deployments' }">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 4h14v4H5zM5 10h14v4H5zM5 16h14v4H5z" />
            <path d="M8 6h.01M8 12h.01M8 18h.01M12 6h5M12 12h5M12 18h5" />
          </svg>
          <span>部署</span>
        </RouterLink>
        <RouterLink v-if="canReadKnowledge" class="nav-item" to="/knowledge" :class="{ 'is-active': activeSection === 'knowledge' }">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v14H7a2 2 0 0 0-2 2z" />
            <path d="M5 19a2 2 0 0 1 2-2h12M9 7h6M9 11h6" />
          </svg>
          <span>知识库</span>
        </RouterLink>
        <RouterLink v-if="isAdmin" class="nav-item" to="/approvals" :class="{ 'is-active': activeSection === 'approvals' }">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 3 4 6v6c0 4 3.4 7.6 8 9 4.6-1.4 8-5 8-9V6z" />
            <path d="m8.5 12 2.3 2.3L16 9.5" />
          </svg>
          <span>审批</span>
        </RouterLink>
        <RouterLink v-if="canAudit" class="nav-item" to="/audit" :class="{ 'is-active': activeSection === 'audit' }">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 4h14v16H5z" />
            <path d="M8 8h8M8 12h8M8 16h5" />
          </svg>
          <span>审计</span>
        </RouterLink>

        <div v-if="isAdmin" class="nav-group">
          <span class="nav-group-label">管理</span>
          <RouterLink class="nav-item" to="/hosts" :class="{ 'is-active': activeSection === 'hosts' }">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="4" y="4" width="16" height="7" rx="1.5" />
              <rect x="4" y="14" width="16" height="6" rx="1.5" />
              <path d="M7 7.5h.01M7 17h.01M11 7.5h5M11 17h5" />
            </svg>
            <span>主机</span>
          </RouterLink>
          <RouterLink class="nav-item" to="/config" :class="{ 'is-active': activeSection === 'config' }">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="3" />
              <path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.3 1a7 7 0 0 0-1.7-1l-.3-2.5h-4l-.3 2.5a7 7 0 0 0-1.7 1l-2.3-1-2 3.4L5.1 11a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 1.7 1l.3 2.5h4l.3-2.5a7 7 0 0 0 1.7-1l2.3 1 2-3.4-2-1.5a7 7 0 0 0 .1-1z" />
            </svg>
            <span>配置</span>
          </RouterLink>
        </div>
      </nav>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <div class="page-title">
          <p>{{ page.kicker }}</p>
          <h1>{{ page.title }}</h1>
        </div>

        <div class="identity">
          <NotificationBell />
          <span class="avatar" :title="me?.display_name || me?.username || ''">{{ initial }}</span>
          <strong class="who" :title="me?.display_name || me?.username || ''">{{ roleText }}</strong>
          <button
            v-if="isAdmin"
            class="totp-entry"
            :class="{ bound: me?.totp_enabled }"
            type="button"
            title="当前登录管理员自己的动态口令；用于查看完整密码"
            @click="totpOpen = true"
          >
            动态口令
            <span>{{ me?.totp_enabled ? '已绑定' : '未绑定' }}</span>
          </button>
          <button class="logout" type="button" @click="handleLogout">退出</button>
        </div>
      </header>

      <div
        class="page-body"
        :class="{
          wide: activeSection === 'deployments',
          'tasks-page': isTaskList,
          'workspace-page': isWorkspacePage,
        }"
      >
        <slot />
      </div>
    </main>

    <TotpBindDialog
      :open="totpOpen"
      :enabled="Boolean(me?.totp_enabled)"
      @close="totpOpen = false"
      @bound="totpBound"
    />
  </div>
</template>

<style scoped>
.shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 236px minmax(0, 1fr);
  background: var(--bg);
}

.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 24px 14px 18px;
  color: #e2e8f0;
  background: var(--nav);
  border-right: 1px solid rgba(148, 163, 184, 0.12);
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 2px 8px;
}

/* 只有管理员配了品牌背景才扩展成一块完整图：暗色蒙层保证文字可读。 */
.brand.has-image {
  position: relative;
  isolation: isolate;
  padding: 12px 10px;
  border-radius: 12px;
  overflow: hidden;
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
}

.brand.has-image::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: -1;
  background: linear-gradient(90deg, rgba(6, 16, 31, 0.82), rgba(6, 16, 31, 0.46));
}

.brand.has-image .brand-mark,
.brand.has-image .brand-text {
  position: relative;
  z-index: 1;
}

.brand-mark {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  color: #fff;
  background: var(--accent);
  box-shadow: 0 8px 20px rgba(37, 99, 235, 0.25);
  font-size: calc(12px * var(--font-scale, 1));
  font-weight: 700;
  letter-spacing: 0.04em;
}

.brand-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.brand-text strong {
  font-size: calc(14px * var(--font-scale, 1));
  font-weight: 650;
  letter-spacing: 0.02em;
}

.brand-text small {
  color: #94a3b8;
  font-size: calc(9px * var(--font-scale, 1));
  letter-spacing: 0.14em;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.nav-group {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid rgba(148, 163, 184, 0.18);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.nav-group-label {
  padding: 0 12px 4px;
  color: #71839b;
  font-size: calc(9px * var(--font-scale, 1));
  font-weight: 700;
  letter-spacing: 0.14em;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: 9px;
  color: #aebdd3;
  font-size: calc(13px * var(--font-scale, 1));
  text-decoration: none;
  transition: color 0.15s ease, background 0.15s ease, border-color 0.15s ease;
}

.nav-item svg {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.nav-item:hover {
  color: #fff;
  background: rgba(148, 163, 184, 0.08);
  border-color: rgba(148, 163, 184, 0.1);
}

.nav-item.router-link-active,
.nav-item.is-active {
  color: #fff;
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.22), rgba(37, 99, 235, 0.05));
  border-color: rgba(96, 165, 250, 0.22);
}

.nav-item.router-link-active::before,
.nav-item.is-active::before {
  content: "";
  position: absolute;
  left: -14px;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: #3b82f6;
}

.workspace {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 17px 34px;
  background: rgba(255, 255, 255, 0.9);
  border-bottom: 1px solid var(--line);
  box-shadow: 0 6px 24px rgba(8, 20, 35, 0.04);
  backdrop-filter: blur(14px);
}

.page-title p {
  margin: 0 0 3px;
  color: var(--accent);
  font-size: calc(10px * var(--font-scale, 1));
  font-weight: 700;
  letter-spacing: 0.14em;
}

.page-title h1 {
  margin: 0;
  font-size: calc(26px * var(--font-scale, 1));
  line-height: 1.2;
  font-weight: 680;
  letter-spacing: -0.025em;
}

.identity {
  display: flex;
  align-items: center;
  gap: 10px;
}

.avatar {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--accent-ink);
  background: var(--accent-soft);
  font-size: calc(12px * var(--font-scale, 1));
  font-weight: 700;
}

.who {
  max-width: 180px;
  overflow: hidden;
  color: var(--ink);
  font-size: calc(12px * var(--font-scale, 1));
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.totp-entry {
  min-height: 32px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  color: var(--ink-2);
  background: var(--surface);
  font-size: calc(12px * var(--font-scale, 1));
  cursor: pointer;
}

.totp-entry span {
  padding: 1px 6px;
  border-radius: 999px;
  background: #fef3c7;
  color: #92400e;
  font-size: calc(11px * var(--font-scale, 1));
}

.totp-entry.bound span {
  background: #dcfce7;
  color: #166534;
}

.totp-entry:hover {
  border-color: #94a3b8;
  color: var(--ink);
}

.logout {
  min-height: 32px;
  padding: 6px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  color: var(--ink-2);
  background: var(--surface);
  font-size: calc(12px * var(--font-scale, 1));
  cursor: pointer;
}

.logout:hover {
  border-color: #94a3b8;
  color: var(--ink);
}

.page-body {
  width: min(1220px, calc(100% - 48px));
  margin: 0 auto;
  padding: 32px 10px 48px;
  overflow-x: clip;
}

.page-body.wide {
  width: min(1580px, calc(100% - 48px));
}

/* 宽屏业务页左对齐到顶栏标题，避免内容居中后整张表看起来偏右。 */
@media (min-width: 1400px) {
  .page-body.tasks-page,
  .page-body.workspace-page,
  .page-body.wide {
    width: min(1580px, calc(100% - 68px));
    margin-left: 34px;
    margin-right: 0;
    padding-left: 0;
  }

  .page-body.tasks-page,
  .page-body.workspace-page {
    width: min(1540px, calc(100% - 68px));
  }

  .page-body.workspace-page .page-stack {
    width: 100%;
    max-width: 1280px;
  }
}

@media (min-width: 1600px) {
  .page-body.workspace-page .page-stack {
    max-width: 1440px;
  }
}

/* 桌面窄窗口（产品浏览器常见 700～900px）仍保留侧栏，只把侧栏收窄。 */
@media (max-width: 980px) and (min-width: 681px) {
  .shell {
    grid-template-columns: 208px minmax(0, 1fr);
  }

  .sidebar {
    gap: 20px;
    padding: 20px 12px;
  }

  .brand {
    padding: 2px 4px;
  }

  .brand-text small {
    display: none;
  }

  .nav-item {
    gap: 9px;
    padding: 9px 10px;
    font-size: calc(12px * var(--font-scale, 1));
  }

  .topbar {
    padding: 22px 24px 14px;
  }

  .page-body {
    width: calc(100% - 28px);
  }
}

/* 手机宽度才退化成横向导航；桌面窗口不再把侧栏顶到顶部。 */
@media (max-width: 680px) {
  .shell {
    grid-template-columns: minmax(0, 1fr);
  }

  .sidebar {
    position: static;
    height: auto;
    flex-direction: row;
    align-items: center;
    gap: 14px;
    padding: 12px 16px;
  }

  .brand-text small,
  .sidebar-foot,
  .who {
    display: none;
  }

  .nav {
    flex: 1;
    min-width: 0;
    flex-direction: row;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .nav::-webkit-scrollbar {
    display: none;
  }

  .nav-group {
    flex-direction: row;
    gap: 6px;
    margin: 0;
    padding: 0;
    border: 0;
  }

  .nav-group-label {
    display: none;
  }

  .nav-item {
    padding: 8px 10px;
    white-space: nowrap;
  }

  .nav-item.router-link-active::before,
  .nav-item.is-active::before {
    display: none;
  }

  .topbar {
    padding: 16px 18px;
  }

  .page-title h1 {
    font-size: calc(20px * var(--font-scale, 1));
  }

  .page-body {
    width: calc(100% - 28px);
    padding-top: 20px;
    padding-bottom: 24px;
  }
}
</style>
