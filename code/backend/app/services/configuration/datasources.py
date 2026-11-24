"""数据源配置的校验、读写、查询与健康检查。"""

from __future__ import annotations

import base64
import re
import urllib.parse
import uuid
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from ...core.audit import AuditEvent, insert_audit_event
from ...core.config import Settings
from ...core.credentials import CREDENTIAL_REF_RE as _CREDENTIAL_REF_RE
from ...core.credentials import (
    CredentialBackendNotReadyError,
    OpenBaoRequestError,
    clear_cache,
    env_var_name,
    resolve,
    write_db_secret,
    write_openbao_secret,
)
from ...core.credentials import load_config as load_credential_config
from ...core.db import connect_app
from .. import datasource_adapters
from .core import (
    _DS_NAMESPACE,
    _DS_SELECT,
    ADAPTERS,
    ALL_KINDS,
    NETWORK_KINDS,
    ConfigError,
)


def _validate_endpoint(kind: str, endpoint: str) -> str:
    """
    地址校验（只看格式，不连接 —— 当前版本不连任何外部系统）。

    网络类数据源必须是 http/https URL；obsidian 必须是目录路径（不允许
    塞个 URL 进来糊弄）。这里不判断「地址通不通」——那是健康检查的事。
    """
    endpoint = (endpoint or "").strip()
    if not endpoint:
        raise ConfigError("地址不能为空（每个数据源都要写清楚它在哪）")
    if kind in NETWORK_KINDS:
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ConfigError("网络类数据源的地址必须是 http(s)://开头的完整地址")
    elif kind == "obsidian":
        if "://" in endpoint:
            raise ConfigError("Obsidian 数据源填的是本机目录路径，不是网址")
        if not re.match(r"^[A-Za-z]:[\\/]", endpoint) and not endpoint.startswith("/"):
            raise ConfigError("目录路径要写全（如 D:/notes/obsidian 或 /data/obsidian）")
    return endpoint


def _validate_common(
    *, name: str, kind: str, adapter: str, endpoint: str, credential_ref: str,
) -> dict:
    """建/改数据源共用的字段校验。返回清洗后的字段值。"""
    name = (name or "").strip()
    if not name or len(name) > 64:
        raise ConfigError("名称必填且不超过 64 字")
    if kind not in ALL_KINDS:
        raise ConfigError(
            "类型只能是：" + "、".join(sorted(ALL_KINDS))
        )
    if adapter not in ADAPTERS:
        raise ConfigError("接法只能是 self（自研适配器）或 mcp（插件层）")
    endpoint = _validate_endpoint(kind, endpoint)
    credential_ref = (credential_ref or "").strip()
    if not _CREDENTIAL_REF_RE.match(credential_ref):
        raise ConfigError("凭据引用只能是字母数字下划线短横线（≤64），它只是个名字，不是密码")
    return {
        "name": name, "kind": kind, "adapter": adapter,
        "endpoint": endpoint, "credential_ref": credential_ref,
    }


def _ds_to_dict(key: str, value: Any, credential_ref: str, enabled: bool,
                updated_at: Any) -> dict:
    """
    一行 v3_configs → 页面要的形状。

    `id` 保留成 key 的别名（值就是 key 本身），前端既有的 `item.id` 拼接不用改；
    新代码使用 `key`。两者值相同，不存在「两套身份」。
    """
    stored = value if isinstance(value, dict) else {}
    return {
        "id": key,
        "key": key,
        "name": key,
        "kind": stored.get("kind", ""),
        "adapter": stored.get("adapter", "self"),
        "endpoint": stored.get("endpoint", ""),
        "credential_ref": credential_ref or "",
        "readonly": bool(stored.get("readonly", True)),
        "default_query": stored.get("default_query", ""),
        "index_pattern": stored.get("index_pattern", ""),
        "enabled": bool(enabled),
        "health_status": stored.get("health_status", "unknown"),
        "health_detail": stored.get("health_detail", ""),
        "health_checked_at": stored.get("health_checked_at"),
        "created_at": stored.get("created_at") or (
            updated_at.isoformat() if updated_at else None
        ),
    }


def _select_ds(conn: Any, key: str) -> tuple | None:
    """按 key 取一行数据源配置（取不到返回 None）。"""
    return conn.execute(
        f"{_DS_SELECT} WHERE namespace = %s AND key = %s",
        (_DS_NAMESPACE, key),
    ).fetchone()


