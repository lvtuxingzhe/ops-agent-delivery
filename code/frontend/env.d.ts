/// <reference types="vite/client" />

/**
 * 让 TypeScript 认识 .vue 单文件组件，以及桌面壳注入的那个标记。
 *
 * desktopBridge 来自 desktop/preload.js，只有跑在 Electron 里才有。
 * 所以声明成「可有可无」，网页版访问时是 undefined，不会报错。
 */
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

interface DesktopBridge {
  /** 标记当前客户端来自桌面壳，供展示与审计标记使用 */
  clientKind: 'desktop'
  /** 桌面壳版本，排障时用来对上是哪个安装包 */
  shellVersion: string
}

interface Window {
  desktopBridge?: DesktopBridge
}
