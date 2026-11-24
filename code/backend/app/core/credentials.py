"""凭据解析、加密存储与轮换。"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .config import Settings, load_sops_values, read_env
from .contract import data_quality_text
from .db import connect_app
from .security import SecretBox

NAMESPACE = "credentials"
CONFIG_KEY = "default"

# 引用名只允许字母、数字、下划线和短横线。
CREDENTIAL_REF_RE = re.compile(r"^[A-Za-z0-9_-]{0,64}$")

# 四个后端字符串：配置允许值 == resolve 分派 == 自检断言，三处必须一致。
# db = 凭据密文存数据库；不引外部服务，改密码不重启。
BACKENDS = ("env", "openbao", "sops", "db")
# 就绪状态。env/db 靠进程内配置；openbao/sops 靠外部文件或 HTTP。
BACKEND_READINESS = {
    "env": "ready", "openbao": "ready", "sops": "ready", "db": "ready",
}

# 数据库加密凭据专用 key；与 TOTP key 分开，避免一把钥匙开两套锁。
CREDENTIAL_ENCRYPTION_KEY_ENV = "CREDENTIAL_ENCRYPTION_KEY"
CREDENTIAL_SECRET_TABLE = "v3_credential_secrets"

# OpenBao 引导项只从进程环境读，不进 v3_configs，避免令牌被配置接口回显。
OPENBAO_TOKEN_ENV = "OPENBAO_TOKEN"
OPENBAO_TOKEN_FILE_ENV = "OPENBAO_TOKEN_FILE"
OPENBAO_WRITE_TOKEN_ENV = "OPENBAO_WRITE_TOKEN"
OPENBAO_WRITE_TOKEN_FILE_ENV = "OPENBAO_WRITE_TOKEN_FILE"
OPENBAO_CACERT_ENV = "OPENBAO_CACERT"
OPENBAO_TIMEOUT_ENV = "OPENBAO_TIMEOUT_SECONDS"

# 状态枚举。刻意【不设 stale】：沿用旧值也是「无数据」的一种，靠
# degraded + value is None + detail 区分成因。少一个状态、少一层解释。
STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_INVALID = "invalid"
STATUS_UNKNOWN_BACKEND = "unknown_backend"


class CredentialError(Exception):
    """凭据层错误基类。所有 message 都不含任何凭据值，可直接进日志/报错。"""


class CredentialBackendNotReadyError(CredentialError):
    """后端还没接入（缺地址、缺令牌、缺部署配置）。"""


class OpenBaoRequestError(CredentialError):
    """OpenBao 请求失败。消息只含状态码，不含 URL 查询、令牌或任何值。"""


class Secret:
    """
    真实凭据值的包装类型 —— 值进得来、出不去（除非显式 reveal）。

    刻意不继承 str：继承了就会自动拥有可被 print / json / 拼接的能力，
    一个手滑就把值写进日志。这里用 __slots__ + 私有属性 + 不可变，
    repr/str/format 全部固定返回掩码，序列化直接抛错。
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        """把真实值装进私有属性，之后只准通过 reveal() 取。"""
        object.__setattr__(self, "_value", value)

    def reveal(self) -> str:
        """唯一出口。调用方拿到明文后自己负责，本层不再管。"""
        return object.__getattribute__(self, "_value")

    def __setattr__(self, *_args: Any) -> None:
        """禁止改值对象，防止缓存里的值被悄悄替换。"""
        raise AttributeError("Secret 不可变，别在缓存里偷偷改值")

    def __repr__(self) -> str:
        """打印时只显示掩码，绝不显示真实值。"""
        return "<Secret redacted>"

    __str__ = __repr__

    def __format__(self, _spec: str) -> str:
        """f-string 里也只显示掩码，防止值漏进日志。"""
        return "<Secret redacted>"

    def __reduce__(self) -> Any:
        """禁止序列化，防止值被 pickle 或 copy 带出去。"""
        raise TypeError("Secret 不允许被序列化（防 pickle / copy 把值带出去）")


