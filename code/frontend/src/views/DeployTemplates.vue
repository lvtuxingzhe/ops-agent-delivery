<script setup lang="ts">
/**
 * 部署模板库 —— 把「写 JSON」换成「贴命令 + 校验 + 发布」。
 *
 * 一线工程师的实际动作是：
 *   1. 把在测试机验证过的一行行命令贴进来；
 *   2. 选 apply / verify / rollback，标 read / dangerous；
 *   3. 点校验，看后端拆出来的 argv 和风险；
 *   4. 管理员发布，团队才能在部署页选到。
 *
 * 页面不自行判断权限：能不能改、能不能发布由后台接口决定，这里只按
 * 返回的 can_edit / can_publish 隐藏按钮，绕过页面直接调接口照样会被拒。
 */

import HostPicker from '../components/HostPicker.vue'
import ResizableTable from '../components/ResizableTable.vue'
import { useDeployTemplates } from './deploy-templates/useDeployTemplates'

const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false })

const {
  DEPLOY_TYPES,
  DEPLOY_TYPE_LABELS,
  TEMPLATE_COLUMNS,
  TEMPLATE_GRID,
  TEMPLATE_COLUMN_WIDTHS_STORAGE_KEY,
  ai,
  aiOpen,
  aiTrace,
  addRow,
  busy,
  canCreate,
  checkTemplate,
  closeEditor,
  cloneItem,
  deleteItem,
  disableItem,
  editor,
  error,
  filtered,
  formatTime,
  fromTaskBusy,
  fromTaskId,
  fromTaskOpen,
  generateAi,
  hasCustomTemplateWidths,
  history,
  isAdmin,
  load,
  loading,
  message,
  openCreate,
  openEdit,
  publishItem,
  removeRow,
  resetTemplateWidths,
  riskText,
  saveFromTask,
  saveTemplate,
  search,
  showHistory,
  sourceText,
  stats,
  statusClass,
  statusFilter,
  statusText,
  templateTable,
} = useDeployTemplates()
</script>