def list_datasources(settings: Settings) -> list[dict]:
    """数据源清单（配置页表格用）。按名称排序，顺序稳定、翻页不会跳。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            f"{_DS_SELECT} WHERE namespace = %s ORDER BY key",
            (_DS_NAMESPACE,),
        ).fetchall()
    return [_ds_to_dict(*row) for row in rows]


def get_datasource(settings: Settings, key: str) -> dict | None:
    """按 key 取一条数据源；不存在返回 None。"""
    with connect_app(settings) as conn:
        row = _select_ds(conn, key)
    return _ds_to_dict(*row) if row else None


def _credential_header(value: str) -> str:
    """把 `user:pass` 转 Basic，其余按 Bearer；只用于本次任务内存。"""
    if ":" in value:
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"
    return f"Bearer {value}"


def reveal_datasource_credential(
    settings: Settings, key: str, *, actor_id: int, client_source: str = "api",
) -> dict:
    """管理员查看数据源完整凭据；Basic 形状拆成用户名和密码分别返回。"""
    ds = get_datasource(settings, key)
    if not ds:
        raise ConfigError("没有这条数据源（可能刚被别人改了名字）")
    ref = str(ds.get("credential_ref") or "")
    if not ref:
        raise ConfigError("这条数据源没有配置凭据引用")
    resolved = resolve(ref, settings, config=load_credential_config(settings))
    if resolved.status != "ok" or resolved.value is None:
        raise ConfigError(f"凭据读取失败：{resolved.status_text()}")
    raw = resolved.value.reveal()
    username = ""
    password = raw
    auth_type = "bearer"
    if ":" in raw:
        username, _, password = raw.partition(":")
        auth_type = "basic"
    with connect_app(settings) as conn:
        with conn.transaction():
            insert_audit_event(conn, AuditEvent(
                trace_id=f"config-reveal-{uuid.uuid4()}",
                event_type="config.datasource.credential_revealed",
                actor=str(actor_id),
                client_source=client_source,
                payload={
                    "key": key, "kind": ds["kind"], "ref": ref,
                    "backend": resolved.backend, "auth_type": auth_type,
                },
            ))
    return {
        "key": key,
        "kind": ds["kind"],
        "credential_ref": ref,
        "backend": resolved.backend,
        "auth_type": auth_type,
        "username": username,
        "password": password,
        "combined": raw,
        "revealed_at": datetime.now(UTC).isoformat(),
    }


def save_datasource_credential(
    settings: Settings, key: str, *, auth_type: str, username: str,
    password: str, actor_id: int, client_source: str = "api",
) -> dict:
    """
    把数据源密码写进 OpenBao，改完立即生效，不重启服务。

    只支持 openbao 后端：env 后端写的是进程环境变量，Docker 下改完后需要
    重建容器，这正是要避免的动作，因此明确拒绝，提示切到 OpenBao。
    """
    ds = get_datasource(settings, key)
    if not ds:
        raise ConfigError("没有这条数据源（可能刚被别人改了名字）")
    ref = str(ds.get("credential_ref") or "")
    if not ref:
        raise ConfigError("先给数据源填凭据引用并保存，再写密码")
    auth_type = str(auth_type or "").strip().lower()
    password = str(password or "")
    username = str(username or "")
    if auth_type not in ("basic", "bearer"):
        raise ConfigError("凭据类型只能是 basic 或 bearer")
    if auth_type == "basic":
        if not username:
            raise ConfigError("Basic 凭据必须填用户名")
        if not password:
            raise ConfigError("密码不能为空")
        value = f"{username}:{password}"
    else:
        if not password:
            raise ConfigError("密码不能为空")
        value = password
    cred_config = load_credential_config(settings)
    if cred_config.backend not in ("openbao", "db"):
        raise ConfigError(
            "当前凭据后端不支持在线写入；只能选 openbao 或 db（数据库加密）",
        )
    try:
        if cred_config.backend == "db":
            written = write_db_secret(
                ref, value, settings, actor_id=str(actor_id),
            )
        else:
            written = write_openbao_secret(ref, value, cred_config)
    except CredentialBackendNotReadyError as exc:
        detail = str(exc)
        messages = {
            "openbao_write_token_missing": (
                "服务端没有配置 OpenBao 写令牌（OPENBAO_WRITE_TOKEN）"
            ),
            "openbao_write_token_file_unreadable": "OpenBao 写令牌文件读不到",
            "openbao_config_missing": "OpenBao 地址或挂载点还没配置",
            "openbao_addr_invalid": "OpenBao 地址格式不对",
            "credential_encryption_key_missing": (
                "服务端没有配置数据库凭据加密密钥（CREDENTIAL_ENCRYPTION_KEY）"
            ),
            "credential_encryption_key_invalid": "数据库凭据加密密钥格式不对",
            "credential_ref_invalid": "凭据引用名不合法",
            "credential_value_empty": "密码不能为空",
        }
        raise ConfigError(messages.get(detail, "凭据写入未就绪")) from exc
    except OpenBaoRequestError as exc:
        raise ConfigError(
            f"OpenBao 写入失败（{str(exc).replace('openbao_write_', '')}）",
        ) from exc
    clear_cache()
    with connect_app(settings) as conn:
        with conn.transaction():
            insert_audit_event(conn, AuditEvent(
                trace_id=f"config-cred-{uuid.uuid4()}",
                event_type="config.datasource.credential_written",
                actor=str(actor_id),
                client_source=client_source,
                payload={
                    "key": key, "kind": ds["kind"], "ref": ref,
                    "backend": cred_config.backend, "auth_type": auth_type,
                    "kv_version": written.get("kv_version", ""),
                    "key_version": written.get("key_version", 0),
                },
            ))
    return {
        "key": key, "credential_ref": ref, "backend": cred_config.backend,
        "auth_type": auth_type,
        "kv_version": written.get("kv_version", ""),
        "key_version": written.get("key_version", 0),
        "written_at": datetime.now(UTC).isoformat(),
    }


def datasource_specs(settings: Settings) -> list[dict]:
    """给 worker 的只读查询清单：只含启用的 Prometheus/ELK。

    凭据在后端解析成 Authorization 头，只放进本次任务的内存载荷，不写入数据库、不写日志；
    worker 也可按 `credential_env` 环境变量读取默认值。
    """
    items = list_datasources(settings)
    cred_config = load_credential_config(settings)
    specs: list[dict] = []
    for item in items:
        if not item.get("enabled") or item.get("kind") not in datasource_adapters.SUPPORTED:
            continue
        # “样例-”前缀是本地演练数据源；它们不能进 worker 的真实 HTTP 分支，
        # 否则 worker 会拿 127.0.0.1 的样例地址去发真实请求，样例就失效了。
        if item["key"].startswith("样例-"):
            continue
        ref = item.get("credential_ref") or ""
        env_name = env_var_name(ref, cred_config) if ref else ""
        auth_header = ""
        if ref:
            resolved = resolve(ref, settings, config=cred_config)
            if resolved.status == "ok" and resolved.value is not None:
                auth_header = _credential_header(resolved.value.reveal())
        specs.append({
            "name": item["key"],
            "kind": item["kind"],
            "endpoint": item["endpoint"],
            "default_query": item.get("default_query", ""),
            "index_pattern": item.get("index_pattern", ""),
            "credential_env": env_name,
            "worker_credential_env": (
                env_var_name(ref + "_worker", cred_config) if ref else ""
            ),
            "credential_header": auth_header,
        })
    return specs


def query_datasource(
    settings: Settings, key: str, *, query_text: str, index: str = "",
    limit: int = 20, actor_id: int, client_source: str = "api",
) -> dict:
    """管理员用一条只读查询验证数据源；返回统一证据形状并留审计。"""
    ds = get_datasource(settings, key)
    if not ds:
        raise ConfigError("没有这条数据源（可能刚被别人改了名字）")
    if not ds["enabled"]:
        raise ConfigError("数据源已停用，先启用再查询")
    if ds["kind"] not in datasource_adapters.SUPPORTED:
        raise ConfigError("只有 Prometheus / ELK 支持真实只读查询")
    try:
        result = datasource_adapters.query(
            settings, ds["kind"], ds["endpoint"],
            query_text=query_text, index=index, credential_ref=ds["credential_ref"],
            limit=limit,
        )
    except datasource_adapters.AdapterError as exc:
        raise ConfigError(str(exc)) from exc

    with connect_app(settings) as conn:
        with conn.transaction():
            insert_audit_event(conn, AuditEvent(
                trace_id=f"config-query-{uuid.uuid4()}",
                event_type="config.datasource.queried",
                actor=str(actor_id),
                client_source=client_source,
                payload={
                    "key": key, "kind": ds["kind"],
                    "count": len(result["items"]),
                    "data_status": result["data_status"],
                },
            ))
    return result


def create_datasource(
    settings: Settings, *, name: str, kind: str, adapter: str, endpoint: str,
    credential_ref: str, enabled: bool, actor_id: int,
    default_query: str = "", index_pattern: str = "",
    client_source: str = "api",
) -> dict:
    """
    建一个数据源。

    只读标记固定为 true，传入 false 会被拒绝：排查范围保持只读。
    该字段保留在表里，为后续部署链路需要时再放开；当前配置「可写」
    会在校验阶段被拒绝。
    """
    fields = _validate_common(
        name=name, kind=kind, adapter=adapter,
        endpoint=endpoint, credential_ref=credential_ref,
    )
    # 新建时就把创建时间写进 value：v3_configs 只有 updated_at，
    # 没有单独的 created_at 列，页面上「先建的在上」需要它。
    stored = {
        "kind": fields["kind"], "adapter": fields["adapter"],
        "endpoint": fields["endpoint"], "readonly": True,
        "default_query": (default_query or "").strip(),
        "index_pattern": (index_pattern or "").strip(),
        "health_status": "unknown", "health_detail": "",
        "health_checked_at": None,
        "created_at": datetime.now(UTC).isoformat(),
    }
    with connect_app(settings) as conn:
        # 同名校验放在事务外第一步：名字冲突直接报给页面，不用进事务
        exists = _select_ds(conn, fields["name"])
        if exists:
            raise ConfigError(f"已有同名数据源：{fields['name']}")
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO v3_configs
                    (namespace, key, value, credential_ref, enabled, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (_DS_NAMESPACE, fields["name"], Jsonb(stored),
                 fields["credential_ref"], enabled, actor_id),
            )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"config-{uuid.uuid4()}",
                    event_type="config.datasource.created",
                    actor=str(actor_id),
                    client_source=client_source,
                    # payload 只放配置本身；credential_ref 是引用名不是秘密
                    payload={**fields, "enabled": enabled},
                ),
            )
            row = _select_ds(conn, fields["name"])
    return _ds_to_dict(*row)


def update_datasource(
    settings: Settings, *, ds_key: str, name: str, kind: str, adapter: str,
    endpoint: str, credential_ref: str, actor_id: int,
    default_query: str = "", index_pattern: str = "",
    client_source: str = "api",
) -> dict:
    """
    改一个数据源（改地址、换凭据引用、改名字都走这里）。

    改名在 v3_configs 里等于换身份（key 就是名字）：同一事务里插新行、删旧行，
    审计里记 old_key → new_key。旧表那种「UPDATE name、id 不变」的做法在这里不成立。

    改地址后健康检查结果自动归零回 unknown —— 老地址的检查结果对新地址
    没有意义，页面会提示「重新做一次健康检查」。
    """
    fields = _validate_common(
        name=name, kind=kind, adapter=adapter,
        endpoint=endpoint, credential_ref=credential_ref,
    )
    with connect_app(settings) as conn:
        with conn.transaction():
            row = _select_ds(conn, ds_key)
            if not row:
                raise ConfigError("没有这条数据源（可能刚被别人改了名字）")
            old = _ds_to_dict(*row)
            stored = {
                "kind": fields["kind"], "adapter": fields["adapter"],
                "endpoint": fields["endpoint"],
                "default_query": (default_query or "").strip(),
                "index_pattern": (index_pattern or "").strip(),
                "readonly": old["readonly"],
                "health_status": "unknown", "health_detail": "",
                "health_checked_at": None,
                "created_at": old["created_at"],
            }
            new_key = fields["name"]
            if new_key != ds_key:
                if _select_ds(conn, new_key):
                    raise ConfigError(f"已有同名数据源：{new_key}")
                conn.execute(
                    """
                    INSERT INTO v3_configs
                        (namespace, key, value, credential_ref, enabled, updated_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (_DS_NAMESPACE, new_key, Jsonb(stored),
                     fields["credential_ref"], old["enabled"], actor_id),
                )
                conn.execute(
                    "DELETE FROM v3_configs WHERE namespace = %s AND key = %s",
                    (_DS_NAMESPACE, ds_key),
                )
            else:
                conn.execute(
                    """
                    UPDATE v3_configs
                       SET value = %s, credential_ref = %s,
                           updated_by = %s, updated_at = now()
                     WHERE namespace = %s AND key = %s
                    """,
                    (Jsonb(stored), fields["credential_ref"], actor_id,
                     _DS_NAMESPACE, ds_key),
                )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"config-{uuid.uuid4()}",
                    event_type="config.datasource.updated",
                    actor=str(actor_id),
                    client_source=client_source,
                    payload={"old_key": ds_key, "new_key": new_key, **fields},
                ),
            )
            row = _select_ds(conn, new_key)
    return _ds_to_dict(*row)


