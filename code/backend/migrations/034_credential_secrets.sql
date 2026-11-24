-- 034：数据库加密凭据存储。
--
-- 目的：把 credential_ref 对应的真实凭据加密后存进本库，运行期按引用名解密。
-- 这样不依赖 OpenBao，也不需要改密码后重启进程。
--
-- 安全边界：
--   * 只存 Fernet 密文（secret_enc），不存明文；
--   * 加密密钥来自环境变量 CREDENTIAL_ENCRYPTION_KEY，独立于 TOTP_ENCRYPTION_KEY；
--   * 应用账号只有 SELECT/INSERT/UPDATE，没有 DELETE；
--   * 引用名不是秘密，可以出现在表里和审计里。

BEGIN;

CREATE TABLE IF NOT EXISTS v3_credential_secrets (
    ref         TEXT        PRIMARY KEY,
    secret_enc  TEXT        NOT NULL,
    key_version INTEGER     NOT NULL DEFAULT 1,
    updated_by  TEXT        NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT v3_credential_secrets_ref_ck
        CHECK (ref ~ '^[A-Za-z0-9_-]{1,64}$'),
    CONSTRAINT v3_credential_secrets_value_ck
        CHECK (length(secret_enc) > 0)
);

COMMENT ON TABLE v3_credential_secrets IS
    '按 credential_ref 存加密凭据；只存密文，明文只在进程内存里短暂出现';
COMMENT ON COLUMN v3_credential_secrets.secret_enc IS
    'Fernet 密文；密钥来自 CREDENTIAL_ENCRYPTION_KEY，丢失后无法解密';
COMMENT ON COLUMN v3_credential_secrets.key_version IS
    '同一引用每写一次加 1，用于排查是否发生过覆盖';

GRANT SELECT, INSERT, UPDATE ON v3_credential_secrets TO ops_app;

COMMIT;
