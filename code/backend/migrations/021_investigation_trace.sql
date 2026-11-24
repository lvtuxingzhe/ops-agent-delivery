-- 021：排查任务也写入过程事件表。
--
-- 018 的事件表只允许 deployment_task / template_version，
-- 排查任务在运行中无法落 trace，页面只能等报告生成后看完整轨迹。
-- 这里把 subject_type 扩到 investigation_task，事件结构不变。

ALTER TABLE v3_task_trace_events
    DROP CONSTRAINT IF EXISTS v3_task_trace_events_subject_type_check;

ALTER TABLE v3_task_trace_events
    ADD CONSTRAINT v3_task_trace_events_subject_type_check
    CHECK (subject_type IN ('deployment_task', 'template_version', 'investigation_task'));

COMMENT ON TABLE v3_task_trace_events IS
    '任务/模板过程事件：模板 AI、部署 AI、排查 AI、执行日志共用；写入失败不得阻断业务';
