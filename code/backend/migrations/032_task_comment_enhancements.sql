-- 032：协作讨论增强。
--
-- 目的：
--   1. 支持 @ 提醒、未读计数和游标分页；
--   2. 撤回/清理只做软删除，保留审计可追溯；
--   3. 应用账号仍不能改正文，只能更新删除标记。

BEGIN;

ALTER TABLE v3_task_comments
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS deleted_by BIGINT,
    ADD COLUMN IF NOT EXISTS mentions BIGINT[] NOT NULL DEFAULT '{}'::bigint[];

CREATE INDEX IF NOT EXISTS v3_task_comments_live_idx
    ON v3_task_comments (task_id, created_at, id)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS v3_task_comment_reads (
    task_id      UUID        NOT NULL REFERENCES v3_tasks(id) ON DELETE CASCADE,
    user_id      BIGINT      NOT NULL,
    last_read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, user_id)
);

COMMENT ON TABLE v3_task_comment_reads IS
    '任务讨论已读游标；只存最后一次读到的时刻，不存评论正文';
COMMENT ON COLUMN v3_task_comments.mentions IS
    '@ 到的用户 id；写评论时按启用账号的用户名/显示名解析';
COMMENT ON COLUMN v3_task_comments.deleted_at IS
    '软删除时间；正文和作者快照保留，列表不再展示';

-- 评论正文仍不可改；只开放软删除两个列。
GRANT UPDATE (deleted_at, deleted_by) ON v3_task_comments TO ops_app;
GRANT SELECT, INSERT, UPDATE ON v3_task_comment_reads TO ops_app;

COMMIT;
