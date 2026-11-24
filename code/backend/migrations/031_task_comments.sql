-- 031：排查任务协作讨论。
--
-- 目的：
--   1. 让提交人、处理人和审计员围绕同一条任务留讨论；
--   2. 评论只追加、不提供编辑/删除入口，作者名存快照，避免改名后历史失真；
--   3. 评论正文不写审计事件，只记长度，避免敏感内容二次扩散。
--
-- 回滚：backend/migrations/031_task_comments.rollback.sql

BEGIN;

CREATE TABLE IF NOT EXISTS v3_task_comments (
    id           UUID        PRIMARY KEY,
    task_id      UUID        NOT NULL REFERENCES v3_tasks(id) ON DELETE CASCADE,
    author_id    BIGINT      NOT NULL,
    author_name  TEXT        NOT NULL,
    body         TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT v3_task_comments_body_len CHECK (char_length(body) BETWEEN 1 AND 2000)
);

CREATE INDEX IF NOT EXISTS v3_task_comments_task_idx
    ON v3_task_comments (task_id, created_at, id);

COMMENT ON TABLE v3_task_comments IS
    '任务协作讨论；只追加，不提供编辑或删除';
COMMENT ON COLUMN v3_task_comments.author_name IS
    '发评论时的显示名快照；用户改名不影响历史';
COMMENT ON COLUMN v3_task_comments.body IS
    '纯文本，1～2000 字；不写审计事件正文';

-- 应用账号只追加和读取，不给 UPDATE/DELETE；评论的“不可编辑”由权限兜底。
GRANT SELECT, INSERT ON v3_task_comments TO ops_app;

COMMIT;
