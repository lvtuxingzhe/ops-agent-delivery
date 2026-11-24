<script setup lang="ts">
/**
 * 角色工作台：模板与入口。
 *
 * 数据和权限判断在 ./home/useHome.ts。
 */

import { useHome } from './home/useHome'

const {
  router,
  me,
  recent,
  investigationCounts,
  deploymentCounts,
  tokens,
  error,
  switchBusy,
  isAdmin,
  canAudit,
  canSubmit,
  canDeploy,
  pendingTotal,
  taskTotal,
  investigationShare,
  deploymentShare,
  tokenDays,
  tokenBudgetWarning,
  dateText,
  handleSwitch,
  formatTime,
  formatTokens,
  formatCost,
  formatUnitPrice,
  statusClass,
  statusText,
  openTask,
} = useHome()
</script>

<template>
  <div class="page-stack">
    <p v-if="error" class="error">{{ error }}</p>

    <section class="overview-band">
      <div class="overview-top">
        <div class="overview-copy">
          <p class="home-date">{{ dateText }}</p>
          <h2>任务概览</h2>
        </div>
        <div v-if="canSubmit || canDeploy || canAudit" class="home-actions">
          <button v-if="canSubmit" class="primary" type="button" @click="router.push('/tasks/new')">
            新建排查
          </button>
          <button v-if="canDeploy" class="ghost deploy-action" type="button" @click="router.push('/deployments')">
            新建部署
          </button>
          <button v-if="isAdmin && pendingTotal" class="ghost" type="button" @click="router.push('/approvals')">
            去审批
            <span class="head-count">{{ pendingTotal }}</span>
          </button>
          <button v-if="canAudit" class="ghost" type="button" @click="router.push('/audit')">
            查审计
          </button>
        </div>
      </div>

      <div class="task-groups">
        <section class="task-group group-investigation">
          <div class="group-head">
            <div class="group-label">
              <span class="group-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24">
                  <path d="M6 4h8l4 4v12H6z" />
                  <path d="M14 4v4h4M9 13h6M9 16h4" />
                </svg>
              </span>
              <span class="group-copy">
                <strong>排查任务</strong>
                <small>INVESTIGATION</small>
              </span>
            </div>
            <button class="group-link" type="button" @click="router.push('/tasks')">
              查看排查 <span aria-hidden="true">→</span>
            </button>
          </div>
          <div class="metric-strip">
            <button class="metric-cell" type="button" @click="router.push('/tasks')">
              <span class="cell-top">
                <span class="cell-label">待审批</span>
                <span class="cell-state" :class="investigationCounts.awaiting ? 'is-action' : 'is-clear'">
                  {{ investigationCounts.awaiting ? 'ACTION' : 'CLEAR' }}
                </span>
              </span>
              <span class="cell-num" :class="{ 'is-zero': investigationCounts.awaiting === 0 }">
                {{ investigationCounts.awaiting }}
              </span>
              <span class="cell-note">
                {{ investigationCounts.awaiting ? '需要你确认后才能执行' : '没有待审批，执行链路正常' }}
              </span>
            </button>
            <button class="metric-cell" type="button" @click="router.push('/tasks')">
              <span class="cell-top">
                <span class="cell-label">处理中</span>
                <span class="cell-state" :class="investigationCounts.running ? 'is-live' : 'is-idle'">
                  {{ investigationCounts.running ? 'LIVE' : 'IDLE' }}
                </span>
              </span>
              <span class="cell-num" :class="{ 'is-zero': investigationCounts.running === 0 }">
                {{ investigationCounts.running }}
              </span>
              <span class="cell-note">
                {{ investigationCounts.running ? 'Agent 正在查，或正在执行' : '没有正在执行的排查' }}
              </span>
            </button>
            <button class="metric-cell cell-total" type="button" @click="router.push('/tasks')">
              <span class="cell-top">
                <span class="cell-label">全部排查</span>
              </span>
              <span class="cell-num" :class="{ 'is-zero': investigationCounts.total === 0 }">
                {{ investigationCounts.total }}
              </span>
            </button>
          </div>
        </section>

        <section class="task-group group-deployment">
          <div class="group-head">
            <div class="group-label">
              <span class="group-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24">
                  <path d="m12 3 8 5v8l-8 5-8-5V8z" />
                  <path d="m4 8 8 5 8-5M12 13v8" />
                </svg>
              </span>
              <span class="group-copy">
                <strong>部署任务</strong>
                <small>DEPLOYMENT</small>
              </span>
            </div>
            <button class="group-link" type="button" @click="router.push('/deployments')">
              查看部署 <span aria-hidden="true">→</span>
            </button>
          </div>
          <div class="metric-strip">
            <button class="metric-cell" type="button" @click="router.push('/deployments')">
              <span class="cell-top">
                <span class="cell-label">待确认</span>
                <span class="cell-state" :class="deploymentCounts.awaiting ? 'is-action' : 'is-clear'">
                  {{ deploymentCounts.awaiting ? 'ACTION' : 'CLEAR' }}
                </span>
              </span>
              <span class="cell-num" :class="{ 'is-zero': deploymentCounts.awaiting === 0 }">
                {{ deploymentCounts.awaiting }}
              </span>
              <span class="cell-note">
                {{ deploymentCounts.awaiting ? '确认方案后才会开始执行' : '没有待确认的部署' }}
              </span>
            </button>
            <button class="metric-cell" type="button" @click="router.push('/deployments')">
              <span class="cell-top">
                <span class="cell-label">执行中</span>
                <span class="cell-state" :class="deploymentCounts.running ? 'is-live' : 'is-idle'">
                  {{ deploymentCounts.running ? 'LIVE' : 'IDLE' }}
                </span>
              </span>
              <span class="cell-num" :class="{ 'is-zero': deploymentCounts.running === 0 }">
                {{ deploymentCounts.running }}
              </span>
              <span class="cell-note">
                {{ deploymentCounts.running ? '部署正在规划或执行命令' : '没有正在执行的部署' }}
              </span>
            </button>
            <button class="metric-cell cell-total" type="button" @click="router.push('/deployments')">
              <span class="cell-top">
                <span class="cell-label">全部部署</span>
              </span>
              <span class="cell-num" :class="{ 'is-zero': deploymentCounts.total === 0 }">
                {{ deploymentCounts.total }}
              </span>
            </button>
          </div>
        </section>
      </div>

      <div v-if="taskTotal" class="task-mix" role="group" aria-label="任务构成">
        <div class="mix-track">
          <span class="mix-seg mix-investigation" :style="{ width: `${investigationShare}%` }" />
          <span class="mix-seg mix-deployment" :style="{ width: `${deploymentShare}%` }" />
        </div>
        <div class="mix-legend">
          <span><i class="mix-dot is-investigation" />排查 {{ investigationCounts.total }}</span>
          <span class="mix-total">共 {{ taskTotal }} 个</span>
          <span><i class="mix-dot is-deployment" />部署 {{ deploymentCounts.total }}</span>
        </div>
      </div>
    </section>

    <section v-if="isAdmin && pendingTotal" class="approval-banner" aria-live="polite">
      <div class="approval-main">
        <span class="approval-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M12 3 4 6v6c0 4 3.4 7.6 8 9 4.6-1.4 8-5 8-9V6z" />
            <path d="M12 8v4M12 16h.01" />
          </svg>
        </span>
        <div>
          <strong>{{ pendingTotal }} 个任务等待审批或确认</strong>
          <p>排查处置和部署命令确认后才会继续；高风险动作仍需人工审批后执行。</p>
        </div>
      </div>
    </section>

    <section v-if="tokens" class="card token-card" aria-labelledby="token-panel-title">
      <div class="token-head">
        <div>
          <p class="token-kicker">MODEL USAGE</p>
          <h2 id="token-panel-title">Token 用量</h2>
          <p class="desc">
            {{ tokens.amount_visible ? '金额按模型单价估算，不是账单；改价只影响新任务。' : '只显示 token 用量；金额仅管理员和审计员可见。' }}
          </p>
        </div>
        <span v-if="tokens.pricing_source" class="token-model">
          {{ tokens.pricing_source.model_name || tokens.pricing_source.model_key || '未设置默认模型' }}
          <small v-if="tokens.pricing_source.configured">
            输入 {{ formatUnitPrice(tokens.pricing_source.input_price_per_1k) }}
            / 输出 {{ formatUnitPrice(tokens.pricing_source.output_price_per_1k) }}
            元 / 千 token
          </small>
          <small v-else>未配单价</small>
        </span>
      </div>

      <p v-if="tokenBudgetWarning" class="token-warning" role="status">
        {{ tokenBudgetWarning }}。预算只提醒，不停止任务。
      </p>

      <div class="token-metrics">
        <div class="token-metric">
          <span>今日 Token</span>
          <strong>{{ formatTokens(tokens.today.total_tokens) }}</strong>
          <small>{{ tokens.today.tasks }} 个任务</small>
        </div>
        <div class="token-metric">
          <span>本月 Token</span>
          <strong>{{ formatTokens(tokens.month.total_tokens) }}</strong>
          <small>{{ tokens.month.tasks }} 个任务</small>
        </div>
        <div class="token-metric">
          <span>今日估算</span>
          <strong>{{ formatCost(tokens.today.cost) }}</strong>
          <small>{{ tokens.today.unpriced_tasks ? `${tokens.today.unpriced_tasks} 个任务缺单价` : '按配置单价（元 / 千 token）' }}</small>
        </div>
        <div class="token-metric" :class="{ 'is-warn': tokens.budget.daily_exceeded }">
          <span>今日预算</span>
          <strong>{{ tokens.budget.per_day_tokens ? formatTokens(tokens.budget.per_day_tokens) : '未设置' }}</strong>
          <small>{{ tokens.budget.per_day_tokens ? (tokens.budget.daily_exceeded ? '已超限' : '未超限') : '在模型页设置' }}</small>
        </div>
      </div>

      <div class="token-lower">
        <div class="token-chart" role="img" aria-label="最近 7 天 Token 用量柱状图">
          <div v-for="day in tokenDays" :key="day.date" class="token-bar">
            <span class="token-bar-track">
              <i :style="{ height: `${day.height}%` }" :title="`${day.date}：${formatTokens(day.total_tokens)} token`" />
            </span>
            <small>{{ day.date.slice(5) }}</small>
          </div>
        </div>
        <div class="token-recent">
          <strong>最近任务</strong>
          <button v-for="item in tokens.recent" :key="item.id" type="button" @click="router.push(`/tasks/${item.id}`)">
            <span>{{ item.title || '（无标题）' }}</span>
            <small>{{ formatTokens(item.total_tokens) }} token</small>
          </button>
          <p v-if="!tokens.recent.length" class="empty">还没有已记账的排查任务。</p>
        </div>
      </div>
    </section>

    <section class="card recent-card">
      <div class="recent-head">
        <div>
          <p class="recent-kicker">ACTIVITY</p>
          <h2>最近排查</h2>
          <p class="desc">只显示最近 5 个，完整列表在“排查”页。</p>
        </div>
        <button class="plain" type="button" @click="router.push('/tasks')">查看全部</button>
      </div>

      <div v-if="recent.length" class="recent-list">
        <button v-for="task in recent" :key="task.id" class="recent-row" type="button" @click="openTask(task)">
          <span class="recent-main">
            <strong>{{ task.title || '（无标题）' }}</strong>
            <small>{{ formatTime(task.created_at) }} · {{ task.submitted_by_name || task.submitted_by }}</small>
          </span>
          <span class="badge" :class="statusClass(task.status)">{{ statusText(task) }}</span>
        </button>
      </div>
      <p v-else class="empty">还没有排查。点右上角“新建排查”开始。</p>
    </section>

    <details v-if="isAdmin" class="admin-tools">
      <summary>管理员设置</summary>
      <div class="switch-row">
        <div class="switch-copy">
          <strong>自审批豁免：{{ me?.self_approval_switch ? '已开启' : '已关闭' }}</strong>
          <small>默认关闭，申请人和审批人必须是两个账号；开启后管理员可以批准自己提交的排查，每次自审批都会写入审计。</small>
        </div>
        <button
          type="button"
          class="toggle"
          :class="{ on: me?.self_approval_switch }"
          :disabled="switchBusy"
          :aria-pressed="Boolean(me?.self_approval_switch)"
          @click="handleSwitch(!me?.self_approval_switch)"
        >
          <span class="knob" />
        </button>
      </div>
    </details>
  </div>
</template>

<style scoped src="./home/page.css"></style>