@dataclass(frozen=True)
class CredentialConfig:
    """
    凭据层配置，整行存在 v3_configs 的 JSONB 里，代码不写死地址/路径/周期。

    backend              选哪个后端（env / openbao / sops）
    rotate_days          缓存按天轮换的跨度，默认 1 天
    on_rotation_failure  重取失败怎么办；use_stale 只对排查侧生效
    env_ref_map          引用名 → 环境变量名的显式映射（可选，加新引用名不改代码）
    openbao_*            OpenBao 连接参数；未配置时为空
    sops_file            SOPS 加密文件路径
    """

    backend: str = "env"
    rotate_days: int = 1
    on_rotation_failure: str = "use_stale"
    env_ref_map: dict[str, str] = field(default_factory=dict)
    openbao_addr: str = ""
    openbao_mount: str = ""
    openbao_path_prefix: str = ""
    sops_file: str = ""
    note: str = ""


@dataclass(frozen=True)
class ResolveResult:
    """一次解析的结果。value 为 None 就是没取到，绝不用空串冒充成功。"""

    ref: str
    status: str
    value: Secret | None = None
    backend: str = ""
    day: str = ""
    cache_hit: bool = False
    degraded: bool = False
    rotated: bool = False
    detail: str = ""

    @property
    def data_quality(self) -> str:
        """
        证据层的数据质量标签，使用契约模块的三值标准。

        解析层的 status 表示解析结果，证据层表示数据能否使用：
        只有 ok 才是 ok，其余一律 no_data。报告里永远显示「无数据」，不显示「正常」。
        """
        return "ok" if self.status == STATUS_OK else "no_data"

    def status_text(self) -> str:
        """页面上的人话。复用契约模块的无数据规则，绝不出现「正常」。"""
        if self.status == STATUS_OK:
            return data_quality_text("ok")
        return self.detail or data_quality_text(self.data_quality)

    def to_audit_payload(self) -> dict:
        """
        留痕用的字段白名单。刻意不含 value、不含任何可能装值的字段。
        表里以后就算加了列，也不会从这里漏到审计记录。
        """
        return {
            "ref": self.ref,
            "backend": self.backend,
            "status": self.status,
            "data_quality": self.data_quality,
            "day": self.day,
            "cache_hit": self.cache_hit,
            "degraded": self.degraded,
            "rotated": self.rotated,
        }


DEFAULT_CONFIG = CredentialConfig()

# 模块级缓存：(后端, 引用名) → (取到值的日期, 值)。
# 进程内内存态，没有后台线程、没有定时器；按天自然失效，重启即清空。
_CACHE: dict[tuple[str, str], tuple[str, Secret]] = {}


def _utc_day() -> str:
    """当前 UTC 日期（ISO 串）。模块级函数，自检可替换成假时钟测轮换。"""
    return datetime.now(UTC).date().isoformat()


def _day_gap(cached_day: str, today: str) -> int:
    """两个 ISO 日期差几天。解析不了就返回一个很大的数（当作早就过期）。"""
    try:
        return (date.fromisoformat(today) - date.fromisoformat(cached_day)).days
    except (ValueError, TypeError):
        return 10**6


def env_var_name(ref: str, config: CredentialConfig) -> str:
    """
    引用名 → 环境变量名。显式映射优先，否则 CRED_ + 大写 + 短横线转下划线。

    含短横线的引用名在 env 后端会被 resolve 直接拒掉：因为 a-b 和 a_b 归一化后
    会指向同一个环境变量，悄悄串到别人的凭据上，运维现场极难排查。
    宁可明确报「无数据」，也不做有歧义的映射。
    """
    if ref in config.env_ref_map:
        return config.env_ref_map[ref]
    return "CRED_" + ref.upper().replace("-", "_")


# ---------------------------------------------------------------------------
# 四个后端。FETCHERS 做成模块级注册表，自检可替换成假的取数函数并计数。
# db 后端不在 FETCHERS 里，因为解密需要 settings；resolve() 单独分派。
# ---------------------------------------------------------------------------
def _fetch_env(ref: str, config: CredentialConfig) -> str | None:
    """默认后端：按引用名读环境变量。读不到就返回 None（诚实说无假值）。"""
    return read_env(env_var_name(ref, config))


