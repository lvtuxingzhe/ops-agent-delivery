"""审计归档导出与清单校验。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .config import Settings
from .db import connect_app

NAMESPACE = "archive"
CONFIG_KEY = "default"


class ArchiveError(Exception):
    """归档失败。消息不含任何凭据或文件内容。"""


class ArchiveBackendNotReadyError(ArchiveError):
    """后端还没接入（比如 S3 对象锁要等部署期配置与审批）。"""


@dataclass(frozen=True)
class ArchiveConfig:
    backend: str = "local"          # local | s3
    local_dir: str = ""             # 空 = 用 code/runtime-data/audit-archive
    s3_endpoint: str = ""           # 上线时填，当前版本不使用
    s3_bucket: str = ""
    s3_credential_ref: str = ""     # 只存引用名，不是密钥
    retention_days: int = 365       # 对象锁保留期【待确认】
    object_lock: bool = False       # 上线时必须开 COMPLIANCE 模式


@dataclass(frozen=True)
class ArchiveResult:
    day: str
    file: str
    sha256: str
    count: int
    backend: str
    object_lock: bool
    manifest: str


DEFAULT_CONFIG = ArchiveConfig()


def load_config(settings: Settings) -> ArchiveConfig:
    """从 v3_configs 读归档配置；没配过就用本地默认值（不写死外部地址）。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT value FROM v3_configs WHERE namespace=%s AND key=%s",
            (NAMESPACE, CONFIG_KEY),
        ).fetchone()
    if not row:
        return DEFAULT_CONFIG
    value = row[0] if isinstance(row[0], dict) else {}
    return ArchiveConfig(
        backend=value.get("backend", DEFAULT_CONFIG.backend),
        local_dir=value.get("local_dir", ""),
        s3_endpoint=value.get("s3_endpoint", ""),
        s3_bucket=value.get("s3_bucket", ""),
        s3_credential_ref=value.get("s3_credential_ref", ""),
        retention_days=int(value.get("retention_days", DEFAULT_CONFIG.retention_days)),
        object_lock=bool(value.get("object_lock", False)),
    )


def default_local_dir() -> Path:
    """本地归档目录：code/runtime-data/audit-archive（已被 .gitignore 排除）。"""
    return Path(__file__).resolve().parents[2] / "runtime-data" / "audit-archive"


def _canonical_line(event: dict) -> str:
    """一行一条事件。键排序保证同样的内容永远得到同样的字节，便于重算校验。"""
    return json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)


def _sha256(path: Path) -> str:
    """按块读文件算 SHA-256，用于归档清单校验。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(path: Path) -> dict:
    """读归档清单 JSON，读不出来就报归档错误。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"归档清单读不出来：{path.name}") from exc


# ---------------------------------------------------------------------------
# 本地后端（用于走通链路；不是真 WORM）
# ---------------------------------------------------------------------------
def write_local(events: list[dict], config: ArchiveConfig, day: str | None = None) -> ArchiveResult:
    """
    导出一天的流水 + 写清单 + 把文件置为只读。

    「只追加」语义：目标文件若已存在且内容一致 → 直接返回（幂等）；
    内容不同 → 报错拒绝覆盖。绝不允许悄悄改写已归档的历史。
    """
    day = day or date.today().isoformat()
    directory = Path(config.local_dir) if config.local_dir else default_local_dir()
    directory.mkdir(parents=True, exist_ok=True)
    data_file = directory / f"{day}.jsonl"
    manifest_file = directory / f"{day}.manifest.json"

    body = "".join(_canonical_line(event) + "\n" for event in events)
    encoded = body.encode("utf-8")

    if data_file.exists():
        if data_file.read_bytes() == encoded:
            manifest = _read_manifest(manifest_file)
            return ArchiveResult(day, str(data_file), manifest["sha256"], len(events),
                                 "local", False, str(manifest_file))
        raise ArchiveError(f"当天的归档已存在且内容不同，拒绝覆盖：{day}")

    data_file.write_bytes(encoded)
    # 只读是防手滑的提示，不是安全边界；真 WORM 靠对象锁。
    os.chmod(data_file, 0o444)

    manifest = {
        "version": 1,
        "day": day,
        "file": data_file.name,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "count": len(events),
        "backend": "local",
        "object_lock": False,
        "retention_days": config.retention_days,
        "generated_at": datetime.now(UTC).isoformat(),
        "note": "本地后端，不是真 WORM；上线改用 S3 Object Lock 合规模式",
    }
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    return ArchiveResult(day, str(data_file), manifest["sha256"], len(events),
                         "local", False, str(manifest_file))


def verify_local(config: ArchiveConfig, day: str) -> tuple[bool, str]:
    """重算文件 SHA-256 与清单比对。任何一处被改都会返回 False。"""
    directory = Path(config.local_dir) if config.local_dir else default_local_dir()
    data_file = directory / f"{day}.jsonl"
    manifest_file = directory / f"{day}.manifest.json"
    if not data_file.is_file() or not manifest_file.is_file():
        return False, "归档文件或清单不存在"
    manifest = _read_manifest(manifest_file)
    actual = _sha256(data_file)
    if actual != manifest.get("sha256"):
        return False, "文件哈希与清单不一致（可能被改过）"
    lines = [line for line in data_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != manifest.get("count"):
        return False, "事件条数与清单不一致"
    return True, f"{manifest['count']} 条，SHA-256 一致"


# ---------------------------------------------------------------------------
# S3 / MinIO 对象锁后端（接口骨架，当前版本不实现网络调用）
# ---------------------------------------------------------------------------
def write_s3(events: list[dict], config: ArchiveConfig, day: str | None = None) -> ArchiveResult:
    """
    写入开启 Object Lock（COMPLIANCE）与保留期的对象存储桶。

    当前未配置对象存储，调用时抛出 ArchiveBackendNotReadyError。
    """
    raise ArchiveBackendNotReadyError(
        "S3 对象锁后端待部署期接入：需配置 endpoint/bucket/保留期并开启 COMPLIANCE 模式"
    )


def write_day(events: list[dict], settings: Settings, day: str | None = None) -> ArchiveResult:
    """统一入口：按配置选后端。"""
    config = load_config(settings)
    if config.backend == "local":
        return write_local(events, config, day)
    if config.backend == "s3":
        return write_s3(events, config, day)
    raise ArchiveError(f"未知的归档后端：{config.backend}")


def verify_day(day: str, settings: Settings) -> tuple[bool, str]:
    """统一校验入口。"""
    config = load_config(settings)
    if config.backend == "local":
        return verify_local(config, day)
    raise ArchiveBackendNotReadyError("S3 后端的校验随对象锁一起接入")
