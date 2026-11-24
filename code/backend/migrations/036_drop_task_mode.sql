-- 036_drop_task_mode.sql
-- 删除任务表的通用 mode 字段。
--
-- 为什么删：
--   mode 同时表示运行环境、任务是否真实执行、worker 能否回退样例数据，
--   三种含义混在一起，历史数据还出现过 dry-run / test / real 三套值。
--   现在环境由部署编排决定，样例数据只由人工启动的演示进程开放，
--   任务记录不再需要这个字段。
--
-- 注意：
--   1. 这是不可逆迁移。执行前必须备份数据库，并导出历史任务快照：
--      SELECT id, trace_id, mode, created_at FROM v3_tasks ORDER BY created_at;
--   2. 执行前先停掉仍会写 mode 的旧 backend / worker，避免列删除后写失败。
--   3. 本文件不提供 rollback。列删掉后只能从备份或快照恢复历史值。
BEGIN;

ALTER TABLE public.v3_tasks
    DROP COLUMN IF EXISTS mode;

COMMIT;
