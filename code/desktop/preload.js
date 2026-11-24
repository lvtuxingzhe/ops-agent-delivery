/**
 * Electron 预加载脚本 —— 页面和主进程之间那道窄门。
 *
 * 主进程开了 contextIsolation + sandbox，页面里的 Vue 代码访问不到 Node、
 * 文件系统或 Electron 的内部能力。它通过这里用 contextBridge 暴露的方法
 * 与外部通信。
 *
 * 当前只暴露一个「来自哪个端」的标记：
 * 后台记审计时会区分「来自网页 / 来自桌面」，靠的就是这类标记，
 * 而不是靠前端自己填一个字符串。
 *
 * 这里不暴露 ipcRenderer 本体，也不暴露 fs / child_process / shell；
 * 本地文件、登录态和凭据都不经过这里。页面请求统一走
 * 前端 src/api/http.ts，指向同一个后台。
 *
 * sandbox 模式下 preload 只 require electron 的少量 API，
 * 加载不了 node:fs 等模块，这本身也是另一层隔离。
 */

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('desktopBridge', {
  /** 告诉前端「当前跑在桌面壳里」，供展示与审计标记使用。 */
  clientKind: 'desktop',

  /** 桌面壳版本，和 package.json 保持一致，排障时能对上是哪个安装包。 */
  shellVersion: '0.1.0',
});
