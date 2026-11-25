<script setup lang="ts">
/**
 * 任务详情页中段：加载态、部署任务桥接、空报告提示和详情页签。
 *
 * 这里不发起请求，只根据父组件传入的状态渲染；切换页签通过事件回传。
 */
import { type TaskInfo, type TaskReport } from '../../../api/tasks'

defineProps<{
  loading: boolean
  isDeployment: boolean
  taskId: string
  task: TaskInfo | null
  showDetailTabs: boolean
  report: TaskReport | null
  activeTab: string
  solutionCount: number
  evidenceCount: number
  realEvidenceCount: number
  traceCount: number
  knowledgeCount: number
}>()

const emit = defineEmits<{
  (event: 'selectDetailTab', tab: 'summary' | 'solution' | 'evidence' | 'process'): void
}>()
</script>

<template>
  <section v-if="loading" class="card"><p class="desc">加载中…</p></section>

  <section v-else-if="isDeployment" class="card deployment-bridge">
    <h2>这是一条部署任务</h2>
    <p class="desc">
      部署执行结果和逐条命令回显已从排查报告里拆出，统一放在「部署 → 执行报告」。
      排查报告只描述主机事实与判断依据，不再和部署结果混在同一页。
    </p>
    <router-link
      class="primary inline-link"
      :to="{ name: 'deployments', query: { tab: 'records', task: taskId } }"
    >
      查看部署执行报告 →
    </router-link>
  </section>

  <section v-else-if="!showDetailTabs" class="card report-empty">
    <div class="empty-state">
      <span class="empty-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path d="M6 3h9l3 3v15H6z" />
          <path d="M15 3v3h3M9 10h6M9 14h6M9 18h4" />
        </svg>
      </span>
      <h2>
        {{
          task?.status === 'running' || task?.status === 'draft'
            ? 'Agent 正在排查'
            : report
              ? '这份报告还没有结论与证据'
              : '报告与证据还没有生成'
        }}
      </h2>
      <p v-if="task?.status === 'running' || task?.status === 'draft'">
        Agent 正在执行；过程事件到达后会实时显示在「过程与参考」标签页。报告完成后，结论、证据快照和完整轨迹会一起归档。
      </p>
      <p v-else-if="report">
        报告已生成，但当前没有可用的结论和证据快照。建议补充数据源后重新排查，不要直接批准执行。
      </p>
      <p v-else>
        Agent 完成排查后，结论和证据快照会自动出现在这里。可以稍后刷新，或先回排查列表查看进度。
      </p>
      <p v-if="report && task?.status === 'awaiting_approval'" class="empty-risk">
        当前报告没有结论与证据，不建议直接批准执行。
      </p>
    </div>
  </section>

  <nav v-if="showDetailTabs" class="detail-tabs" role="tablist" aria-label="排查详情分区">
    <button
      id="tab-btn-summary"
      type="button"
      class="detail-tab"
      :class="{ 'is-active': activeTab === 'summary' }"
      role="tab"
      :aria-selected="activeTab === 'summary'"
      aria-controls="tab-summary"
      @click="emit('selectDetailTab', 'summary')"
    >
      结论
    </button>
    <button
      id="tab-btn-solution"
      type="button"
      class="detail-tab"
      :class="{ 'is-active': activeTab === 'solution' }"
      role="tab"
      :aria-selected="activeTab === 'solution'"
      aria-controls="tab-solution"
      @click="emit('selectDetailTab', 'solution')"
    >
      方案
      <span v-if="solutionCount" class="tab-count">{{ solutionCount }}</span>
    </button>
    <button
      id="tab-btn-evidence"
      type="button"
      class="detail-tab"
      :class="{ 'is-active': activeTab === 'evidence' }"
      role="tab"
      :aria-selected="activeTab === 'evidence'"
      aria-controls="tab-evidence"
      @click="emit('selectDetailTab', 'evidence')"
    >
      证据
      <span v-if="evidenceCount" class="tab-count">{{ evidenceCount }}/{{ realEvidenceCount }}</span>
    </button>
    <button
      id="tab-btn-process"
      type="button"
      class="detail-tab"
      :class="{ 'is-active': activeTab === 'process' }"
      role="tab"
      :aria-selected="activeTab === 'process'"
      aria-controls="tab-process"
      @click="emit('selectDetailTab', 'process')"
    >
      过程与参考
      <span v-if="traceCount" class="tab-count">{{ traceCount }}</span>
      <span v-if="knowledgeCount" class="tab-count">+{{ knowledgeCount }}</span>
    </button>
  </nav>
</template>

<style scoped src="../task-detail.css"></style>
