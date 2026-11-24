<script setup lang="ts">
/**
 * 部署任务页：选模板 → 生成带指纹的方案 → 一次性确认 → 审批执行 → 看验证/回滚。
 *
 * 页面只做展示和转发，所有强制校验在后台：
 * plan_hash 由后台算、确认令牌由后台签、审批后 worker 还会复验模板和 TTL。
 */

import { DEPLOY_RESULTS, DEPLOY_TYPES } from '../api/deployments'
import DeploymentPlanSteps from '../components/DeploymentPlanSteps.vue'
import HostPicker from '../components/HostPicker.vue'
import KnowledgeShortcut from '../components/KnowledgeShortcut.vue'
import DeployTemplates from './DeployTemplates.vue'
import { useDeployments } from './deployments/useDeployments'

const {
  me,
  route,
  router,
  activeTab,
  templates,
  tasks,
  tasksTotal,
  filters,
  exporting,
  selected,
  loading,
  error,
  notice,
  token,
  approvalReason,
  stepUpCode,
  approvers,
  selectedApproverId,
  routingReason,
  routing,
  assignedApproverName,
  isAssignedApprover,
  canAssignApprover,
  exportingReport,
  assignApprover,
  exportReport,
  busy,
  form,
  aiForm,
  aiBusy,
  traceEvents,
  traceLoading,
  traceMode,
  canSubmit,
  isAdmin,
  isSelfSubmitted,
  canSelfApproveNow,
  separationBlocked,
  currentTemplate,
  plan,
  planExpired,
  planConfirmed,
  approvalTimestamp,
  elapsedText,
  approvalFlow,
  execution,
  executionResults,
  deploymentKnowledge,
  filtersActive,
  precheckResults,
  verifyResults,
  rollbackResults,
  postRollbackResults,
  failure,
  reasonLabel,
  aiPlanning,
  planTrace,
  executionTrace,
  executionTraceTotal,
  executionTraceHidden,
  executionLogGroups,
  traceViewport,
  tracePinnedToBottom,
  onTraceScroll,
  scrollTraceToLatest,
  executionGroupDurationText,
  executionGroupOrder,
  executionGroupStatusText,
  executionLineText,
  executionPhaseText,
  executionRoleText,
  planProcess,
  refresh,
  chooseTemplate,
  submit,
  refreshTasks,
  deploymentTimeBounds,
  taskQuery,
  applyFilters,
  deployTypeLabels,
  deployResultLabels,
  exportColumns,
  exportDeployments,
  clearFilters,
  switchTab,
  openTask,
  isActiveStatus,
  stopTracePolling,
  startTracePolling,
  refreshTrace,
  submitAiPlan,
  confirmPlan,
  decide,
  formatTime,
  statusText,
  statusClass,
  resultOk,
  resultReason,
  stageText,
  traceDetail,
} = useDeployments()
</script>

