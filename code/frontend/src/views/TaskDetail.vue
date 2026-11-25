<script setup lang="ts">
/**
 * 任务详情页：把一条任务从“提交”到“收尾”摊开。
 *
 * 这一页只读，不重复审批动作；审批仍在审批中心。
 * 关键是把报告结论和证据快照放在同一屏：
 * 结论里引用的 evidence_id 能点回下面的证据原文，避免“只有结论没有依据”。
 *
 * 数据加载、轮询与动作编排在 task-detail/useTaskDetail.ts；
 * Markdown 与方案步骤解析在 task-detail/markdown.ts；样式在 task-detail/task-detail.css。
 */
import { RouterLink } from 'vue-router'

import { renderInlineMarkdown } from './task-detail/markdown'
import { evidenceProbeLabel, evidenceTargetLabel } from './task-detail/evidencePresentation'
import { useTaskDetail } from './task-detail/useTaskDetail'
import { useAnswerPresentation } from './task-detail/useAnswerPresentation'
import ConclusionPointsCard from './task-detail/components/ConclusionPointsCard.vue'
import ReportFeedback from './task-detail/components/ReportFeedback.vue'
import EvidenceRefs from './task-detail/components/EvidenceRefs.vue'
import IncidentalFindingsCard from './task-detail/components/IncidentalFindingsCard.vue'
import LiveProcessCard from './task-detail/components/LiveProcessCard.vue'
import ServiceInventoryCard from './task-detail/components/ServiceInventoryCard.vue'
import TaskCommentsCard from './task-detail/components/TaskCommentsCard.vue'
import TaskDetailHeader from './task-detail/components/TaskDetailHeader.vue'
import TaskDetailOverview from './task-detail/components/TaskDetailOverview.vue'

const {
  DETAIL_TABS,
  normalizeDetailTab,
  activeTab,
  selectDetailTab,
  task,
  taskId,
  report,
  execution,
  me,
  comments,
  commentsTruncated,
  commentsHasMore,
  commentUnreadNotice,
  commentBody,
  commentKind,
  commentLoading,
  commentSaving,
  commentDeletingId,
  commentError,
  canComment,
  canAssign,
  canDeleteComment,
  assignableUsers,
  selectedAssigneeId,
  assigning,
  assignError,
  loadComments,
  assignSelected,
  removeComment,
  submitComment,
  loading,
  error,
  exporting,
  exportError,
  canceling,
  terminating,
  confirmingWithdraw,
  confirmingTerminate,
  actionError,
  actionNotice,
  markReportNotificationsRead,
  isAdmin,
  isDeployment,
  trace,
  traceIsLive,
  traceLabel,
  isInvestigating,
  canDispose,
  assigneeLabel,
  outcomeLabel,
  statusTone,
  statusBadge,
  aiLabel,
  conclusions,
  reportFeedback,
  feedbackSaving,
  feedbackError,
  myReportFeedback,
  sendReportFeedback,
  knowledge,
  knowledgeScore,
  knowledgeVerdictLabel,
  executionResults,
  hasReportContent,
  evidenceById,
  realEvidences,
  evidenceGroups,
  citedEvidenceIds,
  citedRealEvidences,
  fakeEvidenceCount,
  sourceName,
  dataStatusLabel,
  hypothesisStatusLabel,
  sourceLabelForEvidence,
  evidenceLabel,
  findingGroups,
  visibleFindingGroups,
  untrustedCount,
  factCards,
  summaryPoints,
  solutionHtml,
  solutionSteps,
  solutionStepCount,
  copiedSolutionStep,
  solutionCount,
  evidenceCount,
  processCount,
  showDetailTabs,
  copySolutionCommands,
  rootCauseHtml,
  rootVerification,
  verdictText,
  verdictTone,
  nextAction,
  formatTime,
  evidenceAnchor,
  scrollToEvidence,
  formatArgv,
  resultPreview,
  downloadReport,
  goApprovals,
  handleNextAction,
  withdrawTask,
  terminateRun,
} = useTaskDetail()

