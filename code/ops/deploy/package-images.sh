#!/usr/bin/env bash
# 构建并打包成本地 tar；目标机只需要 docker load，不访问镜像仓库。
#
# 用法：
#   bash ops/deploy/package-images.sh
#   OPS_IMAGE_TAG=2026.11.22 bash ops/deploy/package-images.sh
#
# 产出：
#   dist/ops-agent-docker-<tag>.tar.gz
#   dist/ops-agent-docker-<tag>.tar.gz.sha256

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST="$ROOT/dist"
TAG="${OPS_IMAGE_TAG:-2026.11.22}"
TAR="$DIST/ops-agent-docker-${TAG}.tar.gz"
DOCKER_BIN="${DOCKER_BIN:-docker}"

die() {
  echo "[package] $*" >&2
  exit 1
}

OPS_IMAGE_TAG="$TAG" bash "$ROOT/ops/deploy/build-images.sh"

mkdir -p "$DIST"
[[ ! -e "$TAR" ]] || rm -f "$TAR"

images=(
  "ops-agent-backend:${TAG}"
  "ops-agent-worker:${TAG}"
  "ops-agent-frontend:${TAG}"
)
for image in "${images[@]}"; do
  "$DOCKER_BIN" image inspect "$image" >/dev/null 2>&1 \
    || die "缺少镜像 $image，构建未完成"
done

echo "[package] 导出并压缩 ${TAR} ..."
"$DOCKER_BIN" save "${images[@]}" | gzip -6 > "$TAR"

echo "[package] 计算 SHA256 ..."
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$TAR" | awk -v n="$(basename "$TAR")" '{print $1"  "n}' > "$TAR.sha256"
else
  shasum -a 256 "$TAR" | awk -v n="$(basename "$TAR")" '{print $1"  "n}' > "$TAR.sha256"
fi

echo "[package] 完成："
ls -lh "$TAR" "$TAR.sha256"

MODEL_DIR="$ROOT/var/models/bge-small-zh-v1.5"
MODEL_TAR="$DIST/ops-agent-embedding-bge-small-zh-v1.5.tar.gz"
if [[ -d "$MODEL_DIR" ]]; then
  echo "[package] 打包本地 embedding 模型 ..."
  tar -C "$ROOT/var/models" -czf "$MODEL_TAR" bge-small-zh-v1.5
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$MODEL_TAR" | awk -v n="$(basename "$MODEL_TAR")" '{print $1"  "n}' > "$MODEL_TAR.sha256"
  else
    shasum -a 256 "$MODEL_TAR" | awk -v n="$(basename "$MODEL_TAR")" '{print $1"  "n}' > "$MODEL_TAR.sha256"
  fi
  ls -lh "$MODEL_TAR" "$MODEL_TAR.sha256"
else
  echo "[package] 提示：没有 $MODEL_DIR，跳过 embedding 模型包；语义检索将回退关键词" >&2
fi

echo "[package] 目标机导入：docker load -i $(basename "$TAR")"
echo "[package] 需要语义检索时：tar -xzf $(basename "$MODEL_TAR") -C <部署根>/var/models"

DEPLOY_TAR="$DIST/ops-agent-deploy-${TAG}.tar.gz"
echo "[package] 打包部署骨架（compose + deploy.sh + env 模板）..."
tar -C "$ROOT" -czf "$DEPLOY_TAR" \
  docker-compose.prod.yml .env.production.example \
  ops/deploy/deploy.sh ops/deploy/clean-platform.sh ops/deploy/README.md ops/deploy/nginx-edge.conf
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$DEPLOY_TAR" | awk -v n="$(basename "$DEPLOY_TAR")" '{print $1"  "n}' > "$DEPLOY_TAR.sha256"
else
  shasum -a 256 "$DEPLOY_TAR" | awk -v n="$(basename "$DEPLOY_TAR")" '{print $1"  "n}' > "$DEPLOY_TAR.sha256"
fi
ls -lh "$DEPLOY_TAR" "$DEPLOY_TAR.sha256"
echo "[package] 目标机解压：tar -xzf $(basename "$DEPLOY_TAR") -C <部署根>"
