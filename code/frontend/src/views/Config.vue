<script setup lang="ts">
/**
 * 配置页 —— 只做「进门」与「摆版面」，具体三块各在自己的面板里：
 *
 *   DatasourcePanel  数据源清单 / 新建编辑 / 启停 / 健康检查
 *   LlmPanel         厂商模板 / 模型配置 / 新建编辑 / 测试连通
 *   HostSettingsPanel 主机字段名/显示/顺序 + 密码集中管理
 *   SettingsPanel    平台开关 / 允许触达的网段
 *   UserAdminPanel   用户与权限：建号 / 角色 / 密码 / TOTP / 手机号
 *   NotificationPanel 飞书 webhook / SMTP 邮件 / 测试发送
 *   AppearancePanel  开源字体 / 字号 / 登录背景 / OA 品牌块背景
 *
 * 权限：路由只挡「没登录」；本页挂载后拉 /auth/me，不是管理员就只显示
 * 一句「无权访问」—— 真正的门在后台每个接口上，这里藏按钮只是体验，
 * 绕过页面直接调接口照样 403。
 *
 * 拆分理由：原来 673 行单文件，模板里三块业务互相夹着，改一处要在 400 行
 * 模板里找位置；拆完每个面板自带状态与动作，父页面只剩装配。
 */

import {
  computed, onMounted, ref,
} from 'vue'

import { type MeInfo } from '../api/auth'
import { getOrLogin } from '../api/http'
import AppearancePanel from '../components/config/AppearancePanel.vue'
import DatasourcePanel from '../components/config/DatasourcePanel.vue'
import HostSettingsPanel from '../components/config/HostSettingsPanel.vue'
import LlmPanel from '../components/config/LlmPanel.vue'
import NotificationPanel from '../components/config/NotificationPanel.vue'
import SettingsPanel from '../components/config/SettingsPanel.vue'
import UserAdminPanel from '../components/config/UserAdminPanel.vue'

const me = ref<MeInfo | null>(null)
const activeTab = ref<'datasource' | 'llm' | 'host' | 'user' | 'notification' | 'appearance' | 'settings'>('datasource')
const tabs = [
  { key: 'datasource', label: '数据源' },
  { key: 'llm', label: '模型' },
  { key: 'host', label: '主机配置' },
  { key: 'user', label: '用户与权限' },
  { key: 'notification', label: '通知' },
  { key: 'appearance', label: '外观' },
  { key: 'settings', label: '设置' },
] as const

const isAdmin = computed(() => me.value?.roles.includes('admin') ?? false)

onMounted(async () => {
  me.value = await getOrLogin<MeInfo>('/auth/me')
})
</script>

<template>
  <div class="config-page page-stack">
    <!-- 非管理员：只说一句，没有操作面 -->
    <section v-if="me && !isAdmin" class="card">
      <h2>无权访问</h2>
      <p class="desc">配置页只对管理员开放。数据源清单可向排查/审计角色开放查看（接口权限为准）。</p>
    </section>

    <template v-if="me && isAdmin">
      <nav class="config-tabs" role="tablist" aria-label="配置分组">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          class="config-tab"
          :class="{ active: activeTab === tab.key }"
          type="button"
          role="tab"
          :aria-selected="activeTab === tab.key"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </nav>

      <div v-show="activeTab === 'datasource'" class="config-panel">
        <DatasourcePanel />
      </div>
      <div v-show="activeTab === 'llm'" class="config-panel">
        <LlmPanel />
      </div>
      <div v-show="activeTab === 'host'" class="config-panel">
        <HostSettingsPanel />
      </div>
      <div v-show="activeTab === 'user'" class="config-panel">
        <UserAdminPanel />
      </div>
      <div v-show="activeTab === 'notification'" class="config-panel">
        <NotificationPanel />
      </div>
      <div v-show="activeTab === 'appearance'" class="config-panel">
        <AppearancePanel />
      </div>
      <div v-show="activeTab === 'settings'" class="config-panel">
        <SettingsPanel />
      </div>
    </template>
  </div>
</template>

<style src="../styles/config.css"></style>
