-- 018：任务/模板过程事件表（A1）
--
-- 统一承载三类过程：
--   deployment_task 部署规划、审批前后的 AI/执行事件
--   template_version 模板 AI 生成过程，绑定模板版本而不是任务
--
-- event_id 是全局增量游标；local_seq 是执行器/Agent 本地顺序。
-- 业务时间线按 host_id + local_seq 排，after_event_id 只用于增量拉取。

CREATE TABLE IF NOT EXISTS v3_task_trace_events (
    event_id       BIGSERIAL   PRIMARY KEY,
    subject_type   TEXT        NOT NULL
                   CHECK (subject_type IN ('deployment_task', 'template_version')),
    subject_id     TEXT        NOT NULL,
    attempt_no     INTEGER     NOT NULL DEFAULT 1,
    host_id        TEXT        NOT NULL DEFAULT '',
    local_seq      INTEGER     NOT NULL DEFAULT 0,
    phase          TEXT        NOT NULL DEFAULT 'plan',
    kind           TEXT        NOT NULL DEFAULT 'step',
    level          TEXT        NOT NULL DEFAULT 'info',
    message        TEXT        NOT NULL DEFAULT '',
    payload        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (subject_type, subject_id, attempt_no, host_id, local_seq)
);

CREATE INDEX IF NOT EXISTS idx_v3_trace_subject
    ON v3_task_trace_events (subject_type, subject_id, event_id);
CREATE INDEX IF NOT EXISTS idx_v3_trace_created
    ON v3_task_trace_events (created_at);

COMMENT ON TABLE v3_task_trace_events IS
    '任务/模板过程事件：模板 AI、部署 AI、执行日志共用；写入失败不得阻断业务';
COMMENT ON COLUMN v3_task_trace_events.event_id IS
    '全局增量游标，只用于 after_event_id 拉取，不代表业务时间线';
COMMENT ON COLUMN v3_task_trace_events.local_seq IS
    '执行器/Agent 本地序号，业务时间线按 host_id + local_seq 排序';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON public.v3_task_trace_events TO ops_app';
        EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE public.v3_task_trace_events_event_id_seq TO ops_app';
    END IF;
END
$$;
