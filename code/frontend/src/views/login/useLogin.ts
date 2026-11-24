/**
 * 登录页的状态与请求编排。
 *
 * 页面只负责呈现；登录方式、验证码倒计时、背景配置和错误话术都收在这里，
 * 便于单测和复用，也避免模板里散落请求逻辑。
 */

import { computed, onMounted, onUnmounted, ref, type CSSProperties } from 'vue'
import { useRouter } from 'vue-router'

import { fetchLoginMethods, login, sendSmsCode, type LoginMethods } from '../../api/auth'
import type { LoginPublicConfig } from '../../api/config'
import { setToken } from '../../api/http'
import { syncAppearance } from '../../utils/appearance'

export type LoginMethodKey = 'password' | 'totp' | 'sms'

export interface LoginMethodOption {
  key: LoginMethodKey
  label: string
  hint: string
}

export function useLogin() {
  const router = useRouter()

  const methods = ref<LoginMethods>({ password: true, totp: true, sms: true })
  const activeMethod = ref<LoginMethodKey>('password')
  const loginConfig = ref<LoginPublicConfig | null>(null)

  const username = ref('')
  const password = ref('')
  const totpCode = ref('')
  const phone = ref('')
  const smsCode = ref('')
  const smsCountdown = ref(0)

  const error = ref('')
  const busy = ref(false)
  const devCodeHint = ref('')

  let countdownTimer: ReturnType<typeof setInterval> | null = null

  function stopCountdown() {
    if (countdownTimer !== null) {
      clearInterval(countdownTimer)
      countdownTimer = null
    }
  }

  onMounted(async () => {
    // 背景与字体配置失败不能阻断登录；两种请求各管各的错误。
    void syncAppearance()
      .then((value) => { loginConfig.value = value })
      .catch(() => { loginConfig.value = null })

    try {
      methods.value = await fetchLoginMethods()
      if (methods.value.totp) activeMethod.value = 'totp'
      else if (methods.value.password) activeMethod.value = 'password'
      else if (methods.value.sms) activeMethod.value = 'sms'
    } catch {
      error.value = '暂时连不上后台，请确认服务已启动'
    }
  })

  onUnmounted(stopCountdown)

  const visibleMethods = computed<LoginMethodOption[]>(() => {
    const list: LoginMethodOption[] = []
    if (methods.value.password) list.push({ key: 'password', label: '账号密码', hint: '基础方式' })
    if (methods.value.totp) list.push({ key: 'totp', label: '密码 + 动态口令', hint: '推荐' })
    if (methods.value.sms) list.push({ key: 'sms', label: '手机号 + 短信', hint: '测试通道' })
    return list
  })

  const fromDesktop = computed(() => Boolean(window.desktopBridge))
  const appName = computed(() => loginConfig.value?.app_name || '运维 Agent')
  const backgroundUrl = computed(() => loginConfig.value?.login_background_url || '')
  const backgroundStyle = computed<CSSProperties>(() => {
    const url = backgroundUrl.value
    if (!url) return {}
    // 服务端只允许 http(s) 或 /api/assets/，这里再做一次引号转义，避免样式串断。
    return { backgroundImage: `url("${url.replace(/"/g, '%22')}")` }
  })

  async function handleLogin() {
    error.value = ''
    busy.value = true
    try {
      const result = await login({
        method: activeMethod.value,
        username: username.value,
        password: password.value,
        totp_code: totpCode.value,
        phone: phone.value,
        sms_code: smsCode.value,
      })
      setToken(result.token)
      router.push({ name: 'home' })
    } catch (err) {
      error.value = err instanceof Error ? err.message : '登录失败'
    } finally {
      busy.value = false
    }
  }

  async function handleSendSms() {
    if (!phone.value) {
      error.value = '请先输入手机号'
      return
    }
    error.value = ''
    try {
      const result = await sendSmsCode(phone.value)
      smsCountdown.value = 60
      stopCountdown()
      countdownTimer = setInterval(() => {
        smsCountdown.value -= 1
        if (smsCountdown.value <= 0) stopCountdown()
      }, 1000)
      if (import.meta.env.DEV && result.dev_code) {
        smsCode.value = result.dev_code
        devCodeHint.value = result.dev_note ?? '测试通道已自动填入验证码'
      }
    } catch (err) {
      error.value = err instanceof Error ? err.message : '发送失败'
    }
  }

  return {
    activeMethod,
    appName,
    backgroundStyle,
    busy,
    devCodeHint,
    error,
    fromDesktop,
    handleLogin,
    handleSendSms,
    password,
    phone,
    smsCode,
    smsCountdown,
    totpCode,
    username,
    visibleMethods,
  }
}
