<script setup lang="ts">
/**
 * 告警自动建单配置：开关、范围、限流和“试一条”预检。
 *
 * 这些值仍走统一的平台配置接口，不另造一套配置表；
 * 保存后立即生效，下一次告警同步或 webhook 就会按新规则判断。
 */

import { onMounted, reactive, ref } from 'vue'

import { fetchSettings, setSetting } from '../../api/config'
import { previewAlertPolicy, type AlertPreviewResult } from '../../api/notifications'
import { useAsyncAction } from '../../composables/useAsyncAction'
import FoldToggle from './FoldToggle.vue'

const severities = [
  { value: 'critical', label: 'critical' },
  { value: 'warning', label: 'warning' },
  { value: 'info', label: 'info' },
]

const policy = reactive({
  enabled: true,
  severities: ['critical'],
  hosts: '*',
  alertnames: '*',
  maxPerHour: 2,
  maxPerDay: 6,
  maxConcurrent: 2,
  minIntervalMinutes: 60,
  minFiringSeconds: 120,
})

const preview = reactive({
  host: '',
  alertname: '',
  severity: 'critical',
})
const previewResult = ref<AlertPreviewResult | null>(null)
const loaded = ref(false)
const collapsed = ref(true)

const { busy, error, message, run } = useAsyncAction(reload)

