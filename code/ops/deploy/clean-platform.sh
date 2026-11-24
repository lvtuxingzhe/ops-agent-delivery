#!/usr/bin/env bash
# 清理旧 ops-agent 平台：容器、网络、数据卷、程序镜像、安装目录、中间件数据目录。
#
# 用法：
#   bash ops/deploy/clean-platform.sh              # 交互确认，要求输入 DELETE
#   bash ops/deploy/clean-platform.sh --yes        # 跳过确认
#   bash ops/deploy/clean-platform.sh --keep-data  # 保留 /data/ops-agent-data
#
# 不会删除：
#   /data/backups                备份目录
#   本脚本以外的其他项目目录

set -u

YES=0
KEEP_DATA=0

for arg in "$@"; do
  case "$arg" in
    --yes)
      YES=1
      ;;
    --keep-data)
      KEEP_DATA=1
      ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "未知参数：$arg" >&2
      exit 2
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 执行：sudo bash $0" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "找不到 docker 命令" >&2
  exit 1
fi

if [ "$YES" -ne 1 ]; then
  cat <<'WARN'
即将删除旧 ops-agent 平台：
  - 所有名字含 ops-agent 的容器
  - 所有名字含 ops-agent 的内部网络
  - 所有名字含 ops-agent 的数据卷
  - 所有 ops-agent-backend / worker / frontend 镜像
  - /data/ops-agent、/root/ops-agent 等已知安装目录
  - /data/ops-agent-data（中间件数据目录）

不会删除 /data/backups。
数据库、附件、截图、审计记录需要保留时，请先完成备份。
WARN
  printf '确认请输入 DELETE：'
  read -r answer
  if [ "$answer" != "DELETE" ]; then
    echo "已取消。"
    exit 1
  fi
fi

cd / || exit 1
TS=$(date +%Y%m%dT%H%M%S)
BACKUP_DIR="/data/backups/pre-clean-${TS}"
mkdir -p "$BACKUP_DIR"

echo "[clean] 备份设置文件到 $BACKUP_DIR"
for root in /data/ops-agent /root/ops-agent /opt/ops-agent; do
  if [ -f "$root/.env.production" ]; then
    cp -a "$root/.env.production" "$BACKUP_DIR/$(echo "$root" | tr '/' '_').env.production" 2>/dev/null || true
  fi
done

echo "[clean] 删除旧容器"
docker ps -aq --filter 'name=ops-agent' | xargs -r docker rm -f || true

echo "[clean] 删除旧内部网络"
docker network ls --format '{{.Name}}' | grep 'ops-agent' | xargs -r docker network rm || true

if [ "$KEEP_DATA" -eq 1 ]; then
  echo "[clean] 按要求保留旧数据卷，不执行删除"
else
  echo "[clean] 删除旧数据卷"
  docker volume ls --format '{{.Name}}' | grep 'ops-agent' | xargs -r docker volume rm || true
fi

echo "[clean] 删除旧程序镜像"
docker image ls --format '{{.Repository}}:{{.Tag}}' | grep '^ops-agent' | xargs -r docker rmi -f || true
docker image prune -f >/dev/null 2>&1 || true

echo "[clean] 删除旧安装目录"
for dir in \
  /data/ops-agent \
  /root/ops-agent \
  /root/ops-agent-test \
  /root/ops-agent-finaltest \
  /root/ops-agent-build-src \
  /root/ops-agent-deploy-bundle-test \
  /root/ops-agent-package-test \
  /root/ops-agent-pkg \
  /opt/ops-agent; do
  if [ -e "$dir" ]; then
    echo "  删除 $dir"
    rm -rf "$dir"
  fi
done

if [ "$KEEP_DATA" -eq 1 ]; then
  echo "[clean] 按要求保留 /data/ops-agent-data"
else
  echo "[clean] 删除中间件数据目录 /data/ops-agent-data"
  rm -rf /data/ops-agent-data
fi

echo "[clean] 删除旧上传包"
find /data/upload -maxdepth 1 -type f -name 'ops-agent-*' -delete 2>/dev/null || true

echo "[clean] 完成"
echo "  设置文件备份：$BACKUP_DIR"
echo "  保留目录：/data/backups"
if [ "$KEEP_DATA" -eq 1 ]; then
  echo "  保留数据：/data/ops-agent-data"
fi