<template>
  <div class="page-stack template-page">
    <div v-if="!props.embedded" class="list-head">
      <div>
        <h2>部署模板库</h2>
      </div>
      <div class="head-actions">
        <button class="plain" type="button" :disabled="loading" @click="load">刷新</button>
        <button v-if="canCreate" class="plain" type="button" @click="fromTaskOpen = true">从成功任务沉淀</button>
        <button v-if="canCreate" class="primary" type="button" @click="aiOpen = true">AI 生成草稿</button>
        <button v-if="canCreate" class="primary" type="button" @click="openCreate">新建模板</button>
      </div>
    </div>
    <div v-else class="embedded-head">
      <div class="head-actions">
        <button class="plain" type="button" :disabled="loading" @click="load">刷新</button>
        <button v-if="canCreate" class="plain" type="button" @click="fromTaskOpen = true">从成功任务沉淀</button>
        <button v-if="canCreate" class="primary" type="button" @click="aiOpen = true">AI 生成草稿</button>
        <button v-if="canCreate" class="primary" type="button" @click="openCreate">新建模板</button>
      </div>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="message" class="message">{{ message }}</p>
    <p v-if="!canCreate" class="desc">当前角色只读；创建和编辑需要管理员或排查员。</p>

    <div class="template-stats">
      <div class="stat-tile"><span>已发布</span><strong>{{ stats.published }}</strong></div>
      <div class="stat-tile"><span>草稿</span><strong>{{ stats.draft }}</strong></div>
      <div class="stat-tile"><span>已停用</span><strong>{{ stats.disabled }}</strong></div>
      <div class="stat-tile"><span>累计使用</span><strong>{{ stats.usage }}</strong></div>
      <div class="stat-tile"><span>成功完成</span><strong>{{ stats.success }}</strong></div>
    </div>

    <section class="card">
      <div class="filters">
        <input v-model="search" class="search" placeholder="搜索模板" />
        <select v-model="statusFilter">
          <option value="all">全部状态</option>
          <option value="published">已发布</option>
          <option value="draft">草稿</option>
          <option value="disabled">已停用</option>
        </select>
        <span class="count">{{ filtered.length }} 个模板</span>
        <span class="column-hint">· 拖动表头分隔线调整列宽</span>
        <button class="ghost column-reset" type="button" :disabled="!hasCustomTemplateWidths" @click="resetTemplateWidths">
          重置列宽
        </button>
      </div>

      <p v-if="loading" class="empty">加载中…</p>
      <p v-else-if="!filtered.length" class="empty">没有匹配的模板；先新建一个或让 AI 生成草稿。</p>

      <ResizableTable
        v-else
        ref="templateTable"
        :columns="TEMPLATE_COLUMNS"
        :default-grid="TEMPLATE_GRID"
        :storage-key="TEMPLATE_COLUMN_WIDTHS_STORAGE_KEY"
        :gap="8"
        :padding-x="0"
        card-class="template-table"
        head-class="table-head"
        @custom-change="hasCustomTemplateWidths = $event"
      >
        <div v-for="item in filtered" :key="item.id" class="table-row">
          <div class="template-main">
            <div class="template-name-line">
              <strong>{{ item.name }}</strong>
              <span v-if="item.bad" class="badge badge-danger">结构不完整</span>
            </div>
            <small>{{ item.id }} · {{ DEPLOY_TYPE_LABELS[item.deploy_type || ''] || '未分类' }} · {{ sourceText(item.source) }} · 风险 {{ riskText(item.risk) }}</small>
          </div>
          <div><span class="badge" :class="statusClass(item.status)">{{ statusText(item.status) }}</span></div>
          <div class="muted">
            {{ item.platform }} / {{ item.os_family || 'unknown' }}
            <template v-if="item.package_manager && item.package_manager !== 'none'"> / {{ item.package_manager }}</template>
            / {{ item.default_host }}
          </div>
          <div>v{{ item.version }}</div>
          <div>{{ item.usage_count ?? 0 }} 次 <small class="muted">/ 成功 {{ item.success_count ?? 0 }}</small></div>
          <div class="muted">{{ formatTime(item.updated_at) }}</div>
          <div class="row-actions">
            <button class="link" type="button" @click="showHistory(item)">历史</button>
            <button v-if="item.can_edit" class="link" type="button" @click="openEdit(item)">编辑</button>
            <button v-if="canCreate" class="link" type="button" @click="cloneItem(item)">克隆</button>
            <button v-if="isAdmin && item.status === 'draft'" class="link" type="button" @click="publishItem(item)">发布</button>
            <button v-if="isAdmin && item.status === 'published'" class="link danger" type="button" @click="disableItem(item)">停用</button>
            <button v-if="item.can_delete" class="link danger" type="button" @click="deleteItem(item)">删除</button>
          </div>
        </div>
      </ResizableTable>
    </section>

    <!-- 模板编辑器 -->
    <div v-if="editor" class="modal-mask" @click.self="closeEditor">
      <section class="editor-panel">
        <header class="editor-head">
          <div>
            <p class="kicker">{{ editor.id ? 'EDIT TEMPLATE' : 'NEW TEMPLATE' }}</p>
            <h3>{{ editor.id ? '编辑模板' : '新建模板' }}</h3>
          </div>
          <button class="icon-btn" type="button" :disabled="busy" @click="closeEditor">关闭</button>
        </header>

        <div class="editor-body">
          <details v-if="aiTrace.length" class="ai-trace">
            <summary>
              <strong>AI 生成过程</strong>
              <span>{{ aiTrace.length }} 步 · 展开查看</span>
            </summary>
            <ol>
              <li v-for="step in aiTrace" :key="step.index" :class="`ai-step-${step.kind}`">
                <span class="ai-step-index">{{ step.index }}</span>
                <div>
                  <strong>{{ step.title || (step.kind === 'thinking' ? '模型显式推理' : '步骤') }}</strong>
                  <pre v-if="step.detail">{{ step.detail }}</pre>
                </div>
              </li>
            </ol>
            <p class="ai-trace-note">
              这里记录的是模型生成草稿时的可核对步骤；模型若未返回显式推理，不会伪造思考内容。
            </p>
          </details>

          <div class="form-grid">
            <label class="span-2"><span>模板名称 *</span><input v-model="editor.name" maxlength="80" placeholder="例如：vm-150 重启 crond" /></label>
            <label><span>平台</span><select v-model="editor.platform"><option value="linux">linux</option><option value="windows">windows</option></select></label>
            <label>
              <span>部署类型</span>
              <select v-model="editor.deployType">
                <option value="">自动推断</option>
                <option v-for="item in DEPLOY_TYPES" :key="item.value" :value="item.value">
                  {{ item.label }}
                </option>
              </select>
            </label>
            <label>
              <span>操作系统</span>
              <select v-model="editor.osFamily">
                <option value="">自动按命令识别</option>
                <option value="unknown">unknown（禁止部署）</option>
                <option value="debian">debian</option>
                <option value="rhel">rhel</option>
                <option value="any">any（无包管理动作）</option>
                <option value="windows">windows</option>
              </select>
            </label>
            <label>
              <span>包管理器</span>
              <select v-model="editor.packageManager">
                <option value="">自动</option>
                <option value="unknown">unknown</option>
                <option value="apt">apt</option>
                <option value="dnf">dnf</option>
                <option value="yum">yum</option>
                <option value="none">none</option>
              </select>
            </label>
            <HostPicker
              v-model="editor.defaultHost"
              class="field span-2"
              label="默认主机"
              placeholder="搜索或输入主机 ID"
              hint="模板发布后，部署页会以这个主机作为默认目标。"
            />
            <label><span>方案有效期（分钟）</span><input v-model.number="editor.ttlMinutes" type="number" min="1" max="240" /></label>
            <label class="span-2"><span>说明</span><input v-model="editor.description" maxlength="2000" placeholder="这条模板解决什么问题、什么时候用" /></label>
          </div>

          <p class="editor-tip">
            一行一条命令，按 argv 直接执行。不支持管道、重定向、&&/;、反引号或 $(...)；
            需要多步就写多行。风险级别选 dangerous 会进入审批高危档并触发人工确认。
          </p>

          <section class="cmd-section">
            <div class="cmd-head"><h4>1. 变更前检查（precheck，可空）</h4><button class="link" type="button" @click="addRow('precheck')">+ 加一行</button></div>
            <div v-for="(row, index) in editor.precheck" :key="`pc-${index}`" class="cmd-row verify-row">
              <textarea v-model="row.command" rows="1" placeholder="rpm -q keepalived（变更前应为未安装）" />
              <select v-model="row.scope"><option value="read">read（只读）</option><option value="dangerous">dangerous（有风险）</option></select>
              <input v-model="row.exitCode" class="expect-input" placeholder="期望退出码 0" />
              <input v-model="row.stdout" class="expect-input" placeholder="期望 stdout 包含（可选）" />
              <button class="icon-btn" type="button" @click="removeRow('precheck', index)">删</button>
            </div>
          </section>

          <section class="cmd-section">
            <div class="cmd-head"><h4>2. 变更命令（apply）</h4><button class="link" type="button" @click="addRow('operations')">+ 加一行</button></div>
            <div v-for="(row, index) in editor.operations" :key="`op-${index}`" class="cmd-row">
              <textarea v-model="row.command" rows="1" placeholder="systemctl restart crond" />
              <select v-model="row.scope"><option value="read">read（只读）</option><option value="dangerous">dangerous（有风险）</option></select>
              <button class="icon-btn" type="button" :disabled="editor.operations.length <= 1" @click="removeRow('operations', index)">删</button>
            </div>
          </section>

          <section class="cmd-section">
            <div class="cmd-head"><h4>3. 验证命令（verify）</h4><button class="link" type="button" @click="addRow('verify')">+ 加一行</button></div>
            <div v-for="(row, index) in editor.verify" :key="`vf-${index}`" class="cmd-row verify-row">
              <textarea v-model="row.command" rows="1" placeholder="systemctl is-active crond" />
              <select v-model="row.scope"><option value="read">read（只读）</option><option value="dangerous">dangerous（有风险）</option></select>
              <input v-model="row.exitCode" class="expect-input" placeholder="期望退出码 0" />
              <input v-model="row.stdout" class="expect-input" placeholder="期望 stdout 包含（可选）" />
              <button class="icon-btn" type="button" :disabled="editor.verify.length <= 1" @click="removeRow('verify', index)">删</button>
            </div>
          </section>

          <section class="cmd-section">
            <div class="cmd-head"><h4>4. 回滚命令（rollback）</h4><button class="link" type="button" @click="addRow('rollback')">+ 加一行</button></div>
            <div v-for="(row, index) in editor.rollback" :key="`rb-${index}`" class="cmd-row">
              <textarea v-model="row.command" rows="1" placeholder="systemctl restart crond" />
              <select v-model="row.scope"><option value="read">read（只读）</option><option value="dangerous">dangerous（有风险）</option></select>
              <button class="icon-btn" type="button" :disabled="editor.rollback.length <= 1" @click="removeRow('rollback', index)">删</button>
            </div>
          </section>

          <section class="cmd-section">
            <div class="cmd-head"><h4>5. 回滚后验证（post_rollback，可空）</h4><button class="link" type="button" @click="addRow('postRollback')">+ 加一行</button></div>
            <div v-for="(row, index) in editor.postRollback" :key="`pr-${index}`" class="cmd-row verify-row">
              <textarea v-model="row.command" rows="1" placeholder="rpm -q keepalived（回滚后应恢复为未安装）" />
              <select v-model="row.scope"><option value="read">read（只读）</option><option value="dangerous">dangerous（有风险）</option></select>
              <input v-model="row.exitCode" class="expect-input" placeholder="期望退出码 0" />
              <input v-model="row.stdout" class="expect-input" placeholder="期望 stdout 包含（可选）" />
              <button class="icon-btn" type="button" @click="removeRow('postRollback', index)">删</button>
            </div>
          </section>
        </div>

        <footer class="editor-foot">
          <button class="plain" type="button" :disabled="busy" @click="checkTemplate">校验</button>
          <button class="plain" type="button" :disabled="busy" @click="closeEditor">取消</button>
          <button class="primary" type="button" :disabled="busy" @click="saveTemplate(false)">保存草稿</button>
          <button v-if="isAdmin" class="primary" type="button" :disabled="busy" @click="saveTemplate(true)">保存并发布</button>
        </footer>
      </section>
    </div>

    <!-- AI 草稿 -->
    <div v-if="aiOpen" class="modal-mask" @click.self="aiOpen = false">
      <section class="mini-panel">
        <header class="editor-head"><div><p class="kicker">AI DRAFT</p><h3>让 AI 先写一版草稿</h3></div><button class="icon-btn" type="button" @click="aiOpen = false">关闭</button></header>
        <p class="editor-tip">
          AI 只产出草稿，不会创建任务或执行命令。生成后仍需逐条核对，管理员发布后才可用。
          需要平台已启用默认模型、总开关和网关出网闸口。
        </p>
        <label class="field"><span>要部署什么 *</span><textarea v-model="ai.goal" rows="4" placeholder="例如：在 vm-150 上重启 crond，并确认服务恢复 active；失败时自动重启回滚" /></label>
        <div class="ai-target-grid">
          <label class="field ai-platform-field">
            <span>平台</span>
            <select v-model="ai.platform"><option value="linux">linux</option><option value="windows">windows</option></select>
          </label>
          <HostPicker
            v-model="ai.host"
            label="目标主机"
            placeholder="搜索或输入主机 ID"
            hint="AI 只按这个目标生成草稿，不会执行。"
          />
        </div>
        <label class="field"><span>已经验证过的命令（可选）</span><textarea v-model="ai.commands" rows="3" placeholder="一行一条，AI 会优先沿用你贴的命令" /></label>
        <footer class="editor-foot">
          <button class="plain" type="button" @click="aiOpen = false">取消</button>
          <button class="primary" type="button" :disabled="busy" @click="generateAi">生成草稿</button>
        </footer>
      </section>
    </div>

    <!-- 从任务沉淀 -->
    <div v-if="fromTaskOpen" class="modal-mask" @click.self="fromTaskOpen = false">
      <section class="mini-panel">
        <header class="editor-head"><div><p class="kicker">FROM SUCCESS</p><h3>从成功任务沉淀模板</h3></div><button class="icon-btn" type="button" @click="fromTaskOpen = false">关闭</button></header>
        <p class="editor-tip">只接受已经成功完成的部署任务；生成的是草稿，不会自动发布。</p>
        <label class="field"><span>部署任务 ID *</span><input v-model="fromTaskId" placeholder="在部署记录里复制的任务 id" /></label>
        <footer class="editor-foot">
          <button class="plain" type="button" @click="fromTaskOpen = false">取消</button>
          <button class="primary" type="button" :disabled="fromTaskBusy" @click="saveFromTask">生成草稿</button>
        </footer>
      </section>
    </div>

    <!-- 历史版本 -->
    <div v-if="history" class="modal-mask" @click.self="history = null">
      <section class="mini-panel wide">
        <header class="editor-head"><div><p class="kicker">VERSION HISTORY</p><h3>{{ history.item.name }} · 旧版本</h3></div><button class="icon-btn" type="button" @click="history = null">关闭</button></header>
        <p v-if="!history.rows.length" class="empty">还没有归档版本；第一次改已发布模板时会自动留快照。</p>
        <div v-for="row in history.rows" :key="row.version" class="history-row">
          <strong>v{{ row.version }}</strong>
          <span class="muted">{{ formatTime(row.archived_at) }} · {{ row.status }}</span>
          <small>{{ row.operations.length }} 变更 / {{ row.verify.length }} 验证 / {{ row.rollback.length }} 回滚</small>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped src="./deploy-templates/deploy-templates.css"></style>
