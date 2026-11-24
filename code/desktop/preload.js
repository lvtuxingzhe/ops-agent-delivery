/**
 * Electron 预加载脚本 —— 页面和主进程之间那道窄门。
 *
 * 主进程开了 contextIsolation + sandbox，页面里的 Vue 代码摸不到 Node、
 * 摸不到文件系统、也摸不到 Electron 的内部能力。它想跟外面说话，
 * 只能走这里用 contextBridge 明确列出来的几个方法。
 *
 * 当前只暴露一个「来自哪个端」的标记：
 * 后台记审计时会区分「来自网页 / 来自桌面」，靠的就是这类标记，
 * 而不是靠前端自己随便填一个字符串（那太容易伪造）。
 *
 * 【刻意不做】的事，免得以后有人顺手加上：
 *  - 不暴露 ipcRenderer 本体、不暴露 fs / child_process / shell；
 *  - 不读写本地文件、不缓存登录态、不存任何凭据；
 *  - 不提供「直接连后台之外地址」的通用请求入口。
 *    页面要发请求，走前端 src/api/http.ts 统一封装，指向同一个后台。
 *    这样「桌面端不能直连生产 SSH」在代码层面就无从下手。
 *
 * 注意：sandbox 模式下 preload 里只能 require electron 的少量 API，
 * 不能 require node:fs 之类，这本身就是又一层保护。
 */

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('desktopBridge', {
  /** 告诉前端「当前跑在桌面壳里」，供展示与审计标记使用。 */
  clientKind: 'desktop',

  /** 桌面壳版本，和 package.json 保持一致，排障时能对上是哪个安装包。 */
  shellVersion: '0.1.0',
});
