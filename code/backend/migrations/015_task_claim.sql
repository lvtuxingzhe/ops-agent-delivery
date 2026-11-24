-- 015：任务认领（处理人）。
--
-- 解决的问题
-- ----------
-- 多人同时看到同一条排查单时，不知道谁在处理；需要一个人先认领，
-- 后续交接也能在详情页看见。
--
-- 设计
-- ----
-- * assignee_id：当前处理人，空表示没人认领；
-- * claimed_at：认领时间；
-- * 不引入新状态，也不改任务状态机：认领只是协作字段。
--
-- 回滚
-- ----
--   ALTER TABLE v3_tasks
--     DROP COLUMN IF EXISTS claimed_at,
--     DROP COLUMN IF EXISTS assignee_id;

ALTER TABLE v3_tasks
    ADD COLUMN IF NOT EXISTS assignee_id bigint,
    ADD COLUMN IF NOT EXISTS claimed_at timestamptz;

COMMENT ON COLUMN v3_tasks.assignee_id IS
    '当前处理人；空表示未认领，同一时刻只允许一个处理人';
COMMENT ON COLUMN v3_tasks.claimed_at IS
    '认领时间；释放认领时清空';

CREATE INDEX IF NOT EXISTS v3_tasks_assignee_idx
    ON v3_tasks (assignee_id, status);
