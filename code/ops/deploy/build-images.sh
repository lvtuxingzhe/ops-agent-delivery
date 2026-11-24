#!/usr/bin/env bash
# 构建三套生产镜像。构建机需要 Docker + Compose；目标机不需要源码。
#
# 用法：
#   bash ops/deploy/build-images.sh
#   OPS_IMAGE_TAG=2026.11.22 bash ops/deploy/build-images.sh
#
# 生产编排里没有 build；本脚本通过 docker-compose.build.yml 临时挂上 build。

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"
BUILD_FILE="$ROOT/docker-compose.build.yml"
ENV_FILE="${ENV_FILE:-$ROOT/.env.production}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
TAG="${OPS_IMAGE_TAG:-2026.11.22}"
CREATED_ENV=0

die() {
  echo "[build] $*" >&2
  exit 1
}

[[ -f "$COMPOSE_FILE" ]] || die "缺少 $COMPOSE_FILE"
[[ -f "$BUILD_FILE" ]] || die "缺少 $BUILD_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  # compose 的 env_file 固定指向 .env.production；只构建镜像时用模板占位。
  # 真实部署绝不会走到这里，deploy.sh 会要求真实 .env.production。
  cp "$ROOT/.env.production.example" "$ENV_FILE"
  CREATED_ENV=1
  echo "[build] 未找到 .env.production，临时使用 .env.production.example 占位"
fi
cleanup() {
  if [[ "$CREATED_ENV" -eq 1 ]]; then
    rm -f "$ENV_FILE"
  fi
}
trap cleanup EXIT

[[ -f "$ENV_FILE" ]] || die "缺少环境变量文件"

export OPS_IMAGE_TAG="$TAG"

"$DOCKER_BIN" compose version >/dev/null 2>&1 \
  || die "找不到 docker compose 插件"

cd "$ROOT"
echo "[build] 构建 backend / worker / frontend，标签 ${TAG} ..."
"$DOCKER_BIN" compose \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  -f "$BUILD_FILE" \
  build backend worker frontend

echo "[build] 完成："
for image in "ops-agent-backend:${TAG}" "ops-agent-worker:${TAG}" "ops-agent-frontend:${TAG}"; do
  "$DOCKER_BIN" image inspect "$image" --format '  {{.RepoTags}}  {{.Size}} bytes' 2>/dev/null || true
done
