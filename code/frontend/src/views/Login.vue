<script setup lang="ts">
/**
 * 登录页 —— 控制台入口，不是营销页。
 *
 * 视觉目标：一张可配置的背景图 + 一块安静、克制的登录卡。背景图由管理员
 * 在「配置 → 登录页背景」上传，登录页通过 /api/public/login 公开读取；
 * 没有配置时用内置的深色网格渐变，不依赖任何外部图片。
 *
 * 安全红线不变：
 *   - 密码只在提交请求那一刻存在于内存，不缓存、不打印、不落存储；
 *   - 令牌放 sessionStorage（关窗口即消失）；
 *   - 登录方式由后台开关动态决定，前端不写死。
 */

import { useLogin } from './login/useLogin'

const {
  activeMethod,
  appName,
  backgroundStyle,
  busy,
  devCodeHint,
  error,
  fromDesktop,
  handleLogin,
  handleSendSms,
  password,
  phone,
  smsCode,
  smsCountdown,
  totpCode,
  username,
  visibleMethods,
} = useLogin()
</script>

<template>
  <main class="login-page">
    <div class="bg-image" :style="backgroundStyle" aria-hidden="true" />
    <div class="bg-grid" aria-hidden="true" />
    <div class="bg-veil" aria-hidden="true" />

    <header class="brand">
      <span class="brand-mark">OA</span>
      <div class="brand-copy">
        <strong>{{ appName }}</strong>
        <small>OPS CONTROL PLANE</small>
      </div>
      <span class="brand-status"><i />控制台</span>
    </header>

    <section class="login-stage">
      <div class="login-intro">
        <p class="kicker">AI OPS · HUMAN IN CONTROL</p>
        <h1>让 AI 先排查，<br />人只做决定。</h1>
        <p class="intro-copy">
          把日志、指标、主机和变更上下文交给 AI 归集：异常先被定位，证据先被整理，重复排障先被沉淀；
          高风险动作仍由人审批后执行。
        </p>
        <ul class="intro-points">
          <li><span>01</span>自动归集日志与指标</li>
          <li><span>02</span>分钟级定位异常</li>
          <li><span>03</span>排障经验沉淀成模板</li>
        </ul>
      </div>

      <div class="login-card">
        <p class="card-kicker">SECURE ACCESS</p>
        <h2>进入控制台</h2>
        <p class="subtitle">使用内网账号登录。登录方式由平台开关控制。</p>

        <div class="tabs" role="tablist" aria-label="登录方式">
          <button
            v-for="m in visibleMethods"
            :key="m.key"
            type="button"
            role="tab"
            class="tab"
            :class="{ active: activeMethod === m.key }"
            :aria-selected="activeMethod === m.key"
            @click="activeMethod = m.key"
          >
            <span>{{ m.label }}</span>
            <small>{{ m.hint }}</small>
          </button>
        </div>

        <form class="form" @submit.prevent="handleLogin">
          <template v-if="activeMethod === 'password' || activeMethod === 'totp'">
            <label>
              <span>用户名</span>
              <input v-model="username" autocomplete="username" spellcheck="false" placeholder="内网账号" />
            </label>
            <label>
              <span>密码</span>
              <input v-model="password" type="password" autocomplete="current-password" placeholder="••••••••" />
            </label>
            <label v-if="activeMethod === 'totp'">
              <span>动态口令</span>
              <input v-model="totpCode" inputmode="numeric" maxlength="6" spellcheck="false" placeholder="6 位数字" />
            </label>
          </template>

          <template v-else>
            <label>
              <span>手机号</span>
              <input v-model="phone" inputmode="numeric" spellcheck="false" placeholder="内网登记手机号" />
            </label>
            <label>
              <span>验证码</span>
              <span class="with-btn">
                <input v-model="smsCode" inputmode="numeric" maxlength="6" placeholder="6 位数字" />
                <button type="button" class="sms-btn" :disabled="smsCountdown > 0" @click="handleSendSms">
                  {{ smsCountdown > 0 ? `${smsCountdown}s` : '发送验证码' }}
                </button>
              </span>
            </label>
            <p v-if="devCodeHint" class="dev-hint">{{ devCodeHint }}</p>
          </template>

          <p v-if="error" class="error">{{ error }}</p>

          <button class="login-btn" type="submit" :disabled="busy">
            {{ busy ? '验证中…' : '登录控制台' }}
          </button>
        </form>

        <div class="card-foot">
          <span>{{ fromDesktop ? 'Windows 桌面版' : '网页版' }}</span>
          <span>同一后台 · 同一权限 · 同一审计</span>
        </div>
      </div>
    </section>

    <footer class="login-footer">
      <span>操作留痕 · 审批可追 · 命令可回滚</span>
      <span class="mono">OPS AGENT / v0.4</span>
    </footer>
  </main>
</template>

<style scoped src="./login/login.css"></style>
