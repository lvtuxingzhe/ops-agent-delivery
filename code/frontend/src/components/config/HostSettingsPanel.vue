<script setup lang="ts">
/**
 * 配置 → 主机配置：字段设置 + 密码管理。
 *
 * 状态由 useHostSettings 单例持有，两个子组件 inject 复用；父层只负责
 * 提示与新增字段弹窗，避免拆成两个独立面板后重复拉取/保存。
 */
import { provide } from 'vue'

import HostFieldSettings from './hosts/HostFieldSettings.vue'
import HostPasswordSettings from './hosts/HostPasswordSettings.vue'
import { HOST_SETTINGS_KEY, useHostSettings } from './hosts/useHostSettings'

const settings = useHostSettings()
provide(HOST_SETTINGS_KEY, settings)
const {
  error, message, addOpen, addError, draft, busy, addField,
} = settings
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="message" class="message">{{ message }}</p>

  <HostFieldSettings />
  <HostPasswordSettings />

  <div v-if="addOpen" class="config-modal" @click.self="addOpen = false">
    <section class="config-editor" role="dialog" aria-modal="true">
      <header class="config-editor-head">
        <div><p class="kicker">CMDB ATTRIBUTE</p><h2>新增字段</h2></div>
        <button type="button" class="plain" :disabled="busy" @click="addOpen = false">关闭</button>
      </header>
      <p v-if="addError" class="error">{{ addError }}</p>
      <div class="form-grid add-grid">
        <label class="field">
          <span>字段名称</span>
          <input v-model="draft.label" maxlength="24" placeholder="例如：负责人" :disabled="busy" />
        </label>
        <label class="field">
          <span>字段标识</span>
          <input v-model="draft.key" placeholder="custom_owner" :disabled="busy" />
        </label>
        <label class="field">
          <span>字段类型</span>
          <select v-model="draft.kind" :disabled="busy">
            <option value="text">文本</option>
            <option value="select">下拉</option>
            <option value="bool">布尔</option>
          </select>
        </label>
        <label v-if="draft.kind === 'select'" class="span-2 field">
          <span>下拉选项</span>
          <input v-model="draft.options" placeholder="逗号分隔，例如：生产,测试" :disabled="busy" />
        </label>
        <label class="check-line">
          <input v-model="draft.required" type="checkbox" :disabled="busy" /> 必填
        </label>
        <label class="check-line">
          <input v-model="draft.visible" type="checkbox" :disabled="busy" /> 显示在模板
        </label>
      </div>
      <footer class="config-editor-foot">
        <button type="button" class="plain" :disabled="busy" @click="addOpen = false">取消</button>
        <button type="button" class="primary" :disabled="busy" @click="addField">添加字段</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.add-grid {
  margin-top: 10px;
}
.check-line {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 34px;
}
</style>