def set_datasource_enabled(
    settings: Settings, ds_key: str, enabled: bool, actor_id: int,
    client_source: str = "api",
) -> dict:
    """启 / 停一个数据源。停掉的数据源后续排查链路不会再拿它查证据。"""
    with connect_app(settings) as conn:
        with conn.transaction():
            updated = conn.execute(
                """
                UPDATE v3_configs SET enabled = %s, updated_at = now()
                 WHERE namespace = %s AND key = %s
                RETURNING key
                """,
                (enabled, _DS_NAMESPACE, ds_key),
            ).fetchone()
            if not updated:
                raise ConfigError("没有这条数据源（可能刚被别人改了名字）")
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"config-{uuid.uuid4()}",
                    event_type="config.datasource.enabled",
                    actor=str(actor_id),
                    client_source=client_source,
                    payload={"key": ds_key, "enabled": enabled},
                ),
            )
            row = _select_ds(conn, ds_key)
    return _ds_to_dict(*row)


def delete_datasource(
    settings: Settings, ds_key: str, actor_id: int,
    client_source: str = "api",
) -> dict:
    """
    删除一个数据源配置（物理删除配置行）。

    只删配置，不删 credential_ref 指向的密钥：同一个密钥引用可能被多个
    数据源共用，删密钥会误伤其他配置。历史任务里的证据和报告都在任务
    JSONB 里，不会跟着这条配置消失。
    """
    with connect_app(settings) as conn:
        with conn.transaction():
            row = _select_ds(conn, ds_key)
            if not row:
                raise ConfigError("没有这条数据源（可能刚被别人删了）")
            item = _ds_to_dict(*row)
            conn.execute(
                "DELETE FROM v3_configs WHERE namespace = %s AND key = %s",
                (_DS_NAMESPACE, ds_key),
            )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"config-{uuid.uuid4()}",
                    event_type="config.datasource.deleted",
                    actor=str(actor_id),
                    client_source=client_source,
                    payload={
                        "key": ds_key,
                        "kind": item["kind"],
                        "endpoint": item["endpoint"],
                        "credential_ref": item["credential_ref"],
                        "enabled": item["enabled"],
                    },
                ),
            )
    return item


