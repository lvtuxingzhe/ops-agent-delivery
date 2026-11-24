<script setup lang="ts">
import MultiSelect from '../components/MultiSelect.vue'
import HostPicker from '../components/HostPicker.vue'
import { commandFromArgv } from '../utils/command'
import { useApprovals } from './approvals/useApprovals'

const {
  activeTab,
  addRow,
  aiBusy,
  aiReview,
  approvalState,
  approvalStateOptions,
  approvalTimeline,
  baseTasks,
  baseTotal,
  busy,
  closeDetail,
  commandTemplates,
  deploymentPlan,
  deploymentPlanLoading,
  draftReason,
  error,
  execution,
  exportApprovals,
  exporting,
  filteredTasks,
  formatTime,
  goDeployments,
  handleAiReview,
  handleApprove,
  handleCancel,
  handleReject,
  handleTakeover,
  handledText,
  hasReviewContent,
  hostId,
  inputPlaceholder,
  isAdmin,
  isDeployment,
  isSelfSubmitted,
  jsonMode,
  loading,
  me,
  notice,
  onApprovalStateChange,
  overdueText,
  ownerText,
  platform,
  queueFiltersActive,
  queueQuery,
  queueRange,
  reason,
  removeRow,
  report,
  reportLoading,
  resetQueueFilters,
  resultReason,
  reviewedLoading,
  rows,
  screenshotEvidence,
  selectTask,
  selected,
  stepUpCode,
  separationBlocked,
  submitted,
  submitterFilter,
  submitterOptions,
  taskKind,
  toggleJsonMode,
  total,
  ttlS,
  typeFilters,
  typeOptions,
  waitingText,
} = useApprovals()
</script>

