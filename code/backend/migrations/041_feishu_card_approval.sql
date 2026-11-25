-- 041：飞书应用卡片审批的身份绑定与事件去重。
--
-- 目的：
--   1. 只有手工绑定过的飞书成员才能点卡片批准/驳回，不能靠群里任何人点一下；
--   2. 同一条卡片事件只处理一次，避免飞书重试导致重复签发执行许可；
--   3. 保留绑定和回调处理记录，便于追查是谁、从哪个飞书账号批的。
--
-- 回滚：
--   backend/migrations/041_feishu_card_approval.rollback.sql

BEGIN;

CREATE TABLE IF NOT EXISTS v3_feishu_identities (
    id           UUID        PRIMARY KEY,
    open_id      TEXT        NOT NULL,
    user_id      BIGINT      NOT NULL REFERENCES v3_users(id) ON DELETE CASCADE,
    display_name TEXT        NOT NULL DEFAULT '',
    bound_by     BIGINT      NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ,
    CONSTRAINT ck_v3_feishu_identities_open_id
        CHECK (char_length(open_id) BETWEEN 10 AND 80)
);

-- 一个飞书账号同时只能绑定一个平台账号；解绑后可重新绑定。
CREATE UNIQUE INDEX IF NOT EXISTS uq_v3_feishu_identities_active_open_id
    ON v3_feishu_identities (open_id)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_v3_feishu_identities_user
    ON v3_feishu_identities (user_id)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS v3_feishu_events (
    id          UUID        PRIMARY KEY,
    event_id    TEXT        NOT NULL UNIQUE,
    open_id     TEXT        NOT NULL DEFAULT '',
    actor_id    BIGINT,
    task_id     UUID,
    action      TEXT        NOT NULL,
    result      TEXT        NOT NULL DEFAULT '',
    detail      TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_v3_feishu_events_task
    ON v3_feishu_events (task_id, created_at DESC);

COMMENT ON TABLE v3_feishu_identities IS
    '飞书成员 open_id 到平台账号的绑定；只允许手工绑定，解绑保留历史';
COMMENT ON TABLE v3_feishu_events IS
    '飞书卡片回调处理记录；event_id 唯一，用于挡住飞书重复投递';

GRANT SELECT, INSERT, UPDATE ON v3_feishu_identities TO ops_app;
GRANT SELECT, INSERT ON v3_feishu_events TO ops_app;

COMMIT;