def health_check_datasource(
    settings: Settings, ds_key: str, actor_id: int,
    client_source: str = "api",
) -> dict:
    """
    给一个数据源做「健康检查」。

    Prometheus / ELK 走真实探测：
      * Prometheus 探 `/-/healthy`；
      * ELK 探 `/_cluster/health`，green/yellow 算可达，red 算失败；
      * 网络异常、超时、非 200 如实登记 failed，不显示成「正常」。
    其他类型仍只做本地格式校验，地址合法 → 诚实登记 no_data（「没去连」就是
    「没去连」）；地址不合法 → failed，附一句人话。

    写入用 jsonb_set 只改三个键，不整块覆盖 value：两个人同时点检查时，
    各改各的键，不会互相把对方的写入抹掉。
    """
    with connect_app(settings) as conn:
        row = _select_ds(conn, ds_key)
        if not row:
            raise ConfigError("没有这条数据源（可能刚被别人改了名字）")
        ds = _ds_to_dict(*row)

    try:
        # 先做同一套格式校验；格式不对不发出网请求。
        _validate_endpoint(ds["kind"], ds["endpoint"])
        if ds["kind"] in ("prometheus", "elk"):
            status, detail = datasource_adapters.probe(
                settings, ds["kind"], ds["endpoint"], ds["credential_ref"],
            )
        else:
            status, detail = "no_data", "该类型在当前版本不连外部系统，健康检查只做本地校验，未实际探测"
    except ConfigError as exc:
        status, detail = "failed", str(exc)

    checked_at = datetime.now(UTC).isoformat()
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                UPDATE v3_configs
                   SET value = jsonb_set(
                           jsonb_set(
                               jsonb_set(value, '{health_status}', to_jsonb(%s::text), true),
                               '{health_detail}', to_jsonb(%s::text), true),
                           '{health_checked_at}', to_jsonb(%s::text), true),
                       updated_at = now()
                 WHERE namespace = %s AND key = %s
                """,
                (status, detail, checked_at, _DS_NAMESPACE, ds_key),
            )
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"config-{uuid.uuid4()}",
                    event_type="config.datasource.health_checked",
                    actor=str(actor_id),
                    client_source=client_source,
                    payload={"key": ds_key, "name": ds["name"], "result": status},
                ),
            )
            row = _select_ds(conn, ds_key)
    return _ds_to_dict(*row)