<template>
  <div class="page-stack">
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>
    <p v-if="me && !isAdmin" class="error">当前账号不是管理员，只能查看，不能批准或驳回。</p>

    <nav class="approval-tabs approval-tabs-top" aria-label="审批导航">
      <RouterLink class="approval-tab" to="/approvals">审批队列</RouterLink>
      <RouterLink class="approval-tab" to="/knowledge/governance">知识治理</RouterLink>
    </nav>

    <section v-if="!selected" class="card approval-queue">
      <div class="card-head approval-queue-head">
        <div>
          <h3>{{ approvalState === 'pending' ? '待审批方案' : '审批记录' }}</h3>
          <p class="desc">
            {{ approvalState === 'pending'
              ? '按提交时间排列。点“查看 / 审批”核对部署方案，或排查后的处置方案与报告。'
              : '按提交时间排列。这里可以回看已通过或已驳回的审批记录。' }}
          </p>
        </div>
        <div class="approval-head-actions">
          <span class="badge badge-muted">
            {{ queueFiltersActive ? `${filteredTasks.length} / ${baseTotal}` : baseTotal }} 条
          </span>
          <button class="ghost" type="button" :disabled="loading || exporting || !baseTotal" @click="exportApprovals">
            {{ exporting ? '导出中…' : '导出 CSV' }}
          </button>
        </div>
      </div>

      <div class="approval-filters">
        <input
          v-model="queueQuery"
          class="approval-search"
          type="search"
          placeholder="搜索标题"
        />
        <div class="approval-filter-group">
          <span>类型</span>
          <MultiSelect
            v-model="typeFilters"
            :options="typeOptions"
            aria-label="类型"
            placeholder="全部类型"
          />
        </div>
        <label class="approval-filter-select">
          <span>审批状态</span>
          <select v-model="approvalState" @change="onApprovalStateChange">
            <option v-for="option in approvalStateOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="approval-filter-select">
          <span>提交人</span>
          <select v-model="submitterFilter">
            <option value="">全部</option>
            <option v-for="option in submitterOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </label>
        <label class="approval-filter-select">
          <span>时间</span>
          <select v-model="queueRange">
            <option value="all">全部</option>
            <option value="today">今天</option>
            <option value="7d">近 7 天</option>
            <option value="30d">近 30 天</option>
          </select>
        </label>
        <button v-if="queueFiltersActive" class="ghost approval-filter-reset" type="button" @click="resetQueueFilters">
          重置筛选
        </button>
      </div>

      <div v-if="loading || (reviewedLoading && approvalState !== 'pending')" class="approval-empty approval-loading">
        <span class="approval-spinner" aria-hidden="true"></span>
        {{ approvalState === 'pending' ? '正在加载待审批方案…' : '正在加载审批记录…' }}
      </div>
      <div v-else-if="!baseTasks.length" class="approval-empty approval-empty-state">
        <strong>{{ approvalState === 'pending' ? '当前没有待审批任务' : '当前没有审批记录' }}</strong>
        <p>排查或部署任务进入待审批阶段后，会自动出现在这里。</p>
      </div>
      <div v-else-if="!filteredTasks.length" class="approval-empty approval-empty-state">
        <strong>没有匹配的审批方案</strong>
        <p>换个关键词，或清空类型、审批状态、提交人和时间筛选。</p>
        <button class="ghost" type="button" @click="resetQueueFilters">重置筛选</button>
      </div>
      <div v-else class="approval-list">
        <button
          v-for="task in filteredTasks"
          :key="task.id"
          class="approval-row"
          type="button"
          @click="selectTask(task)"
        >
          <span class="approval-row-main">
            <strong>{{ task.title || '（无标题）' }}</strong>
            <span class="approval-row-meta">
              <span class="approval-kind">{{ taskKind(task) }}</span>
              <span class="mono">{{ task.id.slice(0, 8) }}…</span>
              <span>{{ formatTime(task.created_at) }}</span>
              <span v-if="waitingText(task)" class="approval-age">{{ waitingText(task) }}</span>
              <span v-else-if="handledText(task)" class="approval-age">{{ handledText(task) }}</span>
              <span v-if="ownerText(task)" class="approval-owner">{{ ownerText(task) }}</span>
              <span v-if="overdueText(task)" class="approval-overdue">{{ overdueText(task) }}</span>
            </span>
          </span>
          <span class="approval-row-stage">{{ task.stage }}</span>
          <span class="approval-row-action">{{ task.status === 'awaiting_approval' ? '查看 / 审批' : '查看记录' }}</span>
        </button>
      </div>
    </section>

    <section v-if="selected" class="card">
      <div class="approval-detail-head">
        <h2>{{ selected.title || '（无标题）' }}</h2>
        <div class="approval-detail-actions">
          <router-link
            class="back-link"
            :to="{ name: 'audit', query: { task_id: selected.id } }"
          >
            查审计
          </router-link>
          <button class="ghost" type="button" @click="closeDetail">返回队列</button>
        </div>
      </div>
      <ul class="facts">
        <li>排查号：<span class="mono">{{ selected.id }}</span></li>
        <li>
          申请人：{{ selected.submitted_by_name || `用户 #${selected.submitted_by}` }}
          <span v-if="isSelfSubmitted" class="tag tag-warn">你提交的</span>
        </li>
        <li v-if="selected.confirmed_by_name">审批人：{{ selected.confirmed_by_name }}</li>
        <li v-if="selected.approval_reason">审批理由：{{ selected.approval_reason }}</li>
        <li>阶段：{{ selected.stage }} · 提交时间：{{ formatTime(selected.created_at) }}</li>
        <li v-if="waitingText(selected)">审批时效：{{ waitingText(selected) }}</li>
        <li v-else-if="handledText(selected)">审批时效：{{ handledText(selected) }}</li>
        <li v-if="ownerText(selected)">当前卡在：{{ ownerText(selected) }}</li>
        <li v-if="selected.status === 'awaiting_approval' && selected.approval_expires_at">
          审批截止：{{ formatTime(selected.approval_expires_at) }}
          <span v-if="overdueText(selected)" class="approval-overdue"> · {{ overdueText(selected) }}</span>
        </li>
        <li v-if="report">数据质量：{{ report.data_quality }} · 结论 {{ report.conclusions_count }} 条</li>
        <li v-if="deploymentPlan">
          部署模板：{{ deploymentPlan.plan.template_id }} · v{{ deploymentPlan.plan.template_version }} ·
          方案指纹：<span class="mono">{{ deploymentPlan.plan_hash.slice(0, 16) }}</span>
        </li>
      </ul>

      <ol class="approval-timeline" aria-label="审批时间线">
        <li v-for="step in approvalTimeline" :key="step.key">
          <span class="timeline-label">{{ step.label }}</span>
          <span class="timeline-time">{{ step.time }}</span>
          <span class="timeline-actor">{{ step.actor }}</span>
        </li>
      </ol>
      <p v-if="separationBlocked" class="separation-note">
        职责分离：这是你提交的任务，默认不能自己审批。请让其他管理员处理；
        确需单人闭环，请管理员在配置页开启“自审批豁免”。
      </p>

      <nav class="approval-tabs" role="tablist" aria-label="审批详情分组">
        <button
          class="approval-tab"
          :class="{ active: activeTab === 'review' }"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'review'"
          @click="activeTab = 'review'"
        >
          {{ isDeployment ? '部署方案' : '报告与证据' }}
        </button>
        <button
          v-if="!isDeployment"
          class="approval-tab"
          :class="{ active: activeTab === 'command' }"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'command'"
          @click="activeTab = 'command'"
        >
          命令与审批
        </button>
        <button
          class="approval-tab"
          :class="{ active: activeTab === 'execution' }"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'execution'"
          @click="activeTab = 'execution'"
        >
          执行结果
        </button>
      </nav>

      <div v-show="activeTab === 'review'" class="approval-panel">
        <template v-if="isDeployment">
          <h3>部署方案</h3>
          <p class="desc">
            部署任务没有排查报告，也不需要拿排查结论来证明命令安全；这里审的是模板生成的部署方案。
          </p>
          <div v-if="deploymentPlanLoading" class="approval-empty">
            <p class="desc">正在读取部署方案…</p>
          </div>
          <div v-else-if="deploymentPlan" class="deployment-plan">
            <div class="deployment-plan-meta">
              <span>平台：{{ deploymentPlan.plan.platform }}</span>
              <span v-if="deploymentPlan.plan.os_family">
                操作系统：{{ deploymentPlan.plan.os_family }}
                （目标机 {{ deploymentPlan.plan.host_os_family || 'unknown' }}）
              </span>
              <span>目标主机：{{ deploymentPlan.plan.host }}</span>
              <span>模板版本：v{{ deploymentPlan.plan.template_version }}</span>
              <span>TTL：{{ deploymentPlan.plan.ttl_minutes }} 分钟</span>
              <span>过期：{{ formatTime(deploymentPlan.plan.expires_at) }}</span>
            </div>
            <div v-if="deploymentPlan.plan.precheck?.length" class="deployment-cmd-block">
              <h4>变更前检查</h4>
              <ul>
                <li v-for="(op, index) in deploymentPlan.plan.precheck" :key="`dep-p-${index}`">
                  <code>{{ commandFromArgv(op.argv) }}</code>
                  <small>{{ op.expect ? JSON.stringify(op.expect) : '退出码 0' }}</small>
                </li>
              </ul>
            </div>
            <div class="deployment-cmd-block">
              <h4>变更命令</h4>
              <ul>
                <li v-for="(op, index) in deploymentPlan.plan.operations" :key="`dep-a-${index}`">
                  <code>{{ commandFromArgv(op.argv) }}</code>
                  <small>{{ op.scope }}</small>
                </li>
              </ul>
            </div>
            <div class="deployment-cmd-block">
              <h4>业务验证</h4>
              <ul>
                <li v-for="(op, index) in deploymentPlan.plan.verify" :key="`dep-v-${index}`">
                  <code>{{ commandFromArgv(op.argv) }}</code>
                  <small>{{ op.expect ? JSON.stringify(op.expect) : '退出码 0' }}</small>
                </li>
              </ul>
            </div>
            <div class="deployment-cmd-block">
              <h4>失败回滚</h4>
              <ul>
                <li v-for="(op, index) in deploymentPlan.plan.rollback" :key="`dep-r-${index}`">
                  <code>{{ commandFromArgv(op.argv) }}</code>
                  <small>{{ op.scope }}</small>
                </li>
              </ul>
            </div>
            <div v-if="deploymentPlan.plan.post_rollback?.length" class="deployment-cmd-block">
              <h4>回滚后验证</h4>
              <ul>
                <li v-for="(op, index) in deploymentPlan.plan.post_rollback" :key="`dep-pr-${index}`">
                  <code>{{ commandFromArgv(op.argv) }}</code>
                  <small>{{ op.expect ? JSON.stringify(op.expect) : '退出码 0' }}</small>
                </li>
              </ul>
            </div>
            <p class="deployment-plan-note">
              这份方案已由模板生成并绑定 plan_hash。请在部署页确认方案、取得一次性令牌后完成批准或驳回；
              审批页不重新录入命令，避免绕过方案指纹。
            </p>
            <button class="primary" type="button" @click="goDeployments">
              去部署页确认/审批
            </button>
          </div>
          <div v-else class="approval-empty">
            <p>部署方案读取失败，请刷新后重试。</p>
          </div>
        </template>

        <template v-else>
        <h3>核对报告与证据</h3>
        <div v-if="reportLoading" class="approval-empty">
          <p class="desc">正在读取报告与证据…</p>
        </div>
        <div v-else-if="!hasReviewContent" class="approval-empty approval-risk">
          <p>这份排查没有结论与证据，无法据此判断命令是否安全。</p>
          <p class="desc">不建议直接批准执行；先补数据源或要求重新排查。</p>
          <div class="empty-actions">
            <button
              class="danger"
              type="button"
              :disabled="busy || !isAdmin || submitted"
              @click="handleReject"
            >
              {{ busy ? '处理中…' : '驳回并关闭' }}
            </button>
            <button
              class="ghost"
              type="button"
              :disabled="busy || submitted"
              @click="activeTab = 'command'"
            >
              仍要查看命令
            </button>
          </div>
        </div>
        <p v-if="report?.headline" class="approval-headline">{{ report.headline }}</p>
        <p v-if="report?.scope" class="approval-scope">判断范围：{{ report.scope }}</p>
        <details v-if="report?.summary" class="approval-summary">
          <summary>报告摘要</summary>
          <p>{{ report.summary }}</p>
        </details>
        <details v-if="report?.unknowns.length" class="approval-unknowns">
          <summary>未确认 {{ report.unknowns.length }} 项</summary>
          <ul>
            <li v-for="item in report.unknowns" :key="item">{{ item }}</li>
          </ul>
        </details>
        <div v-if="screenshotEvidence.length" class="approval-shots">
          <h3>报错截图证据（{{ screenshotEvidence.length }} 张）</h3>
          <p class="desc">批准命令前先核对截图与 OCR 原文；关键字段以原图为准。</p>
          <div class="approval-shot-grid">
            <figure v-for="item in screenshotEvidence" :key="item.id" class="approval-shot">
              <img v-if="item.thumbnail" :src="item.thumbnail" :alt="item.target || '报错截图'" />
              <figcaption>
                <span class="mono">{{ item.id }}</span>
                <span>{{ item.target }}</span>
                <span v-if="item.data_status !== 'ok'" class="tag tag-warn">识别不确定</span>
                <span v-if="item.ocr_confidence !== undefined">
                  OCR {{ (item.ocr_confidence * 100).toFixed(1) }}%
                </span>
              </figcaption>
              <pre class="approval-ocr">{{ item.excerpt }}</pre>
            </figure>
          </div>
        </div>
        </template>
      </div>

      <div v-if="!isDeployment && activeTab === 'command'" class="approval-panel">
        <p v-if="submitted" class="action-notice">
          命令已签发，正在执行。审批动作已锁定，请到“执行结果”页签查看进度。
        </p>
        <h3>命令与审批</h3>
        <p class="desc">
        直接写命令，例如 <code>systemctl status nginx</code>。平台不会跑管道、重定向或
        <code>&amp;&amp;</code>，需要多条就一行一条。
      </p>
      <div class="command-templates">
        <button
          v-for="template in commandTemplates"
          :key="template.command"
          class="template"
          type="button"
          @click="addRow(template.command)"
        >
          {{ template.label }}
        </button>
      </div>
      <label class="json-toggle">
        <input v-model="jsonMode" type="checkbox" @change="toggleJsonMode" />
        高级模式：按 JSON 数组写（只有脚本或特殊参数需要）
      </label>

      <label class="field inline">
        <span>平台</span>
        <select v-model="platform">
          <option value="linux">linux</option>
          <option value="windows">windows</option>
        </select>
      </label>
      <HostPicker
        v-model="hostId"
        :platform="platform"
        label="目标主机"
        hint="从已登记主机中选择，也可以搜索 ID 或地址；未登记的主机执行时会被拒绝。"
        @update:platform="platform = $event"
      />
      <p v-if="draftReason" class="draft-reason">提交人说明：{{ draftReason }}</p>

      <div v-for="(row, index) in rows" :key="index" class="op-row">
        <input
          v-model="row.commandText"
          class="argv"
          type="text"
          spellcheck="false"
          :placeholder="inputPlaceholder"
        />
        <select v-model="row.scope">
          <option value="read">只读</option>
          <option value="dangerous">危险（需确认）</option>
        </select>
        <button class="ghost" type="button" :disabled="rows.length === 1" @click="removeRow(index)">删除</button>
      </div>
      <button class="ghost" type="button" @click="addRow()">+ 添加命令</button>

      <div class="ai-review">
        <button class="ghost" type="button" :disabled="busy || aiBusy || !isAdmin || submitted" @click="handleAiReview">
          {{ aiBusy ? 'AI 预审中…' : 'AI 预审（不代替人工批准）' }}
        </button>
        <div v-if="aiReview" class="ai-result">
          <p>
            <span class="tag">{{ aiReview.available ? 'AI 建议' : 'AI 不可用' }}</span>
            <strong>{{ aiReview.decision }}</strong> · 风险 {{ aiReview.risk }}
            <span v-if="aiReview.model" class="desc">模型：{{ aiReview.model }}</span>
          </p>
          <p v-if="aiReview.summary" class="summary">{{ aiReview.summary }}</p>
          <ul v-if="aiReview.reasons.length" class="ai-list">
            <li v-for="(item, index) in aiReview.reasons" :key="`reason-${index}`">{{ item }}</li>
          </ul>
          <ul v-if="aiReview.red_flags.length" class="ai-list warn">
            <li v-for="(item, index) in aiReview.red_flags" :key="`flag-${index}`">风险：{{ item }}</li>
          </ul>
        </div>
      </div>

        <h3>批准、驳回或撤回</h3>
        <label class="field">
          <span>审批 / 驳回理由（可空，最多 200 字；批准时会写入审计）</span>
          <input v-model="reason" type="text" maxlength="200" />
        </label>
        <label class="field">
          <span>动态口令（含高危操作时必填）</span>
          <input
            v-model="stepUpCode"
            type="text"
            inputmode="numeric"
            maxlength="6"
            autocomplete="one-time-code"
            placeholder="6 位 TOTP"
          />
        </label>
        <div class="actions">
          <label class="field inline">
            <span>有效期（秒）</span>
            <input v-model.number="ttlS" type="number" min="60" max="3600" />
          </label>
          <button
            class="primary"
            type="button"
            :disabled="busy || !isAdmin || submitted || separationBlocked"
            @click="handleApprove"
          >
            {{ busy ? '处理中…' : '批准并执行' }}
          </button>
          <button class="danger" type="button" :disabled="busy || !isAdmin || submitted" @click="handleReject">
            驳回
          </button>
          <button
            v-if="selected.status === 'awaiting_approval' && isAdmin && !submitted && !separationBlocked"
            class="ghost"
            type="button"
            :disabled="busy"
            @click="handleTakeover"
          >
            接管超时审批
          </button>
          <button class="ghost" type="button" :disabled="busy || submitted" @click="handleCancel">
            撤回这次排查
          </button>
        </div>
      </div>

      <div v-show="activeTab === 'execution'" class="approval-panel">
        <h3>执行结果</h3>
        <div v-if="!execution" class="approval-empty">
          <p class="desc">
            {{ submitted ? '命令已签发，正在等待执行结果…' : '批准后，这里会显示命令、退出码和输出预览。' }}
          </p>
        </div>
        <template v-else>
          <ul class="facts">
            <li>状态：{{ execution.status }} · 结果：{{ execution.outcome ?? '执行中' }}</li>
            <li>平台：{{ execution.platform }} · 主机：{{ execution.host }} · 签发人：{{ execution.issued_by ?? '—' }}</li>
            <li v-if="execution.approval_reason">审批理由：{{ execution.approval_reason }}</li>
            <li>签发：{{ formatTime(execution.issued_at) }} · 结束：{{ formatTime(execution.finished_at) }}</li>
          </ul>
          <div v-if="execution.ops.length" class="table-wrap">
            <table class="table">
              <thead>
                <tr><th>#</th><th>命令</th><th>scope</th><th>指纹</th></tr>
              </thead>
              <tbody>
                <tr v-for="(op, index) in execution.ops" :key="index">
                  <td>{{ index + 1 }}</td>
                  <td class="mono">{{ JSON.stringify(op.argv) }}</td>
                  <td>{{ op.scope }}</td>
                  <td class="mono">{{ op.fingerprint.slice(0, 8) }}…</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="execution.results.length" class="table-wrap">
            <table class="table">
              <thead>
                <tr><th>指纹</th><th>ok</th><th>退出码</th><th>耗时(ms)</th><th>原因</th></tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in execution.results" :key="index">
                  <td class="mono">{{ String(item.fingerprint ?? '').slice(0, 8) }}…</td>
                  <td>{{ item.ok ? '是' : '否' }}</td>
                  <td>{{ item.exit_code ?? '—' }}</td>
                  <td>{{ item.duration_ms ?? '—' }}</td>
                  <td>{{ resultReason(item) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div
            v-for="(item, index) in execution.results"
            v-show="item.preview && (item.preview.stdout || item.preview.stderr)"
            :key="`preview-${index}`"
            class="preview"
          >
            <p class="desc">
              输出预览（已脱敏，最多保留 10 分钟）· 指纹
              <span class="mono">{{ String(item.fingerprint ?? '').slice(0, 8) }}…</span>
            </p>
            <pre v-if="item.preview?.stdout" class="output">{{ item.preview.stdout }}</pre>
            <pre v-if="item.preview?.stderr" class="output err">{{ item.preview.stderr }}</pre>
          </div>
          <p v-if="!execution.finished_at" class="desc">正在轮询结果…</p>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped src="./approvals/approvals.css"></style>
