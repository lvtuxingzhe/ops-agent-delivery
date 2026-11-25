<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  ReportFeedback,
  ReportFeedbackTarget,
  ReportFeedbackVerdict,
} from '../../../api/tasks'

const props = defineProps<{
  targetType: ReportFeedbackTarget
  targetIndex: number
  feedback?: ReportFeedback
  saving?: boolean
}>()

const emit = defineEmits<{
  submit: [payload: {
    targetType: ReportFeedbackTarget
    targetIndex: number
    verdict: ReportFeedbackVerdict
    correctedText: string
    note: string
  }]
}>()

const editing = ref(false)
const correctedText = ref('')
const note = ref('')
const localError = ref('')

watch(
  () => props.feedback,
  (item) => {
    if (!item) {
      correctedText.value = ''
      note.value = ''
      editing.value = false
      return
    }
    correctedText.value = item.corrected_text
    note.value = item.note
  },
  { immediate: true },
)

const verdictLabel = computed(() => {
  if (!props.feedback) return ''
  if (props.feedback.verdict === 'confirm') return '已标记为准确'
  if (props.feedback.verdict === 'reject') return '已标记为不准确'
  return '已提交纠正'
})

const verdictTone = computed(() => {
  if (!props.feedback) return ''
  if (props.feedback.verdict === 'confirm') return 'is-positive'
  if (props.feedback.verdict === 'reject') return 'is-negative'
  return 'is-corrected'
})

function submit(verdict: ReportFeedbackVerdict) {
  localError.value = ''
  if (verdict === 'correct' && !correctedText.value.trim()) {
    localError.value = '请填写正确的内容'
    return
  }
  emit('submit', {
    targetType: props.targetType,
    targetIndex: props.targetIndex,
    verdict,
    correctedText: verdict === 'correct' ? correctedText.value.trim() : '',
    note: note.value.trim(),
  })
  if (verdict === 'correct') editing.value = false
}

function startCorrect() {
  editing.value = true
  localError.value = ''
  if (props.feedback?.verdict === 'correct') {
    correctedText.value = props.feedback.corrected_text
    note.value = props.feedback.note
  }
}
</script>

<template>
  <div class="report-feedback">
    <div class="report-feedback-actions">
      <span class="feedback-prompt">这条判断</span>
      <div class="feedback-choices" role="group" aria-label="这条判断的反馈">
        <button
          type="button"
          class="feedback-btn"
          :class="{ 'is-active': feedback?.verdict === 'confirm' }"
          :disabled="saving"
          @click="submit('confirm')"
        >
          准确
        </button>
        <button
          type="button"
          class="feedback-btn"
          :class="{ 'is-active': feedback?.verdict === 'reject' }"
          :disabled="saving"
          @click="submit('reject')"
        >
          不准确
        </button>
      </div>
      <span class="feedback-divider" aria-hidden="true"></span>
      <button
        type="button"
        class="feedback-correct"
        :class="{ 'is-active': feedback?.verdict === 'correct' }"
        @click="startCorrect"
      >
        {{ feedback?.verdict === 'correct' ? '修改纠正' : '纠正' }}
      </button>
      <span v-if="saving" class="feedback-state">保存中…</span>
      <span v-else-if="verdictLabel" class="feedback-state" :class="verdictTone">
        <span class="feedback-state-mark" aria-hidden="true"></span>
        {{ verdictLabel }}
      </span>
    </div>

    <div v-if="feedback?.verdict === 'correct' && feedback.corrected_text && !editing" class="feedback-corrected">
      <span class="desc">你的纠正</span>
      <p>{{ feedback.corrected_text }}</p>
    </div>

    <div v-if="editing" class="feedback-editor">
      <label class="field-label" :for="`feedback-corrected-${targetType}-${targetIndex}`">
        正确的内容
      </label>
      <textarea
        :id="`feedback-corrected-${targetType}-${targetIndex}`"
        v-model="correctedText"
        class="textarea"
        rows="3"
        maxlength="2000"
        placeholder="写清楚你认为正确的结论，便于后续复盘和沉淀知识"
      ></textarea>
      <label class="field-label" :for="`feedback-note-${targetType}-${targetIndex}`">
        备注（可不填）
      </label>
      <input
        :id="`feedback-note-${targetType}-${targetIndex}`"
        v-model="note"
        class="input"
        maxlength="1000"
        placeholder="例如：现场日志显示是另一个原因"
      />
      <p v-if="localError" class="error">{{ localError }}</p>
      <div class="feedback-editor-actions">
        <button type="button" class="btn btn-primary btn-sm" :disabled="saving" @click="submit('correct')">
          保存纠正
        </button>
        <button type="button" class="btn btn-ghost btn-sm" @click="editing = false">
          取消
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.report-feedback {
  margin-top: 10px;
}

.report-feedback-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
}

.feedback-prompt {
  color: var(--muted);
  font-size: calc(12px * var(--font-scale, 1));
}

.feedback-choices {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border-radius: 999px;
  background: var(--surface-soft);
}

.feedback-btn {
  border: 0;
  background: transparent;
  color: var(--ink-2);
  border-radius: 999px;
  padding: 3px 11px;
  font-size: calc(12px * var(--font-scale, 1));
  line-height: 1.5;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.feedback-btn:hover {
  background: var(--surface);
  color: var(--accent);
}

.feedback-btn.is-active {
  background: var(--accent-soft);
  color: var(--accent);
}

.feedback-btn.is-active:first-child {
  background: var(--success-soft);
  color: var(--success);
}

.feedback-btn.is-active:last-child {
  background: var(--danger-soft);
  color: var(--danger);
}

.feedback-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.feedback-divider {
  width: 1px;
  height: 16px;
  margin: 0 2px;
  background: var(--line);
}

.feedback-correct {
  border: 0;
  background: transparent;
  color: var(--accent);
  border-radius: 6px;
  padding: 3px 6px;
  font-size: calc(12px * var(--font-scale, 1));
  cursor: pointer;
}

.feedback-correct:hover {
  background: var(--accent-soft);
}

.feedback-correct.is-active {
  color: var(--accent-ink);
}

.feedback-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--surface-soft);
  color: var(--muted);
  font-size: calc(12px * var(--font-scale, 1));
}

.feedback-state.is-positive {
  background: var(--success-soft);
  color: var(--success);
}

.feedback-state.is-negative {
  background: var(--danger-soft);
  color: var(--danger);
}

.feedback-state.is-corrected {
  background: var(--accent-soft);
  color: var(--accent-ink);
}

.feedback-state-mark {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.feedback-corrected {
  margin-top: 6px;
  padding: 8px 10px;
  border-left: 3px solid var(--accent);
  background: var(--accent-soft);
  border-radius: 4px;
}

.feedback-corrected p {
  margin: 2px 0 0;
  white-space: pre-wrap;
}

.feedback-editor {
  margin-top: 8px;
  display: grid;
  gap: 6px;
  max-width: 720px;
}

.feedback-editor-actions {
  display: flex;
  gap: 8px;
}

@media (max-width: 760px) {
  .feedback-editor-actions {
    flex-wrap: wrap;
  }
}
</style>