# 下面两个是真实后端。OpenBao 只走 HTTP API，不引 SDK、不建连接池；
# 令牌只从进程环境读，失败时只记异常类型，绝不把令牌/URL 查询/密文拼进结果。
def _openbao_token() -> str:
    """读取 OpenBao 令牌。令牌文件方式适合容器 secret mount。"""
    token = read_env(OPENBAO_TOKEN_ENV)
    if token:
        return token.strip()
    token_file = read_env(OPENBAO_TOKEN_FILE_ENV)
    if not token_file:
        raise CredentialBackendNotReadyError("openbao_token_missing")
    try:
        return Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CredentialBackendNotReadyError("openbao_token_file_unreadable") from exc


def _openbao_timeout() -> float:
    """默认 5 秒，配置非法就回默认值，不让坏配置把请求挂死。"""
    raw = read_env(OPENBAO_TIMEOUT_ENV)
    try:
        value = float(raw) if raw else 5.0
    except ValueError:
        return 5.0
    return min(max(value, 1.0), 30.0)


def _openbao_context(addr: str) -> ssl.SSLContext | None:
    """HTTPS 默认校验证书；可用 OPENBAO_CACERT 指定私有 CA。"""
    if not addr.startswith("https://"):
        return None
    cacert = read_env(OPENBAO_CACERT_ENV)
    try:
        return ssl.create_default_context(cafile=cacert) if cacert else ssl.create_default_context()
    except (OSError, ssl.SSLError) as exc:
        raise CredentialBackendNotReadyError("openbao_tls_config_invalid") from exc


