<script setup lang="ts">
/**
 * 排查列表页：模板与交互入口。
 *
 * 数据、筛选、分页和导出的实现都在 ./tasks/useTasks.ts。
 */
import ResizableTable from '../components/ResizableTable.vue'
import { useTasks } from './tasks/useTasks'

const {
  router,
  tasks,
  total,
  page,
  status,
  assignee,
  category,
  timeRange,
  keyword,
  loading,
  exporting,
  error,
  TASK_COLUMNS,
  DEFAULT_TASK_GRID,
  COLUMN_WIDTHS_STORAGE_KEY,
  tableRef,
  hasCustomWidths,
  clearNotice,
  categoryOptions,
  statusOptions,
  assigneeOptions,
  timeOptions,
  canSubmit,
  totalPages,
  investigationCounts,
  allTotal,
  activeFilterCount,
  clearLabel,
  summaryItems,
  refreshAll,
  changeFilter,
  clearFilters,
  goPage,
  exportTasks,
  formatTime,
  shortTime,
  assigneeName,
  statusClass,
  statusTone,
  statusText,
  openTask,
  resetColumnWidths,
} = useTasks()
</script>

<template>
  <div class="page-stack">
    <section class="workspace-toolbar" aria-label="排查筛选">
      <div class="toolbar-head">
        <div class="toolbar-summary">
          <span v-if="loading" class="summary-main">正在加载排查任务…</span>
          <span v-else class="summary-main">
            {{ activeFilterCount ? '筛选后' : '共' }}
            <b>{{ activeFilterCount ? total : allTotal }}</b>
            条排查任务
          </span>
          <span v-if="!loading && activeFilterCount && investigationCounts" class="summary-all">
            / 全部 {{ allTotal }}
          </span>
          <span v-if="!loading && summaryItems.length" class="summary-items">
            <span
              v-for="item in summaryItems"
              :key="item.label"
              class="summary-item"
              :class="`tone-${item.tone}`"
            >
              {{ item.label }} <b>{{ item.value }}</b>
            </span>
          </span>
          <button
            v-if="activeFilterCount"
            class="clear-filter"
            type="button"
            @click="clearFilters"
          >
            {{ clearLabel }}
          </button>
          <span v-if="clearNotice" class="clear-feedback" role="status">{{ clearNotice }}</span>
        </div>
        <div class="page-actions">
          <button
            v-if="hasCustomWidths"
            class="ghost"
            type="button"
            @click="resetColumnWidths"
          >
            重置列宽
          </button>
          <button class="ghost" type="button" :disabled="loading" @click="refreshAll">刷新</button>
          <button class="ghost" type="button" :disabled="loading || exporting || !tasks.length" @click="exportTasks">
            {{ exporting ? '导出中…' : '导出 CSV' }}
          </button>
          <button v-if="canSubmit" class="primary" type="button" @click="router.push('/tasks/new')">
            新建排查
          </button>
        </div>
      </div>

      <div class="filters">
        <div class="filter-group">
          <label class="filter">
            <span>状态</span>
            <select v-model="status" @change="changeFilter">
              <option v-for="option in statusOptions" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
          <label class="filter">
            <span>处理人</span>
            <select v-model="assignee" @change="changeFilter">
              <option v-for="option in assigneeOptions" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
          <label class="filter">
            <span>类型</span>
            <select v-model="category" @change="changeFilter">
              <option v-for="option in categoryOptions" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
          <label class="filter">
            <span>时间</span>
            <select v-model="timeRange" @change="changeFilter">
              <option v-for="option in timeOptions" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
        </div>
        <div class="search-field">
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索任务"
            aria-label="搜索任务标题"
            @keyup.enter="changeFilter"
          />
          <button type="button" class="search-submit" @click="changeFilter">搜索</button>
        </div>
      </div>
    </section>

    <ResizableTable
      ref="tableRef"
      :columns="TASK_COLUMNS"
      :default-grid="DEFAULT_TASK_GRID"
      :storage-key="COLUMN_WIDTHS_STORAGE_KEY"
      :gap="12"
      :padding-x="16"
      card-class="card list-card"
      head-class="table-head"
      @custom-change="hasCustomWidths = $event"
    >
      <div v-if="error" class="table-state error-state">
        <strong>排查列表加载失败</strong>
        <small>{{ error }}</small>
        <button class="ghost" type="button" @click="refreshAll">重试</button>
      </div>
      <div v-else-if="loading" class="table-state loading-state" aria-busy="true">
        <span class="loading-spinner" aria-hidden="true" />
        <strong>正在加载排查任务…</strong>
      </div>
      <div v-else-if="tasks.length" class="task-list">
        <button
          v-for="task in tasks"
          :key="task.id"
          class="task-row"
          type="button"
          @click="openTask(task)"
        >
          <span class="task-main">
            <strong :title="task.title || '（无标题）'">{{ task.title || '（无标题）' }}</strong>
            <small><span class="task-id" :title="task.id">#{{ task.id.slice(0, 8) }}</span></small>
          </span>
          <span class="type-cell">{{ task.category_text || '—' }}</span>
          <span class="assignee-cell" :class="{ 'is-unassigned': !task.assignee }">
            {{ assigneeName(task) }}
          </span>
          <span class="status-cell">
            <i class="status-dot" :class="`dot-${statusTone(task)}`" aria-hidden="true" />
            <span class="badge" :class="statusClass(task)">{{ statusText(task) }}</span>
          </span>
          <span class="task-time" :title="formatTime(task.created_at)">
            {{ shortTime(task.created_at) }}
          </span>
          <span class="row-action">查看 <i aria-hidden="true">→</i></span>
        </button>
      </div>
      <div v-else class="table-state empty-state">
        <strong>{{ keyword.trim() ? `没有匹配“${keyword.trim()}”的排查任务` : '这个条件下还没有排查' }}</strong>
        <small>{{ activeFilterCount ? '换个关键词，或清空筛选查看全部任务。' : '新建一条排查后，这里会显示记录。' }}</small>
        <div class="empty-actions">
          <button v-if="activeFilterCount" class="ghost" type="button" @click="clearFilters">
            {{ clearLabel }}
          </button>
          <button v-if="canSubmit" class="primary" type="button" @click="router.push('/tasks/new')">
            新建排查
          </button>
        </div>
      </div>

      <div v-if="tasks.length && totalPages > 1" class="pager">
        <button class="plain" type="button" :disabled="page <= 1 || loading" @click="goPage(page - 1)">上一页</button>
        <span>{{ page }} / {{ totalPages }} · 本页 {{ tasks.length }} 条</span>
        <button class="plain" type="button" :disabled="page >= totalPages || loading" @click="goPage(page + 1)">下一页</button>
      </div>
    </ResizableTable>
  </div>
</template>

<style scoped src="./tasks/tasks.css"></style>
