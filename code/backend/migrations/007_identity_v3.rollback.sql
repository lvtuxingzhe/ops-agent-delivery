-- 007_identity_v3.rollback.sql
-- 007 的回滚：只删新建的 v3_* 认证表，旧表与旧数据一律不动。
--
-- 适用场景：007 已在线上执行、但后端代码还没切到 v3_*，需要退回原状态。
-- 注意：这会丢掉 007 之后写进 v3_* 认证表的增量（迁移窗口内不应有登录写入）。
-- 若已经切了代码，回滚要连同代码一起回，不能只回库。

BEGIN;

DROP TABLE IF EXISTS v3_confirmation_tokens;
DROP TABLE IF EXISTS v3_sms_codes;
DROP TABLE IF EXISTS v3_phone_bindings;

-- v3_auth_credentials 可能承载了切换后的新密码/新 TOTP，回滚前先导出：
--   COPY v3_auth_credentials TO '/tmp/v3_auth_credentials.csv' CSV HEADER;
DROP TABLE IF EXISTS v3_auth_credentials;

COMMIT;
