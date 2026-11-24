-- 016：站内通知。
--
-- 解决的问题
-- ----------
-- 报告完成、进入审批后，提交人和管理员只能靠刷新页面发现；
-- 先做一个站内通知表，支持未读数、标记已读，不接外部渠道。
--
-- 设计
-- ----
-- * 一行一个收件人，按 user_id 隔离；
-- * dedupe_key 可空；有值时用部分唯一索引防止 worker 重试重复通知；
-- * task_id 只做跳转，不把报告正文复制进通知表。
--
-- 回滚
-- ----
--   DROP TABLE IF EXISTS v3_notifications;

CREATE TABLE IF NOT EXISTS v3_notifications (
    id          bigserial PRIMARY KEY,
    user_id     bigint NOT NULL REFERENCES v3_users(id) ON DELETE CASCADE,
    type        text NOT NULL,
    title       text NOT NULL,
    body        text NOT NULL DEFAULT '',
    task_id     uuid REFERENCES v3_tasks(id) ON DELETE CASCADE,
    dedupe_key  text,
    read_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE v3_notifications IS '站内通知；一行一个收件人，不存报告正文';
COMMENT ON COLUMN v3_notifications.dedupe_key IS '非空时用于去重，避免 worker 重试重复写通知';

CREATE UNIQUE INDEX IF NOT EXISTS v3_notifications_dedupe_idx
    ON v3_notifications (user_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS v3_notifications_inbox_idx
    ON v3_notifications (user_id, read_at, created_at DESC);
