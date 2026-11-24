/**
 * 一次「提交 / 刷新 / 报错」的公共节奏 —— 三个配置面板共用。
 *
 * 拆出来的原因：原 `Config.vue` 里 busy/error/message/notice 四个状态和 run()
 * 被数据源、大模型、开关三处共用，拆组件时若各写一份，四份提示的清除时机
 * 迟早会走偏（比如某处忘了清 notice）。这里只留一份。
 *
 * `after` 是「成功之后」要做的刷新；失败不刷新，避免把用户刚填的表单冲掉。
 */

import { ref } from 'vue'

export function useAsyncAction(after?: () => void | Promise<void>) {
  const busy = ref(false)
  const error = ref('')
  const message = ref('')
  const notice = ref('')

  function flash(text: string) {
    message.value = text
    error.value = ''
    notice.value = ''
  }

  async function run(action: () => Promise<unknown>, okText: string): Promise<boolean> {
    busy.value = true
    error.value = ''
    message.value = ''
    notice.value = ''
    try {
      await action()
      flash(okText)
      if (after) await after()
      return true
    } catch (err) {
      error.value = err instanceof Error ? err.message : '操作失败'
      return false
    } finally {
      busy.value = false
    }
  }

  return { busy, error, message, notice, run, flash }
}
