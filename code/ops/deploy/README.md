# 生产部署手册

目标机安装目录统一为 `/data/ops-agent`，不放在 `/root`；`/data` 建议单独挂一块盘。153 已于 2026-11-24 按该路径重新安装，旧 `/root/ops-agent` 已删除。

## 交付物

| 文件 | 作用 |
| --- | --- |
| `.env.production.example` | 生产变量模板；复制成 `.env.production` 后逐项替换 |
| `docker-compose.prod.yml` | 生产编排：PostgreSQL / Redis / MinIO / 一次初始化 / 后台 / worker / 前端；只引用镜像，不 build |
| `docker-compose.build.yml` | 仅构建用的 override；把 `build:` 挂到生产编排上 |
| `backend/Dockerfile.prod` | 后台多阶段镜像；builder 装依赖，runtime 非 root |
| `frontend/Dockerfile.prod` | Vite 构建 + Nginx 静态托管，构建后删 sourcemap |
| `worker/Dockerfile` | 多阶段 worker；过滤 `rapidocr`，保留 `onnxruntime`/`numpy` |
| `ops/deploy/nginx-edge.conf` | 静态前端与 `/api` 反向代理 |
| `ops/deploy/build-images.sh` | 构建三套镜像；缺 `.env.production` 时临时用模板 |
| `ops/deploy/package-images.sh` | 构建并导出 `dist/ops-agent-docker-<tag>.tar.gz`，附 SHA256 |
| `ops/deploy/install-audit-maintenance.sh` | 安装审计维护定时任务；写入 `/etc/cron.d/ops-agent-audit` |
| `ops/deploy/deploy.sh` | 目标机唯一部署入口；只 load 镜像，不构建 |
| `ops/deploy/clean-platform.sh` | 一键清理旧平台：容器、网络、卷、镜像、安装目录和中间件数据 |

镜像内不包含数据库、对象存储和测试业务数据。首次部署由 `migrate`
一次性容器建表、建角色、建管理员、初始化模板；中间件数据目录为空时才是干净环境。

依赖组件使用固定版本号（生产环境不使用 `latest`）。升级时同步更新镜像和文档：

| 组件 | 固定版本 | 默认镜像 |
| --- | --- | --- |
| PostgreSQL + pgvector | PostgreSQL 16 + pgvector 0.8.0 | `docker.1ms.run/pgvector/pgvector:0.8.0-pg16` |
| Redis | 7.4.12 | `docker.1ms.run/library/redis:7.4.12-alpine` |
| MinIO | RELEASE.2025-03-12T18-04-18Z | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/minio/minio:RELEASE.2025-03-12T18-04-18Z` |
| MinIO 客户端 | RELEASE.2025-03-12T17-29-24Z | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/minio/mc:RELEASE.2025-03-12T17-29-24Z` |

## 构建机出包

构建机需要 Docker 和 Bash；目标机不需要源码。目标机默认要能访问镜像仓库，
以便 `deploy.sh` 自动拉取 PostgreSQL / Redis / MinIO；完全离线时先导入依赖镜像，
再设 `OPS_PULL_BASE_IMAGES=0`。

```bash
cd /path/to/code
OPS_IMAGE_TAG=2026.11.22 bash ops/deploy/package-images.sh
```

产出：

- `dist/ops-agent-docker-2026.11.22.tar.gz`
- `dist/ops-agent-docker-2026.11.22.tar.gz.sha256`
- `dist/ops-agent-deploy-2026.11.22.tar.gz`（compose + deploy.sh + clean-platform.sh + env 模板）
- `dist/ops-agent-deploy-2026.11.22.tar.gz.sha256`
- `dist/ops-agent-embedding-bge-small-zh-v1.5.tar.gz`（本地有模型时）
- `dist/ops-agent-embedding-bge-small-zh-v1.5.tar.gz.sha256`

默认 Python 基础镜像走 `docker.1ms.run`，`pip` 走阿里云；能直连 Docker Hub 时
可用 `--build-arg PYTHON_IMAGE=python:3.13-slim` 覆盖。镜像按 CPU 架构区分，
不按 CentOS 小版本区分；本交付只验证 `host-153`（CentOS 7 / x86_64）。

## 目标机部署

