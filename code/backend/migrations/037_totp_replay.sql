-- 037：TOTP 一次性消费与失败锁定。
--
-- 目的：同一 6 位动态口令在一个有效窗口内只能成功使用一次；连续输错
-- 达到阈值后锁定一段时间，避免已登录会话暴力试码。
--
-- 字段：
--   * totp_last_counter   最近一次成功消费的时间计数器（RFC 6238 的 counter）
--   * totp_failed_attempts TOTP 连续失败次数，独立于密码登录的 failed_attempts
--   * totp_locked_until   TOTP 失败锁定截止时间
--
-- 所有字段归 ops_auth 所有的 v3_auth_credentials，不新开表。

BEGIN;

ALTER TABLE v3_auth_credentials
    ADD COLUMN IF NOT EXISTS totp_last_counter BIGINT,
    ADD COLUMN IF NOT EXISTS totp_failed_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS totp_locked_until TIMESTAMPTZ;

COMMENT ON COLUMN v3_auth_credentials.totp_last_counter IS
    '最近一次成功消费的 TOTP 时间计数器；小于等于它的口令一律视为重放';
COMMENT ON COLUMN v3_auth_credentials.totp_failed_attempts IS
    'TOTP 连续失败次数；成功后清零，与密码登录失败计数分开';
COMMENT ON COLUMN v3_auth_credentials.totp_locked_until IS
    'TOTP 连续失败后的锁定截止时间；到期后允许重新尝试';

COMMIT;
