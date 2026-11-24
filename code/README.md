# 运维 Agent 平台 · 工程代码

> 本文件是代码目录的入口：目录职责、启动方式和测试命令。

## 先看这里

- 第一次使用先按本地方式启动。默认 `APP_ENV=test` 只是测试环境，不是演练模式；登记真实主机、数据源或审批执行后，仍会访问真实环境。正式上线由生产编排固定 `APP_ENV=production`。
- 后台默认 `http://127.0.0.1:8000`，前端默认 `http://127.0.0.1:5173`，worker 不暴露端口。
- Windows 本机启动优先用 `code/ops/dev-stack.ps1`；容器方式见下面的“Compose”。
- 配置文件与会话密钥由本机生成，仓库中不包含这些文件。

## 代码目录职责

| 目录 | 职责 |
|---|---|
| `backend/` | FastAPI 后台、服务、迁移和运行脚本 |
| `frontend/` | Vue + Vite 网页界面 |
| `desktop/` | Electron 桌面壳 |
| `worker/` | AgentScope 队列消费者与执行侧工具 |
| `packages/` | 后台与 worker 共用的纯逻辑 |
| `tests/` | pytest 测试 |
| `eval/` | Agent 评测脚手架和报告 |
| `samples/` | 四类本地假数据 |
| `ops/`、`deploy/` | 本地运行栈、备份、部署脚本 |
| `scripts/` | 测试脚本（`selftests/`） |
| `var/` | 模型、工具、上传、审计归档、密钥等运行数据 |

## 四个进程分别是什么

| 进程 | 目录 | 干什么 |
|---|---|---|
| 后台 FastAPI | `backend/` | 网页和桌面共用的接口层；权限、审批、审计都在这层 |
| 前端 Vite | `frontend/` | 网页版界面；登录页就在这 |
| 桌面壳 Electron | `desktop/` | 开一个窗口，把前端装进去 |
| AgentScope worker | `worker/` | 独立队列消费者：领 `investigation` / `exec`，跑排查与命令执行 |

网页和桌面是同一个系统的两个窗口：同一套接口、同一套权限、同一本审计记录。桌面壳里没有第二套 UI，也没有绕过后台的通道。

## 怎么启动

### 方式一：Windows 本机脚本（推荐）

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File code\ops\dev-stack.ps1
```

脚本会确保 PostgreSQL、Redis 容器在线，并维持后端、前端和队列 Worker。按 `Ctrl+C` 只停守护，不删数据。

### 方式二：Compose 一键起

```bash
cd code
cp .env.example .env        # 全是假值，直接复制也能用
docker compose --profile infra up --build
```

- 后台：`http://127.0.0.1:8000/api/health` 应返回 `{"status":"ok","service":"backend","environment":"test"}`
- 前端：`http://127.0.0.1:5173` 应看到登录页
- worker：`docker compose logs worker` 能看到「已启动…就绪」

桌面壳是图形程序，需要在本机另外起：

```bash
cd code/desktop
npm install
npm start
```

### 方式三：不用 Docker，四个终端分别起

```bash
# 终端 1 · 后台
cd code/backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 终端 2 · 队列 worker
cd code
./backend/.venv/Scripts/python.exe backend/scripts/run_queue_worker.py

# 终端 3 · 前端
cd code/frontend && npm install && npm run dev

# 终端 4 · 桌面壳
cd code/desktop && npm install && npm start
```

任务提交是异步的：`POST /api/tasks` 返回 `202`，worker 领取后执行。只想补跑一条用 `python backend/scripts/run_queue_worker.py --once`；需要单独消费部署/命令执行时用 `--kind exec`。

## 运维手册

本仓库只包含代码和公开说明。涉及审计维护、MinIO、桌面打包、执行许可与真实模型联调时，以部署环境中的资料为准。

## 监控

监控接口：

```bash
curl http://127.0.0.1:8000/api/metrics
curl -H "X-Metrics-Token: $METRICS_TOKEN" http://127.0.0.1:8000/api/metrics
```

指标只读计数，不含任务标题、命令或凭据。告警建议看 `ops_agent_up`、`ops_agent_queue{status="pending"}` 和长时间停留在 `executing` 的任务数。

