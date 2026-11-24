/**
 * 前端入口：把 Vue 应用挂到页面上，并装上路由。
 *
 * 这里只做装配，不写业务逻辑。以后加状态管理、权限守卫、全局拦截，
 * 都在本文件注册，但「登录态怎么校验」的判断必须放服务端 ——
 * 前端藏菜单只是好看，不是安全边界（见桌面端红线）。
 */

import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
// 开源字体随构建产物打包，不依赖访问者本机是否安装：
// Noto Sans SC / Noto Serif SC，均为 OFL 授权。
import '@fontsource-variable/noto-sans-sc'
import '@fontsource-variable/noto-serif-sc'
import '@fontsource-variable/noto-sans-mono'
import './styles/theme.css'

createApp(App).use(router).mount('#app')
