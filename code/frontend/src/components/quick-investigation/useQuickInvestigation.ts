/**
 * 自然语言排查入口状态与提交逻辑。
 *
 * 组件只负责渲染输入框、示例和截图列表；文件校验、粘贴/拖拽和提交都在这里。
 */

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { submitTask, submitTaskWithScreenshots } from '../../api/tasks'

export function useQuickInvestigation() {
  const router = useRouter()
  const text = ref('')
  const busy = ref(false)
  const error = ref('')
  const dragging = ref(false)
  const fileInput = ref<HTMLInputElement | null>(null)

  const MAX_FILES = 5
  const MAX_BYTES = 10 * 1024 * 1024
  const ACCEPT = 'image/png,image/jpeg'

  interface Shot {
    file: File
    url: string
  }

  const shots = ref<Shot[]>([])
  const shotCount = computed(() => shots.value.length)

  const examples = [
    '订单服务从 10 点开始变慢，帮我查一下',
    'demo-web-01 磁盘快满了，看看是什么在涨',
    '支付 Pod 一直重启，帮我定位原因',
  ]

  const activeExample = ref('')
  const canSubmit = computed(
    () => text.value.trim().length >= 5 || shots.value.length > 0,
  )

  function useExample(example: string) {
    text.value = example
    activeExample.value = example
    error.value = ''
  }

  function makeTitle(input: string): string {
    const firstLine = input.split('\n')[0].trim()
    if (!firstLine) return shots.value[0]?.file.name.slice(0, 48) || '报错截图排查'
    return firstLine.length > 48 ? `${firstLine.slice(0, 48)}…` : firstLine
  }

  function addFiles(files: File[] | FileList) {
    error.value = ''
    const incoming = Array.from(files)
    for (const file of incoming) {
      if (shots.value.length >= MAX_FILES) {
        error.value = `一次最多上传 ${MAX_FILES} 张截图`
        break
      }
      if (!['image/png', 'image/jpeg'].includes(file.type)) {
        error.value = '只支持 PNG / JPEG 截图'
        continue
      }
      if (file.size > MAX_BYTES) {
        error.value = `“${file.name}”超过 10 MB，请压缩后重传`
        continue
      }
      shots.value.push({ file, url: URL.createObjectURL(file) })
    }
    if (fileInput.value) fileInput.value.value = ''
  }

  function pickFiles(event: Event) {
    const input = event.target as HTMLInputElement
    if (input.files) addFiles(input.files)
  }

  function removeShot(index: number) {
    const shot = shots.value[index]
    if (shot) URL.revokeObjectURL(shot.url)
    shots.value.splice(index, 1)
  }

  function onPaste(event: ClipboardEvent) {
    const files = Array.from(event.clipboardData?.files || []).filter((file) =>
      file.type.startsWith('image/'),
    )
    if (files.length) {
      event.preventDefault()
      addFiles(files)
    }
  }

  function onDrop(event: DragEvent) {
    dragging.value = false
    if (event.dataTransfer?.files?.length) addFiles(event.dataTransfer.files)
  }

  async function submit() {
    if (busy.value) return
    if (!canSubmit.value) {
      error.value = '请先描述现象，或上传一张报错截图。'
      return
    }
    busy.value = true
    error.value = ''
    try {
      const trimmed = text.value.trim()
      const payload = {
        title: makeTitle(trimmed),
        description: trimmed || '请根据上传的报错截图定位问题。',
        target_scope: {},
      }
      const ack = shots.value.length
        ? await submitTaskWithScreenshots(payload, shots.value.map((item) => item.file))
        : await submitTask(payload)
      await router.push(`/tasks/${ack.id}`)
    } catch (err) {
      error.value = err instanceof Error ? err.message : '提交失败'
    } finally {
      busy.value = false
    }
  }

  return {
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
  }
}
