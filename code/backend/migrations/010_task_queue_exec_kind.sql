-- 010 队列 kind 增加 exec（S4-07）
--
-- 背景：审批通过后的执行任务要单独入队（kind='exec'），
-- 但 009 建的 CHECK 只允许 investigation / deployment，插入会被数据库直接拒。
-- 这条迁移只放宽 kind 枚举，不改表结构、不动已有数据。
--
-- 回滚：把 kind 改回 ('investigation','deployment')；若表里还有 exec 行，
--       先确认这些任务已结束或作废，否则回滚会被 CHECK 拒绝。
--       回滚必须包在事务里：DROP 与 ADD 分开提交会留下「无约束」的中间态。
--
--   BEGIN;
--   ALTER TABLE v3_task_queue DROP CONSTRAINT IF EXISTS v3_task_queue_kind_check;
--   ALTER TABLE v3_task_queue
--       ADD CONSTRAINT v3_task_queue_kind_check
--       CHECK (kind IN ('investigation', 'deployment'));
--   COMMIT;

ALTER TABLE v3_task_queue DROP CONSTRAINT IF EXISTS v3_task_queue_kind_check;
ALTER TABLE v3_task_queue
    ADD CONSTRAINT v3_task_queue_kind_check
    CHECK (kind IN ('investigation', 'deployment', 'exec'));
