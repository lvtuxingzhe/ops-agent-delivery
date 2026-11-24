<script setup lang="ts">
/**
 * 《变更实施方案》展示组件。
 *
 * 只读渲染，不生成命令：模板方案和 AI 自主方案都把
 * precheck / operations / verify / rollback / post_rollback 变成人可读的步骤卡片。
 * 原始 argv 仍放在“技术详情”里，默认不打扰工程师。
 */

import { computed } from 'vue'

import type { DeployOperation, DeployPlan } from '../api/deployments'

const props = defineProps<{
  plan: DeployPlan
  title?: string
}>()

interface StepGroup {
  key: 'precheck' | 'apply' | 'verify' | 'rollback' | 'post_rollback'
  label: string
  hint: string
  items: DeployOperation[]
}

const groups = computed<StepGroup[]>(() => [
  { key: 'precheck', label: '变更前检查', hint: '变更开始前执行；只读，用于记录当前状态，失败时不会触发回滚', items: props.plan.precheck ?? [] },
  { key: 'apply', label: '变更步骤', hint: '按顺序执行；任何一条失败都会跳过验证并进入回滚', items: props.plan.operations ?? [] },
  { key: 'verify', label: '验证步骤', hint: '变更成功后才执行；只验证变更后的状态，结果必须满足预期', items: props.plan.verify ?? [] },
  { key: 'rollback', label: '回滚步骤', hint: '变更或验证失败时执行；危险变更必须有回滚', items: props.plan.rollback ?? [] },
  { key: 'post_rollback', label: '回滚后验证', hint: '回滚执行后运行；只读，用于确认是否恢复原状', items: props.plan.post_rollback ?? [] },
])

const visibleGroups = computed(() => groups.value.filter((group) =>
  group.items.length || ['apply', 'verify', 'rollback'].includes(group.key),
))

const summary = computed(() => String(props.plan.summary || '').trim())
const risks = computed(() => (props.plan.risks ?? []).filter((item) => item.label))

function commandText(op: DeployOperation): string {
  return op.command || (op.argv ?? []).join(' ')
}

function stepTitle(op: DeployOperation, index: number): string {
  if (op.title) return op.title
  const argv = op.argv ?? []
  const command = argv.slice(0, 2).join(' ')
  return command ? `${roleText(op.role)} · ${command}` : `第 ${index + 1} 步`
}

function roleText(role?: string): string {
  if (role === 'precheck') return '变更前检查'
  if (role === 'verify') return '验证'
  if (role === 'rollback') return '回滚'
  if (role === 'post_rollback') return '回滚后验证'
  return '变更'
}

function riskText(risk?: string): string {
  return risk === 'high' ? '高风险' : risk === 'low' ? '低风险' : '中风险'
}

function riskClass(risk?: string): string {
  return risk === 'high' ? 'risk-high' : risk === 'low' ? 'risk-low' : 'risk-medium'
}

function expectText(op: DeployOperation): string {
  const expect = op.expect
  if (!expect || typeof expect !== 'object') return '退出码 0'
  const entries = Object.entries(expect)
  if (!entries.length) return '退出码 0'
  return entries
    .map(([key, value]) => {
      if (key === 'exit_code') return `退出码 ${String(value)}`
      if (key === 'stdout_contains') return `输出包含「${String(value)}」`
      if (key === 'stderr_contains') return `错误输出包含「${String(value)}」`
      return `${key}：${String(value)}`
    })
    .join('；')
}

const technical = computed(() => JSON.stringify({
  plan_kind: props.plan.plan_kind || 'template',
  host: props.plan.host,
  platform: props.plan.platform,
  os_family: props.plan.os_family,
  plan_hash: props.plan.plan_hash,
  precheck: props.plan.precheck,
  operations: props.plan.operations,
  verify: props.plan.verify,
  rollback: props.plan.rollback,
  post_rollback: props.plan.post_rollback,
}, null, 2))
</script>

