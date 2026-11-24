# 运维 Agent 平台

审批制、可回滚、可审计的 AI 运维平台：支持受控排查取证、部署审批、命令执行和审计留痕。是否访问真实环境由平台配置和审批决定，不由环境名自动放行；样例数据只在人工启动的演示进程里显式启用。

## 仓库内容

本仓库只包含工程代码、启动脚本和公开说明。

- `code/`：后端、前端、桌面端、worker、共享包、测试与部署脚本
- `code/README.md`：目录职责、启动方式和测试命令

## 快速启动（Windows 本机）

```powershell
cd code
powershell -ExecutionPolicy Bypass -File ops/dev-stack.ps1
```

脚本会确保 PostgreSQL、Redis 容器在线，并维持后端、前端和队列 Worker。按 `Ctrl+C` 只停守护，不删数据。

容器方式和手动四终端方式见 [`code/README.md`](code/README.md)。

默认地址：

- 后台：`http://127.0.0.1:8000`，健康检查 `/api/health`
- 前端：`http://127.0.0.1:5173`