<template>
  <div class="page-stack">
    <div class="deploy-tabs-row">
      <div class="deploy-tabs" role="tablist" aria-label="部署视图">
        <button
          type="button"
          role="tab"
          :class="{ active: activeTab === 'create' }"
          :aria-selected="activeTab === 'create'"
          @click="switchTab('create')"
        >
          新建部署
        </button>
        <button
          type="button"
          role="tab"
          :class="{ active: activeTab === 'records' }"
          :aria-selected="activeTab === 'records'"
          @click="switchTab('records')"
        >
          部署记录
          <span class="tab-count">{{ tasksTotal }}</span>
        </button>
        <button
          type="button"
          role="tab"
          :class="{ active: activeTab === 'templates' }"
          :aria-selected="activeTab === 'templates'"
          @click="switchTab('templates')"
        >
          模板库
        </button>
      </div>
      <button v-if="activeTab !== 'templates'" class="ghost" type="button" :disabled="loading" @click="refresh">刷新</button>
    </div>

    <DeployTemplates v-if="activeTab === 'templates'" embedded />

    <template v-else-if="activeTab === 'records'">
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="notice" class="notice">{{ notice }}</p>

      <div class="filter-bar" role="filter">
        <label class="field inline">
          <span>时间</span>
          <select v-model="filters.range" @change="applyFilters">
            <option value="">全部时间</option>
            <option value="today">今天</option>
            <option value="7d">近 7 天</option>
            <option value="30d">近 30 天</option>
          </select>
        </label>
        <label class="field inline">
          <span>部署类型</span>
          <select v-model="filters.deployType" @change="applyFilters">
            <option value="">全部类型</option>
            <option v-for="item in DEPLOY_TYPES" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <label class="field inline">
          <span>部署结果</span>
          <select v-model="filters.result" @change="applyFilters">
            <option value="">全部结果</option>
            <option v-for="item in DEPLOY_RESULTS" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <button v-if="filtersActive" class="ghost" type="button" @click="clearFilters">
          清除筛选
        </button>
        <button class="ghost" type="button" :disabled="exporting || !tasks.length" @click="exportDeployments">
          {{ exporting ? '导出中…' : '导出 CSV' }}
        </button>
        <span class="filter-note">
          共 {{ tasksTotal }} 条{{ filtersActive ? '（已筛选）' : '' }}
        </span>
      </div>

      <div class="records-workspace">
        <aside class="card records-rail">
          <div class="rail-head">
            <h3>部署记录</h3>
            <span class="rail-count">{{ tasksTotal }}</span>
          </div>
          <p v-if="!tasks.length" class="empty">
            {{ filtersActive ? '没有符合筛选条件的部署记录。' : '还没有部署任务。' }}
          </p>
          <div v-else class="task-list rail-list">
            <button
              v-for="item in tasks"
              :key="item.id"
              type="button"
              class="task-row"
              :class="{ active: selected?.id === item.id }"
              @click="openTask(item.id, item.status === 'awaiting_approval')"
            >
              <span class="task-main">
                <strong>{{ item.title || item.template_id || 'AI 方案' }}</strong>
                <small>{{ item.deploy_type_label || '未分类' }} · {{ formatTime(item.created_at) }} · {{ item.id.slice(0, 8) }}</small>
              </span>
              <span class="badge" :class="statusClass(item)">{{ statusText(item) }}</span>
            </button>
          </div>
        </aside>

        <div class="records-main">
          <section v-if="!selected" class="card empty-detail">
            <p class="desc">从左侧选一条部署记录，右侧会显示方案、审批和执行结果。</p>
          </section>

    <section v-if="selected && aiPlanning" class="card detail-card">
      <div class="detail-head">
        <div>
          <h2>AI 正在规划部署方案</h2>
          <p class="desc">
            {{ plan?.ai_mode === 'freeform'
              ? '生成结构化步骤和命令；不会连接目标机，命令仍要过允许/禁止清单和人工审批。'
              : '只读主机元数据与模板库；不会连接目标机，也不会写命令。' }}
          </p>
        </div>
        <span class="badge badge-info">规划中</span>
      </div>
      <p v-if="!planTrace.length" class="empty">等待 worker 领取任务…</p>
      <ol v-else class="trace-list">
        <li v-for="event in planTrace" :key="event.event_id" :class="`trace-${event.level}`">
          <span class="trace-title">{{ event.message || event.kind }}</span>
          <small v-if="traceDetail(event)">{{ traceDetail(event) }}</small>
        </li>
      </ol>
      <p class="ai-plan-note">规划完成后会进入待审批；如果失败，任务会关闭并保留下面的失败过程。</p>
    </section>

    <section v-if="selected && plan && plan.operations" class="card detail-card">
      <div class="detail-head">
        <div>
          <h2>{{ selected.title || plan.template_id || 'AI 方案' }}</h2>
          <p class="desc">
            {{ plan.platform }} · {{ plan.host }} ·
            <template v-if="plan.plan_kind !== 'ai_freeform'">模板 v{{ plan.template_version }} ·</template>
            <template v-if="plan.os_family"> {{ plan.os_family }}（目标机 {{ plan.host_os_family || 'unknown' }}） · </template>
            {{ selected.status }}<span v-if="selected.outcome"> / {{ selected.outcome }}</span>
          </p>
        </div>
        <span class="badge" :class="selected.status === 'awaiting_approval' ? 'badge-warn' : 'badge-muted'">
          {{ failure ? failure.message : (reasonLabel || selected.status) }}
        </span>
      </div>

      <div class="facts">
        <span>方案指纹 <code>{{ selected.plan_hash ? selected.plan_hash.slice(0, 16) : '—' }}</code></span>
        <span>申请人 {{ selected.submitted_by_name || (selected.submitted_by ? `用户 #${selected.submitted_by}` : '—') }}</span>
        <span v-if="isSelfSubmitted" class="approval-age">你提交的</span>
        <span v-if="selected.confirmed_by_name">审批人 {{ selected.confirmed_by_name }}</span>
        <span v-if="assignedApproverName" class="approval-age">指派审批人 {{ assignedApproverName }}</span>
        <span v-if="isAssignedApprover" class="approval-age">这条任务指派给你</span>
        <span v-if="selected.approval_reason">审批理由 {{ selected.approval_reason }}</span>
        <span>创建 {{ formatTime(plan.created_at) }}</span>
        <span>过期 {{ formatTime(plan.expires_at) }}</span>
        <span>TTL {{ plan.ttl_minutes }} 分钟</span>
        <span v-if="approvalFlow?.confirmedAt">方案确认 {{ formatTime(approvalFlow.confirmedAt) }}</span>
        <span v-if="approvalFlow?.approvedAt">批准 {{ formatTime(approvalFlow.approvedAt) }}</span>
        <span v-if="approvalFlow?.submitToApprove">提交→批准 {{ approvalFlow.submitToApprove }}</span>
        <span v-if="approvalFlow?.waiting" class="approval-age">已等待 {{ approvalFlow.waiting }}</span>
        <span v-if="selected.closed_at">关闭 {{ formatTime(selected.closed_at) }}</span>
        <router-link
          class="back-link"
          :to="{ name: 'audit', query: { task_id: selected.id } }"
        >
          查审计
        </router-link>
      </div>

      <div class="actions report-actions">
        <span class="action-help">部署报告包含方案、审批链、执行/验证/回滚结果：</span>
        <button class="ghost" type="button" :disabled="exportingReport" @click="exportReport('md')">
          {{ exportingReport ? '导出中…' : 'Markdown' }}
        </button>
        <button class="ghost" type="button" :disabled="exportingReport" @click="exportReport('pdf')">PDF</button>
        <button class="ghost" type="button" :disabled="exportingReport" @click="exportReport('docx')">Word</button>
      </div>

      <div v-if="failure" class="failure-card">
        <div class="failure-head">
          <strong>失败原因：{{ failure.message || failure.code }}</strong>
          <code>{{ failure.code }}</code>
        </div>
        <dl>
          <div><dt>阶段</dt><dd>{{ stageText(failure.stage) }}</dd></div>
          <div><dt>命令序号</dt><dd>{{ failure.command_index ?? '—' }}</dd></div>
          <div><dt>退出码</dt><dd>{{ failure.exit_code ?? '—' }}</dd></div>
          <div><dt>耗时</dt><dd>{{ failure.duration_ms ?? '—' }} ms</dd></div>
        </dl>
        <p v-if="failure.command" class="failure-command"><code>{{ failure.command }}</code></p>
        <p v-if="failure.stderr_tail" class="failure-stderr"><strong>stderr 尾部</strong><code>{{ failure.stderr_tail }}</code></p>
        <div v-if="failure.expected || failure.actual" class="failure-expect">
          <span><strong>期望</strong> {{ JSON.stringify(failure.expected) }}</span>
          <span><strong>实际</strong> {{ JSON.stringify(failure.actual) }}</span>
        </div>
        <div v-if="Array.isArray(failure.rollback_failed_commands) && failure.rollback_failed_commands.length" class="failure-rollback">
          <strong>回滚失败命令</strong>
          <ul>
            <li v-for="(item, index) in failure.rollback_failed_commands" :key="`rf-${index}`">
              <code>{{ item.command }}</code>
              <small>{{ item.message }} · 退出码 {{ item.exit_code ?? '—' }}</small>
            </li>
          </ul>
        </div>
      </div>

      <KnowledgeShortcut :count="deploymentKnowledge.length" :keyword="plan?.host" />

      <div v-if="plan.ai_status === 'ready'" class="card ai-reason-card">
        <div class="detail-head">
          <div>
            <h3>{{ plan.plan_kind === 'ai_freeform' ? 'AI 规划说明' : 'AI 选择理由' }}</h3>
            <p class="desc">
              模型 {{ plan.ai_model || '—' }} ·
              {{ plan.plan_kind === 'ai_freeform' ? '生成结构化步骤，不直接执行命令' : '只选模板，不生成命令' }}
            </p>
          </div>
          <span class="badge badge-muted">
            置信度 {{ typeof plan.ai_confidence === 'number' ? plan.ai_confidence.toFixed(2) : '—' }}
          </span>
        </div>
        <p class="ai-reason-text">{{ plan.ai_reason || '模型未给出理由' }}</p>
      </div>

      <details v-if="planTrace.length" class="trace-card">
        <summary>
          <strong>AI 规划过程</strong>
          <span class="badge badge-muted">{{ planTrace.length }} 条</span>
        </summary>
        <ol class="trace-list">
          <li v-for="event in planTrace" :key="`pt-${event.event_id}`" :class="`trace-${event.level}`">
            <span class="trace-title">{{ event.message || event.kind }}</span>
            <small v-if="traceDetail(event)">{{ traceDetail(event) }}</small>
          </li>
        </ol>
        <p class="plan-process-note">
          {{ plan.plan_kind === 'ai_freeform'
            ? 'AI 生成结构化步骤；命令已由后端解析 argv 并过允许/禁止清单，审批前不会执行。'
            : 'AI 只选模板和标题；命令、验证、回滚与 plan_hash 仍由模板确定性生成。' }}
        </p>
      </details>

      <details v-if="executionTrace.length" class="trace-card" open>
        <summary>
          <strong>部署过程实时日志</strong>
          <span class="badge badge-muted">{{ executionTraceTotal }} 条</span>
          <span v-if="traceMode === 'sse'" class="badge badge-muted">SSE 实时</span>
          <span v-else-if="traceMode === 'polling'" class="badge badge-muted">轮询中</span>
        </summary>
        <div class="trace-live-wrap">
          <div
            ref="traceViewport"
            class="trace-live"
            :class="{ 'trace-live-streaming': traceMode !== 'idle' }"
            @scroll="onTraceScroll"
          >
            <article
              v-for="group in executionLogGroups"
              :key="group.key"
              class="trace-command"
              :class="`trace-kind-${group.kind}`"
            >
              <header class="trace-command-head">
                <div class="trace-command-title">
                  <span class="trace-command-order">{{ executionGroupOrder(group) }}</span>
                  <code v-if="group.kind === 'command'">{{ executionPhaseText(group) }}</code>
                  <span v-if="group.kind === 'command'" class="trace-command-name">
                    {{ group.command || group.title }}
                  </span>
                  <span v-else-if="group.kind === 'result'" class="trace-group-label">
                    {{ group.title }}
                  </span>
                </div>
                <span class="badge trace-status" :class="`trace-status-${group.status}`">
                  {{ executionGroupStatusText(group) }}
                </span>
              </header>
              <div
                v-if="group.kind === 'command' || executionGroupDurationText(group)"
                class="trace-command-meta"
              >
                <small v-if="executionGroupDurationText(group)">
                  {{ group.kind === 'phase' ? '阶段耗时' : '耗时' }}
                  {{ executionGroupDurationText(group) }}
                </small>
                <small v-if="group.exitCode !== null">退出码 {{ group.exitCode }}</small>
              </div>
              <ol class="trace-lines">
                <li v-for="event in group.lines" :key="event.event_id" :class="`trace-${event.level}`">
                  <small>{{ executionLineText(event) }}</small>
                </li>
              </ol>
            </article>
          </div>
          <button
            v-if="traceMode !== 'idle' && !tracePinnedToBottom"
            class="trace-jump"
            type="button"
            @click="scrollTraceToLatest"
          >
            回到最新
          </button>
        </div>
        <p v-if="executionTraceHidden" class="trace-live-note">
          实时区只显示最近 300 条，前 {{ executionTraceHidden }} 条已省略。
        </p>
      </details>

      <details v-if="plan.plan_kind !== 'ai_freeform'" class="plan-process">
        <summary>
          <strong>方案生成过程</strong>
          <span class="badge badge-muted">{{ planProcess.length }} 步</span>
        </summary>
        <ol>
          <li v-for="item in planProcess" :key="item.title">
            <span>{{ item.title }}</span>
            <small>{{ item.detail }}</small>
          </li>
        </ol>
        <p class="plan-process-note">
          这份方案由已发布模板确定性生成，不是模型现场推理；模板如果由 AI 生成，
          AI 生成过程在「部署模板」编辑页的「AI 生成过程」里查看。
        </p>
      </details>

      <DeploymentPlanSteps :plan="plan" :title="selected.title" />

      <p v-if="selected.status === 'awaiting_approval' && planExpired" class="error action-note">
        方案已过期，不能再确认或批准执行；请重新生成方案，或直接驳回关闭任务。
      </p>

      <div
        id="deployment-confirm"
        v-if="isSelfSubmitted && selected.status === 'awaiting_approval' && !planExpired && !planConfirmed"
        class="actions"
      >
        <button class="primary" type="button" :disabled="busy" @click="confirmPlan">
          {{ busy ? '处理中…' : '我确认这份方案' }}
        </button>
      </div>
      <p
        v-if="!isSelfSubmitted && selected.status === 'awaiting_approval' && !planExpired && !planConfirmed"
        class="action-help"
      >
        这份方案由 {{ selected.submitted_by_name || '其他用户' }} 提交，只有提交人可以确认。
      </p>
      <p
        v-if="isSelfSubmitted && selected.status === 'awaiting_approval' && !planExpired && planConfirmed"
        class="notice action-note"
      >
        方案已确认，等待审批。批准需要一次性令牌；驳回不需要令牌。
      </p>

      <div v-if="canAssignApprover && !planConfirmed" class="approver-route-box">
        <label class="field inline">
          <span>指派审批人</span>
          <select v-model.number="selectedApproverId">
            <option :value="null" disabled>请选择管理员</option>
            <option
              v-for="item in approvers"
              :key="item.id"
              :value="item.id"
              :disabled="String(item.id) === String(selected.submitted_by)"
            >
              {{ item.display_name }}{{ String(item.id) === String(selected.submitted_by) ? '（提交人，不可选）' : '' }}
            </option>
          </select>
        </label>
        <input v-model="routingReason" maxlength="200" placeholder="指派/转交理由（可空，写入审计）" />
        <button class="ghost" type="button" :disabled="busy" @click="assignApprover">
          {{ assignedApproverName ? '转交审批' : '指派审批人' }}
        </button>
        <p class="action-help">
          跨人审批：指派只确定审批人，不代替一次性令牌；提交人不能作为审批人。
        </p>
      </div>

      <div v-if="token" class="token-box">
        <label>
          <span>一次性确认令牌（只显示一次）</span>
          <input :value="token" readonly />
        </label>
        <small>交给审批人；批准后令牌即作废。</small>
      </div>

      <p v-if="error" class="error action-note">{{ error }}</p>
      <p v-if="notice" class="notice action-note">{{ notice }}</p>
      <p v-if="separationBlocked" class="error action-note">
        职责分离：这是你提交的部署任务，默认不能自己批准。请让其他管理员审批；
        确需单人闭环，请管理员在配置页开启“自审批豁免”（每次自批都会写入审计）。
      </p>

      <div v-if="isAdmin && selected.status === 'awaiting_approval'" class="actions">
        <input v-model="token" :disabled="planExpired || separationBlocked" placeholder="粘贴一次性确认令牌" />
        <input
          v-model="approvalReason"
          maxlength="200"
          :disabled="planExpired"
          placeholder="审批理由（可空，写入审计）"
        />
        <input
          v-model="stepUpCode"
          inputmode="numeric"
          maxlength="6"
          autocomplete="one-time-code"
          :disabled="planExpired || separationBlocked"
          placeholder="6 位动态口令（批准必填）"
        />
        <button
          class="primary"
          type="button"
          :disabled="busy || planExpired || separationBlocked"
          @click="decide('APPROVE')"
        >
          {{ busy ? '处理中…' : '批准并执行' }}
        </button>
        <button class="plain danger" type="button" :disabled="busy" @click="decide('REJECT')">驳回</button>
      </div>
      <p v-if="isAdmin && selected.status === 'awaiting_approval' && !planExpired" class="action-help">
        批准需要一次性确认令牌和当前账号的动态口令；驳回不需要令牌。
      </p>
      <p v-if="isAdmin && selected.status === 'awaiting_approval' && planExpired" class="action-help">
        方案已过期，只能驳回关闭任务；要执行请重新生成方案。
      </p>

      <div v-if="precheckResults.length || executionResults.length || verifyResults.length || rollbackResults.length || postRollbackResults.length" class="result-grid">
        <div v-if="precheckResults.length">
          <h4>变更前检查</h4>
          <ul>
            <li v-for="(item, index) in precheckResults" :key="`ep-${index}`" :class="{ bad: !resultOk(item) }">
              {{ resultOk(item) ? '通过' : '未通过' }} · {{ resultReason(item) }}
            </li>
          </ul>
        </div>
        <div>
          <h4>变更结果</h4>
          <ul>
            <li v-for="(item, index) in executionResults" :key="`ea-${index}`" :class="{ bad: !resultOk(item) }">
              {{ resultOk(item) ? '成功' : '失败' }} · {{ resultReason(item) }}
            </li>
          </ul>
        </div>
        <div>
          <h4>验证结果</h4>
          <ul>
            <li v-for="(item, index) in verifyResults" :key="`ev-${index}`" :class="{ bad: !resultOk(item) }">
              {{ resultOk(item) ? '通过' : '未通过' }} · {{ resultReason(item) }}
            </li>
          </ul>
        </div>
        <div>
          <h4>回滚结果</h4>
          <ul>
            <li v-for="(item, index) in rollbackResults" :key="`er-${index}`" :class="{ bad: !resultOk(item) }">
              {{ resultOk(item) ? '已回滚' : '回滚失败' }} · {{ resultReason(item) }}
            </li>
          </ul>
        </div>
        <div v-if="postRollbackResults.length">
          <h4>回滚后验证</h4>
          <ul>
            <li v-for="(item, index) in postRollbackResults" :key="`epr-${index}`" :class="{ bad: !resultOk(item) }">
              {{ resultOk(item) ? '已恢复' : '未恢复' }} · {{ resultReason(item) }}
            </li>
          </ul>
        </div>
      </div>
    </section>

          <section v-if="selected && !aiPlanning && (!plan || !plan.operations)" class="card detail-card">
            <div class="detail-head">
              <div>
                <h2>{{ selected.title || '部署任务' }}</h2>
                <p class="desc">
                  {{ selected.status }}<span v-if="selected.outcome"> / {{ selected.outcome }}</span>
                  <span v-if="selected.reason_code"> · {{ selected.reason_code }}</span>
                </p>
              </div>
              <span class="badge badge-muted">{{ selected.outcome || selected.status }}</span>
            </div>
            <div class="facts">
              <span>任务 <code>{{ selected.id.slice(0, 8) }}</code></span>
              <span>创建 {{ formatTime(selected.created_at) }}</span>
              <span v-if="selected.stage">阶段 {{ selected.stage }}</span>
            </div>

            <div v-if="failure || selected.reason_code" class="failure-card">
              <div class="failure-head">
                <strong>失败原因：{{ failure?.message || reasonLabel || '未记录具体原因' }}</strong>
                <code>{{ failure?.code || selected.reason_code || 'unknown' }}</code>
              </div>
              <dl v-if="failure?.stage || failure?.reason">
                <div v-if="failure?.stage"><dt>阶段</dt><dd>{{ stageText(failure.stage) }}</dd></div>
                <div v-if="failure?.reason"><dt>原因码</dt><dd>{{ failure.reason }}</dd></div>
              </dl>
            </div>

            <KnowledgeShortcut :count="deploymentKnowledge.length" :keyword="selected?.plan?.host" />

            <details v-if="planTrace.length" class="trace-card" open>
              <summary>
                <strong>AI 规划过程</strong>
                <span class="badge badge-muted">{{ planTrace.length }} 条</span>
              </summary>
              <ol class="trace-list">
                <li v-for="event in planTrace" :key="`fallback-${event.event_id}`" :class="`trace-${event.level}`">
                  <span class="trace-title">{{ event.message || event.kind }}</span>
                  <small v-if="traceDetail(event)">{{ traceDetail(event) }}</small>
                </li>
              </ol>
              <p class="plan-process-note">
                任务已结束，这里是失败前实际执行的规划步骤；最后一条通常是失败原因。
              </p>
            </details>
            <details v-else-if="plan?.ai_mode === 'freeform' || selected.stage === 'ai_plan'" class="trace-card" open>
              <summary>
                <strong>AI 规划过程</strong>
                <span class="badge badge-muted">0 条</span>
              </summary>
              <p class="empty">这条任务没有记录到规划过程事件，可能是任务创建后未进入 worker。</p>
            </details>

            <div class="empty-detail">
              <p class="desc">这条任务没有可展示的方案，可能 AI 规划失败或任务在方案生成前关闭。可以新建部署重试，或选左侧其他记录。</p>
            </div>
          </section>

        </div>
      </div>
    </template>

    <template v-else>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="notice" class="notice">{{ notice }}</p>
      <p v-if="!canSubmit" class="desc">当前角色只能看部署记录；创建和确认需要管理员或排查员。</p>

      <div class="deploy-create-grid">
        <section class="card create-card">
          <div class="card-title-row">
            <div>
              <h3>AI 现场规划</h3>
              <p class="desc">描述目标即可生成方案；命令仍要过后端策略和人工审批。</p>
            </div>
            <span class="badge badge-muted">
              {{ aiForm.mode === 'freeform' ? '生成结构化方案' : '只读选模板' }}
            </span>
          </div>

          <div class="ai-mode-switch" role="group" aria-label="AI 规划模式">
            <button
              type="button"
              :class="{ active: aiForm.mode === 'freeform' }"
              @click="aiForm.mode = 'freeform'"
            >
              自主生成方案
            </button>
            <button
              type="button"
              :class="{ active: aiForm.mode === 'template' }"
              @click="aiForm.mode = 'template'"
            >
              从模板库选择
            </button>
          </div>

          <label>
            <span>要部署什么 *</span>
            <textarea v-model="aiForm.goal" rows="3" placeholder="例如：在 host-150 上安装并启动 keepalived，失败自动回滚" />
          </label>

          <div class="field-grid">
            <HostPicker v-model="aiForm.host" label="目标主机" placeholder="搜索主机 ID 或地址" />
            <label>
              <span>平台</span>
              <select v-model="aiForm.platform">
                <option value="linux">linux</option>
                <option value="windows">windows</option>
              </select>
            </label>
            <label>
              <span>部署类型 *</span>
              <select v-model="aiForm.deployType">
                <option value="">请选择</option>
                <option v-for="item in DEPLOY_TYPES" :key="item.value" :value="item.value">
                  {{ item.label }}
                </option>
              </select>
            </label>
          </div>

          <label>
            <span>标题（可选）</span>
            <input v-model="aiForm.title" placeholder="不填则由 AI 生成" />
          </label>

          <button class="primary" type="button" :disabled="aiBusy || !canSubmit" @click="submitAiPlan">
            {{ aiBusy ? '提交中…' : '让 AI 规划方案' }}
          </button>
          <p class="ai-plan-note">规划失败不会套模板：任务会关闭并保留过程，可以改用右侧模板创建。</p>
        </section>

        <section class="card create-card">
          <div class="card-title-row">
            <div>
              <h3>按模板创建</h3>
              <p class="desc">模板命令、验证和回滚已固定，生成方案后仍要确认与审批。</p>
            </div>
            <span class="badge badge-muted">{{ templates.length }} 个模板</span>
          </div>

          <p v-if="loading" class="empty">加载中…</p>
          <template v-else>
            <p v-if="!templates.length" class="empty">还没有可用模板，先到模板库创建并发布。</p>
            <div v-else class="template-grid">
              <button
                v-for="template in templates"
                :key="template.id"
                type="button"
                class="template-tile"
                :class="{ active: template.id === form.template_id, disabled: !template.enabled }"
                :disabled="!canSubmit || !template.enabled || template.bad"
                @click="chooseTemplate(template.id)"
              >
                <span class="tile-top">
                  <strong>{{ template.name }}</strong>
                  <span v-if="!template.enabled" class="badge badge-muted">已停用</span>
                  <span v-else-if="template.bad" class="badge badge-warn">不完整</span>
                </span>
                <small>
                  {{ template.platform }} · {{ template.os_family || 'unknown' }}
                  <template v-if="template.package_manager && template.package_manager !== 'none'">
                    · {{ template.package_manager }}
                  </template>
                  · v{{ template.version }}
                </small>
              </button>
            </div>

            <div v-if="currentTemplate" class="form-stack">
              <div class="field-grid">
                <HostPicker
                  v-model="form.host"
                  label="目标主机"
                  :placeholder="currentTemplate.default_host || '搜索主机 ID 或地址'"
                  hint="未登记的主机执行时会被拒绝。"
                />
                <label>
                  <span>部署类型 *</span>
                  <select v-model="form.deployType">
                    <option value="">请选择</option>
                    <option v-for="item in DEPLOY_TYPES" :key="item.value" :value="item.value">
                      {{ item.label }}
                    </option>
                  </select>
                </label>
                <label>
                  <span>标题</span>
                  <input v-model="form.title" placeholder="例如：重启 host-150 crond" />
                </label>
              </div>
              <label>
                <span>说明（可选）</span>
                <textarea v-model="form.description" rows="2" placeholder="为什么做、预期结果" />
              </label>
              <button class="primary" type="button" :disabled="busy || !canSubmit" @click="submit">
                {{ busy ? '创建中…' : '生成部署方案' }}
              </button>
            </div>
          </template>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped src="./deployments/deployments.css"></style>
