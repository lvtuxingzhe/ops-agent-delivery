#!/usr/bin/env bash
# 生产部署唯一入口。
#
# 流程：校验 .env.production → 拉取依赖镜像 → 启动编排（migrate 一次性初始化）→
# 等健康 → 打印入口。失败时非零退出，并打印 migrate/backend 最近日志便于定位。
#
# 生产编排固定真实执行：APP_ENV=production 写在 docker-compose.prod.yml，
# 不再要求操作人手工填 APP_MODE 或 REAL_MODE_ACK。

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"
ENV_FILE="$ROOT/.env.production"
DOCKER_BIN="${DOCKER_BIN:-docker}"

die() {
  echo "[deploy] $*" >&2
  exit 1
}

# 逐行解析 KEY=VALUE，不做 source。
# 原因：.env.production 里的值可能带空格（如 APP_NAME），source 会把空格后的
# 单词当命令执行；这里只赋值、不执行，也兼容 Windows 上传留下的 CRLF。
load_env_file() {
  local raw key value
  while IFS= read -r raw || [[ -n "$raw" ]]; do
    raw="${raw%$'\r'}"
    [[ "$raw" =~ ^[[:space:]]*$ ]] && continue
    [[ "$raw" =~ ^[[:space:]]*# ]] && continue
    [[ "$raw" == *=* ]] || continue

    key="${raw%%=*}"
    value="${raw#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue

    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if (( ${#value} >= 2 )); then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi

    export "$key=$value"
  done < "$ENV_FILE"
}

[[ -f "$COMPOSE_FILE" ]] || die "缺少 $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || die "缺少 $ENV_FILE：请从 .env.production.example 复制并填写"

load_env_file

required=(
  POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB
  DB_APP_ROLE DB_APP_PASSWORD
  DB_AUDIT_READER_ROLE DB_AUDIT_READER_PASSWORD
  DB_AUTH_ROLE DB_AUTH_PASSWORD
  DB_USER_ADMIN_ROLE DB_USER_ADMIN_PASSWORD
  AUTH_TOKEN_SECRET TOTP_ENCRYPTION_KEY CREDENTIAL_ENCRYPTION_KEY EXEC_PERMIT_SECRET
  ADMIN_USERNAME ADMIN_PASSWORD
  MINIO_ROOT_USER MINIO_ROOT_PASSWORD MINIO_USER MINIO_PASSWORD
  MINIO_BUCKET MINIO_SCREENSHOT_BUCKET MINIO_AUDIT_BUCKET
  ALERT_WEBHOOK_TOKEN
)
for name in "${required[@]}"; do
  value="${!name:-}"
  [[ -n "$value" ]] || die "生产变量 $name 为空"
  case "$value" in
    CHANGE_ME*|REPLACE_ME*)
      die "生产变量 $name 仍是模板占位值，请先替换"
      ;;
  esac
done

[[ "${SMS_CHANNEL:-}" != "test" ]] \
  || die "production 环境禁止 SMS_CHANNEL=test；只用密码登录时请设 disabled"
(( ${#AUTH_TOKEN_SECRET} >= 40 )) || die "AUTH_TOKEN_SECRET 至少 40 个字符"
(( ${#EXEC_PERMIT_SECRET} >= 40 )) || die "EXEC_PERMIT_SECRET 至少 40 个字符"

model_dir="$ROOT/var/models/bge-small-zh-v1.5"
if [[ ! -f "$model_dir/model.onnx" ]]; then
  echo "[deploy] 提示：未找到 ${model_dir}，语义检索会回退为关键词检索；需要语义检索时解压 embedding 模型包到该目录" >&2
fi

# known_hosts 必须先是普通文件再启动：bind mount 源路径不存在时 Docker 会
# 建成目录，backend/worker 读目录会直接失败。文件只存公钥，不含私钥，
# 放开写权限是为了让容器内的非 root 用户能原子替换。
known_hosts="$ROOT/var/ssh/known_hosts"
mkdir -p "$ROOT/var/ssh"
[[ -L "$known_hosts" ]] && die "known_hosts 不能是符号链接：$known_hosts"
if [[ -e "$known_hosts" && ! -f "$known_hosts" ]]; then
  die "known_hosts 必须是普通文件：$known_hosts"
fi
[[ -f "$known_hosts" ]] || : > "$known_hosts"
chmod 0777 "$ROOT/var/ssh"
chmod 0666 "$known_hosts"

# 审计维护状态文件由容器内非 root 用户写入，宿主机目录需要可写。
audit_status_dir="$ROOT/var/audit-archive"
mkdir -p "$audit_status_dir"
chmod 0777 "$audit_status_dir"

"$DOCKER_BIN" compose version >/dev/null 2>&1 \
  || die "找不到 docker compose 插件"
"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config >/dev/null \
  || die "docker compose 配置校验失败"

image_tag="${OPS_IMAGE_TAG:-prod}"
images=(
  "ops-agent-backend:${image_tag}"
  "ops-agent-worker:${image_tag}"
  "ops-agent-frontend:${image_tag}"
)
for image in "${images[@]}"; do
  "$DOCKER_BIN" image inspect "$image" >/dev/null 2>&1 \
    || die "缺少镜像 $image；请先 docker load -i dist/ops-agent-docker-*.tar，或确认 OPS_IMAGE_TAG"
done

cd "$ROOT"

# 依赖组件镜像不随业务镜像 tar 交付；默认先从仓库拉取，保证一键部署不再
# 因目标机缺 PostgreSQL/Redis/MinIO 而卡住。完全离线时先 docker load，
# 再设 OPS_PULL_BASE_IMAGES=0 跳过。
if [[ "${OPS_PULL_BASE_IMAGES:-1}" == "1" ]]; then
  echo "[deploy] 拉取依赖镜像 PostgreSQL / Redis / MinIO ..."
  if ! "$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull postgres redis minio minio-init; then
    die "依赖镜像拉取失败；检查网络或 POSTGRES_IMAGE/REDIS_IMAGE/MINIO_IMAGE/MINIO_CLIENT_IMAGE；离线部署可先 docker load，再设 OPS_PULL_BASE_IMAGES=0"
  fi
else
  echo "[deploy] 已跳过依赖镜像拉取（OPS_PULL_BASE_IMAGES=0）"
fi

echo "[deploy] 启动 PostgreSQL / Redis / MinIO ..."
"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --wait postgres redis
"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d minio minio-init

echo "[deploy] 执行一次性迁移与初始化（不写自检任务）..."
if ! "$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm migrate; then
  "$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --tail=80 migrate >&2 || true
  die "初始化失败"
fi

echo "[deploy] 启动 backend / worker / frontend ..."
"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans backend worker frontend

edge_port="${EDGE_PORT:-8080}"
echo "[deploy] 等待入口健康检查 http://127.0.0.1:${edge_port}/api/health ..."
healthy=0
for _ in $(seq 1 90); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${edge_port}/api/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done

if [[ "$healthy" -ne 1 ]]; then
  "$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps >&2 || true
  "$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --tail=80 backend frontend >&2 || true
  die "入口未在 180 秒内变健康"
fi

if [[ "${OPS_INSTALL_AUDIT_CRON:-1}" == "1" ]]; then
  if [[ "$(id -u)" -eq 0 ]]; then
    echo "[deploy] 安装审计维护定时任务 ..."
    if ! bash "$ROOT/ops/deploy/install-audit-maintenance.sh"; then
      echo "[deploy] 提示：审计维护定时任务未安装；部署完成后可用 root 重跑 $ROOT/ops/deploy/install-audit-maintenance.sh" >&2
    fi
  else
    echo "[deploy] 提示：当前不是 root，跳过审计维护定时任务安装。" >&2
    echo "[deploy] 部署完成后执行：sudo bash $ROOT/ops/deploy/install-audit-maintenance.sh" >&2
  fi
fi

echo "[deploy] 完成：http://<宿主机IP>:${edge_port}/"
echo "[deploy] 首次登录：${ADMIN_USERNAME}；登录后立即改密码并绑定 TOTP。"
