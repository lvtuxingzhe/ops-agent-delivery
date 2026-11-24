<script setup lang="ts">
/**
 * 独立知识库页：部署经验 + 排查报告 + 手工知识 + 上传文档统一入口。
 *
 * 页面只做展示和转发；聚合、准入、可见范围都由后端负责。
 */

import {
  FILE_TYPES,
  KNOWLEDGE_KINDS,
  KNOWLEDGE_UPLOAD_ACCEPT,
  KNOWLEDGE_UPLOAD_MAX_BYTES,
  TECH_DOMAINS,
} from '../api/knowledge'
import { useKnowledge } from './knowledge/useKnowledge'
import { formatLocalTime } from '../utils/time'

const {
  route,
  me,
  loading,
  exporting,
  error,
  notice,
  entries,
  total,
  counts,
  offset,
  pageSize,
  filters,
  appliedKeyword,
  detail,
  detailLoading,
  deleting,
  confirmDelete,
  downloading,
  editor,
  bodyLength,
  BODY_HEAD_RE,
  bodyBlocks,
  lineClass,
  saving,
  upload,
  uploading,
  fileInput,
  canWrite,
  canGovern,
  hasMore,
  activeFilters,
  timeRangeToDays,
  query,
  load,
  applyFilters,
  clearFilters,
  filterByKind,
  loadMore,
  exportColumns,
  exportKnowledge,
  openDetail,
  openCreate,
  openUpload,
  closeUpload,
  fileStem,
  acceptUploadFile,
  onFileChange,
  onFileDrop,
  submitUpload,
  downloadFile,
  formatBytes,
  openEdit,
  saveManual,
  removeManual,
  kindBadgeClass,
  summaryText,
} = useKnowledge()
</script>

