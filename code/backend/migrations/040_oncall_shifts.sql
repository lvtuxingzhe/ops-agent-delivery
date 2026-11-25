-- 040：审批值班表。
--
-- 目的：
--   1. 待审批和超时通知优先发给当前值班人，不再一律群发所有管理员；
--   2. 没有值班人时仍回落到全体管理员，避免审批没人看见；
--   3. 值班按时间段维护，删除只做软删除，保留历史。
--
-- 回滚：
--   backend/migrations/040_oncall_shifts.rollback.sql

BEGIN;

CREATE TABLE IF NOT EXISTS v3_oncall_shifts (
    id          UUID        PRIMARY KEY,
    scope       TEXT        NOT NULL DEFAULT 'approval',
    user_id     BIGINT      NOT NULL REFERENCES v3_users(id) ON DELETE CASCADE,
    starts_at   TIMESTAMPTZ NOT NULL,
    ends_at     TIMESTAMPTZ NOT NULL,
    note        TEXT        NOT NULL DEFAULT '',
    created_by  BIGINT      NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ,
    CONSTRAINT ck_v3_oncall_shifts_scope
        CHECK (scope IN ('approval')),
    CONSTRAINT ck_v3_oncall_shifts_time
        CHECK (starts_at < ends_at),
    CONSTRAINT ck_v3_oncall_shifts_note_len
        CHECK (char_length(note) <= 200)
);

CREATE INDEX IF NOT EXISTS idx_v3_oncall_shifts_current
    ON v3_oncall_shifts (scope, starts_at, ends_at)
    WHERE deleted_at IS NULL;

COMMENT ON TABLE v3_oncall_shifts IS
    '审批值班表；同一时间段允许安排多人，通知会发给所有当前值班人';
COMMENT ON COLUMN v3_oncall_shifts.deleted_at IS
    '软删除；历史值班记录保留，便于事后追溯当时谁在值班';

GRANT SELECT, INSERT, UPDATE ON v3_oncall_shifts TO ops_app;

COMMIT;