const {
  isInventoryTask,
  primaryAnswer,
  displayHeadline,
  headlinePoints,
  primaryAnswerIsHeadline,
  hasDisplayHeadline,
  hasPrimaryAnswer,
  inventoryGroups,
  coverageIssues,
  incidentalFindings,
  rootCauseIsIncidentalFallback,
  orderedConclusions,
  hasStructuredInventory,
} = useAnswerPresentation(task, report)
</script>

<template>
  <div class="page-stack">
    <p v-if="error" class="error">{{ error }}</p>

    <TaskDetailHeader
      :task="task"
      :task-id="taskId"
      :status-badge="statusBadge"
      :assignee-label="assigneeLabel"
      :can-assign="canAssign"
      :assignable-users="assignableUsers"
      v-model:selectedAssigneeId="selectedAssigneeId"
      :assigning="assigning"
      :assign-error="assignError"
      :format-time="formatTime"
      :outcome-label="outcomeLabel"
      :next-action="nextAction"
      :can-dispose="canDispose"
      :report="report"
      :has-report-content="hasReportContent"
      :exporting="exporting"
      :export-error="exportError"
      :action-notice="actionNotice"
      :action-error="actionError"
      :is-admin="isAdmin"
      :terminating="terminating"
      v-model:confirmingTerminate="confirmingTerminate"
      v-model:confirmingWithdraw="confirmingWithdraw"
      :canceling="canceling"
      @assign-selected="assignSelected"
      @handle-next-action="handleNextAction"
      @download-report="downloadReport"
      @terminate-run="terminateRun"
      @withdraw-task="withdrawTask"
    />

    <div class="detail-layout">
      <div class="detail-main">

    <TaskDetailOverview
      :loading="loading"
      :is-deployment="isDeployment"
      :task-id="taskId"
      :task="task"
      :show-detail-tabs="showDetailTabs"
      :report="report"
      :active-tab="activeTab"
      :solution-count="solutionCount"
      :evidence-count="evidenceCount"
      :real-evidence-count="realEvidences.length"
      :trace-count="trace.length"
      :knowledge-count="knowledge.length"
      @select-detail-tab="selectDetailTab"
    />

    <section v-if="showDetailTabs && activeTab === 'summary'" id="tab-summary" class="tab-panel" role="tabpanel" aria-labelledby="tab-btn-summary">
      <LiveProcessCard
        v-if="isInvestigating"
        :items="trace"
        :total="trace.length"
        @open-process="selectDetailTab('process')"
      />

      <section v-if="report" class="card report-hero">
        <div class="hero-head">
          <div>
            <h2>排查结论</h2>
            <p class="desc">数据质量：{{ report.data_quality }}</p>
          </div>
          <div class="hero-badges">
            <span v-if="verdictText" class="badge" :class="verdictTone">{{ verdictText }}</span>
            <span class="badge badge-muted">{{ aiLabel }}</span>
          </div>
        </div>
        <ol
          v-if="hasDisplayHeadline && headlinePoints.length > 1"
          class="verdict-points verdict-points-headline"
          :class="{ 'verdict-points-unverified': rootVerification && !rootVerification.verified }"
        >
          <li v-for="(point, index) in headlinePoints" :key="`headline-${index}`">
            {{ point }}
          </li>
        </ol>
        <p
          v-else-if="hasDisplayHeadline"
          class="verdict verdict-headline"
          :class="{ 'verdict-unverified': rootVerification && !rootVerification.verified }"
        >
          {{ displayHeadline }}
        </p>
        <p v-if="!hasDisplayHeadline && !summaryPoints.length" class="verdict">
          报告已生成，但暂时没有可提炼的摘要。
        </p>

        <div
          v-if="hasPrimaryAnswer && !primaryAnswerIsHeadline"
          class="primary-answer-panel"
        >
          <span class="section-kicker">先回答</span>
          <p>{{ primaryAnswer }}</p>
        </div>

        <div v-if="report.scope" class="verdict-block">
          <span class="verdict-label">判断范围</span>
          <p class="verdict-scope">{{ report.scope }}</p>
        </div>

        <div v-if="coverageIssues.length && !hasStructuredInventory" class="coverage-warning-card">
          <span class="section-kicker">数据覆盖</span>
          <p>
            有 {{ coverageIssues.length }} 台主机没有取到完整数据，不能据此判断“没有服务”：
            {{ coverageIssues.map((item) => `${item.host}（${item.reason || item.status}）`).join('；') }}
          </p>
        </div>

        <ServiceInventoryCard
          v-if="hasStructuredInventory"
          :groups="inventoryGroups"
          :evidence-label="evidenceLabel"
          @select-evidence="scrollToEvidence"
        />

        <ConclusionPointsCard
          v-if="isInventoryTask && !hasStructuredInventory && orderedConclusions.length"
          title="结论要点"
          description="先看清单和直接事实；根因与验证证据在下方。"
          :points="orderedConclusions"
          :evidence-label="evidenceLabel"
          @select-evidence="scrollToEvidence"
        />

        <IncidentalFindingsCard
          v-if="incidentalFindings.length"
          :findings="incidentalFindings"
          :evidence-label="evidenceLabel"
          @select-evidence="scrollToEvidence"
        />

        <div v-if="report.root_cause && !rootCauseIsIncidentalFallback" class="root-cause-panel">
          <div class="root-cause-head">
            <h3>根因判断</h3>
          </div>
          <div class="root-cause-markdown" v-html="rootCauseHtml"></div>
        </div>

        <div
          v-if="rootVerification && !rootCauseIsIncidentalFallback"
          class="verification-strip"
          :class="rootVerification.human ? 'is-human' : (rootVerification.verified ? 'is-verified' : 'is-unverified')"
        >
          <span class="verification-badge">
            {{ rootVerification.human ? '人工确认根因' : (rootVerification.verified ? '根因已验证' : '根因待验证') }}
          </span>
          <div class="verification-main">
            <p v-if="rootVerification.human">
              {{ rootVerification.note || '管理员已根据现场反馈人工校正根因。' }}
            </p>
            <p v-else-if="rootVerification.verified">
              结论由 {{ rootVerification.supported }} 条被证据支持的假设和
              {{ rootVerification.evidenceIds.length }} 条证据交叉验证后得出；
              已排除 {{ rootVerification.rejected }} 条假设，另有
              {{ rootVerification.inconclusive }} 条证据不足。
            </p>
            <p v-else>
              还没有证据支持的假设，当前只能作为初步判断，不能作为最终根因。
              请先补齐验证证据，或执行只读核对命令后再下结论。
            </p>
            <div v-if="rootVerification.evidenceIds.length" class="verification-evidence">
              <span class="desc">验证证据</span>
              <EvidenceRefs
                :ids="rootVerification.evidenceIds"
                :evidence-label="evidenceLabel"
                @select-evidence="scrollToEvidence"
              />
            </div>
          </div>
        </div>

        <ReportFeedback
          v-if="report.root_cause && !rootCauseIsIncidentalFallback"
          target-type="root_cause"
          :target-index="0"
          :feedback="myReportFeedback('root_cause', 0)"
          :saving="feedbackSaving === 'root_cause-0'"
          @submit="sendReportFeedback"
        />

        <div v-if="report.next_step" class="ai-next-step">
          <span class="section-kicker">建议下一步</span>
          <p>{{ report.next_step }}</p>
        </div>

        <div class="fact-strip hero-facts" aria-label="关键事实">
          <div v-for="fact in factCards" :key="fact.label" class="fact" :class="`fact-${fact.tone}`">
            <span class="fact-label">{{ fact.label }}</span>
            <span class="fact-value">{{ fact.value }}<small>{{ fact.unit }}</small></span>
            <span class="fact-hint">{{ fact.hint }}</span>
          </div>
        </div>
      </section>
      <section v-else class="card card-subtle">
        <p class="desc">报告尚未生成；可先查看「过程与参考」，了解 Agent 正在读取哪些现场数据。</p>
      </section>
    </section>

    <section v-if="showDetailTabs && activeTab === 'solution'" id="tab-solution" class="tab-panel" role="tabpanel" aria-labelledby="tab-btn-solution">
      <section v-if="report?.solution" class="card solution-card">
        <div class="solution-head">
          <div>
            <span class="section-kicker">可执行方案</span>
            <h2>初步方案</h2>
            <p class="desc">
              命令可直接复制执行，但不会自动运行；涉及变更的步骤仍需人工确认或走审批。
            </p>
          </div>
          <span class="badge badge-muted">{{ solutionSteps.length || solutionStepCount }} 步</span>
        </div>
        <div v-if="solutionSteps.length" class="solution-steps">
          <article
            v-for="step in solutionSteps"
            :key="step.index"
            class="solution-step"
          >
            <span class="solution-step-index">{{ step.index }}</span>
            <div class="solution-step-main">
              <h3>{{ step.title }}</h3>
              <div
                v-if="step.descriptionHtml"
                class="solution-step-desc"
                v-html="step.descriptionHtml"
              ></div>
              <div v-if="step.commands.length" class="command-panel">
                <div class="command-panel-head">
                  <span>可执行命令</span>
                  <button
                    type="button"
                    class="command-copy"
                    @click="copySolutionCommands(step)"
                  >
                    {{ copiedSolutionStep === step.index ? '已复制' : '复制' }}
                  </button>
                </div>
                <pre><code>{{ step.commands.join('\n') }}</code></pre>
              </div>
            </div>
          </article>
        </div>
        <div v-else class="solution-markdown" v-html="solutionHtml"></div>
      </section>
      <section v-else class="card card-subtle">
        <p class="desc">这份报告没有可执行方案；请先回到「结论」确认根因，或查看「证据」判断缺口。</p>
      </section>
    </section>

      <section v-if="report && activeTab === 'evidence' && summaryPoints.length" class="card report-points-card">
        <div class="card-head">
          <div>
            <h2>判断依据</h2>
            <p class="desc">报告摘要按句拆开，作为结论的阅读索引；详细核实事实在下方。</p>
          </div>
          <span class="badge badge-muted">{{ summaryPoints.length }} 条</span>
        </div>
        <ol class="verdict-points">
          <li v-for="(point, index) in summaryPoints" :key="index">
            <span class="verdict-point-index" aria-hidden="true">{{ index + 1 }}</span>
            <div class="verdict-point-text" v-html="renderInlineMarkdown(point)"></div>
          </li>
        </ol>
      </section>

      <section v-if="report && activeTab === 'evidence'" class="card findings-card">
        <div class="card-head">
          <div>
            <h2>证据与验证</h2>
            <p class="desc">根因和方案已在上方；这里展开看原因假设与逐条核实事实。</p>
          </div>
          <span class="badge badge-muted">{{ conclusions.length }} 条</span>
        </div>

        <div v-if="report.report_complete === false" class="report-gap-banner">
          <strong>根因未闭环</strong>
          <span>
            这份报告只确认了异常，还缺{{ (report.report_gaps || []).join('、') || '根因或方案' }}；
            不能作为排查完成，也不能进知识库。
          </span>
        </div>

        <section v-if="report.unknowns.length" class="evidence-subsection unknowns">
          <div class="subsection-head">
            <strong>还缺 {{ report.unknowns.length }} 项数据</strong>
            <span class="desc">补齐后才能把推断升级为结论。</span>
          </div>
          <ul>
            <li v-for="item in report.unknowns" :key="item">{{ item }}</li>
          </ul>
        </section>

        <section v-if="report.hypotheses?.length" class="evidence-subsection agentic-block">
          <div class="agentic-head">
            <strong>假设与验证</strong>
            <span class="desc">先列原因假设，再按证据逐条给结论</span>
          </div>
          <div
            v-for="(item, index) in report.hypotheses"
            :key="`hyp-${index}`"
            class="hypothesis-row"
            :class="`hypothesis-${item.status}`"
          >
            <span class="hypothesis-index">{{ index + 1 }}</span>
            <div class="hypothesis-main">
              <p>{{ item.statement }}</p>
              <small v-if="item.note">{{ item.note }}</small>
              <div class="evidence-links">
                <span class="tag">{{ hypothesisStatusLabel(item.status) }}</span>
                <EvidenceRefs
                  :ids="item.evidence_ids"
                  :evidence-label="evidenceLabel"
                  compact
                  @select-evidence="scrollToEvidence"
                />
              </div>
            </div>
          </div>
        </section>

        <section v-if="conclusions.length" class="evidence-subsection findings-groups">
          <div class="agentic-head">
            <strong>发现与验证</strong>
            <span class="desc">{{ conclusions.length }} 条已核实事实，按结论类型分组</span>
          </div>
          <div
            v-for="group in visibleFindingGroups"
            v-show="group.items.length"
            :key="group.key"
            class="finding-group"
          >
            <div class="group-head">
              <span class="group-dot" :class="`dot-${group.key}`" aria-hidden="true" />
              <strong>{{ group.label }} · {{ group.items.length }}</strong>
              <span class="group-desc">{{ group.description }}</span>
            </div>
            <article
              v-for="item in group.items"
              :key="item.index"
              class="finding"
              :class="`finding-${item.tone}`"
            >
              <div class="finding-row">
                <span class="finding-tag" :class="`finding-tag-${item.tone}`">{{ group.label }}</span>
                <span class="finding-source">{{ item.sourceLabel }}</span>
                <span class="finding-text">{{ item.text }}</span>
              </div>
              <div class="evidence-links">
                <span v-if="item.data_status" class="tag">{{ dataStatusLabel(item.data_status) }}</span>
                <EvidenceRefs
                  :ids="item.evidence_ids ?? []"
                  :evidence-label="evidenceLabel"
                  compact
                  @select-evidence="scrollToEvidence"
                />
                <span v-if="!item.evidence_ids?.length" class="desc">未关联证据</span>
              </div>
              <ReportFeedback
                target-type="conclusion"
                :target-index="item.index"
                :feedback="myReportFeedback('conclusion', item.index)"
                :saving="feedbackSaving === `conclusion-${item.index}`"
                @submit="sendReportFeedback"
              />
            </article>
          </div>
        </section>
        <p v-if="feedbackError" class="error report-feedback-error">{{ feedbackError }}</p>
        <p v-if="untrustedCount" class="excluded-line">
          另有 {{ untrustedCount }} 条 AI 结论基于演练/示例数据，已排除，不参与判断。
        </p>
        <div v-if="!conclusions.length && !realEvidences.length" class="empty-state findings-empty-state">
          <span class="empty-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M6 3h9l3 3v15H6z" />
              <path d="M15 3v3h3M9 10h6M9 14h6M9 18h4" />
            </svg>
          </span>
          <h2>这份报告还没有结论与证据</h2>
          <p>报告已经生成，但当前没有可用的结论和证据快照；不要把“无结论”当成“正常”。</p>
          <p class="empty-risk">建议补充数据源后重新排查，或先查看「过程与参考」。</p>
        </div>
        <p v-else-if="!conclusions.length" class="desc findings-empty-note">
          已有证据快照，但还没有形成结论；不要把“无结论”当成“正常”。
        </p>
        <p v-if="fakeEvidenceCount && !conclusions.length && !realEvidences.length" class="desc findings-empty-note">
          已排除 {{ fakeEvidenceCount }} 条演练/示例数据，不参与判断。
        </p>
      </section>

      <section
        v-if="report && activeTab === 'evidence' && realEvidences.length"
        class="card evidence-section"
      >
        <div class="card-head">
          <div>
            <h2>证据快照</h2>
            <p class="desc">展示全部真实证据快照；已引用的证据见上方结论，演练/示例数据已排除。</p>
          </div>
          <span class="badge badge-muted">{{ realEvidences.length }} 条</span>
        </div>
        <div class="evidence-body">
          <section v-for="group in evidenceGroups" :key="group.key" class="evidence-host-group">
            <header class="evidence-host-head">
              <div>
                <h3>{{ group.label }}</h3>
                <p class="desc">{{ group.items.length }} 条检查记录</p>
              </div>
            </header>
            <div class="evidence-grid">
              <article
                v-for="item in group.items"
                :id="evidenceAnchor(item.id)"
                :key="item.id"
                class="evidence-item"
              >
                <div class="evidence-title-row">
                  <span class="evidence-title">
                    <strong>{{ sourceName(item.source) }}</strong>
                    <span class="evidence-target">
                      {{ evidenceTargetLabel(item.target) || item.id }}
                    </span>
                    <em class="mono">{{ evidenceProbeLabel(item.id, item.target) }}</em>
                  </span>
                  <span class="evidence-tags">
                    <span v-if="item.fake" class="tag tag-warn">演练数据</span>
                    <span class="tag" :class="{ 'tag-warn': item.data_status !== 'ok' }">
                      {{ dataStatusLabel(item.data_status) }}
                    </span>
                  </span>
                </div>
                <figure v-if="item.thumbnail" class="evidence-shot">
                  <img :src="item.thumbnail" :alt="`${item.target || '报错截图'} 缩略图`" />
                  <figcaption>
                    截图缩略图（原图不对外展示）
                    <span v-if="item.ocr_confidence !== undefined">
                      · OCR 置信度 {{ (item.ocr_confidence * 100).toFixed(1) }}%
                    </span>
                  </figcaption>
                </figure>
                <dl class="evidence-meta">
                  <div><dt>目标</dt><dd>{{ evidenceTargetLabel(item.target) || '—' }}</dd></div>
                  <div><dt>采集时间</dt><dd>{{ formatTime(item.observed_at) }}</dd></div>
                </dl>
                <pre class="evidence-text">{{ item.excerpt || '快照为空' }}</pre>
              </article>
            </div>
          </section>
        </div>
      </section>

    <section
      v-if="!isDeployment && activeTab === 'process' && (executionResults.length || execution?.terminated_at)"
      class="card execution-card"
    >
      <div class="card-head">
        <div>
          <h2>执行结果</h2>
          <p class="desc">命令、退出码与短期输出回显；许可令牌不在接口里。</p>
        </div>
        <span class="badge badge-muted">{{ execution?.platform || '—' }} / {{ execution?.host || '—' }}</span>
      </div>
      <div class="execution-body">
        <p v-if="execution?.terminated_at" class="terminated-note">
          已于 {{ formatTime(execution.terminated_at) }} 终止执行（{{ execution.termination_reason || '未填写原因' }}）。
          已发出的命令可能已产生部分影响，请结合审计与主机状态核对。
        </p>
        <div v-if="executionResults.length" class="table-wrap">
          <table class="table">
            <thead>
              <tr><th>命令</th><th>范围</th><th>退出码</th><th>输出</th></tr>
            </thead>
            <tbody>
              <tr v-for="(item, index) in executionResults" :key="index">
                <td class="mono">{{ formatArgv(item) }}</td>
                <td>{{ item.scope }}</td>
                <td>{{ item.exit_code ?? '—' }}</td>
                <td>
                  <pre class="evidence-text">{{ resultPreview(item) }}</pre>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <section
      v-if="activeTab === 'process' && trace.length"
      class="card trace-card"
    >
      <div class="card-head">
        <div>
          <h2>{{ traceIsLive ? 'AI 排查过程（实时）' : 'AI 排查过程' }}</h2>
          <p class="desc">
            {{ traceIsLive
              ? '运行中实时显示模型说明、工具调用与回包；内容已脱敏并截断。'
              : '思考链、工具调用与回包按发生顺序排列；内容已脱敏并截断。' }}
          </p>
        </div>
        <span class="badge" :class="traceIsLive ? 'badge-info' : 'badge-muted'">
          {{ traceIsLive ? '实时 · ' : '' }}{{ trace.length }} 步
        </span>
      </div>
      <ol class="trace-list">
        <li
          v-for="item in trace"
          :key="item.index"
          class="trace-item"
          :class="`trace-${item.kind}`"
        >
          <div class="trace-head">
            <span class="trace-index">{{ item.index }}</span>
            <strong>{{ traceLabel[item.kind] || item.kind }}</strong>
            <span v-if="item.name" class="trace-tool mono">{{ item.name }}</span>
            <span v-if="item.state" class="trace-state">{{ item.state }}</span>
          </div>
          <pre v-if="item.text" class="trace-text">{{ item.text }}</pre>
          <div v-if="item.input" class="trace-block">
            <span class="trace-block-label">入参</span>
            <pre class="trace-text">{{ item.input }}</pre>
          </div>
          <div v-if="item.output" class="trace-block">
            <span class="trace-block-label">回包</span>
            <pre class="trace-text">{{ item.output }}</pre>
          </div>
        </li>
      </ol>
    </section>

    <section
      v-if="knowledge.length && activeTab === 'process'"
      class="card knowledge-card"
    >
      <div class="card-head">
        <div>
          <h2>历史参考</h2>
          <p class="desc">
            只读参考，不是本次证据；历史结论不能直接当成本次结论。
          </p>
        </div>
        <span class="badge badge-muted">{{ knowledge.length }} 条</span>
      </div>
      <div class="knowledge-body">
        <article
          v-for="item in knowledge"
          :key="item.task_id"
          class="knowledge-item"
        >
          <div class="knowledge-head">
            <strong>{{ item.title || '未命名历史报告' }}</strong>
            <span class="knowledge-score">{{ knowledgeScore(item.score) }}</span>
          </div>
          <div class="knowledge-meta">
            <span class="tag">{{ knowledgeVerdictLabel(item.verdict) }}</span>
            <span v-if="item.updated_at" class="knowledge-date">
              更新于 {{ formatTime(item.updated_at) }}
            </span>
          </div>
          <p v-if="item.headline" class="knowledge-headline">{{ item.headline }}</p>
          <p v-if="item.summary" class="knowledge-summary">{{ item.summary }}</p>
          <ul v-if="item.conclusions.length" class="knowledge-conclusions">
            <li v-for="(conclusion, index) in item.conclusions" :key="index">
              {{ conclusion.text }}
            </li>
          </ul>
          <div v-if="item.task_id" class="knowledge-foot">
            <RouterLink class="knowledge-link" :to="`/tasks/${item.task_id}`">
              查看历史报告 →
            </RouterLink>
          </div>
        </article>
      </div>
    </section>

    <section
      v-if="activeTab === 'process' && !trace.length && !knowledge.length && !executionResults.length && !execution?.terminated_at"
      class="card card-subtle"
    >
      <p class="desc">这次排查没有过程轨迹、执行结果或历史参考。</p>
    </section>
    <TaskCommentsCard
      v-if="!isDeployment && !loading"
      :comments="comments"
      :comment-loading="commentLoading"
      :comment-unread-notice="commentUnreadNotice"
      :comments-has-more="commentsHasMore"
      :comment-deleting-id="commentDeletingId"
      :comment-error="commentError"
      :can-comment="canComment"
      v-model:comment-body="commentBody"
      v-model:comment-kind="commentKind"
      :comment-saving="commentSaving"
      :format-time="formatTime"
      :can-delete-comment="canDeleteComment"
      @load-more="loadComments(true)"
      @remove-comment="removeComment"
      @submit-comment="submitComment"
    />
      </div>
    </div>
  </div>
</template>

<style scoped src="./task-detail/task-detail.css"></style>
