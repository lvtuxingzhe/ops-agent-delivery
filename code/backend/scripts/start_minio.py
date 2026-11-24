"""启动本地 MinIO（不把密码放到命令行），并创建普通桶 + 对象锁审计桶。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "backend"))

from app.core.config import load_dotenv, load_settings  # noqa: E402

CONTAINER = "ops-minio"
VOLUME = "ops-agent-minio-data"
IMAGE = "minio/minio:latest"


def docker(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          encoding="utf-8", check=check)


def container_exists() -> bool:
    return docker("inspect", CONTAINER).returncode == 0


def container_running() -> bool:
    result = docker("inspect", "-f", "{{.State.Running}}", CONTAINER)
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def start_container(settings) -> None:
    if container_exists():
        if container_running():
            print("container", CONTAINER, "already_running")
        else:
            docker("start", CONTAINER, check=True)
            print("container", CONTAINER, "started")
        return
    docker("volume", "create", VOLUME, check=True)
    env = {
        **os.environ,
        "MINIO_ROOT_USER": settings.minio_user,
        "MINIO_ROOT_PASSWORD": settings.minio_password,
    }
    result = subprocess.run(
        [
            "docker", "run", "-d", "--name", CONTAINER,
            "--restart", "unless-stopped",
            "-p", "127.0.0.1:9000:9000", "-p", "127.0.0.1:9001:9001",
            "-v", f"{VOLUME}:/data",
            "-e", "MINIO_ROOT_USER", "-e", "MINIO_ROOT_PASSWORD",
            IMAGE, "server", "/data", "--console-address", ":9001",
        ],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    if result.returncode != 0:
        raise RuntimeError("docker_run_failed:" + (result.stderr or "").strip()[:200])
    print("container", CONTAINER, "created")


def wait_health(timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:9000/minio/health/live", timeout=2
            ) as response:
                if response.status == 200:
                    print("health=ok")
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError("minio_health_timeout")


def ensure_buckets(settings) -> None:
    from minio import Minio
    from minio.objectlockconfig import COMPLIANCE, DAYS, ObjectLockConfig

    client = Minio(
        settings.minio_endpoint.replace("https://", "").replace("http://", ""),
        access_key=settings.minio_user,
        secret_key=settings.minio_password,
        secure=settings.minio_endpoint.startswith("https://"),
    )
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)
        print("bucket", settings.minio_bucket, "created")
    else:
        print("bucket", settings.minio_bucket, "exists")
    if not client.bucket_exists(settings.minio_audit_bucket):
        client.make_bucket(settings.minio_audit_bucket, object_lock=True)
        print("bucket", settings.minio_audit_bucket, "created_object_lock")
    else:
        print("bucket", settings.minio_audit_bucket, "exists")

    # 桶级默认保留期：COMPLIANCE 90 天。即使某次上传忘了传 retention，
    # 桶也会兜住；单个对象上传时仍会显式传 Retention，双重保险。
    lock = client.get_object_lock_config(settings.minio_audit_bucket)
    if lock.mode is None:
        client.set_object_lock_config(
            settings.minio_audit_bucket,
            ObjectLockConfig(COMPLIANCE, settings.audit_retention_days, DAYS),
        )
        lock = client.get_object_lock_config(settings.minio_audit_bucket)
    versioning = client.get_bucket_versioning(settings.minio_audit_bucket)
    print("audit_lock_mode", lock.mode)
    print("audit_lock_days", lock.duration)
    print("audit_versioning", versioning.status)


def main() -> int:
    load_dotenv()
    settings = load_settings()
    if not settings.minio_user or not settings.minio_password:
        raise SystemExit("minio credentials missing after SOPS decrypt")
    start_container(settings)
    wait_health()
    ensure_buckets(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
