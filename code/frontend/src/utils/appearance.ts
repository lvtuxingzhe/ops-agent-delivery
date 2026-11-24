/**
 * 全站外观：字体、字号与品牌块背景。
 *
 * 服务端只保存「选哪套字体、几号字」这种语义值，具体 font-family 与缩放
 * 系数在前端映射。这样换字体只改这里，不需要碰数据库里的历史值。
 */

import { fetchLoginConfig, type LoginPublicConfig } from '../api/config'

/** 只使用 OFL 开源字体；不把 Segoe UI、微软雅黑等系统专有字体放进栈里。 */
export const FONT_STACKS: Record<string, string> = {
  'noto-sans': '"Noto Sans SC Variable", "Noto Sans SC", "Source Han Sans SC", sans-serif',
  'noto-serif': '"Noto Serif SC Variable", "Noto Serif SC", "Source Han Serif SC", serif',
}

const BASE_FONT_SIZE = 14

/** 把公开配置里的字体、字号、品牌背景落到 CSS 变量上。 */
export function applyAppearance(config: LoginPublicConfig | null | undefined): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  const family = config?.ui_font_family === 'noto-serif' ? 'noto-serif' : 'noto-sans'
  const size = Number(config?.ui_font_size || BASE_FONT_SIZE)
  const scale = Number.isFinite(size) && size >= 13 && size <= 16 ? size / BASE_FONT_SIZE : 1

  root.style.setProperty('--font', FONT_STACKS[family])
  root.style.setProperty('--font-scale', String(scale))
  root.dataset.fontFamily = family
  root.dataset.fontSize = String(size)
}

/** 读取公开外观配置并立即应用；失败不阻塞页面，保留上次/默认值。 */
export async function syncAppearance(): Promise<LoginPublicConfig | null> {
  try {
    const config = await fetchLoginConfig()
    applyAppearance(config)
    return config
  } catch {
    return null
  }
}
