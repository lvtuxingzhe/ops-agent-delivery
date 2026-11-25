-- 038 回滚：删除评论类型列和索引。
BEGIN;

DROP INDEX IF EXISTS idx_v3_task_comments_kind;

ALTER TABLE v3_task_comments
    DROP CONSTRAINT IF EXISTS ck_v3_task_comments_kind;
ALTER TABLE v3_task_comments
    DROP COLUMN IF EXISTS kind;

COMMIT;
