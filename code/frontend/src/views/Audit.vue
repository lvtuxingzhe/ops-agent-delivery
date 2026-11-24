<script setup lang="ts">
import { useAudit } from './audit/useAudit'

const {
  advancedFilterCount,
  advancedOpen,
  applyFilters,
  actorOpen,
  actorSearch,
  actorsLoading,
  clearActors,
  closeActorMenu,
  currentPage,
  error,
  expandedId,
  exportCsv,
  exporting,
  filteredActors,
  filters,
  formatFullTime,
  formatTime,
  hasMore,
  limit,
  loading,
  notice,
  offset,
  openActorMenu,
  prettyPayload,
  removeActor,
  resetFilters,
  rows,
  toggleActor,
  togglePayload,
  total,
  turnPage,
  useTrace,
} = useAudit()
</script>

<template>
  <div class="page-stack audit-page">
    <section class="card audit-filters">
      <div class="audit-filter-primary">
        <input
          v-model.trim="filters.q"
          class="audit-quick-search"
          placeholder="搜索事件"
          @keyup.enter="applyFilters"
        />
        <button class="primary" type="button" :disabled="loading" @click="applyFilters">
          {{ loading ? '查询中…' : '查询' }}
        </button>
        <button class="ghost" type="button" :disabled="loading" @click="resetFilters">重置</button>
        <button class="ghost advanced-toggle" type="button" @click="advancedOpen = !advancedOpen">
          高级筛选
          <span v-if="advancedFilterCount" class="advanced-count">{{ advancedFilterCount }}</span>
          <span class="advanced-caret">{{ advancedOpen ? '收起' : '展开' }}</span>
        </button>
        <button class="ghost" type="button" :disabled="loading || exporting" @click="exportCsv">
          {{ exporting ? '导出中…' : '导出 CSV' }}
        </button>
        <div class="audit-filter-meta">
          <span class="tag tag-on">只读</span>
          <span class="audit-inline-total"><strong>{{ total }}</strong> 条命中</span>
        </div>
        <span v-if="error" class="filter-error">{{ error }}</span>
        <span v-if="notice" class="export-note">{{ notice }}</span>
      </div>

      <div v-if="advancedOpen" class="audit-advanced-grid">
        <label class="field">
          <span>排查号</span>
          <input v-model.trim="filters.task_id" placeholder="例如 1b994238-..." />
        </label>
        <label class="field">
          <span>trace</span>
          <input v-model.trim="filters.trace_id" placeholder="inv- 或 submit- 开头" />
        </label>
        <label class="field">
          <span>事件类型</span>
          <input v-model.trim="filters.event_type" placeholder="例如 task.started" />
        </label>
        <div class="field actor-field" @focusout="closeActorMenu">
          <span>操作人</span>
          <div class="actor-picker">
            <div class="actor-control" @click="openActorMenu">
              <span v-for="actor in filters.actor" :key="actor" class="actor-chip">
                {{ actor }}
                <button type="button" aria-label="移除操作人" @click.stop="removeActor(actor)">×</button>
              </span>
              <input
                v-model="actorSearch"
                type="text"
                autocomplete="off"
                placeholder="搜索账号，可多选"
                role="combobox"
                aria-autocomplete="list"
                aria-controls="audit-actor-menu"
                :aria-expanded="actorOpen"
                @focus="openActorMenu"
              />
            </div>
            <div v-if="actorOpen" id="audit-actor-menu" class="actor-menu">
              <div class="actor-menu-head">
                <span>操作人</span>
                <button v-if="filters.actor.length" class="actor-clear" type="button" @click="clearActors">
                  清空已选
                </button>
              </div>
              <p v-if="actorsLoading" class="actor-menu-empty">正在读取操作人…</p>
              <button
                v-for="option in filteredActors"
                :key="option.actor"
                class="actor-option"
                :class="{ 'is-selected': filters.actor.includes(option.actor) }"
                type="button"
                @click="toggleActor(option.actor)"
              >
                <span class="actor-check">{{ filters.actor.includes(option.actor) ? '✓' : '' }}</span>
                <span class="actor-name">{{ option.actor }}</span>
                <span class="actor-count">{{ option.count }}</span>
              </button>
              <p v-if="!actorsLoading && !filteredActors.length" class="actor-menu-empty">
                没有匹配的操作人
              </p>
            </div>
          </div>
        </div>
        <label class="field">
          <span>起始日期（含）</span>
          <input v-model="filters.since" type="date" />
        </label>
        <label class="field">
          <span>结束日期（不含）</span>
          <input v-model="filters.until" type="date" />
        </label>
        <label class="field">
          <span>每页条数</span>
          <select v-model.number="limit" @change="applyFilters">
            <option :value="20">20</option>
            <option :value="50">50</option>
            <option :value="100">100</option>
            <option :value="200">200</option>
          </select>
        </label>
      </div>
    </section>

    <section class="card audit-results">
      <div class="result-head">
        <div>
          <h3>流水</h3>
          <p class="desc">按时间倒序。点一行展开 payload；payload 已按敏感键做剔除。</p>
        </div>
        <span class="tag tag-on">第 {{ currentPage }} 页</span>
      </div>

      <div v-if="!loading && !rows.length && !error" class="audit-empty-state">
        <strong>没有命中</strong>
        <p>放宽时间范围，或清空排查号 / trace 再查。</p>
      </div>

      <div v-else-if="loading && !rows.length" class="audit-empty-state">
        <span class="audit-spinner" aria-hidden="true"></span>
        <strong>正在查询审计流水…</strong>
        <p>按当前筛选条件读取事件。</p>
      </div>

      <div v-else-if="error" class="audit-empty-state">
        <strong>查询失败</strong>
        <p>{{ error }}</p>
      </div>

      <div v-else class="table-wrap">
        <table class="table audit-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>事件</th>
              <th>操作人</th>
              <th>排查号</th>
              <th>trace</th>
              <th>来源</th>
              <th />
            </tr>
          </thead>
          <tbody>
            <template v-for="item in rows" :key="item.id">
              <tr class="audit-row" @click="togglePayload(item)">
                <td class="mono time-cell" :title="formatFullTime(item.created_at)">
                  {{ formatTime(item.created_at) }}
                </td>
                <td><span class="event" :title="item.event_type">{{ item.event_type }}</span></td>
                <td>{{ item.actor || '—' }}</td>
                <td>
                  <router-link
                    v-if="item.task_id"
                    class="page-link"
                    :to="`/tasks/${item.task_id}`"
                    @click.stop
                  >
                    {{ item.task_id }}
                  </router-link>
                  <span v-else>—</span>
                </td>
                <td class="mono trace" :title="item.trace_id">{{ item.trace_id }}</td>
                <td>{{ item.client_source || '—' }}</td>
                <td class="mono">{{ expandedId === item.id ? '收起' : '展开' }}</td>
              </tr>
              <tr v-if="expandedId === item.id" class="payload-row">
                <td colspan="7">
                  <div class="payload-actions">
                    <button type="button" class="mini-link" @click="useTrace(item.trace_id)">
                      按本 trace 筛选
                    </button>
                  </div>
                  <pre class="payload">{{ prettyPayload(item) }}</pre>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <div class="pager">
        <button class="ghost" type="button" :disabled="loading || offset === 0" @click="turnPage(-1)">
          上一页
        </button>
        <span class="mono">offset {{ offset }} · limit {{ limit }}</span>
        <button class="ghost" type="button" :disabled="loading || !hasMore" @click="turnPage(1)">
          下一页
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped src="./audit/audit.css"></style>
