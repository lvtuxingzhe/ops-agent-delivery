-- 038：把任务讨论分成“普通讨论”和“排查线索”。
--
-- 目的：
--   1. 只有 kind='clue' 的评论才会被带进 Agent 排查上下文；
--   2. 普通讨论仍是人与人之间的留言板，不改变排查方向。
--
-- 回滚：
--   backend/migrations/038_task_comment_clues.rollback.sql

BEGIN;

ALTER TABLE v3_task_comments
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'discussion';

ALTER TABLE v3_task_comments
    DROP CONSTRAINT IF EXISTS ck_v3_task_comments_kind;
ALTER TABLE v3_task_comments
    ADD CONSTRAINT ck_v3_task_comments_kind
    CHECK (kind IN ('discussion', 'clue'));

CREATE INDEX IF NOT EXISTS idx_v3_task_comments_kind
    ON v3_task_comments (task_id, kind, created_at)
    WHERE deleted_at IS NULL;

COMMENT ON COLUMN v3_task_comments.kind IS
    'discussion=讨论；clue=排查线索，会进入 Agent 上下文';

COMMIT;