def _openbao_request(url: str, token: str, context: ssl.SSLContext | None) -> dict | None:
    """
    发一次只读 GET。404 返回 None（当作“这个路径没数据”），其他错误抛异常。

    不打印 URL：路径前缀可能暴露业务命名；错误只带 HTTP 状态码。
    """
    request = urllib.request.Request(
        url,
        headers={"X-Vault-Token": token, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=_openbao_timeout(), context=context,
        ) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise OpenBaoRequestError(f"openbao_http_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OpenBaoRequestError("openbao_unreachable") from exc
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OpenBaoRequestError("openbao_response_invalid") from exc
    return payload if isinstance(payload, dict) else None


def _openbao_write_token() -> str:
    """写令牌单独一套；没配就明确报错，不拿只读令牌硬写。"""
    token = read_env(OPENBAO_WRITE_TOKEN_ENV)
    if token:
        return token.strip()
    token_file = read_env(OPENBAO_WRITE_TOKEN_FILE_ENV)
    if not token_file:
        raise CredentialBackendNotReadyError("openbao_write_token_missing")
    try:
        return Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CredentialBackendNotReadyError(
            "openbao_write_token_file_unreadable",
        ) from exc


def _openbao_put(url: str, token: str, payload: dict,
                 context: ssl.SSLContext | None) -> None:
    """写一次 KV。404 由调用方决定是否回退 KV v1，其它错误统一抛。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "X-Vault-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=_openbao_timeout(), context=context,
        ):
            return
    except urllib.error.HTTPError as exc:
        raise OpenBaoRequestError(f"openbao_write_http_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OpenBaoRequestError("openbao_write_unreachable") from exc


def write_openbao_secret(ref: str, value: str, config: CredentialConfig) -> dict:
    """
    把一条凭据写入 OpenBao 静态 KV。

    先按 KV v2 写 `/v1/<mount>/data/<path>`；404 再回退 KV v1。写成功后
    调用方必须清进程缓存，否则同一天还读旧值。返回结果只含路径与后端，
    不回传写入的值。
    """
    ref = str(ref or "").strip()
    if not CREDENTIAL_REF_RE.match(ref) or not ref:
        raise CredentialBackendNotReadyError("credential_ref_invalid")
    value = str(value or "")
    if not value:
        raise CredentialBackendNotReadyError("credential_value_empty")
    addr = (config.openbao_addr or "").strip().rstrip("/")
    mount = (config.openbao_mount or "").strip().strip("/")
    prefix = (config.openbao_path_prefix or "").strip().strip("/")
    if not addr or not mount:
        raise CredentialBackendNotReadyError("openbao_config_missing")
    if not addr.startswith(("http://", "https://")):
        raise CredentialBackendNotReadyError("openbao_addr_invalid")
    token = _openbao_write_token()
    path = "/".join(part for part in (prefix, ref) if part)
    quoted = urllib.parse.quote(path, safe="/")
    base = f"{addr}/v1/{mount}"
    context = _openbao_context(addr)
    try:
        _openbao_put(
            f"{base}/data/{quoted}", token, {"data": {ref: value}}, context,
        )
        version = "v2"
    except OpenBaoRequestError as exc:
        if str(exc) != "openbao_write_http_404":
            raise
        _openbao_put(f"{base}/{quoted}", token, {ref: value}, context)
        version = "v1"
    return {"ref": ref, "path": path, "kv_version": version, "backend": "openbao"}


def _openbao_value(payload: dict | None, ref: str) -> str | None:
    """
    兼容 KV v2 与 v1 两种响应：

        v2: {"data": {"data": {"REF": "..."}, "metadata": {...}}}
        v1: {"data": {"REF": "..."}}
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    inner = data.get("data")
    bucket = inner if isinstance(inner, dict) else data
    value = bucket.get(ref)
    return value if isinstance(value, str) and value else None


def _fetch_openbao(ref: str, config: CredentialConfig) -> str | None:
    """
    OpenBao 静态 KV 后端。

    地址 / 挂载点 / 路径前缀来自 v3_configs；令牌来自进程环境。
    先按 KV v2 请求 `/v1/<mount>/data/<path>`，404 再回退 KV v1 `/v1/<mount>/<path>`。
    两套都 404 才返回 None，绝不回落到环境变量假值。
    """
    addr = (config.openbao_addr or "").strip().rstrip("/")
    mount = (config.openbao_mount or "").strip().strip("/")
    prefix = (config.openbao_path_prefix or "").strip().strip("/")
    if not addr or not mount:
        raise CredentialBackendNotReadyError("openbao_config_missing")
    if not addr.startswith(("http://", "https://")):
        raise CredentialBackendNotReadyError("openbao_addr_invalid")
    token = _openbao_token()
    if not token:
        raise CredentialBackendNotReadyError("openbao_token_missing")

    path = "/".join(part for part in (prefix, ref) if part)
    quoted = urllib.parse.quote(path, safe="/")
    base = f"{addr}/v1/{mount}"
    context = _openbao_context(addr)
    v2 = _openbao_request(f"{base}/data/{quoted}", token, context)
    if v2 is not None:
        return _openbao_value(v2, ref)
    # v2 404 时再试 v1；v1 也 404 就是没有这条数据。
    # 只读策略常只放行 v2 data 路径，v1 回退会被 403 拒绝；既然 v2 已明确
    # 404，这里的 403 等价于「没有这条数据」，不要把它升级成后端故障。
    try:
        v1 = _openbao_request(f"{base}/{quoted}", token, context)
    except OpenBaoRequestError as exc:
        if str(exc) == "openbao_http_403":
            return None
        raise
    return _openbao_value(v1, ref)


def _fetch_sops(ref: str, config: CredentialConfig) -> str | None:
    """SOPS + age 后端：解密 dotenv 后按 CRED_ 引用名取值。"""
    values = load_sops_values(config.sops_file or None)
    return values.get(env_var_name(ref, config))


def _credential_encryption_key(settings: Settings | None) -> str:
    """取数据库加密凭据专用密钥；没配就明确报未接入，绝不回落明文。"""
    if settings is None:
        raise CredentialBackendNotReadyError("db_settings_missing")
    key = str(getattr(settings, "credential_encryption_key", "") or "").strip()
    if not key:
        raise CredentialBackendNotReadyError("credential_encryption_key_missing")
    return key


def _fetch_db(ref: str, config: CredentialConfig,
              settings: Settings | None) -> str | None:
    """
    数据库加密后端：从 v3_credential_secrets 读密文并解密。

    只返回明文给 resolve 包装成 Secret；本函数不写日志、不碰审计。
    表里没有这条引用时返回 None，让 resolve 按“无数据”处理，绝不回落 env。
    """
    key = _credential_encryption_key(settings)
    assert settings is not None  # _credential_encryption_key 已挡住 None，这里只给类型检查看
    with connect_app(settings) as conn:
        row = conn.execute(
            f"SELECT secret_enc FROM {CREDENTIAL_SECRET_TABLE} WHERE ref = %s",
            (ref,),
        ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return SecretBox(key).decrypt(str(row[0]))
    except (RuntimeError, ValueError) as exc:
        raise CredentialError("db_secret_decrypt_failed") from exc


def write_db_secret(
    ref: str, value: str, settings: Settings | None, *,
    actor_id: str = "system",
) -> dict:
    """
    把一条凭据加密写入数据库。只在 backend=db 的页面写入/迁移脚本里调用。

    返回结果只含引用名、存储类型和版本号，不回传明文或密文。
    """
    ref = str(ref or "").strip()
    if not ref or not CREDENTIAL_REF_RE.fullmatch(ref):
        raise CredentialBackendNotReadyError("credential_ref_invalid")
    value = str(value or "")
    if not value:
        raise CredentialBackendNotReadyError("credential_value_empty")
    key = _credential_encryption_key(settings)
    assert settings is not None
    try:
        sealed = SecretBox(key).encrypt(value)
    except ValueError as exc:
        raise CredentialBackendNotReadyError("credential_encryption_key_invalid") from exc
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                f"""
                INSERT INTO {CREDENTIAL_SECRET_TABLE}
                    (ref, secret_enc, key_version, updated_by, updated_at)
                VALUES (%s, %s, 1, %s, now())
                ON CONFLICT (ref) DO UPDATE SET
                    secret_enc = EXCLUDED.secret_enc,
                    key_version = {CREDENTIAL_SECRET_TABLE}.key_version + 1,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                RETURNING key_version
                """,
                (ref, sealed, str(actor_id)),
            ).fetchone()
    return {
        "ref": ref,
        "storage": "db",
        "key_version": int(row[0]) if row else 1,
        "written_at": datetime.now(UTC).isoformat(),
    }


FETCHERS: dict[str, Callable[[str, CredentialConfig], str | None]] = {
    "env": _fetch_env,
    "openbao": _fetch_openbao,
    "sops": _fetch_sops,
}


def load_config(settings: Settings) -> CredentialConfig:
    """从 v3_configs 读取凭据配置；未配置时使用本地默认值。

    地址、路径、周期都来自配置，不在代码里写死。自检可传入 config 跳过数据库。
    """
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT value FROM v3_configs WHERE namespace=%s AND key=%s",
            (NAMESPACE, CONFIG_KEY),
        ).fetchone()
    if not row or not isinstance(row[0], dict):
        return DEFAULT_CONFIG
    value = row[0]
    ref_map = value.get("env_ref_map") or {}
    return CredentialConfig(
        backend=value.get("backend", DEFAULT_CONFIG.backend),
        rotate_days=int(value.get("rotate_days", DEFAULT_CONFIG.rotate_days)),
        on_rotation_failure=value.get(
            "on_rotation_failure", DEFAULT_CONFIG.on_rotation_failure
        ),
        env_ref_map=ref_map if isinstance(ref_map, dict) else {},
        openbao_addr=value.get("openbao_addr", ""),
        openbao_mount=value.get("openbao_mount", ""),
        openbao_path_prefix=value.get("openbao_path_prefix", ""),
        sops_file=value.get("sops_file", ""),
        note=value.get("note", ""),
    )


def clear_cache() -> None:
    """清空进程内缓存。自检与运维手动轮换时用。"""
    _CACHE.clear()


def _no_data(ref: str, config: CredentialConfig, day: str, detail: str,
             *, degraded: bool = False, status: str = STATUS_NO_DATA,
             value: Secret | None = None) -> ResolveResult:
    """构造一个「没取到」的结果。所有失败路径都必须走它，不许各写各的。"""
    return ResolveResult(
        ref=ref, status=status, value=value, backend=config.backend,
        day=day, degraded=degraded, detail=detail,
    )


def resolve(
    ref: str,
    settings: Settings | None = None,
    *,
    config: CredentialConfig | None = None,
    actor_kind: str = "investigation",
    day: str | None = None,
) -> ResolveResult:
    """
    全平台唯一的凭据解析入口。

    config  注入配置（自检用，不连库）；不传就 load_config(settings) 读库。
    actor_kind  investigation（排查，可降级） / deployment（部署，硬失败）。
    day     注入当天日期（假时钟，测轮换）；不传用真实 UTC 日期。

    返回 ResolveResult，**不抛异常**：解析层不写审计、不碰事务，调用方
    见 status != ok 后按自己的事务边界决定回滚还是降级。
    """
    credential_config = config if config is not None else (
        load_config(settings) if settings is not None else DEFAULT_CONFIG
    )
    today = day or _utc_day()

    # 1) 引用名校验。空名字走无数据；格式不对走 invalid（人话仍是「无数据」）。
    if not ref:
        return _no_data(ref, credential_config, today, "无数据（引用名为空）")
    if not CREDENTIAL_REF_RE.fullmatch(ref):
        return _no_data(ref, credential_config, today, "无数据（引用名非法）",
                        status=STATUS_INVALID)

    # 2) 后端白名单。未知后端明确拒绝，不悄悄回落到默认后端。
    if credential_config.backend not in BACKENDS:
        return _no_data(ref, credential_config, today, "无数据（未知后端）",
                        status=STATUS_UNKNOWN_BACKEND)

    # 3) env 后端拒绝含短横线的引用名（防 CRED_A_B 撞名串凭据）。
    #    空环境变量名映射也一并挡住。
    if credential_config.backend == "env" and "-" in ref:
        return _no_data(ref, credential_config, today, "无数据（引用名含短横线，请用下划线）")

    key = (credential_config.backend, ref)
    cached = _CACHE.get(key)
    fresh = (
        cached is not None
        and _day_gap(cached[0], today) <= max(0, credential_config.rotate_days - 1)
    )
    if fresh:
        # 缓存命中：不调取数函数，不产生任何连接或外部动作。
        return ResolveResult(
            ref=ref, status=STATUS_OK, value=cached[1], backend=credential_config.backend,
            day=today, cache_hit=True,
            detail=data_quality_text("ok"),
        )

    # 4) 重取。db 后端要多一个 settings（连库 + 解密钥），单独分派，
    #    其余后端仍走原 FETCHERS 注册表，不改既有签名。
    try:
        if credential_config.backend == "db":
            credential_value = _fetch_db(ref, credential_config, settings)
        else:
            fetcher = FETCHERS.get(credential_config.backend)
            credential_value = fetcher(ref, credential_config) if fetcher else None
    except CredentialBackendNotReadyError:
        return _degrade(ref, credential_config, today, cached, actor_kind,
                        "无数据（未接入）")
    except Exception as exc:  # 只记异常类型，绝不把异常正文拼进结果
        return _degrade(ref, credential_config, today, cached, actor_kind,
                        f"无数据（取数失败：{type(exc).__name__}）")

    if credential_value is None or credential_value == "":
        return _degrade(ref, credential_config, today, cached, actor_kind, "无数据（无假值）")

    # 5) 取到了：只在成功时写缓存。换没换过值看旧缓存里的内容。
    secret = Secret(credential_value)
    changed = cached is None or cached[1].reveal() != credential_value
    _CACHE[key] = (today, secret)
    return ResolveResult(
        ref=ref, status=STATUS_OK, value=secret, backend=credential_config.backend,
        day=today, rotated=bool(changed and cached is not None),
        detail=data_quality_text("ok"),
    )


def _degrade(
    ref: str, config: CredentialConfig, today: str,
    cached: tuple[str, Secret] | None, actor_kind: str, detail: str,
) -> ResolveResult:
    """凭据重取失败时的降级处理。

    部署侧硬失败；排查侧在配置允许且旧值未过期时可继续只读取证，但标记为 no_data。
    """
    if actor_kind == "deployment":
        return _no_data(ref, config, today, "无数据（部署侧不允许沿用旧值）",
                        degraded=True)
    if (
        config.on_rotation_failure == "use_stale"
        and cached is not None
        and _day_gap(cached[0], today) <= max(0, config.rotate_days)
    ):
        return _no_data(
            ref, config, today, "无数据（沿用旧只读凭据，降级）",
            degraded=True, value=cached[1],
        )
    return _no_data(ref, config, today, detail, degraded=True)
