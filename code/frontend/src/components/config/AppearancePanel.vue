<script setup lang="ts">
/**
 * 外观配置 —— 配置页新增的「外观」标签页。
 *
 * 这里只做三件事：选开源字体与字号、换登录页背景、换 OA 左上角品牌块背景。
 * 业务开关仍在「平台设置」标签页，不混进来。
 */

import { computed, onMounted, ref } from 'vue'

import {
  fetchLoginConfig,
  fetchSettings,
  resetBrandBackground,
  resetLoginBackground,
  setSetting,
  uploadBrandBackground,
  uploadLoginBackground,
  type PlatformSettings,
} from '../../api/config'
import { applyAppearance } from '../../utils/appearance'

const settings = ref<PlatformSettings>({})
const fontFamily = ref<'noto-sans' | 'noto-serif'>('noto-sans')
const fontSize = ref('14')
const busy = ref(false)
const error = ref('')
const message = ref('')

const loginFile = ref<File | null>(null)
const loginPreview = ref('')
const loginInput = ref<HTMLInputElement | null>(null)
const brandFile = ref<File | null>(null)
const brandPreview = ref('')
const brandInput = ref<HTMLInputElement | null>(null)

const fontOptions = [
  { value: 'noto-sans', label: '思源黑体（Noto Sans SC）', hint: '适合后台正文与表格，默认' },
  { value: 'noto-serif', label: '思源宋体（Noto Serif SC）', hint: '衬线字形，适合阅读型页面' },
] as const

const fontSizeOptions = ['13', '14', '15', '16'] as const

const loginBackground = computed(() => settings.value.login_background_url || '')
const brandBackground = computed(() => settings.value.brand_background_url || '')
const loginPreviewUrl = computed(() => loginPreview.value || loginBackground.value)
const brandPreviewUrl = computed(() => brandPreview.value || brandBackground.value)

async function reload() {
  error.value = ''
  try {
    const result = await fetchSettings()
    settings.value = result.settings
    fontFamily.value = result.settings.ui_font_family === 'noto-serif' ? 'noto-serif' : 'noto-sans'
    fontSize.value = ['13', '14', '15', '16'].includes(result.settings.ui_font_size)
      ? result.settings.ui_font_size
      : '14'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '外观配置加载失败'
  }
}

onMounted(reload)

async function saveFont() {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    // 两个键分开写；任一步失败都保留在页面上，不假装成功。
    await setSetting('ui_font_family', fontFamily.value)
    await setSetting('ui_font_size', fontSize.value)
    const publicConfig = await fetchLoginConfig()
    applyAppearance(publicConfig)
    settings.value = { ...settings.value, ui_font_family: fontFamily.value, ui_font_size: fontSize.value }
    message.value = '字体与字号已保存，当前页立即生效，其他标签页刷新后生效。'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '字体保存失败'
  } finally {
    busy.value = false
  }
}

function clearPreview(kind: 'login' | 'brand') {
  const url = kind === 'login' ? loginPreview.value : brandPreview.value
  if (url.startsWith('blob:')) URL.revokeObjectURL(url)
  if (kind === 'login') loginPreview.value = ''
  else brandPreview.value = ''
}

function pickFile(kind: 'login' | 'brand', event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  clearPreview(kind)
  if (kind === 'login') {
    loginFile.value = file
    loginPreview.value = file ? URL.createObjectURL(file) : ''
  } else {
    brandFile.value = file
    brandPreview.value = file ? URL.createObjectURL(file) : ''
  }
}

async function uploadBackground(kind: 'login' | 'brand') {
  const file = kind === 'login' ? loginFile.value : brandFile.value
  if (!file) {
    error.value = '先选择一张图片（PNG / JPEG / WEBP，≤8MB）'
    return
  }
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    if (kind === 'login') {
      await uploadLoginBackground(file)
      loginFile.value = null
      clearPreview('login')
      if (loginInput.value) loginInput.value.value = ''
    } else {
      await uploadBrandBackground(file)
      brandFile.value = null
      clearPreview('brand')
      if (brandInput.value) brandInput.value.value = ''
    }
    await reload()
    message.value = kind === 'login'
      ? '登录页背景已替换；刷新登录页即可看到。'
      : 'OA 品牌块背景已替换；刷新主界面即可看到。'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '背景图上传失败'
  } finally {
    busy.value = false
  }
}

