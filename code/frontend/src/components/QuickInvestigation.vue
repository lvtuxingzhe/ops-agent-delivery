<script setup lang="ts">
/**
 * 自然语言排查入口：模板与交互入口。
 *
 * 文件校验与提交逻辑在 ./quick-investigation/useQuickInvestigation.ts。
 */

import { useQuickInvestigation } from './quick-investigation/useQuickInvestigation'

const {
  text,
  busy,
  error,
  dragging,
  fileInput,
  ACCEPT,
  shots,
  shotCount,
  examples,
  activeExample,
  canSubmit,
  useExample,
  pickFiles,
  removeShot,
  onPaste,
  onDrop,
  submit,
} = useQuickInvestigation()
</script>

<template>
  <div
    class="quick-start"
    :class="{ dragging }"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
  >
    <header class="composer-head">
      <div>
        <h2>描述你看到的现象</h2>
      </div>
      <span class="composer-policy">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect x="5" y="10" width="14" height="10" rx="2" />
          <path d="M8 10V7a4 4 0 0 1 8 0v3" />
        </svg>
        只读优先 · 执行需审批
      </span>
    </header>

    <div class="composer-input">
      <textarea
        v-model="text"
        rows="4"
        aria-label="现象描述"
        placeholder="用一句话描述现象，例如：订单服务从 10 点开始变慢，帮我查一下"
        @input="activeExample = ''"
        @keydown.meta.enter="submit"
        @keydown.ctrl.enter="submit"
        @paste="onPaste"
      />
      <div class="input-meta">
        <span>支持自然语言，不需要写命令</span>
        <span>{{ text.length }} / 2000</span>
      </div>
    </div>

    <div class="example-block">
      <div class="example-head">
        <span>试试这样问</span>
        <span>点击填入</span>
      </div>
      <div class="example-row">
        <button
          v-for="example in examples"
          :key="example"
          type="button"
          class="example"
          :class="{ active: activeExample === example }"
          @click="useExample(example)"
        >
          {{ example }}
        </button>
      </div>
    </div>

    <div class="attach-row">
      <div class="attach-main">
        <button type="button" class="shot-pick" :disabled="busy" @click="fileInput?.click()">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
          上传报错截图
        </button>
        <span class="shot-hint">最多 5 张 · PNG/JPG · 单张 ≤10 MB</span>
      </div>
      <span class="attach-hint">也可以拖入或直接粘贴</span>
      <input
        ref="fileInput"
        type="file"
        :accept="ACCEPT"
        multiple
        class="file-input"
        @change="pickFiles"
      />
    </div>

    <div v-if="shots.length" class="shot-list">
      <figure v-for="(shot, index) in shots" :key="shot.url" class="shot-card">
        <img :src="shot.url" :alt="shot.file.name" />
        <figcaption>
          <span class="shot-name">{{ shot.file.name }}</span>
          <button
            type="button"
            class="shot-remove"
            :aria-label="`删除 ${shot.file.name}`"
            @click="removeShot(index)"
          >
            删除
          </button>
        </figcaption>
      </figure>
    </div>

    <p v-if="shots.length" class="secure-tip">
      上传前请遮住密码、Token、身份证、手机号；服务端还会再检测一次，命中会拒绝本次提交。
    </p>

    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <div class="ask-foot">
      <small>
        Agent 会自动拆排查步骤；截图先做 OCR，识别结果作为“人工截图”证据进入报告。
        涉及执行的命令仍会停下来等你审批。
      </small>
      <div class="submit-zone">
        <span class="shortcut">Ctrl + Enter</span>
        <button class="primary ask-btn" type="button" :disabled="busy" @click="submit">
          {{ busy ? (shotCount ? '识别截图中…' : '提交中…') : '开始排查' }}
          <svg v-if="!busy" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped src="./quick-investigation/page.css"></style>
