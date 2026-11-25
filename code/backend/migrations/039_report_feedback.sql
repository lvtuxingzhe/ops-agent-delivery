-- 039：逐条记录报告结论的人工反馈。
--
-- 目的：
--   1. 根因、每条结论、每个方案步骤都能单独标“准确 / 不准确 / 纠正”；
--   2. 反馈只记目标位置、结论快照和报告指纹，不复制整份报告；
--   3. 同一个人对同一位置只保留一条最新反馈，便于前端回显。
--
-- 回滚：
--   backend/migrations/039_report_feedback.rollback.sql

BEGIN;

CREATE TABLE IF NOT EXISTS v3_report_feedback (
    id              UUID        PRIMARY KEY,
    task_id         UUID        NOT NULL REFERENCES v3_tasks(id) ON DELETE CASCADE,
    target_type     TEXT        NOT NULL,
    target_index    INTEGER     NOT NULL,
    target_text     TEXT        NOT NULL,
    report_hash     TEXT        NOT NULL,
    verdict         TEXT        NOT NULL,
    corrected_text  TEXT        NOT NULL DEFAULT '',
    note            TEXT        NOT NULL DEFAULT '',
    created_by      BIGINT      NOT NULL,
    created_by_name TEXT        NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_v3_report_feedback_target
        CHECK (target_type IN ('root_cause', 'conclusion', 'solution')),
    CONSTRAINT ck_v3_report_feedback_verdict
        CHECK (verdict IN ('confirm', 'reject', 'correct')),
    CONSTRAINT ck_v3_report_feedback_text_len
        CHECK (char_length(corrected_text) <= 2000 AND char_length(note) <= 1000),
    CONSTRAINT uq_v3_report_feedback_target
        UNIQUE (task_id, created_by, target_type, target_index)
);

CREATE INDEX IF NOT EXISTS idx_v3_report_feedback_task
    ON v3_report_feedback (task_id, target_type, target_index);

COMMENT ON TABLE v3_report_feedback IS
    '报告逐条反馈；目标位置 + 报告指纹，任务报告更新后可识别旧反馈';
COMMENT ON COLUMN v3_report_feedback.target_index IS
    '根因固定 0；结论按 conclusions 下标；方案步骤按 steps 下标';
COMMENT ON COLUMN v3_report_feedback.target_text IS
    '提交反馈时的结论快照，便于报告更新后仍能看懂反馈指向';
COMMENT ON COLUMN v3_report_feedback.report_hash IS
    '提交时报告内容摘要；与当前报告不一致时前端提示可能已过期';

GRANT SELECT, INSERT, UPDATE ON v3_report_feedback TO ops_app;

COMMIT;
