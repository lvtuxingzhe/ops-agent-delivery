/**
 * 路由表（第 ④ 步：登录页 + 主页 + 配置页）。
 *
 * 守卫规则：没令牌只能待在登录页；有令牌不往登录页凑。
 * 配置页这里只挡「没登录」；是不是管理员由页面拉 /auth/me 自己判断，
 * 而真正的门在后台每个接口上 —— 手改 hash 混进配置页，接口照样 403。
 */

import { createRouter, createWebHashHistory } from 'vue-router'

import { getToken } from '../api/http'
import Login from '../views/Login.vue'

// 业务页面按路由懒加载：登录首屏只带外壳，不把报告、部署、知识库一起打进主包。
// 这些文件名保持原名，路由记录和组件名不变。
const Approvals = () => import('../views/Approvals.vue')
const Audit = () => import('../views/Audit.vue')
const Config = () => import('../views/Config.vue')
const Deployments = () => import('../views/Deployments.vue')
const Home = () => import('../views/Home.vue')
const HostForm = () => import('../views/HostForm.vue')
const Hosts = () => import('../views/Hosts.vue')
const Knowledge = () => import('../views/Knowledge.vue')
const KnowledgeGovernance = () => import('../views/KnowledgeGovernance.vue')
const NewTask = () => import('../views/NewTask.vue')
const TaskDetail = () => import('../views/TaskDetail.vue')
const TaskDisposition = () => import('../views/TaskDisposition.vue')
const Tasks = () => import('../views/Tasks.vue')

const router = createRouter({
  // 用 hash 模式：Electron 打包后是 file:// 协议，
  // history 模式在 file:// 下刷新会白屏，hash 模式两边都能正常工作。
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'login', component: Login },
    { path: '/home', name: 'home', component: Home },
    { path: '/tasks', name: 'tasks', component: Tasks },
    { path: '/tasks/new', name: 'task-new', component: NewTask },
    { path: '/tasks/:id', name: 'task-detail', component: TaskDetail },
    { path: '/tasks/:id/disposition', name: 'task-disposition', component: TaskDisposition },
    { path: '/deployments', name: 'deployments', component: Deployments },
    // 部署记录已并入部署页页签；旧地址保留重定向，不再单占导航。
    {
      path: '/deployments/records',
      redirect: (to) => ({ name: 'deployments', query: { ...to.query, tab: 'records' } }),
    },
    // 兼容旧书签：模板库已并入部署页，旧的独立地址重定向回去，不再单独占导航。
    { path: '/deployments/templates', redirect: { name: 'deployments', query: { tab: 'templates' } } },
    { path: '/approvals', name: 'approvals', component: Approvals },
    // 独立知识库：部署经验 / 排查报告 / 手工知识统一入口。
    { path: '/knowledge', name: 'knowledge', component: Knowledge },
    { path: '/knowledge/governance', name: 'knowledge-governance', component: KnowledgeGovernance },
    { path: '/audit', name: 'audit', component: Audit },
    { path: '/hosts', name: 'hosts', component: Hosts },
    { path: '/hosts/new', name: 'host-new', component: HostForm },
    { path: '/hosts/:id/edit', name: 'host-edit', component: HostForm },
    { path: '/config', name: 'config', component: Config },
  ],
})

router.beforeEach((to) => {
  const loggedIn = Boolean(getToken())
  if (['home', 'tasks', 'task-new', 'task-detail', 'task-disposition', 'deployments', 'approvals', 'knowledge', 'knowledge-governance', 'audit', 'hosts', 'host-new', 'host-edit', 'config'].includes(String(to.name)) && !loggedIn) return { name: 'login' }
  if (to.name === 'login' && loggedIn) return { name: 'home' }
  return true
})

export default router