重装或换版本前，先清理旧平台。脚本会删除名字含 `ops-agent` 的容器、网络、数据卷、程序镜像、安装目录和 `/data/ops-agent-data`，保留 `/data/backups`：

```bash
bash ops/deploy/clean-platform.sh
# 跳过确认
bash ops/deploy/clean-platform.sh --yes
# 保留中间件数据
bash ops/deploy/clean-platform.sh --keep-data
```

```bash
mkdir -p /data/ops-agent
mkdir -p /data/ops-agent-data/postgres /data/ops-agent-data/redis /data/ops-agent-data/minio
chown 999:999 /data/ops-agent-data/postgres && chmod 700 /data/ops-agent-data/postgres
chown 999:1000 /data/ops-agent-data/redis && chmod 755 /data/ops-agent-data/redis
chown 0:0 /data/ops-agent-data/minio && chmod 755 /data/ops-agent-data/minio
cd /data/ops-agent
tar -xzf /path/to/ops-agent-deploy-2026.11.22.tar.gz
docker load -i /path/to/ops-agent-docker-2026.11.22.tar.gz
sha256sum -c /path/to/ops-agent-docker-2026.11.22.tar.gz.sha256

mkdir -p var/models
tar -xzf /path/to/ops-agent-embedding-bge-small-zh-v1.5.tar.gz -C var/models

cp .env.production.example .env.production
chmod 600 .env.production
vi .env.production          # 替换全部 CHANGE_ME / REPLACE_ME；OPS_IMAGE_TAG 保持 2026.11.22
bash ops/deploy/deploy.sh
```

`deploy.sh` 默认会先 `docker compose pull postgres redis minio minio-init`，
把依赖组件镜像拉齐后再启动。依赖镜像默认走 `docker.1ms.run`，可用
`POSTGRES_IMAGE`、`REDIS_IMAGE`、`MINIO_IMAGE`、`MINIO_CLIENT_IMAGE` 覆盖为其他
镜像站或官方镜像。目标机完全离线时，先 `docker load` 这四个基础镜像，
再执行 `OPS_PULL_BASE_IMAGES=0 bash ops/deploy/deploy.sh` 跳过拉取。

`sha256sum -c` 要在包所在目录执行，或把校验文件里的文件名替换成实际路径；
它只校验内容，不会因为文件换目录而误判。

`deploy.sh` 启动编排时不传 `--build`；启动前会检查
`ops-agent-backend/worker/frontend:<OPS_IMAGE_TAG>` 是否存在，缺失时提示先
`docker load`。没有 `var/models/bge-small-zh-v1.5` 时只提示，语义检索回退关键词。

`deploy.sh` 会先做变量与 `docker compose config` 校验，默认拉取依赖组件镜像，
再启动编排，等待 `/api/health` 通过；失败时非零退出并打印 `migrate` / `backend` 日志。

前置条件：

- 生产编排固定 `APP_ENV=production`，不再填 `APP_MODE` 或 `REAL_MODE_ACK`
- `SMS_CHANNEL` 使用真实通道时不使用 `test`；只用密码 + TOTP 时设为 `disabled`
- `AUTH_TOKEN_SECRET`、`EXEC_PERMIT_SECRET` 至少 40 个字符
- `ALERT_WEBHOOK_TOKEN` 需要设置；未设置时 Webhook 接口返回 503（失败关闭）

## 审计维护定时任务

镜像里的 `backend/scripts/audit_maintenance.py` 由宿主机 cron 每天调用一次，不放在容器内运行。

`deploy.sh` 在健康检查通过后，如果当前用户是 root，会执行：

```bash
bash ops/deploy/install-audit-maintenance.sh
```

脚本写入 `/etc/cron.d/ops-agent-audit`，每天 00:05 执行：

```cron
5 0 * * * <用户> cd '<部署根>' && <docker> compose --env-file .env.production -f docker-compose.prod.yml run --rm --no-deps -T backend python scripts/audit_maintenance.py >> '<部署根>/var/logs/audit-maintenance.log' 2>&1
```

部署用户不是 root 时，`deploy.sh` 会打印需要手动执行的命令：

```bash
sudo bash ops/deploy/install-audit-maintenance.sh
```

可用变量：

