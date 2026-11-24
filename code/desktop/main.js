/**
 * Electron 主进程 —— 桌面端的「壳」。
 *
 * 它有多薄？薄到只负责开一个窗口、把前端页面装进去。
 *
 * 为什么这么薄（这几条是定稿里的桌面端红线）
 * ------------------------------------------
 *  - 桌面端【不是另一套系统】，它和网页版打开的是同一份前端代码、
 *    调的是同一个后台接口、走的是同一套权限与审批、记的是同一本审计记录。
 *  - 桌面端【不能直接 SSH 连生产】：所有查询、所有写操作都发请求给后台，
 *    由后台执行。本文件里不出现任何 SSH / 生产主机 / 凭据相关的代码。
 *  - 桌面端【不能本地存凭据】：不写密码、不写长期令牌，登录令牌短时有效、
 *    退出即清。所以这里也没有 keystore / localStorage 持久化登录态的逻辑。
 *  - 桌面端【不能自己算权限】：菜单能不能点由服务端说了算，
 *    前端藏起来只是为了好看，不是安全边界。
 *  - 【不能因为「在本机」或「我是管理员」就免审批】：动生产的结构化确认、
 *    二次认证、方案指纹一次都不能少，全在后台判断。
 *
 * 前端从哪来（复用，不复制）
 * --------------------------
 *  开发期：加载 Vite 开发服务器地址，默认 http://127.0.0.1:5173
 *  打包期：加载 ../frontend/dist/index.html（前端构建产物）
 *  两种情况都由环境变量 FRONTEND_URL / 打包标志决定，代码里不写死业务地址。
 *
 * 第 ① 步只做到「能弹出窗口、能看到登录页」。
 * 托盘告警、本地文件拖入只读分析、离线脱敏摘要、导出留痕，
 * 是后面步骤的事，不在这里提前铺。
 */

const { app, BrowserWindow, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs'); // 仅 OPS_SELFTEST 自检模式用来落结果文件
const os = require('node:os');

// `npm run start:prod` 走的是「加载前端构建产物」，不依赖 cross-env：
// 原先脚本用 cross-env 设 NODE_ENV=production，但 cross-env 没进 devDependencies，
// 在新机器上 `npm run start:prod` 会直接报「找不到命令」。改成显式参数，
// 少一个依赖，Windows / macOS 行为也一致。
const FORCE_PROD = process.argv.includes('--prod');

// 开发期前端地址从环境变量读；没配就用本机默认地址。
// 注意这是「本机页面地址」，不是任何生产系统地址，也不含账号密码。
const DEV_FRONTEND_URL = process.env.FRONTEND_URL || 'http://127.0.0.1:5173';

// 自检模式：不弹窗，页面加载完成后把「标题 + 登录页文字」报出来就退出。
// 这样在没有桌面的机器 / 自动化环境里，也能真实验证「桌面壳能打开登录页」，
// 而不是只查一下 electron.exe 在不在。正常启动完全不受这个开关影响。
//
// 结果文件写到系统临时目录：打包后 __dirname 在 app.asar 里，asar 是只读归档，
// 往里写会得到 ENOENT。
const SELFTEST = process.env.OPS_SELFTEST === '1';
const SELFTEST_RESULT = path.join(os.tmpdir(), `ops-agent-selftest-${process.pid}.json`);

/** 决定这个窗口该装哪个页面。 */
function resolveFrontendEntry() {
  // 打包后（或 start:prod）用前端构建出来的静态文件，跟网页版是同一份产物。
  // 打包态的前端放在 resources/frontend-dist，不再沿 __dirname 往上找 ——
  // asar 里的目录层级和源码目录不一样，写死 ../frontend/dist 一定找不到。
  if (app.isPackaged) {
    return {
      kind: 'file',
      target: path.join(process.resourcesPath, 'frontend-dist/index.html'),
    };
  }
  if (FORCE_PROD) {
    return { kind: 'file', target: path.join(__dirname, '../frontend/dist/index.html') };
  }
  // 开发时直接连 Vite，改前端代码能热更新
  return { kind: 'url', target: DEV_FRONTEND_URL };
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    title: '运维 Agent 平台',
    // 自检时不弹窗，避免打扰桌面；正常启动照常显示
    show: !SELFTEST,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      // 下面两项是安全底线，别为了「方便调接口」关掉：
      // 关了就等于让页面里的脚本能直接摸到 Node 和文件系统，
      // 一条被注入的日志内容就可能变成一次本地执行。
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // 页面里点开的链接一律交给系统浏览器；不在壳里新开窗口。
  // 不做这一层，页面里的 <a target="_blank"> 会弹出一个没有 preload、
  // 也没人管权限的新窗口，等于绕开了壳的安全设置。
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://') || url.startsWith('http://')) {
      void shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  const entry = resolveFrontendEntry();
  if (entry.kind === 'file') {
    win.loadFile(entry.target);
  } else {
    win.loadURL(entry.target);
  }

  // ---- 自检模式：验证「桌面壳真的把登录页渲染出来了」 ----
  // 走的是和正常启动一模一样的 loadURL → 渲染进程 → Vue 挂载链路，
  // 只是不显示窗口、验完自己退出。结果同时打日志和写文件：
  // Windows 上 GUI 程序的 stdout 不一定能被父进程捕获，落一份文件更可靠。
  if (SELFTEST) {
    let reported = false;
    const report = (ok, title, heading, note) => {
      if (reported) return; // 成功和失败事件可能都来，只认第一次
      reported = true;
      const result = { ok, title, heading, note, entry: entry.target };
      try {
        fs.writeFileSync(SELFTEST_RESULT, JSON.stringify(result, null, 2), 'utf8');
      } catch (err) {
        console.log(`[selftest] 结果文件写入失败：${err.message}`);
      }
      console.log(`[selftest] 页面加载完成=${ok} 标题=${title} 登录页标题=${heading}`);
      app.quit();
    };

    win.webContents.once('did-finish-load', () => {
      const title = win.webContents.getTitle();
      // 从真实渲染后的 DOM 里取标题文字，比只看一个 HTTP 200 有说服力
      win.webContents
        .executeJavaScript("document.querySelector('.login-card h1')?.textContent || ''")
        .then((heading) => report(Boolean(heading), title, heading, ''))
        .catch((err) => report(false, title, '', err.message));
    });

    win.webContents.once('did-fail-load', (_event, errorCode, errorDescription) => {
      report(false, '', '', `加载失败 ${errorCode} ${errorDescription}`);
    });
  }

  // 开发期才开调试工具；打包后不开，避免把内部结构暴露出去
  if (!app.isPackaged && process.env.OPEN_DEVTOOLS === '1') {
    win.webContents.openDevTools();
  }
}

// 单实例：第二次双击图标不再开一个新进程，而是把已有窗口拉到前面。
// 不加锁的话两个窗口可能各自登录、各自提交，审计里会出现两条来源相同
// 但会话不同的记录，排查时很难说清「是谁点的」。
const hasLock = app.requestSingleInstanceLock();
if (!hasLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    const windows = BrowserWindow.getAllWindows();
    if (windows.length > 0) {
      const win = windows[0];
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });
}

app.whenReady().then(() => {
  createWindow();

  // macOS 习惯：点 dock 图标且没窗口时重新开一个。
  // 本项目第一期是 Windows，但留着不影响。
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

// 所有窗口关掉就退出（Windows / Linux）。
// 因为不持有任何长期凭据，退出即清这条自然满足。
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
