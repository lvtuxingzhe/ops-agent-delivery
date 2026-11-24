<script setup lang="ts">
/**
 * 知识治理页：只看 AI 准入的 review / reject，决定放行、驳回或重新判。
 *
 * 页面只做展示和转发；改判必须写理由，AI 原判仍保留可追溯。
 */

import { useKnowledgeGovernance } from './knowledge/useKnowledgeGovernance'
import { formatLocalTime } from '../utils/time'

const {
  me,
  loading,
  error,
  notice,
  items,
  total,
  offset,
  pageSize,
  detail,
  filters,
  overrideForm,
  saving,
  requeueing,
  stats,
  statsLoading,
  canOverride,
  canApprove,
  hasMore,
  query,
  load,
  applyFilters,
  loadMore,
  openDetail,
  submitOverride,
  requeue,
  decisionClass,
  decisionLabel,
} = useKnowledgeGovernance()
</script>

<template>
  <div class="page-stack">
    <div class="subpage-nav-row">
      <nav v-if="canApprove" class="approval-tabs" aria-label="审批导航">
        <RouterLink class="approval-tab" to="/approvals">审批队列</RouterLink>
        <RouterLink class="approval-tab" to="/knowledge/governance">知识治理</RouterLink>
      </nav>
      <div class="page-actions">
        <RouterLink class="ghost link-btn" to="/knowledge">返回知识库</RouterLink>
        <button class="ghost" type="button" :disabled="loading" @click="load()">刷新</button>
      </div>
    </div>

    <p v-if="!canOverride && me" class="notice">
      当前角色只读：可以查看准入记录，改判需要管理员权限。
    </p>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <section v-if="stats" class="card governance-stats">
      <div class="card-head">
        <div>
          <h2>治理指标</h2>
          <p class="desc">
            准入结果、人工改判、重判和近 {{ stats.retrieval.window_days }} 天检索命中；
            检索只记查询哈希和分数，不存查询原文。
          </p>
        </div>
        <span class="badge badge-muted">{{ statsLoading ? '刷新中…' : '实时聚合' }}</span>
      </div>
      <dl class="stats-grid">
        <div><dt>准入记录</dt><dd>{{ stats.total }}</dd></div>
        <div><dt>已入库</dt><dd>{{ stats.admitted }}<small>{{ (stats.admit_ratio * 100).toFixed(1) }}%</small></dd></div>
        <div><dt>待复核</dt><dd>{{ stats.review }}<small>{{ (stats.review_ratio * 100).toFixed(1) }}%</small></dd></div>
        <div><dt>已拒绝</dt><dd>{{ stats.rejected }}<small>{{ (stats.reject_ratio * 100).toFixed(1) }}%</small></dd></div>
        <div><dt>人工改判</dt><dd>{{ stats.overrides }}<small>{{ (stats.override_rate * 100).toFixed(1) }}%</small></dd></div>
        <div><dt>重判</dt><dd>{{ stats.rejudged }}</dd></div>
        <div><dt>AI 失败</dt><dd>{{ stats.ai_failures }}</dd></div>
        <div>
          <dt>检索命中</dt>
          <dd>
            {{ stats.retrieval.hits }} / {{ stats.retrieval.total }}
            <small>命中率 {{ (stats.retrieval.hit_rate * 100).toFixed(1) }}%</small>
          </dd>
        </div>
      </dl>
    </section>

    <section class="card filter-card">
      <div class="filter-row">
        <label class="field inline">
          <span>决策</span>
          <select v-model="filters.decision" @change="applyFilters">
            <option value="">全部</option>
            <option value="admit">已入库</option>
            <option value="review">待复核</option>
            <option value="reject">已拒绝</option>
          </select>
        </label>
        <label class="field inline">
          <span>来源</span>
          <select v-model="filters.sourceKind" @change="applyFilters">
            <option value="">全部</option>
            <option value="deployment">部署经验</option>
            <option value="investigation">排查报告</option>
          </select>
        </label>
        <div class="keyword-field">
          <input
            v-model="filters.keyword"
            type="search"
            placeholder="搜标题"
            @keyup.enter="applyFilters"
          />
          <button class="ghost" type="button" @click="applyFilters">搜索</button>
        </div>
        <span class="meta">共 {{ total }} 条</span>
      </div>
    </section>

    <p v-if="loading && !items.length" class="empty">正在读取准入记录…</p>
    <p v-else-if="!items.length" class="empty">没有符合条件的准入记录。</p>

    <section v-else class="decision-list">
      <button
        v-for="item in items"
        :key="`${item.source_kind}-${item.task_id}`"
        type="button"
        class="decision-row"
        @click="openDetail(item)"
      >
        <div class="decision-main">
          <div class="decision-tags">
            <span class="badge" :class="decisionClass(item.effective_decision)">
              {{ decisionLabel(item.effective_decision) }}
            </span>
            <span class="badge badge-muted">
              {{ item.source_kind === 'deployment' ? '部署经验' : '排查报告' }}
            </span>
            <span v-if="item.manual_override" class="badge badge-info">人工改判</span>
            <span v-if="item.signals.includes('ai_unavailable')" class="badge badge-warn">
              AI 不可用
            </span>
          </div>
          <strong>{{ item.title || item.task_id }}</strong>
          <p>{{ item.reason || '没有理由' }}</p>
          <div class="decision-meta">
            <span>任务 {{ item.task_id.slice(0, 8) }}</span>
            <span>价值分 {{ item.value_score }}</span>
            <span>{{ formatLocalTime(item.updated_at) }}</span>
            <span v-if="item.signals.length">{{ item.signals.slice(0, 4).join(' · ') }}</span>
          </div>
        </div>
        <span class="chevron">查看</span>
      </button>
    </section>

    <div v-if="hasMore" class="load-more">
      <button class="ghost" type="button" :disabled="loading" @click="loadMore">
        {{ loading ? '加载中…' : `加载更多（已显示 ${items.length} / ${total}）` }}
      </button>
    </div>

    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <section class="decision-detail">
        <header class="detail-head">
          <div>
            <div class="decision-tags">
              <span class="badge" :class="decisionClass(detail.effective_decision)">
                {{ decisionLabel(detail.effective_decision) }}
              </span>
              <span class="badge badge-muted">
                {{ detail.source_kind === 'deployment' ? '部署经验' : '排查报告' }}
              </span>
              <span v-if="detail.manual_override" class="badge badge-info">人工改判</span>
            </div>
            <h3>{{ detail.title || detail.task_id }}</h3>
            <p class="meta">
              任务 {{ detail.task_id }} · 模型 {{ detail.model || '规则' }}
              · 价值分 {{ detail.value_score }}
            </p>
          </div>
          <button class="icon-btn" type="button" @click="detail = null">关闭</button>
        </header>

        <div class="detail-body">
          <div class="reason-block">
            <strong>准入理由</strong>
            <p>{{ detail.reason || '没有理由' }}</p>
            <div v-if="detail.signals.length" class="tag-row">
              <span v-for="signal in detail.signals" :key="signal" class="tag">{{ signal }}</span>
            </div>
          </div>
          <p v-if="detail.summary" class="detail-summary">{{ detail.summary }}</p>
          <pre class="detail-content">{{ detail.body || '还没有生成入库正文。' }}</pre>
          <p v-if="detail.manual_reason" class="meta">人工改判理由：{{ detail.manual_reason }}</p>
        </div>

        <footer v-if="canOverride" class="detail-foot override-bar">
          <label class="field inline">
            <span>改判为</span>
            <select v-model="overrideForm.decision">
              <option value="admit">放行入库</option>
              <option value="review">待复核</option>
              <option value="reject">拒绝入库</option>
            </select>
          </label>
          <input
            v-model="overrideForm.reason"
            type="text"
            placeholder="填写改判理由（必填）"
            maxlength="300"
          />
          <button class="primary" type="button" :disabled="saving" @click="submitOverride">
            {{ saving ? '保存中…' : '保存改判' }}
          </button>
          <button class="ghost" type="button" :disabled="requeueing" @click="requeue(detail)">
            {{ requeueing ? '入队中…' : '重新判' }}
          </button>
        </footer>
      </section>
    </div>
  </div>
</template>

<style scoped src="./knowledge/knowledge-governance.css"></style>
