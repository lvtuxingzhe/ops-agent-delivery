-- =============================================================================
-- 运维 Agent 平台 · 第 ③ 步：登录认证表（独立权限）+ 平台开关
--
-- 这份文件和 001 的分工
--   001 建的是业务表（users / roles / tasks / 审计记录……），归业务侧管；
--   002 建的是【认证专属表】，建成之后整体移交给独立的数据库账号 ops_auth
--   （角色名是配置项，实际移交在 backend/scripts/init_auth.py 里做）。
--
-- 为什么要独立权限（方案 8.2 的原话：密码「单独一张认证表、独立数据库权限，
-- 应用主账号查不到」）
--   应用主账号 ops_app 万一代码被注入、被 SQL 注入，它【物理上】也读不到
--   密码哈希和 TOTP 密钥 —— 因为数据库压根没授过这个权。
--   登录时后台用 ops_auth 这个专属连接去校验，别的业务一概用不到它。
--
-- 本文件里的表清单
--   auth_credentials  密码哈希 + TOTP 密钥（加密后存）+ 登录失败锁定状态
--   phone_bindings    手机号和账号的绑定关系（短信登录用）
--   sms_codes         短信验证码（只存哈希，短时有效、一次性、防刷计数）
--   platform_settings 平台级开关（登录方式开关、管理员自审批开关……）
--                      —— 这张是业务配置表，不是秘密，归业务侧读写
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 认证凭据表：一列秘密都别多存
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auth_credentials (
    user_id          BIGINT      PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    -- 密码【只存加盐哈希】，格式 pbkdf2_sha256$轮数$盐$哈希，见 core/security.py
    -- 明文密码在任何地方都不出现：不进日志、不进接口返回、不进报错。
    password_hash    TEXT        NOT NULL,
    -- TOTP 密钥用 Fernet 加密后存（密钥来自环境变量，开发期是假值）。
    -- 方案 8.2：绑定时展示一次二维码，之后不回显 —— 所以这里加密存，
    -- 只有「重新绑定」时才会再生成新的。
    totp_secret_enc  TEXT,
    totp_enabled     BOOLEAN     NOT NULL DEFAULT FALSE,
    totp_bound_at    TIMESTAMPTZ,
    -- 连续失败锁定（防爆破）：失败次数到阈值就锁一段时间，成功登录清零
    failed_attempts  INTEGER     NOT NULL DEFAULT 0,
    locked_until     TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE auth_credentials IS '认证专属表，owner 是独立数据库账号；应用主账号零权限（见 init_auth.py 授权）';

-- -----------------------------------------------------------------------------
-- 手机号绑定
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS phone_bindings (
    user_id     BIGINT      PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    phone       TEXT        NOT NULL UNIQUE,
    bound_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    bound_by    BIGINT      REFERENCES users(id)      -- 谁给绑的（管理员代绑会留痕）
);

-- -----------------------------------------------------------------------------
-- 短信验证码：只存哈希、短时有效、一次性、带防刷计数
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sms_codes (
    id          BIGSERIAL   PRIMARY KEY,
    phone       TEXT        NOT NULL,
    -- 验证码也只存哈希（HMAC）。明文码只通过短信通道发出去；
    -- 开发期测试通道的回显见 core/sms.py 注释，换真实通道后自然消失。
    code_hash   TEXT        NOT NULL,
    trace_id    TEXT        NOT NULL,          -- 发送也留审计链路号
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,                   -- 有值 = 已被用过，一次性
    attempts    INTEGER     NOT NULL DEFAULT 0, -- 校验失败次数，到 5 作废
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sms_codes_phone ON sms_codes(phone, created_at DESC);

-- -----------------------------------------------------------------------------
-- 平台开关（键值对）。这不是秘密表，业务账号要读它，所以归业务侧权限。
-- 已知的键（都给默认值，见 init_auth.py 的种子）：
--   auth_password_enabled   纯账号密码登录开不开（默认 true）
--   auth_totp_enabled       账号密码+TOTP 登录开不开（默认 true）
--   auth_sms_enabled        手机号短信登录开不开（默认 true）
--   self_approval_enabled   管理员自审批豁免开不开（默认 true，方案 8.4）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform_settings (
    key        TEXT        PRIMARY KEY,
    value      TEXT        NOT NULL,
    updated_by BIGINT      REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE platform_settings IS '平台级开关。改配置不改代码 —— 铁律「不写死」的落点之一';