| 变量 | 默认 | 作用 |
|---|---|---|
| `OPS_INSTALL_AUDIT_CRON` | `1` | 设为 `0` 时 `deploy.sh` 不安装定时任务 |
| `OPS_CRON_USER` | 存在 `opsagent` 时用它，否则用 sudo 调用者/当前用户 | 指定 cron 以哪个用户执行 |

手动执行一次，确认配置、数据库、MinIO 和归档都正常：

```bash
sudo -u opsagent bash -lc 'cd /data/ops-agent && /usr/bin/docker compose --env-file .env.production -f docker-compose.prod.yml run --rm --no-deps -T backend python scripts/audit_maintenance.py'
```

退出码 0 表示成功。状态文件写在 `var/audit-archive/maintenance-status.json`，该目录已挂载到 backend 容器，容器重建后仍保留。

## 首次初始化做了什么

`migrate` 容器按顺序执行：

1. `init_db.py --seed-only`：全部迁移、业务角色与表权限；
2. `init_auth.py --seed-only`：认证角色/表、六种角色、管理员账号、平台开关；
3. `init_config.py --seed-only`：配置默认值；
4. `init_user_admin.py`：用户管理专用数据库账号；
5. `init_alert_system_user.py`：不可登录的 `system-alert` 告警 actor；
6. `init_deployment_templates.py`：内置部署模板，含新增的 systemd/Nginx/Redis/MySQL 模板。

`--seed-only` 不跑自检任务，也不写自检审计事件，避免镜像初始化时夹带测试数据。

## 告警接入

Alertmanager 配置示例（Bearer 令牌与 `.env.production` 的 `ALERT_WEBHOOK_TOKEN` 一致）：

```yaml
receivers:
  - name: ops-agent
    webhook_configs:
      - url: http://<ops-agent-host>:8080/api/alerts/alertmanager
        send_resolved: true
        http_config:
          authorization:
            type: Bearer
            credentials: <ALERT_WEBHOOK_TOKEN>
```

接口返回 `202` 表示已受理；`401` 令牌错误；`413` 请求体超过
`ALERT_WEBHOOK_MAX_BYTES`；`503` 未配置令牌。

## 备份与恢复

备份沿用现有 `ops/backup/`，不另造脚本套件。生产新库的建议：

```bash
docker exec ops-agent-postgres-1 pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
  | gzip > "opsdb-$(date +%F).sql.gz"
```

对象存储数据在 `/data/ops-agent-data/minio`；审计桶开启 COMPLIANCE 对象锁，
删除受保留策略约束。

## 重装与升级

按现行规则，换版本前先用 `clean-platform.sh` 清理旧平台。旧镜像不保留在本机，
回退依赖机器外保存的旧发布包和数据库备份；机器上不保留上一版镜像。

## SSH 主机公钥

`deploy.sh` 启动前会确保 `var/ssh/known_hosts` 是普通文件，并以
`./var/ssh/known_hosts:/srv/ssh/known_hosts` 挂给 backend（读写）和 worker（只读）。
文件只存公钥，不含私钥；空文件不等于信任任何主机。

- 默认策略 `prompt`：首次接入在「主机」页点「确认密钥」，平台只握手取公钥，
  管理员到目标机控制台核对后再登记。
- `strict`：只认已登记主机，未登记直接失败。
- `off`：仅测试环境；还要在 `.env.production` 设 `SSH_HOST_KEY_INSECURE_ACK=1`，
  否则 worker 返回 `ssh_host_key_policy_insecure_unack`。
- 已登记主机公钥发生变化时硬失败，不提供覆盖按钮；先排查重装/换机/冒充。

`var/ssh` 与 `known_hosts` 权限会在部署时放开到容器非 root 用户可原子替换；
该文件不包含秘密，但改动它等于改变信任锚，仍应按受控配置管理并纳入备份。

## 已知边界

- 当前只实现测试短信通道；生产用 `SMS_CHANNEL=disabled` 关闭手机验证码登录，
  只用账号密码 + TOTP。接真实短信服务商时在 `backend/app/core/sms.py` 注册实现。
- TLS 不在 Compose 内终止；由宿主机 Nginx / 负载均衡负责 443。
- 真实执行会实际跑 SSH 命令和部署模板；执行前确认目标主机、模板和回滚预检。