async function resetBackground(kind: 'login' | 'brand') {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    if (kind === 'login') {
      await resetLoginBackground()
      clearPreview('login')
      loginFile.value = null
    } else {
      await resetBrandBackground()
      clearPreview('brand')
      brandFile.value = null
    }
    await reload()
    message.value = kind === 'login' ? '已恢复登录页内置渐变。' : '已恢复 OA 品牌块默认底色。'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '恢复默认失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="message" class="message">{{ message }}</p>

  <section class="card">
    <div class="appearance-head">
      <div>
        <h2>字体与字号</h2>
      </div>
      <span class="tag tag-on">开源授权</span>
    </div>

    <div class="appearance-form">
      <label class="appearance-field">
        <span>界面字体</span>
        <select v-model="fontFamily" :disabled="busy">
          <option v-for="item in fontOptions" :key="item.value" :value="item.value">
            {{ item.label }} · {{ item.hint }}
          </option>
        </select>
      </label>

      <div class="appearance-field">
        <span>正文字号</span>
        <div class="size-options" role="group" aria-label="正文字号">
          <button
            v-for="size in fontSizeOptions"
            :key="size"
            type="button"
            class="size-chip"
            :class="{ active: fontSize === size }"
            :disabled="busy"
            @click="fontSize = size"
          >
            {{ size }}px
          </button>
        </div>
      </div>
    </div>

    <div class="appearance-actions">
      <button type="button" class="primary" :disabled="busy" @click="saveFont">保存外观</button>
    </div>
  </section>

  <section class="card">
    <div class="appearance-head">
      <div>
        <h2>登录页背景</h2>
      </div>
      <span :class="['tag', loginBackground ? 'tag-on' : 'tag-off']">
        {{ loginBackground ? '已配置' : '内置渐变' }}
      </span>
    </div>

    <div class="appearance-bg-row">
      <div
        class="appearance-bg-preview login-preview"
        :style="loginPreviewUrl ? { backgroundImage: `url(${loginPreviewUrl})` } : undefined"
      >
        <span v-if="!loginPreviewUrl">当前使用内置渐变</span>
      </div>
      <div class="appearance-bg-actions">
        <input
          ref="loginInput"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          @change="pickFile('login', $event)"
        />
        <div class="appearance-bg-buttons">
          <button type="button" class="primary" :disabled="busy || !loginFile" @click="uploadBackground('login')">上传并替换</button>
          <button type="button" class="plain" :disabled="busy || !loginBackground" @click="resetBackground('login')">恢复默认</button>
        </div>
      </div>
    </div>
  </section>

  <section class="card">
    <div class="appearance-head">
      <div>
        <h2>OA 品牌块背景</h2>
      </div>
      <span :class="['tag', brandBackground ? 'tag-on' : 'tag-off']">
        {{ brandBackground ? '已配置' : '默认底色' }}
      </span>
    </div>

    <div class="appearance-bg-row">
      <div
        class="appearance-bg-preview brand-preview"
        :style="brandPreviewUrl ? { backgroundImage: `url(${brandPreviewUrl})` } : undefined"
      >
        <span v-if="!brandPreviewUrl">当前使用侧栏默认深色</span>
        <b v-else>OA · 运维 Agent</b>
      </div>
      <div class="appearance-bg-actions">
        <input
          ref="brandInput"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          @change="pickFile('brand', $event)"
        />
        <div class="appearance-bg-buttons">
          <button type="button" class="primary" :disabled="busy || !brandFile" @click="uploadBackground('brand')">上传并替换</button>
          <button type="button" class="plain" :disabled="busy || !brandBackground" @click="resetBackground('brand')">恢复默认</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.appearance-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.appearance-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 0.8fr);
  gap: 20px;
  align-items: end;
}

.appearance-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.appearance-field > span {
  color: var(--ink);
  font-weight: 600;
  font-size: calc(13px * var(--font-scale, 1));
}

.appearance-field select {
  width: 100%;
}

.size-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.size-chip {
  min-width: 64px;
}

.size-chip.active {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-ink);
}

.appearance-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 16px;
}

.appearance-bg-row {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(240px, 1fr);
  gap: 16px;
  align-items: stretch;
}

.appearance-bg-preview {
  min-height: 158px;
  display: grid;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: 12px;
  background-size: cover;
  background-position: center;
  color: rgba(226, 232, 240, 0.72);
  font-size: calc(12px * var(--font-scale, 1));
  overflow: hidden;
}

.login-preview {
  background:
    radial-gradient(700px 260px at 80% 0%, rgba(37, 99, 235, 0.16), transparent 60%),
    linear-gradient(135deg, #0b1220, #16233a 60%, #0f2e3a);
}

.brand-preview {
  background: var(--nav);
}

.brand-preview b {
  color: #fff;
  font-size: calc(16px * var(--font-scale, 1));
  letter-spacing: 0.02em;
}

.appearance-bg-actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.appearance-bg-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.muted {
  color: var(--muted);
}

@media (max-width: 860px) {
  .appearance-form,
  .appearance-bg-row {
    grid-template-columns: 1fr;
  }
}
</style>
