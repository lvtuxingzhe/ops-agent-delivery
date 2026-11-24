-- 037 回滚：删除 TOTP 一次性消费和失败锁定字段。
-- 只回滚新增列，不改变已有 TOTP 密钥或密码哈希。

BEGIN;

ALTER TABLE v3_auth_credentials
    DROP COLUMN IF EXISTS totp_locked_until,
    DROP COLUMN IF EXISTS totp_failed_attempts,
    DROP COLUMN IF EXISTS totp_last_counter;

COMMIT;
