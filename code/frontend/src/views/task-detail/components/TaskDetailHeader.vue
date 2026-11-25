<script setup lang="ts">
/**
 * 任务详情页顶部：标题、状态、处理人、下一步和页面级动作。
 *
 * 数据仍由 TaskDetail 的 useTaskDetail 管理；这里只负责把头部展示和
 * 交互拆出来，避免详情页模板继续膨胀。
 */
import { computed } from 'vue'

import { type AssignableUser, type TaskInfo, type TaskReport } from '../../../api/tasks'

const props = defineProps<{
  task: TaskInfo | null
  taskId: string
  statusBadge: { tone: string; label: string }
  assigneeLabel: string
  canAssign: boolean
  assignableUsers: AssignableUser[]
  selectedAssigneeId: number | null
  assigning: boolean
  assignError: string
  formatTime: (value: string | null) => string
  outcomeLabel: string
  nextAction: { title: string; text: string; cta?: string }
  canDispose: boolean
  report: TaskReport | null
  hasReportContent: boolean
  exporting: string | null
  exportError: string
  actionNotice: string
  actionError: string
  isAdmin: boolean
  terminating: boolean
  confirmingTerminate: boolean
  confirmingWithdraw: boolean
  canceling: boolean
}>()

const emit = defineEmits<{
  (event: 'update:selectedAssigneeId', value: number | null): void
  (event: 'update:confirmingTerminate', value: boolean): void
  (event: 'update:confirmingWithdraw', value: boolean): void
  (event: 'assignSelected'): void
  (event: 'handleNextAction'): void
  (event: 'downloadReport', format: 'md' | 'pdf' | 'docx'): void
  (event: 'terminateRun'): void
  (event: 'withdrawTask'): void
}>()

const selectedAssignee = computed({
  get: () => props.selectedAssigneeId,
  set: (value: number | null) => emit('update:selectedAssigneeId', value),
})

const confirmingTerminate = computed({
  get: () => props.confirmingTerminate,
  set: (value: boolean) => emit('update:confirmingTerminate', value),
})

const confirmingWithdraw = computed({
  get: () => props.confirmingWithdraw,
  set: (value: boolean) => emit('update:confirmingWithdraw', value),
})
</script>

<template>
  <section class="card detail-head">
    <div class="detail-title">
      <h2>
        {{ task?.title || '排查详情' }}
        <span v-if="task" class="badge" :class="statusBadge.tone">{{ statusBadge.label }}</span>
      </h2>
      <p class="desc mono">{{ taskId }}</p>
      <p v-if="task?.assignee" class="assignee-line">
        处理人：{{ assigneeLabel }}
      </p>
      <div v-if="task && canAssign" class="assign-bar">
        <label for="task-assignee">转派给</label>
        <select id="task-assignee" v-model.number="selectedAssignee">
          <option :value="null">选择处理人</option>
          <option v-for="item in assignableUsers" :key="item.id" :value="item.id">
            {{ item.display_name || item.username }}
          </option>
        </select>
        <button
          type="button"
          class="ghost"
          :disabled="assigning || !selectedAssigneeId"
          @click="emit('assignSelected')"
        >
          {{ assigning ? '转派中…' : '转派' }}
        </button>
        <span v-if="assignError" class="action-error">{{ assignError }}</span>
      </div>
    </div>
    <div v-if="task" class="meta-line">
      <span class="meta-item">
        <span class="meta-label">提交人</span>
        <span class="meta-value">{{ task.submitted_by_name || task.submitted_by }}</span>
      </span>
      <span class="meta-sep" aria-hidden="true">|</span>
      <span class="meta-item">
        <span class="meta-label">提交时间</span>
        <span class="meta-value">{{ formatTime(task.created_at) }}</span>
      </span>
      <span class="meta-sep" aria-hidden="true">|</span>
      <span class="meta-item">
        <span class="meta-label">结束时间</span>
        <span class="meta-value">{{ formatTime(task.closed_at) }}</span>
      </span>
      <template v-if="task.outcome">
        <span class="meta-sep" aria-hidden="true">|</span>
        <span class="meta-item">
          <span class="meta-label">执行结果</span>
          <span class="meta-value" :class="{ 'meta-danger': task.outcome === 'failed' }">{{ outcomeLabel }}</span>
        </span>
      </template>
    </div>

    <p v-if="task" class="next-action-line">
      <span class="next-action-label">下一步</span>
      <strong v-if="nextAction.title !== statusBadge.label">{{ nextAction.title }}</strong>
      <span class="next-action-text">{{ nextAction.text }}</span>
      <button
        v-if="nextAction.cta"
        class="secondary next-action-cta"
        type="button"
        @click="emit('handleNextAction')"
      >
        {{ nextAction.cta }}
      </button>
    </p>

    <div class="head-toolbar">
      <div class="head-links">
        <router-link
          v-if="task"
          class="back-link"
          :to="{ name: 'audit', query: { trace_id: task.trace_id } }"
        >
          查审计
        </router-link>
        <router-link class="back-link" to="/tasks">返回排查</router-link>
        <router-link
          v-if="task?.status === 'report_ready' && canDispose"
          class="disposition-link"
          :to="{ name: 'task-disposition', params: { id: taskId } }"
        >
          创建处置方案
        </router-link>
      </div>

      <div v-if="report && hasReportContent" class="head-export">
        <span class="head-export-label">报告导出</span>
        <button class="ghost" type="button" :disabled="Boolean(exporting)" @click="emit('downloadReport', 'md')">
          {{ exporting === 'md' ? '导出中…' : 'Markdown' }}
        </button>
        <button class="ghost" type="button" :disabled="Boolean(exporting)" @click="emit('downloadReport', 'pdf')">
          {{ exporting === 'pdf' ? '导出中…' : 'PDF' }}
        </button>
        <button class="ghost" type="button" :disabled="Boolean(exporting)" @click="emit('downloadReport', 'docx')">
          {{ exporting === 'docx' ? '导出中…' : 'Word' }}
        </button>
      </div>

      <div v-if="task?.status === 'executing' && isAdmin" class="head-danger">
        <button
          class="danger"
          type="button"
          :disabled="terminating"
          @click="emit('terminateRun')"
        >
          {{ terminating ? '终止中…' : confirmingTerminate ? '确认终止' : '终止执行' }}
        </button>
        <button
          v-if="confirmingTerminate"
          class="muted-text"
          type="button"
          :disabled="terminating"
          @click="confirmingTerminate = false"
        >
          取消
        </button>
      </div>

      <div v-if="task?.status === 'awaiting_approval'" class="head-danger">
        <span v-if="confirmingWithdraw" class="secondary-hint">确认后本次排查结束，不会执行任何命令。</span>
        <button
          class="danger-text"
          type="button"
          :disabled="canceling"
          @click="emit('withdrawTask')"
        >
          {{ canceling ? '取消中…' : confirmingWithdraw ? '确认取消' : '取消本次排查' }}
        </button>
        <button
          v-if="confirmingWithdraw"
          class="muted-text"
          type="button"
          :disabled="canceling"
          @click="confirmingWithdraw = false"
        >
          取消
        </button>
      </div>
    </div>

    <p v-if="exportError" class="export-error">{{ exportError }}</p>
    <p v-if="actionNotice" class="action-notice">{{ actionNotice }}</p>
    <p v-if="actionError" class="action-error">{{ actionError }}</p>
  </section>
</template>

<style scoped src="../task-detail.css"></style>