<template>
  <div class="page-stack">
    <div class="page-actions page-actions-row">
      <RouterLink v-if="canGovern" class="ghost link-btn" to="/knowledge/governance">知识治理</RouterLink>
      <button v-if="canWrite" class="primary" type="button" @click="openUpload">上传文档</button>
      <button v-if="canWrite" class="ghost" type="button" @click="openCreate">新增知识</button>
      <button class="ghost" type="button" :disabled="loading" @click="load()">刷新</button>
      <button class="ghost" type="button" :disabled="loading || exporting || !entries.length" @click="exportKnowledge">
        {{ exporting ? '导出中…' : '导出 CSV' }}
      </button>
    </div>

    <section class="card filter-card">
      <div class="filter-row">
        <label class="field inline">
          <span>类型</span>
          <select v-model="filters.kind" @change="applyFilters">
            <option value="">全部类型</option>
            <option v-for="item in KNOWLEDGE_KINDS" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <label class="field inline">
          <span>技术域</span>
          <select v-model="filters.techDomain" @change="applyFilters">
            <option value="">全部技术域</option>
            <option v-for="item in TECH_DOMAINS" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <label class="field inline">
          <span>文件类型</span>
          <select v-model="filters.fileType" @change="applyFilters">
            <option value="">全部文件类型</option>
            <option v-for="item in FILE_TYPES" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <label class="field inline">
          <span>时间</span>
          <select v-model="filters.range" @change="applyFilters">
            <option value="">全部时间</option>
            <option value="7d">近 7 天</option>
            <option value="30d">近 30 天</option>
            <option value="90d">近 90 天</option>
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
        <button v-if="activeFilters" class="plain" type="button" @click="clearFilters">清除筛选</button>
      </div>

      <div class="kind-stats">
        <button
          v-for="item in KNOWLEDGE_KINDS"
          :key="item.value"
          type="button"
          class="stat-chip"
          :class="{ active: filters.kind === item.value }"
          @click="filterByKind(item.value)"
        >
          {{ item.label }}
          <strong>{{ counts[item.value] ?? 0 }}</strong>
        </button>
        <span class="stat-total">共 {{ total }} 条</span>
      </div>
    </section>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <p v-if="loading && !entries.length" class="empty">正在聚合知识…</p>
    <p v-else-if="!entries.length" class="empty">
      {{ activeFilters ? '没有符合筛选条件的知识。' : '知识库还是空的：完成一次部署或排查后会自动沉淀。' }}
    </p>

    <div v-else class="knowledge-grid">
      <button
        v-for="entry in entries"
        :key="entry.id"
        type="button"
        class="knowledge-card"
        @click="openDetail(entry)"
      >
        <div class="knowledge-card-head">
          <span class="badge" :class="kindBadgeClass(entry.kind)">{{ entry.kind_label }}</span>
          <span class="domain-chip">{{ entry.tech_domain_label }}</span>
          <span v-if="entry.file_type" class="file-chip">{{ entry.file_type_label || entry.file_type }}</span>
          <span v-if="entry.deduped" class="dedupe-chip" title="同主题还折叠了相似条目">折叠 {{ entry.deduped }} 条相似</span>
        </div>
        <strong class="knowledge-title">{{ entry.title }}</strong>
        <p class="knowledge-summary">{{ summaryText(entry) }}</p>
        <div class="knowledge-meta">
          <span>{{ formatLocalTime(entry.updated_at) }}</span>
          <span v-if="entry.tags.length">{{ entry.tags.slice(0, 3).join(' · ') }}</span>
        </div>
      </button>
    </div>

    <div v-if="hasMore" class="load-more">
      <button class="ghost" type="button" :disabled="loading" @click="loadMore">
        {{ loading ? '加载中…' : `加载更多（已显示 ${entries.length} / ${total}）` }}
      </button>
    </div>

    <!-- 详情 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <section class="knowledge-detail">
        <header class="detail-head">
          <div>
            <div class="knowledge-card-head">
              <span class="badge" :class="kindBadgeClass(detail.kind)">{{ detail.kind_label }}</span>
              <span class="domain-chip">{{ detail.tech_domain_label }}</span>
              <span v-if="detail.file_type" class="file-chip">{{ detail.file_type_label || detail.file_type }}</span>
            </div>
            <h3>{{ detail.title }}</h3>
            <p class="desc">
              更新 {{ formatLocalTime(detail.updated_at) }}
              <span v-if="detail.source_task_id"> · 来源任务 {{ detail.source_task_id.slice(0, 8) }}</span>
            </p>
          </div>
          <button class="icon-btn" type="button" @click="detail = null">关闭</button>
        </header>

        <div class="detail-body">
          <div v-if="detail.file_type" class="file-card">
            <div>
              <strong>{{ detail.file_name || '原文件' }}</strong>
              <p class="desc">
                {{ detail.file_type_label || detail.file_type }}
                <span v-if="detail.file_size"> · {{ formatBytes(detail.file_size) }}</span>
              </p>
            </div>
            <button class="plain" type="button" :disabled="downloading" @click="downloadFile">
              {{ downloading ? '下载中…' : '下载原文件' }}
            </button>
          </div>
          <p v-if="detail.summary" class="detail-summary">{{ detail.summary }}</p>
          <div v-if="bodyBlocks.length" class="body-blocks">
            <section v-for="(block, index) in bodyBlocks" :key="`${block.title}-${index}`">
              <h4 v-if="block.title">{{ block.title }}</h4>
              <p
                v-for="(line, lineIndex) in block.lines"
                :key="`${index}-${lineIndex}`"
                :class="lineClass(line)"
              >
                {{ line }}
              </p>
            </section>
          </div>
          <pre v-else class="detail-content">{{ detail.body || '这条知识没有正文。' }}</pre>
          <div v-if="detail.tags.length" class="tag-row">
            <span v-for="tag in detail.tags" :key="tag" class="tag tag-on">{{ tag }}</span>
          </div>
          <div
            v-if="(detail.target_hosts && detail.target_hosts.length) || (detail.target_services && detail.target_services.length)"
            class="target-row"
          >
            <span v-if="detail.target_hosts && detail.target_hosts.length" class="target-group">
              <span class="desc">目标主机</span>
              <span v-for="host in detail.target_hosts" :key="host" class="tag tag-target">{{ host }}</span>
            </span>
            <span v-if="detail.target_services && detail.target_services.length" class="target-group">
              <span class="desc">目标服务</span>
              <span v-for="service in detail.target_services" :key="service" class="tag tag-target">{{ service }}</span>
            </span>
          </div>
        </div>

        <footer class="detail-foot">
          <template v-if="(detail.kind === 'manual' || detail.kind === 'document') && detail.editable">
            <button class="plain" type="button" @click="openEdit(detail)">编辑</button>
            <button v-if="!confirmDelete" class="plain danger" type="button" :disabled="deleting" @click="confirmDelete = true">删除</button>
            <template v-else>
              <span class="confirm-text">删除后列表立即不可见，审计仍保留记录；原文件不随软删除自动清理。</span>
              <button class="plain" type="button" @click="confirmDelete = false">取消</button>
              <button class="danger" type="button" :disabled="deleting" @click="removeManual">
                {{ deleting ? '删除中…' : '确认删除' }}
              </button>
            </template>
          </template>
          <span v-else-if="detail.kind === 'manual' || detail.kind === 'document'" class="desc">这条知识由其他人创建，当前账号只能查看。</span>
          <span v-else class="desc">自动聚合条目只读：需要修正请到源任务的详情页处理。</span>
        </footer>
      </section>
    </div>

    <!-- 新增 / 编辑手工知识 -->
    <div v-if="editor" class="modal-mask" @click.self="editor = null">
      <section class="knowledge-editor">
        <header class="detail-head">
          <div>
            <p class="kicker">{{ editor.id ? 'EDIT KNOWLEDGE' : 'NEW KNOWLEDGE' }}</p>
            <h3>{{ editor.id ? '编辑手工知识' : '新增手工知识' }}</h3>
          </div>
          <button class="icon-btn" type="button" @click="editor = null">关闭</button>
        </header>

        <div class="editor-form">
          <label class="field">
            <span>标题 *</span>
            <input v-model="editor.title" maxlength="120" placeholder="例如：keepalived 双机漂移不生效的排查顺序" />
          </label>
          <div class="editor-row">
            <label class="field">
              <span>技术域</span>
              <select v-model="editor.techDomain">
                <option v-for="item in TECH_DOMAINS" :key="item.value" :value="item.value">
                  {{ item.label }}
                </option>
              </select>
            </label>
            <label class="field">
              <span>标签（逗号分隔，可选）</span>
              <input v-model="editor.tags" placeholder="keepalived, vip, 漂移" />
            </label>
          </div>
          <div class="editor-row">
            <label class="field">
              <span>目标主机（逗号分隔，可选）</span>
              <input v-model="editor.targetHosts" placeholder="192.0.2.1, host-150" />
              <small class="field-tip">填了只按精确主机匹配；留空则按正文匹配。</small>
            </label>
            <label class="field">
              <span>目标服务（逗号分隔，可选）</span>
              <input v-model="editor.targetServices" placeholder="nginx, mysql" />
              <small class="field-tip">填了只按精确服务匹配；留空则按正文匹配。</small>
            </label>
          </div>
          <label class="field">
            <span>摘要</span>
            <input v-model="editor.summary" maxlength="500" placeholder="一句话说明结论或适用场景" />
          </label>
          <label class="field">
            <span>正文 <small class="field-count">{{ bodyLength }} 字</small></span>
            <textarea
              v-model="editor.body"
              rows="10"
              placeholder="排查步骤、判断依据、处理方案；正文不限制字数，完整保留，不要粘贴密码或密钥"
            />
          </label>
        </div>

        <footer class="detail-foot">
          <button class="plain" type="button" :disabled="saving" @click="editor = null">取消</button>
          <button class="primary" type="button" :disabled="saving || !editor.title.trim()" @click="saveManual">
            {{ saving ? '保存中…' : (editor.id ? '保存修改' : '新增') }}
          </button>
        </footer>
      </section>
    </div>

    <!-- 上传文档 -->
    <div v-if="upload" class="modal-mask" @click.self="closeUpload">
      <section class="knowledge-editor upload-editor">
        <header class="detail-head">
          <div>
            <p class="kicker">UPLOAD DOCUMENT</p>
            <h3>上传文档</h3>
          </div>
          <button class="icon-btn" type="button" @click="closeUpload">关闭</button>
        </header>

        <div class="editor-form">
          <input
            ref="fileInput"
            class="upload-file-input"
            type="file"
            :accept="KNOWLEDGE_UPLOAD_ACCEPT"
            @change="onFileChange"
          />
          <div
            class="upload-drop"
            :class="{ 'has-file': upload.file }"
            @click="fileInput?.click()"
            @dragover.prevent
            @drop="onFileDrop"
          >
            <template v-if="upload.file">
              <strong>{{ upload.file.name }}</strong>
              <p class="desc">{{ formatBytes(upload.file.size) }} · 点击可重新选择</p>
            </template>
            <template v-else>
              <strong>点击或拖拽文档到这里</strong>
              <p class="desc">
                PDF / 文本 / Excel / HTML / Word(.docx) / Markdown，单文件不超过 20MB
              </p>
            </template>
          </div>

          <label class="field">
            <span>标题</span>
            <input v-model="upload.title" maxlength="120" placeholder="默认用文件名" />
          </label>
          <label class="field">
            <span>技术域</span>
            <select v-model="upload.techDomain">
              <option v-for="item in TECH_DOMAINS" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
          </label>
          <p class="desc">
            上传后自动抽取文字进入知识库，原文件保留在详情里可下载；
            扫描版 PDF 没有文字层，会提示解析不出内容。
          </p>
        </div>

        <footer class="detail-foot">
          <button class="plain" type="button" :disabled="uploading" @click="closeUpload">取消</button>
          <button class="primary" type="button" :disabled="uploading || !upload.file" @click="submitUpload">
            {{ uploading ? '解析上传中…' : '上传并入库' }}
          </button>
        </footer>
      </section>
    </div>
  </div>
</template>

<style scoped src="./knowledge/knowledge.css"></style>
