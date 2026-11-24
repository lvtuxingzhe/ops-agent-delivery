#!/usr/bin/env bash
# 安装审计维护定时任务（镜像部署，Linux）。
#
# 由 deploy.sh 在部署成功后调用，也可单独执行：
#   sudo bash ops/deploy/install-audit-maintenance.sh
#
# 定时任务不跑在容器里：容器只保留一个主进程，宿主机 cron 每天调用一次
# docker compose run，执行镜像内的 backend/scripts/audit_maintenance.py。
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"
ENV_FILE="$ROOT/.env.production"
CRON_FILE="/etc/cron.d/ops-agent-audit"
LOG_DIR="$ROOT/var/logs"
STATUS_DIR="$ROOT/var/audit-archive"
DOCKER_BIN="${DOCKER_BIN:-$(command -v docker || true)}"

die() {
  echo "[audit-cron] $*" >&2
  exit 1
}

[[ "$(id -u)" -eq 0 ]] || die "需要 root 权限写入 /etc/cron.d；请用 sudo 执行"
[[ -d /etc/cron.d ]] || die "目标机没有 /etc/cron.d；请先安装 cron 服务"
[[ -f "$COMPOSE_FILE" ]] || die "缺少 $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || die "缺少 $ENV_FILE：请从 .env.production.example 复制并填写"
[[ -n "$DOCKER_BIN" ]] || die "找不到 docker 命令"

if [[ -n "${OPS_CRON_USER:-}" ]]; then
  cron_user="$OPS_CRON_USER"
elif id opsagent >/dev/null 2>&1; then
  cron_user="opsagent"
elif [[ -n "${SUDO_USER:-}" ]]; then
  cron_user="$SUDO_USER"
else
  cron_user="$(id -un)"
fi
id "$cron_user" >/dev/null 2>&1 || die "定时任务用户不存在：$cron_user"

mkdir -p "$LOG_DIR" "$STATUS_DIR"
touch "$LOG_DIR/audit-maintenance.log"
chown "$cron_user" "$LOG_DIR/audit-maintenance.log" 2>/dev/null || true
chmod 0777 "$STATUS_DIR"

if command -v runuser >/dev/null 2>&1; then
  if ! runuser -u "$cron_user" -- "$DOCKER_BIN" info >/dev/null 2>&1; then
    echo "[audit-cron] 警告：$cron_user 不能直接访问 Docker，定时任务会执行失败。" >&2
    echo "[audit-cron] 处理方式：把 $cron_user 加入 docker 组，重新登录后再执行本脚本。" >&2
  fi
  if ! runuser -u "$cron_user" -- test -r "$ENV_FILE"; then
    echo "[audit-cron] 警告：$cron_user 读不到 $ENV_FILE，docker compose 无法解析配置。" >&2
    echo "[audit-cron] 处理方式：调整 .env.production 的属主或权限，或设置 OPS_CRON_USER。" >&2
  fi
fi

cron_line="5 0 * * * ${cron_user} cd '${ROOT}' && ${DOCKER_BIN} compose --env-file .env.production -f docker-compose.prod.yml run --rm --no-deps -T backend python scripts/audit_maintenance.py >> '${LOG_DIR}/audit-maintenance.log' 2>&1"

cat > "$CRON_FILE" <<EOF
# 由 ops/deploy/install-audit-maintenance.sh 生成，重复执行会覆盖本文件。
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
${cron_line}
EOF
chmod 0644 "$CRON_FILE"

echo "[audit-cron] 已写入 $CRON_FILE"
echo "[audit-cron] 每天 00:05 以 $cron_user 执行；日志 $LOG_DIR/audit-maintenance.log"