function splitList(value: string): string[] {
  return String(value || '')
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function joinList(items: string[]): string {
  return items.filter(Boolean).join(',')
}

async function reload() {
  try {
    const { settings } = await fetchSettings()
    policy.enabled = settings.alert_auto_investigation_enabled === 'true'
    policy.severities = splitList(settings.alert_auto_allow_severities || 'critical')
    policy.hosts = settings.alert_auto_allow_hosts ?? '*'
    policy.alertnames = settings.alert_auto_allow_alertnames ?? '*'
    policy.maxPerHour = Number(settings.alert_auto_max_per_hour || 2)
    policy.maxPerDay = Number(settings.alert_auto_max_per_day || 6)
    policy.maxConcurrent = Number(settings.alert_auto_max_concurrent || 2)
    policy.minIntervalMinutes = Number(settings.alert_auto_min_interval_minutes || 60)
    policy.minFiringSeconds = Number(settings.alert_auto_min_firing_seconds || 120)
    loaded.value = true
  } catch (err) {
    error.value = err instanceof Error ? err.message : '告警自动建单配置加载失败'
  }
}

onMounted(reload)

function save() {
  void run(async () => {
    const values: Array<[string, string]> = [
      ['alert_auto_investigation_enabled', policy.enabled ? 'true' : 'false'],
      ['alert_auto_allow_severities', joinList(policy.severities)],
      ['alert_auto_allow_hosts', policy.hosts.trim()],
      ['alert_auto_allow_alertnames', policy.alertnames.trim()],
      ['alert_auto_max_per_hour', String(policy.maxPerHour)],
      ['alert_auto_max_per_day', String(policy.maxPerDay)],
      ['alert_auto_max_concurrent', String(policy.maxConcurrent)],
      ['alert_auto_min_interval_minutes', String(policy.minIntervalMinutes)],
      ['alert_auto_min_firing_seconds', String(policy.minFiringSeconds)],
    ]
    for (const [key, value] of values) {
      await setSetting(key, value)
    }
    await reload()
  }, '告警自动建单配置已保存，下一条告警按新规则判断')
}

function runPreview() {
  void run(async () => {
    previewResult.value = await previewAlertPolicy({
      host: preview.host.trim(),
      alertname: preview.alertname.trim(),
      severity: preview.severity,
    })
  }, '预检完成')
}

function toggleSeverity(value: string) {
  if (policy.severities.includes(value)) {
    policy.severities = policy.severities.filter((item) => item !== value)
  } else {
    policy.severities = [...policy.severities, value]
  }
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="message" class="message">{{ message }}</p>

  <section v-if="loaded" class="card alert-auto-card">
    <div class="card-head">
      <div>
        <p class="kicker">ALERT AUTO INVESTIGATION</p>
        <h2>告警自动建单</h2>
        <p class="desc">
          监控告警满足范围后自动创建只读排查任务。留空表示不建单；填
          <code>*</code> 表示全部。限流和风暴判断始终生效。
        </p>
      </div>
      <div class="card-actions">
        <FoldToggle
          :expanded="!collapsed"
          controls="alert-auto-body"
          @toggle="collapsed = !collapsed"
        />
      </div>
    </div>

    <div v-show="!collapsed" id="alert-auto-body">
    <div class="alert-auto-state">
      <span :class="['tag', policy.enabled ? 'tag-on' : 'tag-off']">
        {{ policy.enabled ? '总开关已开启' : '总开关已关闭' }}
      </span>
      <span
        :class="['tag', (policy.hosts && policy.alertnames && policy.severities.length) ? 'tag-on' : 'tag-warn']"
      >
        {{ (policy.hosts && policy.alertnames && policy.severities.length) ? '范围已配置' : '范围为空，不会建单' }}
      </span>
      <span class="desc">
        当前最多 {{ policy.maxPerHour }} 条/小时、{{ policy.maxPerDay }} 条/天，
        同时最多 {{ policy.maxConcurrent }} 条排查。
      </span>
    </div>

    <div class="form-grid alert-auto-form">
      <label class="check">
        <input v-model="policy.enabled" type="checkbox" />
        启用告警自动建单
      </label>

      <div class="wide">
        <span class="field-label">允许的告警级别</span>
        <div class="alert-severity-list">
          <label v-for="item in severities" :key="item.value" class="check">
            <input
              :checked="policy.severities.includes(item.value)"
              type="checkbox"
              @change="toggleSeverity(item.value)"
            />
            {{ item.label }}
          </label>
        </div>
      </div>

      <label class="wide">
        主机白名单
        <input
          v-model="policy.hosts"
          placeholder="* 表示全部；也可填 host-150,192.0.2.153"
        />
      </label>

      <label class="wide">
        告警名白名单
        <input
          v-model="policy.alertnames"
          placeholder="* 表示全部；也可填 HighCPU,DiskFull"
        />
      </label>

      <label>
        每小时最多
        <input v-model.number="policy.maxPerHour" type="number" min="1" max="20" />
      </label>
      <label>
        每天最多
        <input v-model.number="policy.maxPerDay" type="number" min="1" max="100" />
      </label>
      <label>
        同时最多
        <input v-model.number="policy.maxConcurrent" type="number" min="1" max="10" />
      </label>
      <label>
        同组最小间隔（分钟）
        <input v-model.number="policy.minIntervalMinutes" type="number" min="1" max="1440" />
      </label>
      <label>
        最短持续时间（秒）
        <input v-model.number="policy.minFiringSeconds" type="number" min="0" max="3600" />
      </label>
    </div>

    <div class="alert-auto-actions">
      <button type="button" class="primary" :disabled="busy" @click="save">
        保存
      </button>
    </div>

    <div class="alert-preview">
      <div class="card-head">
        <div>
          <h3>试一条告警</h3>
          <p class="desc">只判断规则，不会真的建任务，也不发通知。</p>
        </div>
      </div>
      <div class="form-grid alert-preview-form">
        <label>
          主机
          <input v-model="preview.host" placeholder="host-150 或 192.0.2.153" />
        </label>
        <label>
          告警名
          <input v-model="preview.alertname" placeholder="HighCPU" />
        </label>
        <label>
          级别
          <select v-model="preview.severity">
            <option v-for="item in severities" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <div class="alert-preview-action">
          <button type="button" class="plain" :disabled="busy" @click="runPreview">
            检查
          </button>
        </div>
      </div>
      <div v-if="previewResult" class="alert-preview-result">
        <span :class="['tag', previewResult.would_create ? 'tag-on' : 'tag-warn']">
          {{ previewResult.would_create ? '会建单' : '不会建单' }}
        </span>
        <span>{{ previewResult.reason_text }}</span>
        <span v-if="!previewResult.host_registered" class="desc">
          这台主机没有登记；只有白名单含 * 时才会建 no_ssh 排查。
        </span>
      </div>
    </div>
    </div>
  </section>
</template>

<style scoped>
.alert-auto-card {
  margin-top: 16px;
}

.alert-auto-state {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 12px 0;
}

.alert-severity-list {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  padding-top: 6px;
}

.field-label {
  display: block;
  margin-bottom: 6px;
}

.alert-auto-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

.alert-preview {
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--line);
}

.alert-preview-form {
  align-items: end;
}

.alert-preview-action {
  display: flex;
  align-items: end;
}

.alert-preview-result {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}
</style>
