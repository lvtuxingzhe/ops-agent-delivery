-- 014：队列租约与执行中终止。
--
-- 解决的问题
-- ----------
-- 009 的队列只有 locked_at，没有「租约到期时间」。worker 进程被 kill
-- 或者机器断电后，running 行会永久占着，任务一直卡在执行中。
-- 另外执行中的命令原来没有终止通道：前端只能等，不能 kill。
--
-- 设计
-- ----
-- * lease_expires_at：领取时写入到期时间，worker 运行期间每 20 秒续租；
-- * heartbeat_at：最后一次续租时间，排障时能看出 worker 是否还活着；
-- * cancel_requested_at / cancel_reason：终止请求写入数据库，执行器轮询后 kill 子进程；
-- * 只加列，不改状态枚举；队列行仍用 queued/running/done/failed。
--
-- 回滚
-- ----
--   ALTER TABLE v3_task_queue
--     DROP COLUMN IF EXISTS cancel_reason,
--     DROP COLUMN IF EXISTS cancel_requested_at,
--     DROP COLUMN IF EXISTS heartbeat_at,
--     DROP COLUMN IF EXISTS lease_expires_at;
--
-- 授权：ops_app 对 v3_task_queue 已有表级 ALL 权限，新增列自动覆盖；
--       新环境仍由 init_roles 的 GRANT ... ON ALL TABLES 作为默认。

ALTER TABLE v3_task_queue
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz,
    ADD COLUMN IF NOT EXISTS cancel_requested_at timestamptz,
    ADD COLUMN IF NOT EXISTS cancel_reason text NOT NULL DEFAULT '';

COMMENT ON COLUMN v3_task_queue.lease_expires_at IS
    '租约到期时间；worker 每 20 秒续租，过期说明进程可能已死';
COMMENT ON COLUMN v3_task_queue.heartbeat_at IS
    '最后一次续租时间，用于排障，不参与业务状态';
COMMENT ON COLUMN v3_task_queue.cancel_requested_at IS
    '执行中终止请求时间；执行器轮询后 kill 子进程并结束';
COMMENT ON COLUMN v3_task_queue.cancel_reason IS
    '终止原因，只记短文本，不记命令内容';

CREATE INDEX IF NOT EXISTS v3_task_queue_lease_idx
    ON v3_task_queue (status, lease_expires_at);