<template>
  <section class="plan-doc">
    <header class="plan-doc-head">
      <div>
        <p class="kicker">CHANGE IMPLEMENTATION PLAN</p>
        <h3>{{ title || plan.title || plan.template_id || '变更实施方案' }}</h3>
      </div>
      <span :class="['tag', plan.plan_kind === 'ai_freeform' ? 'tag-warn' : 'tag-off']">
        {{ plan.plan_kind === 'ai_freeform' ? 'AI 自主方案' : '模板方案' }}
      </span>
    </header>

    <p v-if="summary" class="plan-summary">{{ summary }}</p>
    <p v-else class="plan-summary muted">
      这份方案由模板确定性生成；命令、验证、回滚和方案指纹在确认前都已固定。
    </p>

    <div v-if="risks.length" class="plan-risks">
      <span class="risk-label">风险提示</span>
      <span v-for="item in risks" :key="item.label" :class="['risk-chip', riskClass(item.level)]">
        {{ item.label }} · {{ riskText(item.level) }}
      </span>
    </div>

    <div class="plan-groups">
      <section v-for="group in visibleGroups" :key="group.key" class="plan-group">
        <div class="plan-group-head">
          <h4>{{ group.label }}</h4>
          <span>{{ group.items.length }} 条</span>
        </div>
        <p class="plan-group-hint">{{ group.hint }}</p>
        <p v-if="!group.items.length" class="empty">方案里没有这一类步骤。</p>
        <ol v-else class="plan-steps">
          <li
            v-for="(op, index) in group.items"
            :key="`${group.key}-${index}`"
            :class="['plan-step', op.risk ? riskClass(op.risk) : '']"
          >
            <span class="step-index">{{ String(index + 1).padStart(2, '0') }}</span>
            <div class="step-body">
              <div class="step-head">
                <h5>{{ stepTitle(op, index) }}</h5>
                <span v-if="op.risk" :class="['risk-badge', riskClass(op.risk)]">{{ riskText(op.risk) }}</span>
              </div>
              <p v-if="op.description" class="step-desc">{{ op.description }}</p>
              <code class="step-command">{{ commandText(op) }}</code>
              <p v-if="['precheck', 'verify', 'post_rollback'].includes(group.key)" class="step-expect">
                预期：{{ expectText(op) }}
              </p>
              <p v-if="op.rollback_note" class="step-rollback">
                回滚说明：{{ op.rollback_note }}
              </p>
            </div>
          </li>
        </ol>
      </section>
    </div>

    <details class="plan-technical">
      <summary>技术详情（argv、plan_hash、原始结构）</summary>
      <pre>{{ technical }}</pre>
    </details>
  </section>
</template>

<style scoped>
.plan-doc {
  display: grid;
  gap: 18px;
}

.plan-doc-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.plan-doc-head h3 {
  margin: 4px 0 0;
  font-size: calc(18px * var(--font-scale, 1));
  letter-spacing: -0.01em;
}

.plan-summary {
  margin: 0;
  padding: 12px 14px;
  color: var(--ink-2);
  background: var(--surface-soft);
  border-left: 3px solid #2563eb;
  border-radius: 0 10px 10px 0;
  font-size: calc(13px * var(--font-scale, 1));
  line-height: 1.75;
}

.plan-risks {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.risk-label {
  color: var(--ink-3);
  font-size: calc(11px * var(--font-scale, 1));
  font-weight: 650;
  letter-spacing: 0.08em;
}

.risk-chip {
  padding: 3px 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: calc(11px * var(--font-scale, 1));
}

.risk-low { color: #15803d; background: #f0fdf4; border-color: #bbf7d0; }
.risk-medium { color: #a16207; background: #fffbeb; border-color: #fde68a; }
.risk-high { color: #b42318; background: #fff1f0; border-color: #fecaca; }

.plan-groups {
  display: grid;
  gap: 18px;
}

.plan-group {
  display: grid;
  gap: 8px;
}

.plan-group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 7px;
  border-bottom: 1px solid var(--line);
}

.plan-group-head h4 {
  margin: 0;
  font-size: calc(13px * var(--font-scale, 1));
}

.plan-group-head span {
  color: var(--ink-3);
  font-size: calc(11px * var(--font-scale, 1));
}

.plan-group-hint {
  margin: 0;
  color: var(--ink-3);
  font-size: calc(11px * var(--font-scale, 1));
}

.plan-steps {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.plan-step {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 10px;
  padding: 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 3px solid var(--line);
  border-radius: 10px;
}

.plan-step.risk-high { border-left-color: #dc2626; }
.plan-step.risk-medium { border-left-color: #d97706; }
.plan-step.risk-low { border-left-color: #16a34a; }

.step-index {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  color: var(--ink-3);
  background: var(--surface-soft);
  border-radius: 50%;
  font-family: var(--mono);
  font-size: calc(11px * var(--font-scale, 1));
}

.step-body {
  min-width: 0;
  display: grid;
  gap: 7px;
}

.step-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.step-head h5 {
  margin: 0;
  font-size: calc(13px * var(--font-scale, 1));
  line-height: 1.45;
}

.risk-badge {
  flex: 0 0 auto;
  padding: 2px 7px;
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: calc(10px * var(--font-scale, 1));
  white-space: nowrap;
}

.step-desc,
.step-expect,
.step-rollback {
  margin: 0;
  color: var(--ink-3);
  font-size: calc(11px * var(--font-scale, 1));
  line-height: 1.65;
}

.step-command {
  display: block;
  max-width: 100%;
  padding: 9px 10px;
  overflow-x: auto;
  color: #dbeafe;
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 8px;
  font-family: var(--mono);
  font-size: calc(12px * var(--font-scale, 1));
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.plan-technical {
  padding: 10px 12px;
  background: var(--surface-soft);
  border: 1px dashed var(--line);
  border-radius: 10px;
}

.plan-technical summary {
  color: var(--ink-3);
  font-size: calc(11px * var(--font-scale, 1));
  cursor: pointer;
}

.plan-technical pre {
  max-height: 360px;
  margin: 10px 0 0;
  overflow: auto;
  color: var(--ink-2);
  font-size: calc(11px * var(--font-scale, 1));
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 640px) {
  .plan-doc-head { flex-direction: column; }
  .plan-step { grid-template-columns: 24px minmax(0, 1fr); padding: 10px; }
  .step-index { width: 22px; height: 22px; }
}
</style>
