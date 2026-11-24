-- 019 队列 kind 增加 deployment_plan（B1）
--
-- 背景：AI 现场规划要由独立 worker 执行，不能堵塞 API；队列 010 的 CHECK
-- 只允许 investigation / deployment / exec。本迁移只放宽枚举，不动已有行。
--
-- 回滚：先确认没有 deployment_plan 的 pending/running 行，再把 CHECK 改回
--       ('investigation','deployment','exec')。

ALTER TABLE v3_task_queue DROP CONSTRAINT IF EXISTS v3_task_queue_kind_check;
ALTER TABLE v3_task_queue
    ADD CONSTRAINT v3_task_queue_kind_check
    CHECK (kind IN ('investigation', 'deployment', 'exec', 'deployment_plan'));
