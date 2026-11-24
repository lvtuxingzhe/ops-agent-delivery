# 运维 Agent 平台

审批制、可回滚、可审计的 AI 运维平台：支持受控排查取证、部署审批、命令执行和审计留痕。生产环境使用真实执行，测试环境使用测试数据；样例数据只在人工启动的演示进程里显式启用。

## 仓库内容

本仓库只包含工程代码，不包含内部文档、运维手册和截图。

- `code/`：后端、前端、桌面端、worker、共享包、测试与部署脚本
- `code/README.md`：目录职责、启动方式、自检命令和敏感文件边界

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

## 安全边界

以下文件或目录属于敏感资产，不提交、不发聊天工具、不随意移动：

- `code/.env`
- `code/secrets.encrypted.env`
- `code/secrets.encrypted.env.*`
- `code/var/secrets/`
- `code/var/backups/secrets/`

后台是唯一对外入口；桌面端不直连 SSH，worker 不暴露端口。凭据不得出现在日志、报告、前端或报错信息中。
