-- 034 回滚：删除数据库加密凭据表。
-- 会丢掉所有已加密的凭据；执行前必须先导出或确认可以回到 OpenBao/env。

BEGIN;
DROP TABLE IF EXISTS v3_credential_secrets;
COMMIT;
