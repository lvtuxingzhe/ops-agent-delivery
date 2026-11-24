/**
 * Vite 配置 —— 前端怎么起、怎么打包、怎么找到后台。
 *
 * 一份配置同时服务两个端，这是「桌面复用前端」的关键：
 *   网页版：浏览器打开 http://127.0.0.1:5173
 *   桌面版：Electron 开发期加载同一个地址；打包期加载 dist/index.html
 * 两边是同一份源码、同一份构建产物，不维护第二套 UI。
 */

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后台地址从环境变量读，没配就用本机默认。
// 这是「本机开发地址」，不是任何生产监控 / 生产主机地址，也不含账号密码。
// 第 ④ 步做配置页之后，真实地址由部署时填，代码这边一个字都不用改。
const backendTarget = process.env.BACKEND_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],

  // 关键：`base` 是 Vite 的**顶层**配置，不是 `build.base`。
  // base 必须放在顶层：打包成 file:// 后 HTML 需要从当前目录查找 /assets，
  // 桌面端就只剩一个空壳（标题对、登录卡片渲染不出来）。
  base: './',

  server: {
    host: '127.0.0.1',
    port: 5173,
    // 前端所有 /api 请求转发给后台。
    // 好处：页面只认同源的 /api，不写死后台域名；
    // 桌面端打包成 file:// 后也走同一套相对路径，不用改代码。
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },

  build: {
    // 产物固定放 frontend/dist，Electron 主进程就是按这个路径去 loadFile 的。
    // 两边共用同一个约定，别只改一头。
    outDir: 'dist',
  },
})
