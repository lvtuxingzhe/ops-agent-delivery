-- 020：过程事件补充父事件（A1 评审要求）
--
-- parent_event_id 记录“这条日志/结果由哪条 step 触发”，用于把同一命令的
-- 开始、逐行输出、结果串成一棵树；不建外键，避免事件清理受引用约束。

ALTER TABLE v3_task_trace_events
    ADD COLUMN IF NOT EXISTS parent_event_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_v3_trace_parent
    ON v3_task_trace_events (parent_event_id)
    WHERE parent_event_id IS NOT NULL;

COMMENT ON COLUMN v3_task_trace_events.parent_event_id IS
    '父事件 event_id；流式日志/结果挂到对应命令 step，空表示顶层事件';
