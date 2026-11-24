"""应用配置读取入口。"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """一次启动期间固定不变的配置快照。frozen=True 表示运行中禁止修改。"""

    app_name: str
    host: str
    port: int
    cors_origins: tuple[str, ...]
    log_level: str

    # 运行环境。production 固定真实执行，test 固定测试执行。
    # 运维不在生产环境里手工切模式；这个值由部署编排内置。
    environment: str
    # Prometheus 抓取令牌。为空时只允许本机回环访问，避免把任务量暴露到全网。
    metrics_token: str
    # Alertmanager Webhook Bearer Token；为空时 Webhook 直接关闭（失败关闭）。
    alert_webhook_token: str
    # Webhook 请求体上限，防止一个超大 payload 打满内存。
    alert_webhook_max_bytes: int
    # 集中 known_hosts 文件：worker 连接前按它校验主机公钥。
    ssh_known_hosts_file: str
    # 主机公钥策略：strict（只认已登记）/ prompt（首次未登记可人工确认）/ off（不校验）
    ssh_host_key_policy: str

    # ---- 数据库：应用、管理、审计只读三个连接身份 ----
    database_url: str           # 应用账号连接串（日常业务用它）
    db_admin_url: str           # 管理账号连接串（仅 init_db.py / init_auth.py 用）
    db_audit_reader_url: str    # 审计只读账号连接串（只按 trace_id 查审计记录）
    db_app_role: str            # 角色名，也是配置项，不写死
    db_audit_reader_role: str
    db_app_password: str        # 只在建角色时用，绝不进日志
    db_audit_reader_password: str

    # ---- 缓存 / 队列 / 对象存储 ----
    redis_url: str
    mq_url: str
    mq_management_url: str
    mq_user: str
    mq_password: str
    mq_queue_investigation: str  # 根因排查队列
    mq_queue_deployment: str     # 部署队列（两条队列分开，不能混）
    minio_endpoint: str
    minio_user: str
    minio_password: str
    minio_bucket: str
    # 排查截图原图单独一个私有桶；数据库只留元数据、对象键和缩略图。
    minio_screenshot_bucket: str
    # 审计归档单独一个桶：这个桶要开对象锁（COMPLIANCE），
    # 不能和报告/证据的普通桶混用，否则上传会被锁住。
    minio_audit_bucket: str
    audit_retention_days: int

    # ---- 接口限流 ----
    # 进程内固定窗口计数。单实例够用；多副本部署必须换成 Redis 共享计数，
    # 否则每个副本各给一份配额，等于把校验放大 N 倍。
    rate_limit_enabled: bool
    rate_limit_login_per_min: int
    rate_limit_tasks_per_min: int
    # 只有部署在可信反向代理后面才开：否则 X-Forwarded-For 可被伪造，
    # 攻击者换一个头就换一个「IP」，限流形同虚设。
    rate_limit_trust_xff: bool

    # ---- 部署执行流式日志（A2）----
    # 默认关闭：保持一次性返回结果的执行路径。
    exec_stream_enabled: bool

    # ---- 登录与权限 ----
    # 认证表专属数据库账号：只用来校验密码 / TOTP / 短信码。
    # 应用主账号 ops_app 对认证表零权限，所以这里必须是第四条独立连接串。
    db_auth_url: str
    db_auth_role: str
    db_auth_password: str
    # 用户管理专用连接：只给 /api/admin 的账号服务用，不用于登录校验。
    db_user_admin_url: str
    db_user_admin_role: str
    db_user_admin_password: str
    # 会话令牌（JWT）签名密钥与有效期。密钥只在环境变量里，开发期是假值。
    auth_token_secret: str
    auth_token_ttl_minutes: int
    # TOTP：发行方名称（二维码里显示「哪个系统的口令」）+ 密钥加密 key
    auth_totp_issuer: str
    totp_encryption_key: str
    # 数据库加密凭据专用的对称密钥。与 TOTP 密钥分开，避免一把钥匙开两套锁。
    credential_encryption_key: str
    # 短信通道：test = 开发期测试通道（码会回显），上线前换真实通道
    sms_channel: str
    sms_code_ttl_seconds: int
    # 连续登录失败几次锁账号、锁多久（防爆破）
    login_lockout_threshold: int
    login_lockout_minutes: int
    # 管理员账号：首次部署由它招人分角色（账号密码来自环境变量）
    admin_username: str
    admin_password: str
    admin_phone: str
    # 密码哈希强度。做成配置是为了低配机器能调低跑测试，不改代码。
    pbkdf2_rounds: int

    def __repr__(self) -> str:
        """
        只打印非敏感字段。

        dataclass 默认的 repr 会把所有字段拼出来，包括四个数据库密码、
        JWT 签名密钥、TOTP 加密密钥、管理员密码。一次 logger.debug(settings)
        或一条带 locals 的异常栈，就等于把整套凭据写进日志。
        所以这里把 repr 收窄到「可以公开的四项」。
        """
        return (
            f"Settings(app_name={self.app_name!r}, environment={self.environment!r}, "
            f"host={self.host!r}, port={self.port!r})"
        )

    @property
    def is_production(self) -> bool:
        """生产环境固定真实执行，测试环境固定测试执行。"""
        return self.environment == "production"


def _runtime_environment() -> str:
    """读取运行环境。

    优先 APP_ENV=production/test；未配置时按 APP_MODE 推断，二者都未配置按测试环境处理。
    """
    explicit = os.environ.get("APP_ENV", "").strip().lower()
    if explicit:
        return explicit
    legacy = os.environ.get("APP_MODE", "").strip().lower()
    if legacy:
        return "production" if legacy == "real" else "test"
    return "test"


def _get(name: str, default: str) -> str:
    """读一个环境变量；没配就用开发期默认值。"""
    value = os.environ.get(name)
    # 空字符串当作没配，避免 .env 里留了个空值把默认值顶掉
    return value if value else default


def _get_bool(name: str, default: bool) -> bool:
    """读布尔配置：true/1/yes 为真，其余（含拼错）一律按默认值处理。"""
    value = os.environ.get(name)
    if not value:
        return default
    return value.strip().lower() in ("true", "1", "yes")


def read_env(name: str) -> str | None:
    """按名称读取环境变量；未配置返回 None。

    空字符串不等同于已配置，避免配置缺失被当成有效值。
    """
    value = os.environ.get(name)
    return value if value else None


# ---------------------------------------------------------------------------
# SOPS + age：启动时解密一份 dotenv，值只进当前进程环境，不落明文文件。
# ---------------------------------------------------------------------------
CODE_ROOT = Path(__file__).resolve().parents[3]
SOPS_SECRETS_ENV = "SOPS_SECRETS_FILE"
SOPS_AGE_KEY_ENV = "SOPS_AGE_KEY_FILE"
SOPS_BIN_ENV = "SOPS_BIN"
DEFAULT_SECRETS_FILE = "secrets.encrypted.env"
DEFAULT_AGE_KEY_FILE = "var/secrets/age.key"
DEFAULT_SOPS_BIN = "var/tools/sops.exe"


def _project_path(value: str) -> Path:
    """把 .env 里的相对路径固定解析到 code/，不随进程 cwd 漂移。"""
    path = Path(value)
    return path if path.is_absolute() else (CODE_ROOT / path)


def parse_dotenv_text(text: str) -> dict[str, str]:
    """解析 dotenv 文本。只做 KEY=VALUE，值两端引号去掉，和 load_dotenv 一致。"""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def load_sops_values(path: str | None = None) -> dict[str, str]:
    """
    用 SOPS 解密 age 加密的 dotenv，返回键值。

    设计约束：
    * 文件不存在/未配置时返回空，不把「没配」当成错误 —— 开发机可以继续用
      环境变量跑测试；
    * 文件存在但解密失败时抛 RuntimeError，绝不回落到 .env 里的旧明文值；
    * 不回显 sops 的 stderr，避免把文件路径或片段带进日志。
    """
    secret_file = path or read_env(SOPS_SECRETS_ENV) or DEFAULT_SECRETS_FILE
    target = _project_path(secret_file)
    if not target.exists():
        return {}
    sops_bin = _project_path(read_env(SOPS_BIN_ENV) or DEFAULT_SOPS_BIN)
    if not sops_bin.exists():
        raise RuntimeError("sops_binary_missing")
    age_key = _project_path(read_env(SOPS_AGE_KEY_ENV) or DEFAULT_AGE_KEY_FILE)
    if not age_key.exists():
        raise RuntimeError("sops_age_key_missing")
    cmd = [str(sops_bin), "-d", "--input-type", "dotenv",
           "--output-type", "dotenv", str(target)]
    env = {**os.environ, "SOPS_AGE_KEY_FILE": str(age_key)}
    completed = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        timeout=60, cwd=str(CODE_ROOT), env=env,
    )
    if completed.returncode != 0:
        # 不拼 stderr：里面可能出现文件路径或密文片段
        raise RuntimeError("sops_decrypt_failed")
    return parse_dotenv_text(completed.stdout)


def load_dotenv(path: Path | None = None) -> int:
    """
    开发期便利函数：把 code/.env 里的 KEY=VALUE 读进当前进程环境。

    只由【本地脚本】和 main.py 的开发期便利调用（后者见 create_app 的注释：
    文件不存在直接跳过、已有环境变量优先不覆盖，生产环境零影响）。
    已有环境变量优先，不覆盖；文件不存在就静默跳过，返回读进来的条数。

    先读 .env 的引导项（SOPS 路径/age 私钥路径），再解密 secrets.encrypted.env；
    解密值只进当前进程，不写回 .env，也不出现在任何日志里。
    """
    dotenv_path = path or (Path(__file__).resolve().parents[3] / ".env")
    loaded = 0
    if dotenv_path.exists():
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # 去掉值两边可能有的引号，但不处理转义 —— 假值里用不到
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                loaded += 1

    # SOPS 的值优先于 .env 里的同名占位，但不覆盖外部显式注入的环境变量。
    for key, value in load_sops_values().items():
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def load_settings() -> Settings:
    """
    启动时调用一次，把散在环境里的配置收成一个对象。

    哪些有默认值：本机监听、端口、日志级别、队列名这类跟业务无关的东西。
    哪些没有默认值：所有连接串与密码（默认空串）。故意不给默认值 ——
    没配就是没配，绝不能因为「忘了配」就悄悄连去某个地方。
    """
    # CORS 白名单支持逗号分隔多个地址。网页端和桌面端加载的是同一份前端，
    # 所以共用这个白名单 —— 不给「我在本机运行」开任何后门。
    raw_origins = _get("CORS_ORIGINS", "http://127.0.0.1:5173")
    origins = tuple(item.strip() for item in raw_origins.split(",") if item.strip())

    return Settings(
        app_name=_get("APP_NAME", "运维 Agent 平台 · 后台"),
        host=_get("BACKEND_HOST", "127.0.0.1"),
        port=int(_get("BACKEND_PORT", "8000")),
        cors_origins=origins,
        log_level=_get("LOG_LEVEL", "INFO"),
        environment=_runtime_environment(),
        metrics_token=_get("METRICS_TOKEN", ""),
        alert_webhook_token=_get("ALERT_WEBHOOK_TOKEN", ""),
        alert_webhook_max_bytes=int(_get("ALERT_WEBHOOK_MAX_BYTES", "65536")),
        ssh_known_hosts_file=_get("SSH_KNOWN_HOSTS_FILE", "var/ssh/known_hosts"),
        ssh_host_key_policy=_get("SSH_HOST_KEY_POLICY", "prompt"),
        # ↓ 连接串一律不给默认值：漏配就报错，不误连
        database_url=_get("DATABASE_URL", ""),
        db_admin_url=_get("DB_ADMIN_URL", ""),
        db_audit_reader_url=_get("DB_AUDIT_READER_URL", ""),
        db_app_role=_get("DB_APP_ROLE", "ops_app"),
        db_audit_reader_role=_get("DB_AUDIT_READER_ROLE", "ops_audit_reader"),
        db_app_password=_get("DB_APP_PASSWORD", ""),
        db_audit_reader_password=_get("DB_AUDIT_READER_PASSWORD", ""),
        redis_url=_get("REDIS_URL", ""),
        mq_url=_get("MQ_URL", ""),
        mq_management_url=_get("MQ_MANAGEMENT_URL", ""),
        mq_user=_get("MQ_USER", ""),
        mq_password=_get("MQ_PASSWORD", ""),
        # 两条队列分开是定稿要求：根因排查和部署不能共用一个通道
        mq_queue_investigation=_get("MQ_QUEUE_INVESTIGATION", "ops.investigation"),
        mq_queue_deployment=_get("MQ_QUEUE_DEPLOYMENT", "ops.deployment"),
        minio_endpoint=_get("MINIO_ENDPOINT", ""),
        minio_user=_get("MINIO_USER", ""),
        minio_password=_get("MINIO_PASSWORD", ""),
        minio_bucket=_get("MINIO_BUCKET", "ops-agent-artifacts"),
        minio_screenshot_bucket=_get("MINIO_SCREENSHOT_BUCKET", "ops-agent-screenshots"),
        minio_audit_bucket=_get("MINIO_AUDIT_BUCKET", "ops-agent-audit"),
        audit_retention_days=int(_get("AUDIT_RETENTION_DAYS", "90")),
        # 接口限流：默认开启，登录 10 次/分钟、提交 20 次/分钟（按 IP）
        rate_limit_enabled=_get_bool("RATE_LIMIT_ENABLED", True),
        rate_limit_login_per_min=int(_get("RATE_LIMIT_LOGIN_PER_MIN", "10")),
        rate_limit_tasks_per_min=int(_get("RATE_LIMIT_TASKS_PER_MIN", "20")),
        rate_limit_trust_xff=_get_bool("RATE_LIMIT_TRUST_XFF", False),
        exec_stream_enabled=_get_bool("EXEC_STREAM_ENABLED", False),
        # 认证相关配置：密钥和密码不给默认值，漏配就明确报错
        db_auth_url=_get("DB_AUTH_URL", ""),
        db_auth_role=_get("DB_AUTH_ROLE", "ops_auth"),
        db_auth_password=_get("DB_AUTH_PASSWORD", ""),
        db_user_admin_url=_get("DB_USER_ADMIN_URL", ""),
        db_user_admin_role=_get("DB_USER_ADMIN_ROLE", "ops_user_admin"),
        db_user_admin_password=_get("DB_USER_ADMIN_PASSWORD", ""),
        auth_token_secret=_get("AUTH_TOKEN_SECRET", ""),
        auth_token_ttl_minutes=int(_get("AUTH_TOKEN_TTL_MINUTES", "480")),
        auth_totp_issuer=_get("AUTH_TOTP_ISSUER", "ops-agent"),
        # 正式变量名使用全称。
        totp_encryption_key=_get("TOTP_ENCRYPTION_KEY", ""),
        credential_encryption_key=_get("CREDENTIAL_ENCRYPTION_KEY", ""),
        sms_channel=_get("SMS_CHANNEL", "test"),
        sms_code_ttl_seconds=int(_get("SMS_CODE_TTL_SECONDS", "300")),
        login_lockout_threshold=int(_get("LOGIN_LOCKOUT_THRESHOLD", "5")),
        login_lockout_minutes=int(_get("LOGIN_LOCKOUT_MINUTES", "15")),
        admin_username=_get("ADMIN_USERNAME", "admin"),
        admin_password=_get("ADMIN_PASSWORD", ""),
        admin_phone=_get("ADMIN_PHONE", ""),
        # 轮数默认 10 万：自检能接受的速度里偏保守的一档，生产可调高
        pbkdf2_rounds=int(_get("PBKDF2_ROUNDS", "100000")),
    )


def require_database(settings: Settings) -> str:
    """业务要用库时调一下：没配连接串就明确报错，别拿空串去连。"""
    if not settings.database_url:
        raise RuntimeError(
            "缺少 DATABASE_URL。请复制 code/.env.example 为 .env 并填好连接串；"
            "代码里不允许写死默认地址。"
        )
    return settings.database_url


def validate_runtime(settings: Settings) -> None:
    """
    启动期的一致性校验：宁可起不来，也不要带着危险配置静默运行。

    production 有三条硬约束：不能用测试短信通道、必须配置 Redis、
    环境值不能拼错。测试通道会回显验证码，Redis 决定执行许可 nonce
    能否跨 worker 去重；任何一条缺失都直接拒绝启动，而不是只打警告。
    """
    if settings.environment not in ("production", "test"):
        raise RuntimeError(
            f"APP_ENV 只能是 production 或 test，当前是 {settings.environment!r}"
        )
    if settings.environment == "production" and settings.sms_channel == "test":
        raise RuntimeError(
            "production 环境不允许使用测试短信通道：测试通道会回显验证码。"
            "请换成真实通道，或把 APP_ENV 改成 test。"
        )
    # 执行许可的一次性 nonce 必须跨 worker 共享：没有 Redis 时进程内集合
    # 只能保证单 worker 内不重放，多副本会各跑一次，属于安全降级。
    if settings.environment == "production" and not str(settings.redis_url or "").strip():
        raise RuntimeError(
            "production 环境必须配置 REDIS_URL：执行许可的 nonce 去重依赖 Redis "
            "SET NX EX；未配置时 worker 会拒绝执行许可。"
        )

