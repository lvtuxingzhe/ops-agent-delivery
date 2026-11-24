"""
审计归档：把一天的审计记录导出成 JSONL，写进「开了对象锁」的桶。

为什么单独一个模块
------------------
设计定稿（说明书第 2 条）把防篡改交给存储层：
  * 审计记录表仍只追加、审计与状态同事务 —— 保证「改了没留痕」不会发生；
  * 不可篡改由 MinIO / S3 **Object Lock（COMPLIANCE）** 在保留期内保证；
  * 不自己写哈希链，所以这里只有「序列化 + 摘要 + 上传」。

三条硬约束
----------
1. **异步、失败不阻塞业务**：归档是旁路任务。桶挂了只返回 `archived: False`，
   绝不把异常抛回排查/部署主流程（设计原文：安全措施不能把主流程卡死）。
2. **只读审计记录用只读账号**：导出走 `audit_v3.read_events_for_day`，用的是
   `ops_audit_reader` 连接串，不是业务账号。
3. **桶必须单独且带锁**：审计桶和报告/证据桶分开，否则普通上传会被对象锁拦住。

对象布局
--------
    audit/<YYYY-MM-DD>/events.jsonl     # 一行一条事件，字段顺序固定
    audit/<YYYY-MM-DD>/manifest.json    # 条数、字节数、sha256、保留期、生成时间

校验只要重算 `events.jsonl` 的 sha256 对比清单 —— 不需要第二份数据库，也不需要验证器。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from ..core import audit_v3

SCHEMA = "ops-audit-archive/1"
ARCHIVE_PREFIX = "audit"
DEFAULT_RETENTION_DAYS = 90
ARCHIVE_DIR_ENV = "AUDIT_ARCHIVE_DIR"


def _midnight_utc(day: date) -> datetime:
    """把日期转成「当天 00:00 UTC」的带时区时间，供对象锁保留期使用。"""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


class ArchiveConfigError(RuntimeError):
    """归档配置不全或依赖缺失。属于部署问题，不是业务故障。"""


class Transport(Protocol):
    """上传/下载的最小接口。真实现是 MinIO；本地目录实现用于测试与自检。"""

    name: str

    def put(self, key: str, data: bytes, *, content_type: str,
            retain_until: date | None) -> None: ...

    def get(self, key: str) -> bytes: ...


class FileTransport:
    """
    写本地目录。用途有两个：
      * 没有对象存储的本地环境里，先把归档产物落盘；
      * 自检里给「上传 — 校验」全链路一个不依赖网络的实现。
    `retain_until` 落成旁挂的 `.lock.json`，用来表达「本来应该锁多久」。
    """

    name = "file"

    def __init__(self, root: str | Path):
        """root 是归档目录的根，所有键都相对它落盘。"""
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        """把对象键换成绝对路径，并挡住越界写入。"""
        # 只允许落在根目录内，挡住 `../` 这类键
        target = (self._root / key).resolve()
        if self._root not in target.parents and target != self._root:
            raise ArchiveConfigError(f"归档键越界：{key}")
        return target

    def put(self, key: str, data: bytes, *, content_type: str,
            retain_until: date | None) -> None:
        """写文件；给了保留期就落一份旁挂说明，表达「本该锁多久」。"""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if retain_until is not None:
            meta = {"content_type": content_type,
                    "retain_until": retain_until.isoformat()}
            Path(str(path) + ".lock.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def get(self, key: str) -> bytes:
        """读回文件字节，供校验使用。"""
        return self._path(key).read_bytes()


class MinioTransport:
    """
    真上传：`put_object` 带 `Retention(COMPLIANCE, date)`。
    SDK 缺失时不静默降级成本地文件 —— 那会让人以为「已经锁上了」，最危险。
    """

    name = "minio"

    def __init__(self, settings):
        """按配置建客户端。缺 SDK / 缺连接信息都当成部署错误抛出来。"""
        try:
            from minio import Minio
            from minio.commonconfig import COMPLIANCE
            from minio.retention import Retention
        except ImportError as exc:  # 与 gemini 那条一个处理方式：给安装提示
            raise ArchiveConfigError(
                "未安装 minio SDK，审计归档无法上传对象锁桶；"
                "请 pip install minio 后重试"
            ) from exc

        endpoint = settings.minio_endpoint
        bucket = settings.minio_audit_bucket
        if not endpoint or not bucket:
            raise ArchiveConfigError("缺少 MINIO_ENDPOINT 或 MINIO_AUDIT_BUCKET")
        if not settings.minio_user or not settings.minio_password:
            raise ArchiveConfigError("缺少 MINIO_USER / MINIO_PASSWORD")

        self._bucket = bucket
        self._retention = Retention
        self._mode = COMPLIANCE
        self._client = Minio(
            endpoint.replace("https://", "").replace("http://", ""),
            access_key=settings.minio_user,
            secret_key=settings.minio_password,
            # 明文 http 只允许出现在本机；生产给 https 地址
            secure=endpoint.startswith("https://"),
        )

    def put(self, key: str, data: bytes, *, content_type: str,
            retain_until: date | None) -> None:
        """上传对象，并把保留期交给存储层的对象锁（COMPLIANCE 模式）。"""
        retention = None
        if retain_until is not None:
            # SDK 需要带时区的 datetime；只传 date 会抛 ValueError。
            retention = self._retention(self._mode, _midnight_utc(retain_until))
        self._client.put_object(
            self._bucket, key, io.BytesIO(data), length=len(data),
            content_type=content_type, retention=retention,
        )

    def get(self, key: str) -> bytes:
        """读回对象并释放连接（SDK 的响应要显式 close + release）。"""
        response = self._client.get_object(self._bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def check(self) -> dict:
        """部署自检：桶是否存在、对象锁是否启用。只读，不写任何对象。"""
        result: dict = {
            "transport": self.name,
            "bucket": self._bucket,
            "bucket_exists": False,
            "object_lock": "unknown",
        }
        try:
            result["bucket_exists"] = bool(self._client.bucket_exists(self._bucket))
        except Exception as exc:  # noqa: BLE001 - 自检把原因写清楚即可
            result["error"] = f"{type(exc).__name__}: {exc}"
            return result
        if not result["bucket_exists"]:
            return result
        try:
            lock = self._client.get_object_lock_config(self._bucket)
            mode = getattr(lock, "mode", None)
            result["object_lock"] = "enabled" if mode else "disabled"
        except Exception as exc:  # noqa: BLE001 - 某些 S3 兼容实现不支持该查询
            result["object_lock"] = f"unknown: {type(exc).__name__}"
        return result


def default_transport(settings) -> Transport:
    """
    选归档落地方式，优先级从高到低：

    1. 显式配了 `AUDIT_ARCHIVE_DIR` → 写本地目录（本地/离线环境的明确选择）；
    2. 配了 `MINIO_ENDPOINT` → 真上传到对象锁桶（生产）；
    3. 都没配 → 默认 `code/var/audit-archive`。

    为什么第 1 条压过 MinIO：内网离线机常常既留着 MINIO_ENDPOINT（给产物桶用），
    又起不了 MinIO 容器。这时把归档指到本地目录是一个**显式决定**，
    不是悄悄降级 —— 配置里写了这一行，就知道归档落在哪。
    """
    root = os.environ.get(ARCHIVE_DIR_ENV, "").strip()
    if root:
        return FileTransport(root)
    if settings.minio_endpoint:
        return MinioTransport(settings)
    return FileTransport(str(Path(__file__).resolve().parents[3] / "var" / "audit-archive"))


def describe(settings) -> dict:
    """给命令行/自检用的一句话说明，不暴露密码。与 default_transport 的优先级一致。"""
    root = os.environ.get(ARCHIVE_DIR_ENV, "").strip()
    if root:
        return {"transport": "file", "bucket": "",
                "retention_days": settings.audit_retention_days, "dir": root}
    if settings.minio_endpoint:
        return {"transport": "minio", "bucket": settings.minio_audit_bucket,
                "retention_days": settings.audit_retention_days}
    return {"transport": "file", "bucket": "",
            "retention_days": settings.audit_retention_days,
            "dir": "(默认 code/var/audit-archive)"}


def object_keys(day: str) -> tuple[str, str]:
    """`YYYY-MM-DD` → (事件对象键, 清单对象键)。日期格式不合法就抛。"""
    try:
        normalized = date.fromisoformat(day).isoformat()
    except ValueError as exc:
        raise ValueError(f"日期必须是 YYYY-MM-DD：{day}") from exc
    base = f"{ARCHIVE_PREFIX}/{normalized}"
    return f"{base}/events.jsonl", f"{base}/manifest.json"


def build_records(events: list[dict]) -> list[dict]:
    """把审计记录行转成归档记录。字段白名单固定，避免以后加列就悄悄改归档格式。"""
    records = []
    for event in events:
        records.append({
            "id": event.get("id"),
            "trace_id": event.get("trace_id") or "",
            "event_type": event.get("event_type") or "",
            "actor": event.get("actor") or "",
            "client_source": event.get("client_source") or "",
            "payload": event.get("payload") or {},
            "created_at": event.get("created_at") or "",
        })
    return records


def render_jsonl(records: list[dict]) -> bytes:
    """一行一条、键排序、紧凑分隔符 —— 同样的输入永远得到同样的字节。"""
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    ]
    text = "\n".join(lines)
    if lines:
        text += "\n"
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """内容摘要。清单里只放它，校验时重算对比即可。"""
    return hashlib.sha256(data).hexdigest()


def build_manifest(day: str, records: list[dict], data: bytes, *,
                   retention_days: int, generated_at: str) -> dict:
    """生成清单：条数、字节数、摘要、首尾 id、保留期、生成时间。"""
    first_id = records[0].get("id") if records else None
    last_id = records[-1].get("id") if records else None
    return {
        "schema": SCHEMA,
        "day": day,
        "event_count": len(records),
        "bytes": len(data),
        "sha256": sha256_hex(data),
        "first_id": first_id,
        "last_id": last_id,
        "retention_days": retention_days,
        "generated_at": generated_at,
    }


def archive_day(
    settings,
    day: str,
    *,
    transport: Transport | None = None,
    reader: Callable[[object, str], list[dict]] | None = None,
    retention_days: int | None = None,
    now: date | None = None,
) -> dict:
    """
    归档一天。**永不抛异常**，结果统一用 dict 表达：

        {"archived": True, ...}                 成功
        {"archived": False, "reason": "..."}    没东西可归档 / 读取失败 / 上传失败

    调用方（定时任务或命令行）按 reason 决定告警级别，业务侧不看返回值。
    """
    try:
        events_key, manifest_key = object_keys(day)
        normalized_day = date.fromisoformat(day).isoformat()
    except ValueError:
        return {"archived": False, "reason": "bad_day", "day": day}

    # 读审计记录默认走只读账号；测试可注入假 reader（所以不覆盖同名参数）
    read_events = reader or audit_v3.read_events_for_day
    try:
        events = read_events(settings, normalized_day)
    except Exception as exc:  # noqa: BLE001 - 归档是旁路，不打断主流程
        return {"archived": False, "reason": f"read_failed:{type(exc).__name__}",
                "day": normalized_day}

    if not events:
        return {"archived": False, "reason": "no_events", "day": normalized_day}

    records = build_records(events)
    data = render_jsonl(records)
    retention = retention_days if retention_days is not None else settings.audit_retention_days
    if retention <= 0:
        retention = DEFAULT_RETENTION_DAYS
    generated = datetime.now(UTC)
    retain_until = (now or generated.date()) + timedelta(days=retention)

    manifest = build_manifest(
        normalized_day, records, data,
        retention_days=retention,
        generated_at=generated.isoformat(),
    )
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    try:
        transport = transport or default_transport(settings)
        transport.put(events_key, data, content_type="application/x-ndjson",
                      retain_until=retain_until)
        transport.put(manifest_key, manifest_bytes, content_type="application/json",
                      retain_until=retain_until)
    except ArchiveConfigError as exc:
        return {"archived": False, "reason": f"config_error:{exc}",
                "day": normalized_day}
    except Exception as exc:  # noqa: BLE001 - 上传失败只降级，不抛
        return {"archived": False, "reason": f"put_failed:{type(exc).__name__}",
                "day": normalized_day}

    return {
        "archived": True,
        "day": normalized_day,
        "event_count": len(records),
        "bytes": len(data),
        "sha256": manifest["sha256"],
        "events_key": events_key,
        "manifest_key": manifest_key,
        "retain_until": retain_until.isoformat(),
        "transport": transport.name,
    }


def verify_day(settings, day: str, *, transport: Transport | None = None) -> dict:
    """
    重算归档文件摘要并对比清单。返回：
        {"ok": True, "event_count": n, ...}
        {"ok": False, "reason": "manifest_missing" / "content_mismatch" / ...}
    """
    try:
        events_key, manifest_key = object_keys(day)
    except ValueError:
        return {"ok": False, "reason": "bad_day"}

    try:
        transport = transport or default_transport(settings)
        raw_manifest = transport.get(manifest_key)
        raw_events = transport.get(events_key)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"fetch_failed:{type(exc).__name__}"}

    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "reason": "manifest_not_json"}

    actual = sha256_hex(raw_events)
    if manifest.get("sha256") != actual:
        return {"ok": False, "reason": "content_mismatch",
                "manifest_sha256": manifest.get("sha256"), "actual_sha256": actual}
    if manifest.get("bytes") not in (None, len(raw_events)):
        return {"ok": False, "reason": "size_mismatch",
                "manifest_bytes": manifest.get("bytes"), "actual_bytes": len(raw_events)}
    if manifest.get("schema") != SCHEMA:
        return {"ok": False, "reason": "schema_mismatch",
                "manifest_schema": manifest.get("schema")}
    return {"ok": True, "day": manifest.get("day"), "event_count": manifest.get("event_count"),
            "sha256": actual, "retention_days": manifest.get("retention_days")}


def diagnose(settings) -> dict:
    """生产归档自检：配置硬约束 + SDK + 桶/对象锁。不会创建桶或写对象。"""
    warnings: list[str] = []
    errors: list[str] = []
    root = os.environ.get(ARCHIVE_DIR_ENV, "").strip()
    if root:
        warnings.append("当前使用本地目录归档，不具备对象锁；生产应改为 MinIO 审计桶")
    if settings.minio_audit_bucket == settings.minio_bucket:
        errors.append("审计桶不能与普通产物桶同名，否则对象锁会拦住普通上传")
    if settings.audit_retention_days < 30:
        warnings.append("审计保留期低于 30 天，请按合规要求调整 AUDIT_RETENTION_DAYS")
    if settings.minio_endpoint.startswith("http://") and not any(
        host in settings.minio_endpoint for host in ("127.0.0.1", "localhost")
    ):
        warnings.append("MinIO 地址是明文 http，生产应使用 https 或内网 TLS 终止")

    result: dict = {
        **describe(settings),
        "warnings": warnings,
        "errors": errors,
        "ok": not errors,
    }
    if root:
        return result
    try:
        transport = default_transport(settings)
    except ArchiveConfigError as exc:
        result["errors"].append(str(exc))
        result["ok"] = False
        return result
    if isinstance(transport, FileTransport):
        directory = transport._root
        result["dir"] = str(directory)
        result["writable"] = os.access(directory.parent, os.W_OK)
        return result
    check = transport.check()
    result.update(check)
    if not check.get("bucket_exists"):
        result["errors"].append("审计桶不存在或当前账号无权访问")
        result["ok"] = False
    elif check.get("object_lock") in ("disabled", "unknown"):
        warnings.append("审计桶未确认启用对象锁（COMPLIANCE）")
    return result


__all__ = [
    "ArchiveConfigError", "FileTransport", "MinioTransport", "SCHEMA",
    "archive_day", "build_manifest", "build_records", "default_transport",
    "describe", "diagnose", "object_keys", "render_jsonl", "sha256_hex",
    "verify_day",
]
